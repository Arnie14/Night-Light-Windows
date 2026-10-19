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


def test_exemptions(window):
    window.exempt_fullscreen.setChecked(True)
    window.exempt_apps.add("Photoshop.EXE")
    window.exempt_apps.add("photoshop.exe")  # once only
    night_light = window.current_config().night_light
    assert night_light.exempt_fullscreen and night_light.exempt_apps == ("photoshop.exe",)
    assert apply_button(window).isEnabled()


def test_brightness_schedule_steps(window):
    # Beta 4's step list, now the schedule editor's points: the same assertions.
    points = (SchedulePoint(80, "07:00"), SchedulePoint(40, anchor=Anchor.SUNSET, offset_minutes=-30))
    window.schedule.set_config(BrightnessScheduleConfig(True, "custom", points))
    schedule = window.current_config().brightness_schedule
    assert schedule.enabled and schedule.points == points
    window.schedule._remove(window.schedule._rows[1])
    assert window.current_config().brightness_schedule.points == (SchedulePoint(80, "07:00"),)


def test_display_names_and_hiding():
    from PySide6.QtCore import QObject, Signal

    from nightlight.config import DisplayPrefs
    from nightlight.hardware.brightness import MonitorInfo

    class Monitors(QObject):
        monitorsChanged = Signal(list)
        monitors = [MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2"), MonitorInfo(1, "TV", True, key="SAM7136")]

    controller = NightLightController(FakeGamma(), KYIV, lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    monitors = Monitors()
    saved = (DisplayPrefs("DEL41A2", "Left"),)
    window = SettingsWindow(controller, AppConfig(KYIV, displays=saved), monitors=monitors)
    assert window.current_config().displays == saved
    name, show = window.displays._rows["SAM7136"]
    show.setChecked(False)
    assert window.current_config().displays == (DisplayPrefs("DEL41A2", "Left"), DisplayPrefs("SAM7136", "", True))
    monitors.monitorsChanged.emit(monitors.monitors)  # re-detection keeps unsaved edits
    assert window.current_config().displays[-1].hidden
    window.buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).click()
    assert window.current_config().displays  # names are kept by Restore Defaults
    controller.shutdown()


def test_windows_night_light_notice(window):
    assert window.windows_night_light.isHidden()
    window.set_windows_night_light(True)
    assert not window.windows_night_light.isHidden()
    window.show()
    assert window.windows_night_light.isVisible()  # its holder too
    window.set_windows_night_light(None)  # unknown: no notice
    assert not window.windows_night_light.isVisible()


def test_idle_dimming_options(window):
    assert not window.idle_dim.isChecked() and not window.idle_minutes.isEnabled()  # off by default
    window.idle_dim.setChecked(True)
    window.idle_minutes.setValue(10)
    window.idle_level.setValue(15)
    general = window.current_config().general
    assert (general.idle_dim, general.idle_minutes, general.idle_level) == (True, 10, 15)
    assert window.idle_minutes.isEnabled()


def test_focus_and_laptop_key_options(window):
    assert window.respect_fullscreen.isChecked() and not window.follow_laptop.isChecked()  # the defaults
    window.respect_fullscreen.setChecked(False)
    window.follow_laptop.setChecked(True)
    general = window.current_config().general
    assert (general.respect_fullscreen, general.follow_laptop_brightness) == (False, True)
    assert apply_button(window).isEnabled()


def test_every_setting_has_its_place_on_a_page(window):
    # Each control from beta 4 sits on the page its settings belong to.
    pages = [window._stack.widget(i).widget() for i in range(window.page_count())]
    where = {
        0: (window.exempt_fullscreen, window.exempt_apps),
        1: (window.schedule, window.follow_laptop, window.idle_dim, window.idle_minutes, window.idle_level),
        2: (window.displays,),
        3: (window.respect_fullscreen, window.start_with_windows),
    }
    for page, controls in where.items():
        for control in controls:
            assert pages[page].isAncestorOf(control), (page, control)


def test_report_a_problem_from_the_about_card():
    controller = NightLightController(FakeGamma(), KYIV, lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    reports = []
    window = SettingsWindow(controller, AppConfig(KYIV), report_problem=lambda: reports.append(True))
    from PySide6.QtWidgets import QPushButton

    [button] = [b for b in window.findChildren(QPushButton) if b.text() == "Report a Problem…"]
    button.click()
    assert reports == [True]
    without = SettingsWindow(controller, AppConfig(KYIV))
    assert not [b for b in without.findChildren(QPushButton) if b.text() == "Report a Problem…"]
    controller.shutdown()


def test_display_names_saved_under_an_older_key_show_on_their_display():
    from PySide6.QtCore import QObject, Signal

    from nightlight.config import DisplayPrefs
    from nightlight.hardware.brightness import MonitorInfo

    class Monitors(QObject):
        monitorsChanged = Signal(list)
        monitors = [MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2:7XKJ9F3")]

    controller = NightLightController(FakeGamma(), KYIV, lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    window = SettingsWindow(controller, AppConfig(KYIV, displays=(DisplayPrefs("DEL41A2", "Left", True),)), monitors=Monitors())
    assert list(window.displays._rows) == ["DEL41A2:7XKJ9F3"]  # one row, not a "not connected" one for the old key
    name, show = window.displays._rows["DEL41A2:7XKJ9F3"]
    assert name.text() == "Left" and not show.isChecked()
    assert window.current_config().displays == (DisplayPrefs("DEL41A2:7XKJ9F3", "Left", True),)  # saved under the new key on Apply
    controller.shutdown()


def test_out_of_range_idle_settings_are_shown_in_range():
    window, controller = make_window(general=GeneralConfig(idle_dim=True, idle_minutes=500, idle_level=-5))
    assert (window.idle_minutes.value(), window.idle_level.value()) == (240, 0)
    controller.shutdown()


def test_keeping_color_calibrations_is_an_option_on_the_displays_page(window):
    assert not window.keep_calibration.isChecked()  # off by default
    pages = [window._stack.widget(i).widget() for i in range(window.page_count())]
    assert pages[2].isAncestorOf(window.keep_calibration)
    window.keep_calibration.setChecked(True)
    assert window.current_config().general.keep_calibration and apply_button(window).isEnabled()
