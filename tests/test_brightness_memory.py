# SPDX-License-Identifier: GPL-3.0-only
import json
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QObject, Signal

from nightlight import brightness_memory
from nightlight.brightness_memory import BrightnessMemory
from nightlight.config import BrightnessScheduleConfig, BrightnessStep
from nightlight.hardware.brightness import MonitorInfo, with_unique_keys

NOW = {"now": datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)}

DELL = MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2")
LG = MonitorInfo(1, "LG 27GL850", True, key="GSM5B7F")
PANEL = MonitorInfo(2, "Built-in display", True, key="BOE0812", internal=True, minimum=1)
NO_DDC = MonitorInfo(3, "Old monitor", False, key="ACI27A1")


class FakeManager(QObject):
    """MonitorManager's signals and the two calls BrightnessMemory makes."""

    monitorsChanged = Signal(list)
    brightnessChanged = Signal(int, object)
    brightnessSet = Signal(int, int)
    restoreDeferred = Signal(int)

    def __init__(self):
        super().__init__()
        self.monitors = []
        self.sent = []  # (index, value) set on the hardware
        self.asleep = set()  # indexes whose power state says standby/off
        self.faded = []  # whether each restore faded

    def monitor(self, index):
        return next((m for m in self.monitors if m.index == index), None)

    def detect(self, monitors, readings):
        """A detection: the list, then each monitor's current level (None: not answering)."""
        self.monitors = monitors
        self.monitorsChanged.emit(monitors)
        for monitor in monitors:
            if monitor.supported:
                self.brightnessChanged.emit(monitor.index, readings.get(monitor.index))

    def set_brightness(self, index, value):  # same order as MonitorManager
        self.sent.append((index, value))
        self.brightnessSet.emit(index, value)
        self.brightnessChanged.emit(index, value)

    def restore_brightness(self, index, value, fade=False):  # as MonitorManager's worker answers
        if index in self.asleep:
            self.restoreDeferred.emit(index)
            return
        self.sent.append((index, value))
        self.faded.append(fade)
        self.brightnessChanged.emit(index, value)


def make(tmp_path, levels=None, enabled=True):
    path = tmp_path / "brightness.json"
    if levels is not None:
        brightness_memory.save(levels, {}, path)
    manager = FakeManager()
    return manager, BrightnessMemory(manager, path, enabled, parent=manager, clock=lambda: NOW["now"]), path  # parent: kept alive with the manager


def test_restores_at_start(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30, "GSM5B7F": 80})
    manager.detect([DELL, LG, NO_DDC], {0: 30, 1: 50})
    assert manager.sent == [(1, 80)]  # the DELL was already right


def test_waits_for_a_monitor_that_does_not_answer_yet(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {0: None})  # straight after waking: no answer
    assert manager.sent == []
    manager.detect([DELL], {0: 100})  # the next scheduled detection
    assert manager.sent == [(0, 30)]
    manager.detect([DELL], {0: 100})  # restored once only, not on every detection
    assert manager.sent == [(0, 30)]


def test_leaves_monitors_that_stay_connected_alone(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30, "GSM5B7F": 80})
    manager.detect([DELL], {0: 30})
    manager.detect([DELL], {0: 65})  # changed with the monitor's own buttons
    assert manager.sent == []


def test_restores_a_reconnected_monitor(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30, "GSM5B7F": 80})
    manager.detect([DELL], {0: 30})
    manager.detect([DELL, LG], {0: 65, 1: 100})  # the LG is plugged in
    assert manager.sent == [(1, 80)]


def test_restores_everything_after_waking(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {0: 30})
    memory.restore_all_soon()
    manager.detect([DELL], {0: 100})
    assert manager.sent == [(0, 30)]


def test_remembers_levels_set_in_the_app_and_saves_them(tmp_path):
    manager, memory, path = make(tmp_path)
    manager.detect([DELL], {0: 70})
    manager.set_brightness(0, 45)
    assert memory.levels == {"DEL41A2": 45}
    memory.shutdown()  # writes the pending change
    saved = json.loads(path.read_text())
    assert saved["levels"] == {"DEL41A2": 45} and "DEL41A2" in saved["set_at"]


def test_levels_read_from_the_monitor_are_not_remembered(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {0: None})
    manager.brightnessChanged.emit(0, 100)  # a wrong level after waking...
    assert memory.levels == {"DEL41A2": 30}  # ...doesn't replace the remembered one


def test_user_change_wins_over_a_pending_restore(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {0: None})
    manager.set_brightness(0, 90)  # the user drags before the monitor answered
    manager.detect([DELL], {0: 90})
    assert manager.sent == [(0, 90)]


def test_laptop_panel_is_never_restored_to_black(tmp_path):
    manager, _, _ = make(tmp_path, {"BOE0812": 0})
    manager.detect([PANEL], {2: 60})
    assert manager.sent == [(2, 1)]


def test_turned_off_neither_restores_nor_saves(tmp_path):
    manager, memory, path = make(tmp_path, {"DEL41A2": 30}, enabled=False)
    manager.detect([DELL], {0: 100})
    manager.set_brightness(0, 55)
    memory.shutdown()
    assert manager.sent == [(0, 55)]  # no restore to 30
    assert brightness_memory.load(path)[0] == {"DEL41A2": 30}  # 55 not saved


def test_turning_on_does_not_restore_immediately(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30}, enabled=False)
    manager.detect([DELL], {0: 100})
    memory.set_enabled(True)
    manager.detect([DELL], {0: 100})
    assert manager.sent == []


def test_identical_monitors_are_told_apart(tmp_path):
    twins = with_unique_keys([MonitorInfo(0, "DELL", True, key="DEL41A2"), MonitorInfo(1, "DELL", True, key="DEL41A2")])
    assert [m.key for m in twins] == ["DEL41A2", "DEL41A2#2"]
    manager, _, _ = make(tmp_path, {"DEL41A2": 20, "DEL41A2#2": 60})
    manager.detect(twins, {0: 50, 1: 50})
    assert manager.sent == [(0, 20), (1, 60)]


def test_corrupt_or_odd_files_are_ignored(tmp_path):
    path = tmp_path / "brightness.json"
    path.write_text("{oops")
    assert brightness_memory.load(path) == ({}, {})
    path.write_text(json.dumps({"levels": {"A": 50, "B": "high", "C": 150, "D": True}, "set_at": {"A": "nonsense", "C": "2026-06-15T12:00:00+00:00"}}))
    levels, set_at = brightness_memory.load(path)
    assert levels == {"A": 50, "C": 100} and list(set_at) == ["C"]


def test_sleeping_monitor_is_restored_once_awake(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30})
    manager.asleep.add(0)
    manager.detect([DELL], {0: 100})  # answers reads, but its power state says standby
    assert manager.sent == []
    manager.asleep.clear()
    manager.detect([DELL], {0: 100})  # the next detection, now awake
    assert manager.sent == [(0, 30)]


def test_restores_do_not_count_as_the_users_choice(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {0: 100})
    assert manager.sent == [(0, 30)] and memory.levels == {"DEL41A2": 30}


# --- With the brightness schedule -------------------------------------------

SCHEDULE = BrightnessScheduleConfig(True, (BrightnessStep("07:00", 80), BrightnessStep("20:00", 30)))


def at(hour, minute=0):
    return datetime(2026, 6, 15, hour, minute, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def noon():
    NOW["now"] = at(12)


def test_schedule_level_applies_at_start(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    memory.set_schedule(SCHEDULE)
    manager.detect([DELL, LG], {0: 50, 1: 50})
    assert manager.sent == [(0, 80), (1, 80)]  # the schedule wins over an old remembered level


def test_a_manual_change_pauses_the_schedule_until_its_next_step(tmp_path):
    manager, memory, _ = make(tmp_path)
    memory.set_schedule(SCHEDULE)
    manager.detect([DELL], {0: 80})
    NOW["now"] = at(13)
    manager.set_brightness(0, 55)  # the user prefers 55 this afternoon
    memory.restore_all_soon()
    NOW["now"] = at(15)
    manager.detect([DELL], {0: 100})  # e.g. after sleep: their 55 comes back, not 80
    assert manager.sent[-1] == (0, 55)
    NOW["now"] = at(20, 1)
    memory._check_schedule()  # the next step: the schedule takes over again
    assert manager.sent[-1] == (0, 30) and manager.faded[-1] is True


def test_steps_are_applied_once_each(tmp_path):
    manager, memory, _ = make(tmp_path)
    memory.set_schedule(SCHEDULE)
    manager.detect([DELL], {0: 80})
    before = len(manager.sent)
    memory._check_schedule()
    memory._check_schedule()
    assert len(manager.sent) == before


def test_turning_the_schedule_on_applies_it_at_once(tmp_path):
    manager, memory, _ = make(tmp_path)
    manager.detect([DELL], {0: 50})
    memory.set_schedule(SCHEDULE)
    assert manager.sent == [(0, 80)]


def test_hidden_displays_are_left_alone(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30, "GSM5B7F": 30})
    memory.set_hidden(frozenset({"GSM5B7F"}))
    memory.set_schedule(SCHEDULE)
    manager.detect([DELL, LG], {0: 50, 1: 50})
    assert [index for index, _ in manager.sent] == [0]


def test_scheduled_steps_wait_for_a_sleeping_monitor(tmp_path):
    manager, memory, _ = make(tmp_path)
    memory.set_schedule(SCHEDULE)
    manager.detect([DELL], {0: 80})
    manager.asleep.add(0)  # displays off by 20:00
    NOW["now"] = at(20, 1)
    memory._check_schedule()
    assert manager.sent == []  # nothing sent to the sleeping monitor
    manager.asleep.clear()
    manager.detect([DELL], {0: 80})  # it wakes and answers
    assert manager.sent == [(0, 30)]
