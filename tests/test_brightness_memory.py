# SPDX-License-Identifier: GPL-3.0-only
import json

from PySide6.QtCore import QObject, Signal

from nightlight import brightness_memory
from nightlight.brightness_memory import BrightnessMemory
from nightlight.hardware.brightness import MonitorInfo, with_unique_keys

DELL = MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2")
LG = MonitorInfo(1, "LG 27GL850", True, key="GSM5B7F")
PANEL = MonitorInfo(2, "Built-in display", True, key="BOE0812", internal=True)
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

    def restore_brightness(self, index, value):  # as MonitorManager's worker answers
        if index in self.asleep:
            self.restoreDeferred.emit(index)
            return
        self.sent.append((index, value))
        self.brightnessChanged.emit(index, value)


def make(tmp_path, levels=None, enabled=True):
    path = tmp_path / "brightness.json"
    if levels is not None:
        brightness_memory.save(levels, path)
    manager = FakeManager()
    return manager, BrightnessMemory(manager, path, enabled, parent=manager), path  # parent: kept alive with the manager


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
    assert json.loads(path.read_text())["levels"] == {"DEL41A2": 45}


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
    assert manager.sent == [(2, brightness_memory.MIN_INTERNAL)]


def test_turned_off_neither_restores_nor_remembers(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30}, enabled=False)
    manager.detect([DELL], {0: 100})
    manager.set_brightness(0, 55)
    assert manager.sent == [(0, 55)]
    assert memory.levels == {"DEL41A2": 30}


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
    assert brightness_memory.load(path) == {}
    path.write_text(json.dumps({"levels": {"A": 50, "B": "high", "C": 150, "D": True}}))
    assert brightness_memory.load(path) == {"A": 50, "C": 100}


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
