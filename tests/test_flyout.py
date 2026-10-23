# SPDX-License-Identifier: GPL-3.0-only
from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QObject, Signal

from nightlight.brightness.monitors import STATUS_TEXT, DisplayState, Status
from nightlight.color.controller import NightLightController
from nightlight.config import NightLightConfig
from nightlight.hardware.brightness import MonitorInfo
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

    def retry(self):
        pass

    def reread_brightness(self):
        pass


@pytest.fixture
def flyout():
    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
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

    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
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


def test_hidden_displays_and_custom_names(flyout):
    from nightlight.config import DisplayPrefs

    flyout, monitors = flyout
    flyout.set_display_prefs((DisplayPrefs("DEL", "Left screen"), DisplayPrefs("BOE", "", True)))
    assert sorted(flyout._rows) == ["ACI", "DEL"]  # the laptop panel is hidden
    from PySide6.QtWidgets import QLabel

    assert "Left screen" in [label.text() for label in flyout._rows["DEL"].findChildren(QLabel)]
    flyout.set_linked(True)
    flyout._rows["DEL"].slider.setValue(80)
    assert monitors.sent == [("DEL", 80)]  # a hidden display isn't changed with the others


def test_names_and_hiding_saved_under_an_older_key_still_apply(flyout):
    # The app passes AppConfig.display, which finds prefs saved by beta 4
    # under the hardware id alone; these displays' keys add a serial.
    from dataclasses import replace

    from nightlight.config import AppConfig, DisplayPrefs

    flyout, monitors = flyout
    monitors.monitors = [replace(m, key=f"{m.key}:SERIAL1") for m in monitors.monitors]
    prefs = AppConfig(displays=(DisplayPrefs("DEL", "Left screen"), DisplayPrefs("BOE", "", True)))
    flyout.set_display_prefs(prefs.display)
    assert sorted(flyout._rows) == ["ACI:SERIAL1", "DEL:SERIAL1"]


def test_every_display_hidden_says_where_to_show_them(flyout):
    from nightlight.config import DisplayPrefs

    flyout, monitors = flyout
    flyout.set_display_prefs(tuple(DisplayPrefs(m.key, "", True) for m in monitors.monitors))
    assert flyout._rows == {} and "Settings > Displays" in flyout._monitor_status.text()


def test_windows_night_light_notice(flyout):
    flyout, _ = flyout
    assert flyout.windows_night_light.isHidden()
    flyout.set_windows_night_light(True)
    assert not flyout.windows_night_light.isHidden()


def test_a_paused_display_is_named_in_the_panel():
    # Night light pauses on the display showing a chosen app or a fullscreen
    # game; with several displays, the panel says which one, by its own name.
    from dataclasses import replace

    from nightlight.config import DisplayPrefs, Mode

    monitors = FakeMonitors([replace(MONITORS[0], display=r"\.\DISPLAY1"), replace(MONITORS[1], display=r"\.\DISPLAY2")])
    from nightlight.hardware.gamma import FakeGammaBackend

    controller = NightLightController(FakeGammaBackend(), NightLightConfig(enabled=True, mode=Mode.ALWAYS),
                                      lambda: datetime(2026, 6, 15, 22, tzinfo=UTC))
    try:
        flyout = Flyout(monitors, controller, as_window=True)
        flyout.set_display_prefs((DisplayPrefs("DEL", "Left screen"),))
        controller.set_paused({r"\.\DISPLAY1": "photoshop.exe"})
        assert "Paused on Left screen for photoshop.exe" in flyout._night_text.text()
        controller.set_paused({r"\.\DISPLAY2": "game.exe, fullscreen"})
        assert "Paused on Built-in display for a fullscreen game.exe" in flyout._night_text.text()
        controller.set_paused({r"\.\DISPLAY9": "an app, fullscreen"})  # not a known display
        assert "Paused on display 9 for a fullscreen app" in flyout._night_text.text()
        controller.set_paused({})
        assert "Paused" not in flyout._night_text.text()
    finally:
        controller.shutdown()


def test_suspend_button_suspends_until_resumed_with_timed_choices_on_its_arrow():
    from PySide6.QtWidgets import QToolButton

    from nightlight.config import Mode
    from nightlight.core.schedule import OverrideKind

    night = NightLightConfig(enabled=True, mode=Mode.TIMES, night_start="20:00", day_start="06:00")
    controller = NightLightController(FakeGamma(), night, lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
    panel = Flyout(FakeMonitors(), controller, as_window=True)
    button = panel._toggle
    # In the day too: suspended, the evening doesn't turn night light on.
    assert button.text() == "Suspend" and button.popupMode() is QToolButton.ToolButtonPopupMode.MenuButtonPopup
    assert button.menu().actions()[0].text() == "For 1 hour"  # the timed choices
    button.click()
    assert controller.override.kind is OverrideKind.SUSPEND and controller.override.until is None
    assert button.text() == "Resume" and button.menu() is None
    button.click()
    assert controller.override is None and button.text() == "Suspend"
    controller.shutdown()


def test_with_night_light_off_the_button_only_offers_to_activate(flyout):
    from PySide6.QtWidgets import QToolButton

    panel, _ = flyout
    assert panel._toggle.text() == "Activate"
    assert panel._toggle.popupMode() is QToolButton.ToolButtonPopupMode.InstantPopup
    panel._on_toggle_clicked()  # (a click opens the menu) never suspends night light that is off
    assert panel._controller.override is None
