# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timezone

import pytest

from nightlight.config import Mode, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.core import locations
from nightlight.ui.settings_window import CUSTOM_COORDINATES, SettingsWindow
from nightlight.ui.theme import theme


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


@pytest.fixture
def window():
    controller = NightLightController(FakeGamma(), NightLightConfig(enabled=True, mode=Mode.LOCATION), lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    window = SettingsWindow(controller)
    yield window
    controller.shutdown()


def test_picking_a_place_sets_the_coordinates(window):
    window.location.setCurrentIndex(window.location.findData("Australia/Sydney"))
    config = window.current_config()
    sydney = locations.find("Australia/Sydney")
    assert (config.timezone, config.latitude, config.longitude) == ("Australia/Sydney", sydney.latitude, sydney.longitude)


def test_typed_coordinates_are_kept_under_custom(window):
    window.location.setCurrentIndex(window.location.findData(CUSTOM_COORDINATES))
    window.latitude.setValue(12.5)
    window.longitude.setValue(-45.25)
    config = window.current_config()
    assert (config.timezone, config.latitude, config.longitude) == ("", 12.5, -45.25)


def test_coordinate_fields_only_show_for_custom(window):
    window.show()
    window.location.setCurrentIndex(window.location.findData("Europe/Kyiv"))
    assert not window.latitude.isVisible() and window.location.isVisible()
    window.location.setCurrentIndex(window.location.findData(CUSTOM_COORDINATES))
    assert window.latitude.isVisible() and window.longitude.isVisible()


def test_saved_place_is_selected_again(window):
    saved = NightLightConfig(enabled=True, mode=Mode.LOCATION, timezone="Asia/Tokyo", latitude=35.6544, longitude=139.7447)
    window._load(saved)
    assert window.location.currentData() == "Asia/Tokyo"
    assert window.current_config() == saved


def test_unknown_saved_zone_falls_back_to_custom(window):
    window._load(NightLightConfig(enabled=True, mode=Mode.LOCATION, timezone="Mars/Olympus", latitude=1, longitude=2))
    assert window.location.currentData() == CUSTOM_COORDINATES
    assert (window.latitude.value(), window.longitude.value()) == (1, 2)


def test_use_system_zone_selects_a_known_zone(window, monkeypatch):
    from PySide6.QtCore import QTimeZone

    monkeypatch.setattr(QTimeZone, "systemTimeZoneId", staticmethod(lambda: b"Asia/Calcutta"))  # an old name for Kolkata
    window.use_system_zone.click()
    assert window.location.currentData() == "Asia/Kolkata"


def test_theme_has_light_and_dark_tokens():
    from nightlight.ui.theme import DARK, LIGHT, stylesheet, _tokens

    assert LIGHT.keys() == DARK.keys()
    assert "QPushButton" in stylesheet(_tokens(True, "#3daee9"))
    assert theme.tokens.accent
