# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QObject, Signal

from nightlight.config import NightLightConfig
from nightlight.controller import NightLightController
from nightlight.hardware.brightness import MonitorInfo
from nightlight.ui.flyout import Flyout

MONITORS = [
    MonitorInfo(0, "DELL", True, key="DEL"),
    MonitorInfo(1, "Built-in display", True, key="BOE", internal=True),
    MonitorInfo(2, "Old monitor", False, key="ACI"),
]


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


class FakeMonitors(QObject):
    monitorsChanged = Signal(list)
    brightnessChanged = Signal(int, object)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.monitors = MONITORS
        self.brightness = {0: 50, 1: 50}
        self.sent = []

    def set_brightness(self, index, value):
        self.sent.append((index, value))
        self.brightness[index] = value
        self.brightnessChanged.emit(index, value)

    def refresh(self):
        pass

    def reread_brightness(self):
        pass


@pytest.fixture
def flyout():
    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    monitors = FakeMonitors()
    flyout = Flyout(monitors, controller, as_window=True)
    yield flyout, monitors
    controller.shutdown()


def test_one_slider_moves_one_monitor(flyout):
    flyout, monitors = flyout
    flyout._rows[0].slider.setValue(80)
    assert monitors.sent == [(0, 80)]


def test_linked_sliders_move_every_monitor(flyout):
    flyout, monitors = flyout
    flyout.set_linked(True)
    flyout._rows[0].slider.setValue(80)
    assert monitors.sent == [(0, 80), (1, 80)]  # not the unsupported one
    assert flyout._rows[1].slider.value() == 80  # its slider follows


def test_linked_never_takes_the_laptop_panel_to_zero(flyout):
    flyout, monitors = flyout
    flyout.set_linked(True)
    flyout._rows[0].slider.setValue(0)
    assert monitors.sent == [(0, 0), (1, 1)]


def test_laptop_panel_slider_stops_at_one_percent(flyout):
    flyout, _ = flyout
    assert flyout._rows[1].slider.minimum() == 1
    assert flyout._rows[0].slider.minimum() == 0
