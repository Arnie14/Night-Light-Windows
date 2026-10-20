# SPDX-License-Identifier: GPL-3.0-only
import pytest
from PySide6.QtCore import QObject, Signal

from nightlight.brightness.keys import BrightnessKeys
from nightlight.hardware.brightness import MonitorInfo

PANEL = MonitorInfo(0, "Built-in display", True, key="BOE0812", internal=True, minimum=1)
DELL = MonitorInfo(1, "DELL", True, key="DEL41A2")
TV = MonitorInfo(2, "TV", True, key="SAM7136", control="software", minimum=20)
OLD = MonitorInfo(3, "Old", False, key="ACI0001")


class FakeMonitors(QObject):
    """MonitorManager as BrightnessKeys uses it (by display key)."""

    observed = Signal(str, object)
    monitorsChanged = Signal(list)

    def __init__(self, monitors):
        super().__init__()
        self.monitors = monitors
        self.brightness = {"BOE0812": 50, "DEL41A2": 50, "SAM7136": 50}
        self.sent = []
        self.reads = []
        self.own = False  # whether the app "just wrote" the panel

    def value(self, key):
        return self.brightness.get(key)

    def note_level(self, key, value):  # shown on the slider, like a reading
        self.brightness[key] = value

    def set_brightness(self, key, value, source="user", fade=False):
        self.sent.append((key, value))

    def read(self, key):
        self.reads.append(key)

    def recently_written(self, key, seconds):
        return self.own


@pytest.fixture
def keys():
    monitors = FakeMonitors([PANEL, DELL, TV, OLD])
    hidden = {"keys": frozenset()}
    keys = BrightnessKeys(monitors, lambda: hidden["keys"])
    keys.set_enabled(True)
    yield keys, monitors, hidden
    keys.set_enabled(False)


def test_windows_reporting_the_panel_moves_external_displays(keys):
    keys, monitors, _ = keys
    keys.on_windows_event(10)
    assert monitors.sent == [("DEL41A2", 10), ("SAM7136", 20)]  # each display's own floor; the unsupported one skipped
    assert monitors.brightness["BOE0812"] == 10  # the panel's slider follows too


def test_a_check_of_the_panel_finds_a_change(keys):
    keys, monitors, _ = keys
    monitors.observed.emit("BOE0812", 50)  # first reading: nothing to compare with
    monitors.observed.emit("BOE0812", 70)  # the keys moved it since
    assert monitors.sent == [("DEL41A2", 70), ("SAM7136", 70)]


def test_the_apps_own_changes_are_not_passed_on(keys):
    keys, monitors, _ = keys
    monitors.own = True  # e.g. the user dragged the panel's own slider in the flyout
    keys.on_windows_event(10)
    monitors.observed.emit("BOE0812", 50)
    monitors.observed.emit("BOE0812", 30)
    assert monitors.sent == []


def test_hidden_displays_are_left_alone(keys):
    keys, monitors, hidden = keys
    hidden["keys"] = frozenset({"DEL41A2"})
    keys.on_windows_event(10)
    assert monitors.sent == [("SAM7136", 20)]


def test_off_by_default_but_the_panel_slider_still_updates():
    monitors = FakeMonitors([PANEL, DELL])
    keys = BrightnessKeys(monitors)
    keys.on_windows_event(10)
    assert monitors.sent == [] and monitors.brightness["BOE0812"] == 10


def test_the_panel_is_checked_while_on(keys):
    keys, monitors, _ = keys
    keys._read_panel()
    assert monitors.reads == ["BOE0812"]


def test_no_laptop_panel_no_checks():
    monitors = FakeMonitors([DELL])
    keys = BrightnessKeys(monitors)
    keys.set_enabled(True)
    assert not keys._poll.isActive()
    keys.on_windows_event(10)
    assert monitors.sent == []


def test_a_reading_from_another_display_is_not_the_panel(keys):
    keys, monitors, _ = keys
    monitors.observed.emit("DEL41A2", 50)
    monitors.observed.emit("DEL41A2", 90)
    monitors.observed.emit("BOE0812", None)  # not answering: nothing to compare
    assert monitors.sent == []


def test_a_new_detection_forgets_the_last_panel_level(keys):
    # After a display change the next reading is a baseline, not a key press.
    keys, monitors, _ = keys
    monitors.observed.emit("BOE0812", 50)
    monitors.monitorsChanged.emit(monitors.monitors)
    monitors.observed.emit("BOE0812", 80)
    assert monitors.sent == []
