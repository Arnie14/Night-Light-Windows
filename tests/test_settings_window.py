# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QByteArray, QTimeZone
from PySide6.QtWidgets import QDialogButtonBox

from nightlight.config import Anchor, AppConfig, BrightnessScheduleConfig, GeneralConfig, Mode, NightLightConfig, SchedulePoint
from nightlight.controller import NightLightController
from nightlight.core import locations
from nightlight.core.brightness_schedule import PRESETS
from nightlight.ui.settings_window import CUSTOM_LOCATION, SettingsWindow

KYIV = NightLightConfig(enabled=True, mode=Mode.LOCATION, timezone="Europe/Kyiv", latitude=50.4333, longitude=30.5167)


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


def make_window(night_light=KYIV, general=GeneralConfig(), can_autostart=True):
    controller = NightLightController(FakeGamma(), night_light, lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    return SettingsWindow(controller, AppConfig(night_light, general), can_autostart), controller


@pytest.fixture
def window():
    window, controller = make_window()
    yield window
    controller.shutdown()


def apply_button(window):
    return window.buttons.button(QDialogButtonBox.StandardButton.Apply)


def test_opens_with_the_saved_settings_unchanged(window):
    assert window.current_config() == AppConfig(KYIV, GeneralConfig())
    assert window.location.currentData() == "Europe/Kyiv"
    assert not apply_button(window).isEnabled()


def test_picking_a_place_sets_its_coordinates(window):
    window.location.setCurrentIndex(window.location.findData("Australia/Sydney"))
    night_light = window.current_config().night_light
    sydney = locations.find("Australia/Sydney")
    assert (night_light.timezone, night_light.latitude, night_light.longitude) == ("Australia/Sydney", sydney.latitude, sydney.longitude)
    assert apply_button(window).isEnabled()


def test_custom_location_keeps_typed_coordinates(window):
    window.location.setCurrentIndex(window.location.findData(CUSTOM_LOCATION))
    window.latitude.setValue(12.5)
    window.longitude.setValue(-45.25)
    night_light = window.current_config().night_light
    assert (night_light.timezone, night_light.latitude, night_light.longitude) == ("", 12.5, -45.25)


def test_coordinates_only_show_for_a_custom_location(window):
    window.show()
    assert window.location.isVisible() and not window.latitude.isVisible()
    window.location.setCurrentIndex(window.location.findData(CUSTOM_LOCATION))
    assert window.latitude.isVisible() and window.longitude.isVisible()
    window.mode.setCurrentIndex(window.mode.findData(Mode.TIMES))
    assert not window.location.isVisible() and not window.latitude.isVisible()


def test_unknown_saved_zone_shows_as_custom():
    window, controller = make_window(NightLightConfig(enabled=True, mode=Mode.LOCATION, timezone="Mars/Olympus", latitude=1, longitude=2))
    assert window.location.currentData() == CUSTOM_LOCATION
    assert (window.latitude.value(), window.longitude.value()) == (1, 2)
    controller.shutdown()


def test_typing_a_place_name_selects_it(window):
    edit = window.location.lineEdit()
    edit.setText("tokyo, japan")
    edit.editingFinished.emit()
    assert window.location.currentData() == "Asia/Tokyo"


def test_typing_nonsense_goes_back_to_the_current_place(window):
    edit = window.location.lineEdit()
    edit.setText("nowhere at all")
    edit.editingFinished.emit()
    assert window.location.currentText() == "Kyiv, Ukraine"
    assert window.location.currentData() == "Europe/Kyiv"


def test_detect_uses_the_system_time_zone(window, monkeypatch):
    monkeypatch.setattr(QTimeZone, "systemTimeZoneId", staticmethod(lambda: QByteArray(b"Asia/Calcutta")))  # Windows may report the old name
    window.use_system_zone.click()
    assert window.location.currentData() == "Asia/Kolkata"


def test_detect_with_an_unknown_zone_explains(window, monkeypatch):
    monkeypatch.setattr(QTimeZone, "systemTimeZoneId", staticmethod(lambda: QByteArray(b"UTC")))
    window.use_system_zone.click()
    assert window.location.currentData() == "Europe/Kyiv"
    assert "isn't in the list" in window.schedule_info.text()


def test_startup_options(window):
    window.start_with_windows.setChecked(True)
    window.start_minimized.setChecked(False)
    assert window.current_config().general == GeneralConfig(start_with_windows=True, start_minimized=False)
    applied = []
    window.applied.connect(applied.append)
    apply_button(window).click()
    assert applied == [window.current_config()]
    assert not apply_button(window).isEnabled()


def test_autostart_unavailable_is_disabled_and_off():
    window, controller = make_window(general=GeneralConfig(start_with_windows=True), can_autostart=False)
    assert not window.start_with_windows.isEnabled()
    assert not window.current_config().general.start_with_windows
    controller.shutdown()


def test_restore_defaults(window):
    window.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).click()
    assert window.current_config() == AppConfig()


def test_brightness_options(window):
    assert window.remember_brightness.isChecked() and not window.link_brightness.isChecked()  # the defaults
    window.remember_brightness.setChecked(False)
    window.link_brightness.setChecked(True)
    general = window.current_config().general
    assert (general.remember_brightness, general.link_brightness) == (False, True)
    assert apply_button(window).isEnabled()


def test_settings_are_grouped_in_pages(window):
    assert window.page_titles() == ["Night Light", "Brightness", "Displays", "General"]
    assert window.current_page() == 0
    window._nav[2].click()  # the navigation pane
    assert window.current_page() == 2
    window.show_page(1)
    assert window.current_page() == 1 and window._nav[1].isChecked() and not window._nav[2].isChecked()


def test_on_off_settings_are_switches_that_work_like_check_boxes(window):
    from nightlight.ui.settings_parts import ToggleSwitch

    switch = window.remember_brightness
    assert isinstance(switch, ToggleSwitch) and switch.isChecked()
    switch.click()  # a click (or Space) flips it
    assert not window.current_config().general.remember_brightness and apply_button(window).isEnabled()
    assert switch.accessibleName() == "Remember each display's brightness"


def test_rows_hidden_for_other_switching_times_leave_no_stray_dividers(window):
    window.show()
    window.mode.setCurrentIndex(window.mode.findData(Mode.ALWAYS))
    card = window._night_card
    visible_rows = [row for row in card._card_rows if not row.isHidden()]
    assert len(visible_rows) == 1  # only "Switching times"
    dividers = [row.property("divider") for row in card._card_rows if row.property("divider") is not None]
    assert not any(divider.isVisibleTo(card) for divider in dividers)


def test_picking_a_schedule_preset_fills_in_its_times(window):
    editor = window.schedule
    editor.enabled.setChecked(True)
    editor.preset.setCurrentIndex(editor.preset.findData("office"))
    schedule = window.current_config().brightness_schedule
    assert schedule.enabled and schedule.preset == "office" and schedule.points == PRESETS["office"].points
    assert apply_button(window).isEnabled()


def test_editing_a_preset_makes_it_custom(window):
    editor = window.schedule
    editor.enabled.setChecked(True)
    editor.preset.setCurrentIndex(editor.preset.findData("day_night"))
    editor._rows[1].level.setValue(35)
    schedule = window.current_config().brightness_schedule
    assert schedule.preset == "custom" and schedule.points[1].level == 35


def test_schedule_times_can_be_added_removed_and_tied_to_the_sun(window):
    editor = window.schedule
    editor.enabled.setChecked(True)
    count = len(editor._rows)
    editor.add_button.click()
    assert len(editor._rows) == count + 1
    row = editor._rows[-1]
    row.anchor.setCurrentIndex(row.anchor.findData(Anchor.SUNSET))
    row.offset.setValue(-30)
    point = window.current_config().brightness_schedule.points[-1]
    assert point.anchor is Anchor.SUNSET and point.offset_minutes == -30
    while len(editor._rows) > 1:
        editor._remove(editor._rows[-1])
    editor._remove(editor._rows[0])
    assert len(editor._rows) == 1  # a schedule keeps at least one time


def test_schedule_controls_are_disabled_while_it_is_off(window):
    assert not window.schedule.enabled.isChecked()
    assert not window.schedule.preset.isEnabled() and not window.schedule.add_button.isEnabled()
    window.show()
    assert not window.schedule.preset.isVisibleTo(window.schedule)  # the details fold away while it's off
    window.schedule.enabled.setChecked(True)
    assert window.schedule.preset.isEnabled() and window.schedule.preset.isVisibleTo(window.schedule)


def test_a_saved_schedule_loads_back():
    schedule = BrightnessScheduleConfig(enabled=True, preset="custom", points=(SchedulePoint(60, "09:15"), SchedulePoint(25, "21:45")),
                                        transition_minutes=15)
    controller = NightLightController(FakeGamma(), KYIV, lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    window = SettingsWindow(controller, AppConfig(KYIV, GeneralConfig(), schedule))
    assert window.current_config().brightness_schedule == schedule
    assert not apply_button(window).isEnabled()
    controller.shutdown()


def test_the_displays_tab_lists_each_display_and_its_control():
    from nightlight import compat
    from nightlight.hardware.brightness import FakeBrightnessBackend

    monitors = FakeBrightnessBackend(latency=0).list_monitors()
    controller = NightLightController(FakeGamma(), KYIV, lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    window = SettingsWindow(controller, AppConfig(KYIV), compatibility=lambda: compat.entries(monitors), report=lambda: "REPORT")
    rows = window.display_rows
    assert [row.title.text() for row in rows] == ["DELL U2720Q", "LG 27GP950 (HDR)", "Living room TV", "Old projector"]
    assert rows[0].description.text() == "DisplayPort · DDC/CI" and rows[0].control._text == "Works"
    assert "Night light: No" in rows[1].description.text()  # HDR
    assert rows[3].control._text == "Not controllable" and "DDC/CI didn't answer" in rows[3].description.text()
    assert "Turn DDC/CI on" in rows[3].toolTip()
    window._update_displays()
    assert window.display_rows is rows  # unchanged: not rebuilt on every reading
    window.copy_report.click()
    from PySide6.QtGui import QGuiApplication

    assert QGuiApplication.clipboard().text() == "REPORT"
    controller.shutdown()


def test_software_dimming_option(window):
    assert window.software_dimming.isChecked()  # on by default
    window.software_dimming.setChecked(False)
    assert window.current_config().general.software_dimming is False
    assert apply_button(window).isEnabled()
