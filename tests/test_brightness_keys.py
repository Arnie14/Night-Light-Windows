# SPDX-License-Identifier: GPL-3.0-only
import pytest
from PySide6.QtCore import QObject, Signal

from nightlight.brightness_keys import BrightnessKeys
from nightlight.hardware.brightness import MonitorInfo

PANEL = MonitorInfo(0, "Built-in display", True, key="BOE0812", internal=True, minimum=1)
DELL = MonitorInfo(1, "DELL", True, key="DEL41A2")
TV = MonitorInfo(2, "TV", True, key="SAM7136", control="software", minimum=20)
OLD = MonitorInfo(3, "Old", False, key="ACI0001")


class FakeMonitors(QObject):
    brightnessChanged = Signal(int, object)
    monitorsChanged = Signal(list)

    def __init__(self, monitors):
        super().__init__()
        self.monitors = monitors
        self.brightness = {0: 50, 1: 50, 2: 50}
        self.sent = []
        self.reads = []
        self.own = False  # whether the app "just wrote" the panel

    def set_brightness(self, index, value):
        self.sent.append((index, value))

    def read(self, index):
        self.reads.append(index)

    def recently_written(self, index, seconds):
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
    assert monitors.sent == [(1, 10), (2, 20)]  # each display's own floor; the unsupported one skipped
    assert monitors.brightness[0] == 10  # the panel's slider follows too


def test_a_check_of_the_panel_finds_a_change(keys):
    keys, monitors, _ = keys
    monitors.brightnessChanged.emit(0, 50)  # first reading: nothing to compare with
    monitors.brightnessChanged.emit(0, 70)  # the keys moved it since
    assert monitors.sent == [(1, 70), (2, 70)]


def test_the_apps_own_changes_are_not_passed_on(keys):
    keys, monitors, _ = keys
    monitors.own = True  # e.g. the user dragged the panel's own slider in the flyout
    keys.on_windows_event(10)
    monitors.brightnessChanged.emit(0, 50)
    monitors.brightnessChanged.emit(0, 30)
    assert monitors.sent == []


def test_hidden_displays_are_left_alone(keys):
    keys, monitors, hidden = keys
    hidden["keys"] = frozenset({"DEL41A2"})
    keys.on_windows_event(10)
    assert monitors.sent == [(2, 20)]


def test_off_by_default_but_the_panel_slider_still_updates():
    monitors = FakeMonitors([PANEL, DELL])
    keys = BrightnessKeys(monitors)
    keys.on_windows_event(10)
    assert monitors.sent == [] and monitors.brightness[0] == 10


def test_the_panel_is_checked_while_on(keys):
    keys, monitors, _ = keys
    keys._read_panel()
    assert monitors.reads == [0]


def test_no_laptop_panel_no_checks():
    monitors = FakeMonitors([DELL])
    keys = BrightnessKeys(monitors)
    keys.set_enabled(True)
    assert not keys._poll.isActive()
    keys.on_windows_event(10)
    assert monitors.sent == []
