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


def test_windows_night_light_notice_follows_its_state(start, monkeypatch):
    from nightlight.hardware import winsystem

    state = {"on": False}
    monkeypatch.setattr(winsystem, "windows_night_light_on", lambda: state["on"])
    night_light, _ = start(GeneralConfig())
    night_light._check_windows_night_light()
    assert night_light.flyout.windows_night_light.isHidden()
    state["on"] = True
    night_light._check_windows_night_light()
    assert not night_light.flyout.windows_night_light.isHidden()


def test_display_preferences_reach_the_panel_and_brightness(start):
    from nightlight.config import DisplayPrefs

    night_light, _ = start(GeneralConfig())
    night_light._apply_settings(AppConfig(displays=(DisplayPrefs("SAM7136", "", True),)))
    assert night_light.flyout._prefs["SAM7136"].hidden
    assert night_light.brightness_memory._hidden == {"SAM7136"}


def test_an_exempt_app_pauses_night_light_on_its_display(start, monkeypatch):
    from nightlight.config import NightLightConfig
    from nightlight.hardware.winsystem import ForegroundApp

    night_light, _ = start(GeneralConfig())
    night_light._exemptions._probe = lambda: ForegroundApp("game.exe", r"\\.\DISPLAY2", True)
    night_light._apply_settings(AppConfig(NightLightConfig(exempt_apps=("game.exe",))))
    assert night_light.controller.paused == {r"\\.\DISPLAY2": "game.exe"}
    night_light._apply_settings(AppConfig())
    assert night_light.controller.paused == {}


def test_diagnostics_from_the_command_line(start, monkeypatch, tmp_path):
    from nightlight import diagnostics

    monkeypatch.setattr(diagnostics, "default_folder", lambda: tmp_path / "desk")
    night_light, _ = start(GeneralConfig())
    replies = []
    night_light.handle_request({"command": "diagnostics"}, replies.append)
    assert replies[0]["ok"] and len(list((tmp_path / "desk").glob("Winlight-diagnostics-*.zip"))) == 1
    night_light.handle_request({"command": "list"}, replies.append)
    assert replies[1]["ok"]


def test_tray_menu_has_report_a_problem(start):
    night_light, _ = start(GeneralConfig())
    assert "Report a Problem…" in [a.text() for a in night_light.tray._menu.actions()]


def test_no_windows_open_on_their_own_over_a_fullscreen_game(start, monkeypatch):
    from nightlight.hardware import winsystem

    busy = {"now": True}
    monkeypatch.setattr(winsystem, "user_busy", lambda: busy["now"])
    night_light, _ = start(GeneralConfig(start_minimized=False))
    assert night_light._settings is None  # not opened at start over the game
    night_light.show_panel()  # a second launch asking for the panel
    assert not night_light.flyout.isVisible()
    busy["now"] = False
    night_light.show_panel()
    assert night_light.flyout.isVisible()
    night_light.flyout.hide()


def test_the_focus_guard_can_be_turned_off(start, monkeypatch):
    from nightlight.hardware import winsystem

    monkeypatch.setattr(winsystem, "user_busy", lambda: True)
    night_light, _ = start(GeneralConfig(start_minimized=False, respect_fullscreen=False))
    assert night_light._settings is not None and night_light._settings.isVisible()


def test_laptop_keys_option_reaches_the_follower(start):
    night_light, _ = start(GeneralConfig())
    assert not night_light.brightness_keys._enabled
    night_light._apply_settings(AppConfig(general=GeneralConfig(follow_laptop_brightness=True)))
    assert night_light.brightness_keys._enabled


def test_a_wake_is_handled_once(start, monkeypatch):
    night_light, _ = start(GeneralConfig())
    restores = []
    monkeypatch.setattr(night_light.brightness_memory, "restore_all_soon", lambda: restores.append(1))
    night_light._on_resumed()  # Windows' event
    night_light._on_resumed()  # the clock check, a moment later
    assert restores == [1]


def test_idle_dimming_settings_reach_the_dimmer(start):
    night_light, _ = start(GeneralConfig())
    assert not night_light.idle_dimmer._enabled
    night_light._apply_settings(AppConfig(general=GeneralConfig(idle_dim=True, idle_minutes=3, idle_level=10)))
    dimmer = night_light.idle_dimmer
    assert dimmer._enabled and (dimmer._after_s, dimmer._level) == (180, 10)
