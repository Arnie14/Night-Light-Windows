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
    MonitorInfo(1, "Built-in display", True, key="BOE", internal=True, minimum=1),
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


def test_popup_that_grows_while_open_stays_on_screen():
    """The first opening can happen before monitors are detected; when they
    arrive the panel grows, and must stay inside the screen's corner."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest

    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    monitors = FakeMonitors()
    monitors.monitors = []
    popup = Flyout(monitors, controller, as_window=False)
    popup.show_near(None)
    QTest.qWait(20)
    small = popup.geometry()
    monitors.monitors = [MonitorInfo(i, f"Monitor {i}", True, key=f"M{i}") for i in range(4)]
    monitors.brightness = {i: 50 for i in range(4)}
    monitors.monitorsChanged.emit(monitors.monitors)
    QTest.qWait(20)
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert popup.height() > small.height()
    assert area.contains(popup.geometry()), (popup.geometry(), area)
    assert popup.geometry().bottom() == small.bottom()  # still against the taskbar edge
    popup.hide()
    controller.shutdown()


def test_displays_say_how_their_brightness_is_controlled():
    from nightlight.ui.widgets import MonitorBrightness, SecondaryLabel

    def captions(info):
        row = MonitorBrightness(info)  # kept alive while its labels are read
        return [label.text() for label in row.findChildren(SecondaryLabel) if label.text() != "…"]

    assert captions(MonitorInfo(0, "DELL", True)) == []
    assert captions(MonitorInfo(0, "HDR monitor", True, control="sdr")) == ["HDR · SDR content brightness"]
    assert captions(MonitorInfo(0, "TV", True, control="software", minimum=20)) == ["Software dimming"]
    assert MonitorBrightness(MonitorInfo(0, "TV", True, control="software", minimum=20)).slider.minimum() == 20
