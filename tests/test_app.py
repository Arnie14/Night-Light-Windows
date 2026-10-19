# SPDX-License-Identifier: GPL-3.0-only
import dataclasses
import json
import time

import pytest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from nightlight import app as app_module
from nightlight.config import AppConfig, GeneralConfig
from nightlight.hardware import create_backends
from nightlight.wake_report import RESCAN_AFTER_WAKE_S


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
    # Not this machine's real state: a fullscreen window here would hold back the settings at start.
    monkeypatch.setattr(app_module.winsystem, "user_busy", lambda: False)
    entry = RecordingAutostart()
    monkeypatch.setattr(app_module.autostart, "create", lambda: entry)
    started = []

    def run(general: GeneralConfig, backends=None, night_light=None):
        path = tmp_path / "settings.json"
        settings = {"general": dataclasses.asdict(general)}
        if night_light is not None:
            settings["night_light"] = night_light
        path.write_text(json.dumps(settings))
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
    monitors = night_light.monitors

    def recorded(obj, name, entry):
        original = getattr(obj, name)

        def wrapper(*args, **kwargs):
            calls.append(entry(*args, **kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(obj, name, wrapper)

    recorded(monitors, "suspend", lambda: "suspend")
    recorded(monitors, "resume", lambda delays=(): ("resume", tuple(delays)))
    recorded(night_light.wake_report, "woke", lambda: "wake report")
    recorded(night_light.brightness_memory, "restore_all_soon", lambda: "restore all")
    recorded(night_light.scheduler, "evaluate", lambda reason="": ("schedule", reason))
    watcher = night_light._watcher
    watcher.suspending.emit()
    assert monitors.paused  # nothing goes to the displays while asleep
    watcher.resumed.emit()
    assert not monitors.paused
    assert [round(due - time.monotonic()) for due, _ in monitors._scheduled] == list(RESCAN_AFTER_WAKE_S)
    watcher.displaysOff.emit()
    watcher.displaysOn.emit()
    watcher.clockChanged.emit()
    assert calls == [
        "suspend",
        "wake report", ("resume", ()), "restore all", ("schedule", "wake"),  # detection on the wake report's schedule
        "suspend",
        ("resume", app_module.RESCAN_AFTER_DISPLAYS_ON_S), "restore all",
        ("schedule", "clock"),
    ]


def test_displays_turning_back_on_without_having_slept_detects_again(start, monkeypatch):
    # E.g. Windows reports "on" while the app never saw "off": not paused, so just look again.
    night_light, _ = start(GeneralConfig())
    later = []
    monkeypatch.setattr(night_light.monitors, "refresh_later", lambda delays: later.append(tuple(delays)))
    night_light._watcher.displaysOn.emit()
    assert later == [app_module.RESCAN_AFTER_DISPLAYS_ON_S] and not night_light.monitors.paused


def test_a_second_sleep_soon_after_a_wake_still_resumes(start):
    # The wake report counts a wake within a minute of the last as the same
    # one; the displays must still be resumed after the second sleep.
    night_light, _ = start(GeneralConfig())
    watcher = night_light._watcher
    watcher.suspending.emit()
    watcher.resumed.emit()
    watcher.suspending.emit()
    watcher.resumed.emit()
    assert not night_light.monitors.paused


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
    assert night_light.flyout._display_prefs("SAM7136").hidden
    assert night_light.brightness_memory._hidden("SAM7136") and not night_light.brightness_memory._hidden("DEL41A2")
    assert night_light.scheduler._hidden("SAM7136")
    # Saved by beta 4 under the hardware id alone: still hidden under a key with a serial.
    assert night_light.brightness_memory._hidden("SAM7136:0x01ABCDEF")


def test_an_exempt_app_pauses_night_light_on_its_display(start, monkeypatch):
    from nightlight.config import NightLightConfig
    from nightlight.hardware.winsystem import ForegroundApp

    night_light, _ = start(GeneralConfig())
    night_light._exemptions._probe = lambda: ForegroundApp("game.exe", r"\\.\DISPLAY2", True)
    night_light._apply_settings(AppConfig(NightLightConfig(exempt_apps=("game.exe",))))
    assert night_light.controller.paused == {r"\\.\DISPLAY2": "game.exe"}
    night_light._apply_settings(AppConfig())
    assert night_light.controller.paused == {}


def test_an_app_already_in_front_at_start_pauses_night_light(start, monkeypatch):
    # Found running the app: the first check happened before the controller
    # was listening, and later checks only report changes, so a fullscreen
    # game already in front at start stayed tinted.
    from nightlight.hardware.winsystem import ForegroundApp

    monkeypatch.setattr(app_module.winsystem, "foreground_app", lambda: ForegroundApp("game.exe", r"\\.\DISPLAY2", True))
    night_light, _ = start(GeneralConfig(), night_light={"enabled": True, "exempt_fullscreen": True})
    assert night_light.controller.paused == {r"\\.\DISPLAY2": "game.exe, fullscreen"}


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


def test_a_time_zone_change_moves_both_schedules(start, monkeypatch):
    from zoneinfo import ZoneInfo

    night_light, _ = start(GeneralConfig())
    evaluated = []
    monkeypatch.setattr(night_light.scheduler, "evaluate", lambda reason="": evaluated.append(reason))
    tokyo = ZoneInfo("Asia/Tokyo")
    monkeypatch.setattr(app_module, "system_zone", lambda: tokyo)
    night_light._watcher.clockChanged.emit()  # Windows: WM_TIMECHANGE
    assert night_light.controller.zone is tokyo and night_light.scheduler.zone is tokyo
    assert evaluated == ["time zone"]
    night_light._check_zone()  # the periodic check: nothing new
    night_light._watcher.clockChanged.emit()  # a plain clock change
    assert evaluated == ["time zone", "clock"]


def test_a_failing_command_line_request_is_still_answered(start, monkeypatch):
    night_light, _ = start(GeneralConfig())

    def broken(request):
        raise RuntimeError("a bug")

    monkeypatch.setattr(night_light.commands, "handle", broken)
    replies = []
    night_light.handle_request({"command": "list"}, replies.append)
    assert replies == [{"ok": False, "output": "Winlight couldn't do that; see its log for why."}]


def test_keeping_calibrations_reaches_the_gamma_backend(start):
    from nightlight.hardware.gamma import FakeGammaBackend
    from nightlight.hardware.brightness import FakeBrightnessBackend

    class Gamma(FakeGammaBackend):
        def __init__(self):
            super().__init__()
            self.kept = []

        def set_keep_calibration(self, enabled):
            self.kept.append(enabled)

    gamma = Gamma()
    night_light, _ = start(GeneralConfig(keep_calibration=True), backends=(FakeBrightnessBackend(latency=0), gamma))
    assert gamma.kept == [True]  # at start, before night light sets any ramp
    night_light._apply_settings(AppConfig(general=GeneralConfig(keep_calibration=False)))
    assert gamma.kept == [True, False]
