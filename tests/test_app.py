# SPDX-License-Identifier: GPL-3.0-only
import dataclasses
import json

import pytest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from nightlight import app as app_module
from nightlight.config import AppConfig, GeneralConfig
from nightlight.hardware import create_backends


class RecordingAutostart:
    def __init__(self):
        self.enabled = False

    def is_enabled(self):
        return self.enabled

    def set_enabled(self, enabled):
        self.enabled = enabled


@pytest.fixture
def start(tmp_path, monkeypatch):
    """Start the app with the fake backends, a tray, and the given general settings."""
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    entry = RecordingAutostart()
    monkeypatch.setattr(app_module.autostart, "create", lambda: entry)
    started = []

    def run(general: GeneralConfig):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"general": dataclasses.asdict(general)}))
        brightness, gamma = create_backends("fake")
        night_light = app_module.NightLightApp(QApplication.instance(), brightness, gamma, path)
        started.append(night_light)
        return night_light, entry

    yield run
    for night_light in started:
        if night_light._settings:
            night_light._settings.close()
        night_light.tray.hide()
        night_light._shutdown()


def test_start_minimized_shows_only_the_tray(start):
    night_light, _ = start(GeneralConfig(start_minimized=True))
    assert night_light._settings is None
    assert not night_light.flyout.isVisible()


def test_start_not_minimized_opens_settings(start):
    night_light, _ = start(GeneralConfig(start_minimized=False))
    assert night_light._settings is not None and night_light._settings.isVisible()


def test_sign_in_entry_follows_the_setting(start):
    night_light, entry = start(GeneralConfig(start_with_windows=True))
    assert entry.enabled  # written at startup
    night_light._apply_settings(AppConfig(general=GeneralConfig(start_with_windows=False)))
    assert not entry.enabled


def test_brightness_settings_reach_the_memory_and_flyout(start):
    night_light, _ = start(GeneralConfig(remember_brightness=True, link_brightness=False))
    assert night_light.brightness_memory.enabled and not night_light.flyout._linked
    night_light._apply_settings(AppConfig(general=GeneralConfig(remember_brightness=False, link_brightness=True)))
    assert not night_light.brightness_memory.enabled and night_light.flyout._linked


def test_brightness_file_sits_next_to_the_settings(start, tmp_path):
    night_light, _ = start(GeneralConfig())
    assert night_light.brightness_memory._path == tmp_path / "brightness.json"


def test_turning_software_dimming_off_reaches_the_monitors(start, monkeypatch):
    night_light, _ = start(GeneralConfig(software_dimming=True))
    calls = []
    monkeypatch.setattr(night_light.monitors, "set_software_dimming", calls.append)
    night_light._apply_settings(AppConfig(general=GeneralConfig(software_dimming=False)))
    night_light._apply_settings(AppConfig(general=GeneralConfig(software_dimming=False)))  # unchanged: nothing
    assert calls == [False]
