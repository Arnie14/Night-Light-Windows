# SPDX-License-Identifier: GPL-3.0-only
"""Renaming and hiding displays, across the whole app: a renamed display is
still the same display everywhere (its remembered level, the command line,
reports), and a hidden one is left alone by everything that acts on its own,
while staying reachable when asked for by name."""

import json
import zipfile

import pytest
from PySide6.QtTest import QTest

from nightlight import app as app_module
from nightlight import diagnostics
from nightlight.config import AppConfig, DisplayPrefs
from nightlight.hardware.brightness import FakeBrightnessBackend
from nightlight.hardware.gamma import FakeGammaBackend

DELL, LG, TV = "DEL41A2:7XKJ9F3", "GSM5BBF:204NTAB1", "SAM7136"
PREFS = {"displays": {DELL: {"name": "Left screen", "hidden": False}, TV: {"name": "", "hidden": True}}}


def wait_for(condition, ms=5000):
    for _ in range(ms // 20):
        if condition():
            return True
        QTest.qWait(20)
    return condition()


@pytest.fixture
def running(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module.QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    monkeypatch.setattr(app_module.autostart, "create", lambda: None)
    monkeypatch.setattr(app_module.winsystem, "user_busy", lambda: False)
    apps = []

    def start(settings, remembered=None):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps(settings))
        if remembered is not None:
            (tmp_path / "brightness.json").write_text(json.dumps({"version": 2, "levels": remembered, "set_at": {}}))
        backend = FakeBrightnessBackend(latency=0)
        night_light = app_module.NightLightApp(app_module.QApplication.instance(), backend, FakeGammaBackend(), path)
        apps.append(night_light)
        assert wait_for(lambda: night_light.monitors.monitors and all(
            night_light.monitors.state(m.key).controllable for m in night_light.monitors.monitors if m.supported))
        return night_light, backend

    yield start
    for night_light in apps:
        night_light.tray.hide()
        night_light._shutdown()


def test_the_panel_shows_the_new_name_and_leaves_out_the_hidden_display(running):
    night_light, _ = running(PREFS)
    rows = night_light.flyout._rows
    assert TV not in rows and DELL in rows
    from PySide6.QtWidgets import QLabel

    assert "Left screen" in [label.text() for label in rows[DELL].findChildren(QLabel)]


def test_the_command_line_knows_the_new_name_and_skips_the_hidden_display_for_all(running):
    night_light, backend = running(PREFS)
    replies = []
    night_light.handle_request({"command": "list"}, replies.append)
    listed = {d["id"]: d for d in replies[-1]["data"]["displays"]}
    assert listed[DELL]["name"] == "Left screen" and listed[TV]["hidden"] is True
    night_light.handle_request({"command": "set", "value": "33", "display": "left"}, replies.append)
    assert replies[-1]["ok"]
    assert wait_for(lambda: backend._brightness[0] == 33)
    tv_before = backend._brightness[2]
    night_light.handle_request({"command": "set", "value": "44"}, replies.append)  # all
    assert wait_for(lambda: backend._brightness[0] == 44)
    QTest.qWait(300)
    assert backend._brightness[2] == tv_before  # the hidden display was left alone
    night_light.handle_request({"command": "set", "value": "50", "display": "living room"}, replies.append)
    assert replies[-1]["ok"]  # asked for by name, a hidden display still answers
    assert wait_for(lambda: backend._brightness[2] == 50)


def test_remembered_levels_follow_a_display_through_a_rename_and_skip_hidden_ones(running):
    night_light, backend = running(PREFS, remembered={DELL: 25, TV: 40})
    assert wait_for(lambda: backend._brightness[0] == 25)  # renamed: still restored (same identity)
    QTest.qWait(300)
    assert backend._brightness[2] != 40  # hidden: not restored


def test_the_brightness_schedule_leaves_hidden_displays_alone(running):
    settings = {**PREFS, "brightness_schedule": {"enabled": True, "preset": "custom", "points": [
        {"level": 60, "at": "00:00", "anchor": "clock", "offset_minutes": 0}]}}
    night_light, backend = running(settings)
    assert wait_for(lambda: backend._brightness[0] == 60 and backend._brightness[1] == 60)
    QTest.qWait(300)
    assert backend._brightness[2] != 60


def test_idle_dimming_and_the_laptop_keys_leave_hidden_displays_alone(running):
    night_light, _ = running({})
    night_light.idle_dimmer.configure(True, 5, 20)
    levels, software = night_light.idle_dimmer.plan()
    assert r"\\.\DISPLAY3" in software  # shown: the TV is dimmed in software when idle
    night_light._apply_settings(AppConfig(displays=(DisplayPrefs(TV, "", True),)))
    levels, software = night_light.idle_dimmer.plan()
    assert TV not in levels and r"\\.\DISPLAY3" not in software  # the TV is dimmed in software when shown
    assert night_light._hidden_keys() == frozenset({TV})
    night_light.idle_dimmer.configure(False, 5, 20)


def test_hiding_and_renaming_take_effect_at_once_without_a_restart(running):
    night_light, _ = running({})
    assert TV in night_light.flyout._rows
    night_light._apply_settings(AppConfig(displays=(DisplayPrefs(TV, "Den TV", True),)))
    assert TV not in night_light.flyout._rows and night_light.brightness_memory._hidden(TV) and night_light.scheduler._hidden(TV)
    night_light._apply_settings(AppConfig(displays=(DisplayPrefs(TV, "Den TV", False),)))
    from PySide6.QtWidgets import QLabel

    assert "Den TV" in [label.text() for label in night_light.flyout._rows[TV].findChildren(QLabel)]


def test_reports_keep_the_displays_own_names_and_never_the_custom_ones(running, tmp_path, monkeypatch):
    night_light, _ = running(PREFS)
    report = night_light.compatibility_report()
    assert "DELL U2720Q" in report and "Left screen" not in report  # custom names can be personal
    assert "Living room TV" in report  # hidden from the panel, still a connected display
    monkeypatch.setattr(diagnostics, "default_folder", lambda: tmp_path / "out")
    path, summary = diagnostics.write_bundle(night_light._diagnostics_sources())
    with zipfile.ZipFile(path) as bundle:
        everything = "\n".join(bundle.read(name).decode() for name in bundle.namelist())
    assert "Left screen" not in everything and "renamed" in summary and "hidden" in summary


def test_a_display_hidden_under_its_older_key_stays_hidden(running):
    # Beta 4 saved display preferences by hardware id only.
    night_light, _ = running({"displays": {"SAM7136": {"name": "", "hidden": True}, "DEL41A2": {"name": "Left screen", "hidden": False}}})
    assert TV not in night_light.flyout._rows
    replies = []
    night_light.handle_request({"command": "list"}, replies.append)
    assert {d["id"]: d["name"] for d in replies[-1]["data"]["displays"]}[DELL] == "Left screen"
