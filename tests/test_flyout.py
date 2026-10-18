# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QObject, Signal

from nightlight.config import NightLightConfig
from nightlight.controller import NightLightController
from nightlight.hardware.brightness import MonitorInfo
from nightlight.monitors import STATUS_TEXT, DisplayState, Status
from nightlight.ui.flyout import Flyout
from nightlight.ui.widgets import MonitorBrightness

MONITORS = [
    MonitorInfo(0, "DELL", True, key="DEL"),
    MonitorInfo(1, "Built-in display", True, key="BOE", internal=True, minimum=1),
    MonitorInfo(2, "Old monitor", False, key="ACI", reason="DDC/CI didn't answer", detail="Turn DDC/CI on in its menu", status="unsupported"),
]


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


class FakeMonitors(QObject):
    monitorsChanged = Signal(list)
    displayChanged = Signal(str)
    error = Signal(str)

    def __init__(self, monitors=MONITORS):
        super().__init__()
        self.monitors = monitors
        self.states = {}
        for info in monitors:
            if info.supported:
                self.states[info.key] = DisplayState(info, Status.OK, observed=50)
            else:
                self.states[info.key] = DisplayState(info, Status.UNSUPPORTED, message=info.reason, detail=info.detail)
        self.sent = []

    def state(self, key):
        return self.states.get(key)

    def set_brightness(self, key, value, source="user"):
        self.sent.append((key, value))
        self.states[key].desired = value
        self.displayChanged.emit(key)

    def set_status(self, key, status):
        state = self.states[key]
        state.status = status
        state.message, state.detail = STATUS_TEXT.get(status, ("", ""))
        self.displayChanged.emit(key)

    def refresh(self, force=False):
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
    flyout._rows["DEL"].slider.setValue(80)
    assert monitors.sent == [("DEL", 80)]


def test_linked_sliders_move_every_monitor(flyout):
    flyout, monitors = flyout
    flyout.set_linked(True)
    flyout._rows["DEL"].slider.setValue(80)
    assert monitors.sent == [("DEL", 80), ("BOE", 80)]  # not the unsupported one
    assert flyout._rows["BOE"].slider.value() == 80  # its slider follows


def test_linked_never_takes_the_laptop_panel_to_zero(flyout):
    flyout, monitors = flyout
    flyout.set_linked(True)
    flyout._rows["DEL"].slider.setValue(0)
    assert monitors.sent == [("DEL", 0), ("BOE", 1)]


def test_linked_skips_a_display_that_cannot_be_changed_now(flyout):
    flyout, monitors = flyout
    flyout.set_linked(True)
    monitors.set_status("BOE", Status.NOT_RESPONDING)
    flyout._rows["DEL"].slider.setValue(30)
    assert monitors.sent == [("DEL", 30)]


def test_laptop_panel_slider_stops_at_one_percent(flyout):
    flyout, _ = flyout
    assert flyout._rows["BOE"].slider.minimum() == 1
    assert flyout._rows["DEL"].slider.minimum() == 0


def test_an_unsupported_display_has_a_disabled_slider_that_says_why(flyout):
    flyout, monitors = flyout
    row = flyout._rows["ACI"]
    assert not row.slider.isEnabled()
    assert row.explanation.text() == "DDC/CI didn't answer" and row.explanation.isVisibleTo(row)
    assert "Turn DDC/CI on" in row.explanation.toolTip()
    row.slider.setValue(70)  # even if it is moved somehow, nothing is sent
    assert monitors.sent == []


@pytest.mark.parametrize(
    "status",
    [Status.NOT_RESPONDING, Status.ASLEEP, Status.PAUSED, Status.GONE, Status.FAILING, Status.UNREADABLE, Status.READING, Status.DETECTING],
)
def test_sliders_are_disabled_whenever_the_display_cannot_be_changed(flyout, status):
    flyout, monitors = flyout
    monitors.set_status("DEL", status)
    row = flyout._rows["DEL"]
    assert not row.slider.isEnabled()
    assert row.explanation.text() == STATUS_TEXT[status][0]  # a few words saying why
    monitors.set_status("DEL", Status.OK)
    assert row.slider.isEnabled() and not row.explanation.isVisibleTo(row)


def test_a_reading_does_not_yank_a_held_slider(flyout):
    flyout, monitors = flyout
    row = flyout._rows["DEL"]
    row.slider.setSliderDown(True)
    row.slider.setValue(90)
    monitors.states["DEL"].observed, monitors.states["DEL"].desired = 20, None
    monitors.displayChanged.emit("DEL")
    assert row.slider.value() == 90
    row.slider.setSliderDown(False)


def test_popup_that_grows_while_open_stays_on_screen():
    """The first opening can happen before monitors are detected; when they
    arrive the panel grows, and must stay inside the screen's corner."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest

    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    monitors = FakeMonitors([])
    popup = Flyout(monitors, controller, as_window=False)
    popup.show_near(None)
    QTest.qWait(20)
    small = popup.geometry()
    grown = FakeMonitors([MonitorInfo(i, f"Monitor {i}", True, key=f"M{i}") for i in range(4)])
    monitors.monitors, monitors.states = grown.monitors, grown.states
    monitors.monitorsChanged.emit(monitors.monitors)
    QTest.qWait(20)
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert popup.height() > small.height()
    assert area.contains(popup.geometry()), (popup.geometry(), area)
    assert popup.geometry().bottom() == small.bottom()  # still against the taskbar edge
    popup.hide()
    controller.shutdown()


def test_displays_say_how_their_brightness_is_controlled():
    from nightlight.ui.widgets import SecondaryLabel

    def captions(info):
        row = MonitorBrightness(info, DisplayState(info, Status.OK, observed=50))  # kept alive while its labels are read
        return [label.text() for label in row.findChildren(SecondaryLabel) if label.text() not in ("…", "50%", "")]

    assert captions(MonitorInfo(0, "DELL", True)) == []
    assert captions(MonitorInfo(0, "HDR monitor", True, control="sdr")) == ["HDR · SDR content brightness"]
    assert captions(MonitorInfo(0, "TV", True, control="software", minimum=20)) == ["Software dimming"]
    assert MonitorBrightness(MonitorInfo(0, "TV", True, control="software", minimum=20)).slider.minimum() == 20
