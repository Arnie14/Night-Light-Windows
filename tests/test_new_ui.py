# SPDX-License-Identifier: GPL-3.0-only
"""The 0.4.0 look: the day ribbon, the warmth slider and tile, the heading
font. Each widget is rendered straight to an image, where an error inside a
paintEvent raises (in the running app it would only be logged, and the
widget left blank), in the states that have broken UIs before: daylight
saving days, a day ending mid-preview, dark mode, disabled, tiny widths,
fractional scaling, settings that can't be worked out, and stale state."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtTest import QTest

from nightlight.color.controller import NightLightController
from nightlight.config import AppConfig, Mode, NightLightConfig
from nightlight.core.schedule import compute_state
from nightlight.ui import widgets
from nightlight.ui.day_ribbon import DayRibbon, day_bounds
from nightlight.ui.widgets import WarmthSlider, kelvin_color

BERLIN = ZoneInfo("Europe/Berlin")
NOON = datetime(2026, 6, 15, 12, tzinfo=UTC)
TIMES = NightLightConfig(enabled=True, mode=Mode.TIMES, night_start="20:00", day_start="07:00", night_temperature=3000)


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


def render(widget, width=480, ratio=1.0) -> QImage:
    widget.resize(width, widget.height())
    image = QImage(round(widget.width() * ratio) or 1, round(widget.height() * ratio) or 1, QImage.Format.Format_ARGB32)
    image.setDevicePixelRatio(ratio)
    image.fill(QColor("white"))
    widget.render(image)  # raises on an error in paintEvent
    return image


def dark(widget):
    palette = widget.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#ffffff"))
    widget.setPalette(palette)


def needle_x(image, row=8) -> int:
    """The darkest column of the bar: where the needle is (light mode)."""
    return min(range(2, image.width() - 2), key=lambda x: image.pixelColor(x, row).lightness())


# --- The day ribbon -------------------------------------------------------------


@pytest.mark.parametrize(("day", "hours"), [((3, 29), 23), ((10, 25), 25), ((6, 15), 24)])
def test_a_day_is_midnight_to_midnight_on_the_clock(day, hours):
    start, end = day_bounds(datetime(2026, *day, 15, tzinfo=BERLIN), BERLIN)
    assert (end - start) == timedelta(hours=hours)
    assert start.astimezone(BERLIN).hour == 0 and end.astimezone(BERLIN).hour == 0


def test_on_the_spring_change_noon_sits_eleven_23rds_along():
    """The 02:00 hour doesn't exist that day: noon is 11 of 23 hours in."""
    moment = datetime(2026, 3, 29, 12, tzinfo=BERLIN)
    ribbon = DayRibbon(lambda _m: 6500, lambda: moment, zone=lambda: BERLIN)
    image = render(ribbon, 461)
    assert abs(needle_x(image) - 0.5 - 460 * 11 / 23) <= 2


def test_a_day_ending_mid_preview_is_worked_out_afresh():
    asked = []
    ribbon = DayRibbon(lambda m: asked.append(m.date()) or 6500, lambda: NOON)
    render(ribbon)
    ribbon.set_marker(NOON + timedelta(hours=14))  # the 24-hour preview, past midnight
    render(ribbon)
    assert ribbon._day[0] == (NOON + timedelta(days=1)).date() and (NOON + timedelta(days=1)).date() in asked
    render(ribbon)
    calls = len(asked)
    render(ribbon)
    assert len(asked) == calls  # the same day isn't worked out twice


def test_settings_changes_are_picked_up_only_on_refresh():
    calls = []
    ribbon = DayRibbon(lambda m: calls.append(m) or 6500, lambda: NOON)
    render(ribbon)
    first = len(calls)
    render(ribbon)
    assert len(calls) == first
    ribbon.refresh()
    render(ribbon)
    assert len(calls) == 2 * first


@pytest.mark.parametrize("width", [0, 1, 3, 40, 2000])
@pytest.mark.parametrize("slim", [False, True])
def test_any_width_paints(width, slim):
    render(DayRibbon(lambda _m: 3000, lambda: NOON, slim=slim), width)


@pytest.mark.parametrize("ratio", [1.25, 1.5, 2.0])
def test_fractional_scaling_paints(ratio):
    render(DayRibbon(lambda _m: 3000, lambda: NOON), 480, ratio)


def test_dark_mode_softens_the_day_and_keeps_the_needle_visible():
    ribbon = DayRibbon(lambda m: 6500, lambda: NOON)
    dark(ribbon)
    image = render(ribbon)
    day = image.pixelColor(100, 8)
    assert day.lightness() < 235  # not a glaring white bar
    assert image.pixelColor(240, 8).lightness() > day.lightness()  # the needle: light on it


def test_a_broken_setting_paints_a_neutral_day():
    def broken(_moment):
        raise ValueError("a time half typed in")

    image = render(DayRibbon(broken, lambda: NOON))
    assert image.pixelColor(100, 8).blue() > 240


def test_the_ribbon_is_named_for_screen_readers():
    assert DayRibbon(lambda _m: 6500, lambda: NOON).accessibleName() == "Night light today"


# --- The warmth slider --------------------------------------------------------


def warmth_slider(inverted=True):
    slider = WarmthSlider(Qt.Orientation.Horizontal)
    slider.setRange(10, 65)
    slider.setInvertedAppearance(inverted)
    slider.setInvertedControls(inverted)  # as TemperatureSlider: right is warmer
    slider.setValue(65 if inverted else 10)  # the handle at the cool end
    return slider


def test_the_track_runs_cool_to_warm_whichever_way_it_is_drawn():
    for inverted, warm_side in ((True, 260), (False, 40)):
        slider = warmth_slider(inverted)
        slider.setValue(38)  # the handle in the middle, out of the samples' way
        image = render(slider, 300)
        middle = slider.height() // 2
        assert image.pixelColor(warm_side, middle).blue() < image.pixelColor(300 - warm_side, middle).blue() - 80


def test_a_disabled_slider_shows_its_track_faded():
    enabled, disabled = warmth_slider(), warmth_slider()
    disabled.setEnabled(False)
    middle = enabled.height() // 2
    warm = render(enabled, 300).pixelColor(200, middle)
    faded = render(disabled, 300).pixelColor(200, middle)
    assert faded.hsvSaturation() < warm.hsvSaturation()


def test_the_slider_still_takes_the_keyboard_and_the_mouse():
    slider = warmth_slider()
    slider.resize(300, slider.sizeHint().height())
    slider.show()
    slider.setFocus()
    before = slider.value()
    QTest.keyClick(slider, Qt.Key.Key_Right)  # inverted controls: right is warmer, a lower value
    assert slider.value() == before - 1
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(250, slider.height() // 2))
    assert slider.value() < before - 1  # a click on the warm side moves it there
    render(slider, 300)  # with focus and hover state
    slider.hide()


@pytest.mark.parametrize("ratio", [1.25, 1.5])
def test_the_slider_paints_at_fractional_scaling(ratio):
    render(warmth_slider(), 300, ratio)


# --- The panel's tile and the heading font ------------------------------------------


def test_the_tile_takes_on_the_warmth_on_screen():
    from nightlight.ui.flyout import WarmthTile

    kelvin = {"now": 6500}
    tile = WarmthTile(lambda: kelvin["now"])
    tile.resize(300, 80)
    day = render(tile, 300).pixelColor(150, 40)
    kelvin["now"] = 3000
    night = render(tile, 300).pixelColor(150, 40)
    assert night.blue() < day.blue() and abs(night.red() - day.red()) < 12
    dark(tile)
    render(tile, 300)


def test_headings_fall_back_to_bold_without_windows_11s_display_face(monkeypatch):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(widgets, "_has_display_family", lambda: False)
    label = QLabel("Night Light")
    widgets.set_heading_font(label, 1.8)
    assert label.font().weight() == QFont.Weight.Bold
    monkeypatch.setattr(widgets, "_has_display_family", lambda: True)
    widgets.set_heading_font(label, 1.0)
    assert widgets.DISPLAY_FAMILY in label.font().families() and label.font().weight() == QFont.Weight.Normal


def test_kelvin_colors_are_the_gamma_ramps_whites():
    assert kelvin_color(6500) == QColor.fromRgbF(1.0, 1.0, 1.0)
    assert kelvin_color(1000).blue() < kelvin_color(3000).blue() < kelvin_color(5000).blue()
    kelvin_color(0)  # out of range: clamped, no error
    kelvin_color(99999)


# --- In the panel and the settings window -----------------------------------------


def settings_window(config=TIMES):
    from nightlight.ui.settings_window import SettingsWindow

    controller = NightLightController(FakeGamma(), config, lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
    return SettingsWindow(controller, AppConfig(config)), controller


def test_the_settings_ribbon_shows_changes_before_they_are_applied_and_forgets_them_on_reset():
    from PySide6.QtCore import QTime
    from PySide6.QtWidgets import QDialogButtonBox

    window, controller = settings_window()
    ribbon = window.day_ribbon
    at_19 = 19 * 6  # samples are 10 minutes apart

    def kelvins():
        start, end = day_bounds(controller.now(), None)
        return ribbon.kelvins(start, end)

    assert kelvins()[at_19] == 6500
    window.night_start.setTime(QTime(18, 0))  # not applied
    assert kelvins()[at_19] == 3000
    window.buttons.button(QDialogButtonBox.StandardButton.Reset).click()
    assert kelvins()[at_19] == 6500
    controller.shutdown()


def test_the_ribbon_follows_the_24_hour_preview_and_lets_go_after(monkeypatch):
    from nightlight.ui import settings_window as module

    monkeypatch.setattr(module, "DAY_PREVIEW_TICK_MS", 1)
    window, controller = settings_window()
    window.show()
    window._toggle_day_preview()
    window._day_timer.stop()
    window._day_preview_step()
    assert window.day_ribbon._marker == window._day_at
    window._stop_day_preview()
    assert window.day_ribbon._marker is None
    window.close()
    controller.shutdown()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_whole_window_paints_at_its_smallest(theme):
    from PySide6.QtWidgets import QApplication

    from nightlight.ui import theme as theme_module

    window, controller = settings_window()
    theme_module.apply(theme, "windows")
    try:
        window.resize(window.minimumSize())
        window.show()
        QApplication.processEvents()
        for page in range(window.page_count()):
            window.show_page(page)
            render(window, window.width())
        assert window.day_ribbon.width() > 100
    finally:
        theme_module.apply("system", "windows")
        window.close()
        controller.shutdown()


def test_the_panel_ribbon_hides_with_night_light_off_and_follows_new_settings():
    from nightlight.ui.flyout import Flyout
    from tests.test_flyout import FakeMonitors

    controller = NightLightController(FakeGamma(), TIMES, lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
    panel = Flyout(FakeMonitors(), controller, as_window=True)
    panel.show()
    assert panel._night_ribbon.isVisibleTo(panel)
    start, end = day_bounds(controller.now(), controller.zone)
    assert panel._night_ribbon.kelvins(start, end)[-1] == 3000
    controller.set_config(NightLightConfig(enabled=True, mode=Mode.TIMES, night_start="20:00", day_start="07:00",
                                           night_temperature=2000))
    assert panel._night_ribbon.kelvins(start, end)[-1] == 2000  # worked out again for the new settings
    controller.set_config(NightLightConfig())  # night light off
    assert not panel._night_ribbon.isVisibleTo(panel)
    render(panel, panel.width())
    panel.hide()
    controller.shutdown()


def test_the_ribbon_matches_the_schedule_it_shows():
    ribbon = DayRibbon(lambda m: compute_state(TIMES, m).temperature, lambda: NOON)
    start, end = day_bounds(NOON, None)
    kelvins = ribbon.kelvins(start, end)
    assert kelvins[0] == 3000 and kelvins[12 * 6] == 6500 and kelvins[-1] == 3000
