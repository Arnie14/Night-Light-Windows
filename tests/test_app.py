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

    def run(general: GeneralConfig, backends=None):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"general": dataclasses.asdict(general)}))
        brightness, gamma = backends or create_backends("fake")
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


def test_sleep_and_waking_reach_every_part(start, monkeypatch):
    night_light, _ = start(GeneralConfig())
    calls = []
    monkeypatch.setattr(night_light.monitors, "suspend", lambda: calls.append("suspend"))
    monkeypatch.setattr(night_light.monitors, "resume", lambda delays: calls.append(("resume", tuple(delays))))
    monkeypatch.setattr(night_light.brightness_memory, "restore_all_soon", lambda: calls.append("restore all"))
    monkeypatch.setattr(night_light.scheduler, "evaluate", lambda reason="": calls.append(("schedule", reason)))
    watcher = night_light._watcher
    watcher.suspending.emit()
    watcher.resumed.emit()
    watcher.displaysOff.emit()
    watcher.displaysOn.emit()
    watcher.clockChanged.emit()
    assert calls == [
        "suspend",
        "restore all", ("resume", app_module.RESCAN_AFTER_RESUME_S), ("schedule", "wake"),
        "suspend",
        ("resume", app_module.RESCAN_AFTER_DISPLAYS_ON_S),
        ("schedule", "clock"),
    ]


def test_signing_out_shuts_down_once(start, monkeypatch):
    night_light, _ = start(GeneralConfig())
    shutdowns = []
    original = night_light.controller.shutdown
    monkeypatch.setattr(night_light.controller, "shutdown", lambda: (shutdowns.append(1), original()))
    night_light.system_events.endingSession.emit()  # Windows is signing out
    night_light._shutdown()  # and then the app quits as usual
    assert shutdowns == [1]


def test_the_compatibility_report_describes_the_displays(start):
    night_light, _ = start(GeneralConfig())
    from PySide6.QtTest import QTest

    for _ in range(300):
        if night_light.monitors.monitors:
            break
        QTest.qWait(10)
    report = night_light.compatibility_report()
    assert "DELL U2720Q" in report and "Software dimming" in report and "Old projector" in report


class StuckBackend:
    """One display whose writes never return."""

    def __init__(self):
        import threading

        self.release = threading.Event()

    def list_monitors(self):
        from nightlight.hardware.brightness import MonitorInfo

        return [MonitorInfo(0, "Stuck", True, key="STUCK", channel="ddc")]

    def get_brightness(self, index):
        return 50

    def set_brightness(self, index, value):
        self.release.wait(10)


def test_quitting_during_a_stuck_write_is_bounded_and_restores_colors(start, monkeypatch):
    import time

    from PySide6.QtTest import QTest

    from nightlight import monitors as monitors_module
    from nightlight.hardware.gamma import FakeGammaBackend

    monkeypatch.setattr(monitors_module, "SHUTDOWN_WAIT_S", 0.3)
    backend, gamma = StuckBackend(), FakeGammaBackend()
    night_light, _ = start(GeneralConfig(), backends=(backend, gamma))
    for _ in range(300):
        state = night_light.monitors.state("STUCK")
        if state is not None and state.controllable:
            break
        QTest.qWait(10)
    gamma.apply(4000)
    gamma.set_dimming("x", 50)
    night_light.monitors.set_brightness("STUCK", 30)
    QTest.qWait(50)  # the write is now stuck in the display
    started = time.monotonic()
    night_light._shutdown()
    assert time.monotonic() - started < 2  # quitting doesn't wait for the stuck call
    assert gamma.kelvin == 6500 and not gamma.dimmed  # colors and dimming undone first
    assert night_light.monitors.hung  # main.py then exits without waiting for it
    backend.release.set()


def test_night_light_and_brightness_schedule_firing_together(monkeypatch):
    # Both schedules change at 20:00: each does its part, neither blocks the other.
    from datetime import datetime, timezone

    from PySide6.QtTest import QTest

    from nightlight import controller as controller_module
    from nightlight.brightness_scheduler import BrightnessScheduler
    from nightlight.config import BrightnessScheduleConfig, Mode, NightLightConfig, SchedulePoint
    from nightlight.controller import NightLightController
    from nightlight.hardware.brightness import FakeBrightnessBackend
    from nightlight.hardware.gamma import FakeGammaBackend
    from nightlight.monitors import MonitorManager

    monkeypatch.setattr(controller_module, "QUICK_ADJUST_MS", 100)
    now = datetime(2026, 6, 15, 20, 0, 1, tzinfo=timezone.utc)
    gamma, backend = FakeGammaBackend(), FakeBrightnessBackend(latency=0)
    monitors = MonitorManager(backend)
    scheduler = BrightnessScheduler(monitors, BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(100, "07:00"), SchedulePoint(50, "20:00"))),
                                    None, clock=lambda: now)
    night = NightLightController(gamma, NightLightConfig(enabled=True, mode=Mode.TIMES, night_start="20:00", day_start="07:00",
                                                         transition_minutes=0, night_temperature=4500), clock=lambda: now)
    try:
        monitors.refresh()
        for _ in range(300):
            if all(backend._brightness.get(i) == 50 for i in (0, 1, 2)) and gamma.kelvin == 4500:
                break
            QTest.qWait(10)
        assert gamma.kelvin == 4500
        assert {backend._brightness[i] for i in (0, 1, 2)} == {50}
    finally:
        scheduler.shutdown()
        night.shutdown()
        monitors.shutdown()


def test_turning_software_dimming_off_reaches_the_monitors(start, monkeypatch):
    night_light, _ = start(GeneralConfig(software_dimming=True))
    calls = []
    monkeypatch.setattr(night_light.monitors, "set_software_dimming", calls.append)
    night_light._apply_settings(AppConfig(general=GeneralConfig(software_dimming=False)))
    night_light._apply_settings(AppConfig(general=GeneralConfig(software_dimming=False)))  # unchanged: nothing
    assert calls == [False]
