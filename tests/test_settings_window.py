# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QByteArray, QTimeZone
from PySide6.QtWidgets import QDialogButtonBox

from nightlight.config import AppConfig, GeneralConfig, Mode, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.core import locations
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
