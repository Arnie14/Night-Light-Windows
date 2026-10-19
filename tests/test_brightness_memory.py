# SPDX-License-Identifier: GPL-3.0-only
import json
from datetime import datetime, timezone

from PySide6.QtCore import QObject, Signal

from nightlight import brightness_memory
from nightlight.brightness_memory import BrightnessMemory
from nightlight.hardware.brightness import MonitorInfo, with_unique_keys
from nightlight.monitors import RESTORE, SCHEDULE, USER

DELL = MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2")
LG = MonitorInfo(1, "LG 27GL850", True, key="GSM5B7F")
PANEL = MonitorInfo(2, "Built-in display", True, key="BOE0812", internal=True, minimum=1)
NO_DDC = MonitorInfo(3, "Old monitor", False, key="ACI27A1")
NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


class FakeManager(QObject):
    """MonitorManager's signals and the calls BrightnessMemory makes."""

    monitorsChanged = Signal(list)
    observed = Signal(str, object)
    brightnessSet = Signal(str, int, str)
    restoreDeferred = Signal(str, str)
    changedElsewhere = Signal(str, int)

    def __init__(self):
        super().__init__()
        self.monitors = []
        self.sent = []  # (key, value) set on the hardware
        self.asleep = set()  # keys whose power state says standby/off

    def monitor(self, key):
        return next((m for m in self.monitors if m.key == key), None)

    def detect(self, monitors, readings):
        """A detection: the list, then each monitor's current level (None: not answering)."""
        self.monitors = monitors
        self.monitorsChanged.emit(monitors)
        for monitor in monitors:
            if monitor.supported:
                self.observed.emit(monitor.key, readings.get(monitor.key))

    def set_brightness(self, key, value, source=USER):  # same order as MonitorManager
        self.sent.append((key, value))
        self.brightnessSet.emit(key, value, source)

    def restore_brightness(self, key, value, fade=False, source=RESTORE):  # as MonitorManager's service answers
        if key in self.asleep:
            self.restoreDeferred.emit(key, source)
            return
        self.sent.append((key, value))


def make(tmp_path, levels=None, enabled=True):
    path = tmp_path / "brightness.json"
    if levels is not None:
        brightness_memory.save(levels, {}, path)
    manager = FakeManager()
    memory = BrightnessMemory(manager, path, enabled, parent=manager, clock=lambda: NOON)  # parent: kept alive with the manager
    return manager, memory, path


def test_restores_at_start(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30, "GSM5B7F": 80})
    manager.detect([DELL, LG, NO_DDC], {"DEL41A2": 30, "GSM5B7F": 50})
    assert manager.sent == [("GSM5B7F", 80)]  # the DELL was already right


def test_waits_for_a_monitor_that_does_not_answer_yet(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {"DEL41A2": None})  # straight after waking: no answer
    assert manager.sent == []
    manager.detect([DELL], {"DEL41A2": 100})  # the next scheduled detection
    assert manager.sent == [("DEL41A2", 30)]
    manager.detect([DELL], {"DEL41A2": 100})  # restored once only, not on every detection
    assert manager.sent == [("DEL41A2", 30)]


def test_leaves_monitors_that_stay_connected_alone(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30, "GSM5B7F": 80})
    manager.detect([DELL], {"DEL41A2": 30})
    manager.detect([DELL], {"DEL41A2": 65})  # changed with the monitor's own buttons
    assert manager.sent == []


def test_restores_a_reconnected_monitor(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30, "GSM5B7F": 80})
    manager.detect([DELL], {"DEL41A2": 30})
    manager.detect([DELL, LG], {"DEL41A2": 65, "GSM5B7F": 100})  # the LG is plugged in
    assert manager.sent == [("GSM5B7F", 80)]


def test_a_monitor_found_at_another_position_is_still_itself(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30, "GSM5B7F": 80})
    from dataclasses import replace

    manager.detect([replace(LG, index=0), replace(DELL, index=1)], {"DEL41A2": 50, "GSM5B7F": 50})  # order swapped
    assert sorted(manager.sent) == [("DEL41A2", 30), ("GSM5B7F", 80)]


def test_restores_everything_after_waking(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {"DEL41A2": 30})
    memory.restore_all_soon()
    manager.detect([DELL], {"DEL41A2": 100})
    assert manager.sent == [("DEL41A2", 30)]


def test_remembers_levels_set_in_the_app_and_saves_them(tmp_path):
    manager, memory, path = make(tmp_path)
    manager.detect([DELL], {"DEL41A2": 70})
    manager.set_brightness("DEL41A2", 45)
    assert memory.levels == {"DEL41A2": 45}
    memory.shutdown()  # writes the pending change
    saved = json.loads(path.read_text())
    assert saved["levels"] == {"DEL41A2": 45} and saved["set_at"] == {"DEL41A2": NOON.isoformat()}
    assert brightness_memory.load(path) == ({"DEL41A2": 45}, {"DEL41A2": NOON})


def test_levels_set_by_the_schedule_are_remembered_too(tmp_path):
    # So a monitor reconnected in the evening comes back at the evening level.
    manager, memory, _ = make(tmp_path, {"DEL41A2": 80})
    manager.detect([DELL], {"DEL41A2": 80})
    manager.set_brightness("DEL41A2", 40, SCHEDULE)
    assert memory.levels == {"DEL41A2": 40}


def test_levels_read_from_the_monitor_are_not_remembered(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {"DEL41A2": None})
    manager.observed.emit("DEL41A2", 100)  # a wrong level after waking...
    assert memory.levels == {"DEL41A2": 30}  # ...doesn't replace the remembered one


def test_user_change_wins_over_a_pending_restore(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {"DEL41A2": None})
    manager.set_brightness("DEL41A2", 90)  # the user drags before the monitor answered
    manager.detect([DELL], {"DEL41A2": 90})
    assert manager.sent == [("DEL41A2", 90)]


def test_laptop_panel_is_never_restored_to_black(tmp_path):
    manager, _, _ = make(tmp_path, {"BOE0812": 0})
    manager.detect([PANEL], {"BOE0812": 60})
    assert manager.sent == [("BOE0812", 1)]


def test_turned_off_neither_restores_nor_remembers(tmp_path):
    manager, memory, path = make(tmp_path, {"DEL41A2": 30}, enabled=False)
    manager.detect([DELL], {"DEL41A2": 100})
    manager.set_brightness("DEL41A2", 55)
    memory.shutdown()
    assert manager.sent == [("DEL41A2", 55)]  # no restore to 30
    assert memory.levels == {"DEL41A2": 30}
    assert brightness_memory.load(path)[0] == {"DEL41A2": 30}  # 55 not saved


def test_turning_on_does_not_restore_immediately(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30}, enabled=False)
    manager.detect([DELL], {"DEL41A2": 100})
    memory.set_enabled(True)
    manager.detect([DELL], {"DEL41A2": 100})
    assert manager.sent == []


def test_identical_monitors_are_told_apart(tmp_path):
    twins = with_unique_keys([MonitorInfo(0, "DELL", True, key="DEL41A2"), MonitorInfo(1, "DELL", True, key="DEL41A2")])
    assert [m.key for m in twins] == ["DEL41A2", "DEL41A2#2"]
    manager, _, _ = make(tmp_path, {"DEL41A2": 20, "DEL41A2#2": 60})
    manager.detect(twins, {"DEL41A2": 50, "DEL41A2#2": 50})
    assert manager.sent == [("DEL41A2", 20), ("DEL41A2#2", 60)]


def test_levels_saved_by_older_versions_carry_over_to_the_serial_key(tmp_path):
    # Older versions keyed by hardware id only; now the EDID serial is part of the key.
    manager, memory, _ = make(tmp_path, {"DEL41A2": 25})
    dell = MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2:7XKJ9F3")
    manager.detect([dell], {"DEL41A2:7XKJ9F3": 70})
    assert manager.sent == [("DEL41A2:7XKJ9F3", 25)]
    assert memory.levels == {"DEL41A2:7XKJ9F3": 25}


def test_old_levels_are_not_guessed_between_two_identical_monitors(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 25})
    twins = [MonitorInfo(0, "DELL", True, key="DEL41A2:AAA"), MonitorInfo(1, "DELL", True, key="DEL41A2:BBB")]
    manager.detect(twins, {"DEL41A2:AAA": 70, "DEL41A2:BBB": 70})
    assert manager.sent == []  # which one had 25%? Unknown: neither gets it
    assert memory.levels == {"DEL41A2": 25}


def test_corrupt_or_odd_files_are_ignored(tmp_path):
    path = tmp_path / "brightness.json"
    path.write_text("{oops")
    assert brightness_memory.load(path) == ({}, {})
    path.write_text(json.dumps({"levels": {"A": 50, "B": "high", "C": 150, "D": True}}))  # version 1: levels only
    assert brightness_memory.load(path) == ({"A": 50, "C": 100}, {})
    path.write_text(json.dumps({"levels": {"A": 50, "C": 150}, "set_at": {"A": "nonsense", "C": "2026-06-15T12:00:00+00:00",
                                                                        "Z": "2026-06-15T12:00:00+00:00", "N": "2026-06-15T12:00:00"}}))
    levels, set_at = brightness_memory.load(path)
    assert levels == {"A": 50, "C": 100} and list(set_at) == ["C"]  # no level, or no time zone: dropped
    path.write_text(json.dumps(["not", "a", "dict"]))
    assert brightness_memory.load(path) == ({}, {})


def test_sleeping_monitor_is_restored_once_awake(tmp_path):
    manager, _, _ = make(tmp_path, {"DEL41A2": 30})
    manager.asleep.add("DEL41A2")
    manager.detect([DELL], {"DEL41A2": 100})  # answers reads, but its power state says standby
    assert manager.sent == []
    manager.asleep.clear()
    manager.detect([DELL], {"DEL41A2": 100})  # the next detection, now awake
    assert manager.sent == [("DEL41A2", 30)]


def test_restores_do_not_count_as_the_users_choice(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {"DEL41A2": 100})
    assert manager.sent == [("DEL41A2", 30)] and memory.levels == {"DEL41A2": 30}


def test_a_deferred_schedule_change_is_not_a_restore(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {"DEL41A2": 30})
    manager.restoreDeferred.emit("DEL41A2", SCHEDULE)  # the scheduler retries that itself
    manager.detect([DELL], {"DEL41A2": 60})
    assert manager.sent == []


def test_hidden_displays_are_left_alone(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30, "GSM5B7F": 30})
    memory.set_hidden(frozenset({"GSM5B7F"}))
    manager.detect([DELL, LG], {"DEL41A2": 50, "GSM5B7F": 50})
    assert manager.sent == [("DEL41A2", 30)]
    assert memory.target(LG) is None


def test_hidden_can_be_a_predicate_that_knows_older_keys(tmp_path):
    # AppConfig.hidden finds displays hidden under their key from an older version.
    manager, memory, _ = make(tmp_path, {"DEL41A2:7XKJ9F3": 30})
    memory.set_hidden(lambda key: key.split(":")[0] == "DEL41A2")
    manager.detect([MonitorInfo(0, "DELL", True, key="DEL41A2:7XKJ9F3")], {"DEL41A2:7XKJ9F3": 70})
    assert manager.sent == []


def test_hiding_a_display_cancels_its_pending_restore(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {"DEL41A2": None})  # due, not answering yet
    memory.set_hidden({"DEL41A2"})
    manager.detect([DELL], {"DEL41A2": 100})
    assert manager.sent == []


def test_restores_wait_while_dimmed_for_idle(tmp_path):
    manager, memory, _ = make(tmp_path, {"DEL41A2": 30})
    manager.detect([DELL], {"DEL41A2": 30})
    memory.set_held(True)
    memory.restore_all_soon()
    manager.detect([DELL], {"DEL41A2": 20})  # e.g. a wake while dimmed
    assert manager.sent == []  # nothing brightened the idle screen
    memory.set_held(False)
    assert memory.target(DELL) == 30  # what coming back sets it to (IdleDimmer does that)


def test_a_level_set_with_the_laptops_keys_is_not_undone_at_the_next_start(tmp_path):
    # Set 60% in the app, then 45% with the keys: the next start used to put 60% back.
    manager, memory, path = make(tmp_path, {"BOE0812": 60})
    manager.detect([PANEL], {"BOE0812": 60})
    manager.changedElsewhere.emit("BOE0812", 45)  # Windows reported the keys' change
    memory.shutdown()
    assert memory.levels == {} and brightness_memory.load(path)[0] == {}
    manager2, _, _ = make(tmp_path)
    manager2.detect([PANEL], {"BOE0812": 45})
    assert manager2.sent == []  # 45 stays


def test_a_change_elsewhere_cancels_a_pending_restore(tmp_path):
    manager, memory, _ = make(tmp_path, {"BOE0812": 60})
    manager.detect([PANEL], {"BOE0812": None})  # due, not read yet
    manager.changedElsewhere.emit("BOE0812", 45)
    manager.detect([PANEL], {"BOE0812": 45})
    assert manager.sent == []
