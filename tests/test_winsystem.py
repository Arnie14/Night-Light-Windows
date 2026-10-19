# SPDX-License-Identifier: GPL-3.0-only
import ctypes
import sys
import uuid

import pytest

from nightlight.hardware import winsystem
from nightlight.hardware.winsystem import (
    DISPLAY_CHANGED,
    DISPLAY_ON,
    GUID_CONSOLE_DISPLAY_STATE,
    RESUMED,
    UNLOCKED,
    classify,
    is_fullscreen,
    night_light_on_from_blob,
)

MONITOR = (0, 0, 2560, 1440)


@pytest.mark.parametrize(
    ("window", "class_name", "expected"),
    [
        ((0, 0, 2560, 1440), "UnityWndClass", True),  # borderless fullscreen game
        ((-8, -8, 2568, 1448), "Chrome_WidgetWin_1", True),  # maximised past the edges counts too
        ((0, 0, 2560, 1400), "Chrome_WidgetWin_1", False),  # maximised above the taskbar
        ((100, 100, 900, 700), "Notepad", False),
        ((0, 0, 2560, 1440), "Progman", False),  # the desktop
        ((0, 0, 2560, 1440), "WorkerW", False),
    ],
)
def test_fullscreen(window, class_name, expected):
    assert is_fullscreen(window, MONITOR, class_name) is expected


def test_fullscreen_on_a_second_monitor():
    assert is_fullscreen((2560, 0, 4480, 1080), (2560, 0, 4480, 1080), "Game")
    assert not is_fullscreen((0, 0, 2560, 1440), (2560, 0, 4480, 1080), "Game")


@pytest.mark.parametrize(
    ("message", "wparam", "setting", "data", "expected"),
    [
        (winsystem.WM_WTSSESSION_CHANGE, winsystem.WTS_SESSION_UNLOCK, None, None, UNLOCKED),
        (winsystem.WM_WTSSESSION_CHANGE, winsystem.WTS_SESSION_LOCK, None, None, winsystem.LOCKED),  # reported; the app needs nothing then
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_APMRESUMEAUTOMATIC, None, None, RESUMED),
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_POWERSETTINGCHANGE, GUID_CONSOLE_DISPLAY_STATE, 1, DISPLAY_ON),
        # Turning off: reported now, so nothing is sent over DDC/CI to displays that are off.
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_POWERSETTINGCHANGE, GUID_CONSOLE_DISPLAY_STATE, 0, winsystem.DISPLAY_OFF),
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_POWERSETTINGCHANGE, GUID_CONSOLE_DISPLAY_STATE, 2, None),  # dimmed: still on
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_APMSUSPEND, None, None, winsystem.SUSPENDING),
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_APMRESUMESUSPEND, None, None, RESUMED),
        (winsystem.WM_TIMECHANGE, 0, None, None, winsystem.CLOCK_CHANGED),
        (winsystem.WM_ENDSESSION, 1, None, None, winsystem.ENDING_SESSION),
        (winsystem.WM_ENDSESSION, 0, None, None, None),  # signing out was cancelled
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_POWERSETTINGCHANGE, uuid.uuid4(), 1, None),
        (winsystem.WM_DISPLAYCHANGE, 0, None, None, DISPLAY_CHANGED),
        (0x0010, 0, None, None, None),
    ],
)
def test_classify_messages(message, wparam, setting, data, expected):
    assert classify(message, wparam, setting, data) == expected


def test_guid_round_trip():
    guid = winsystem._GUID.from_uuid(GUID_CONSOLE_DISPLAY_STATE)
    assert guid.to_uuid() == GUID_CONSOLE_DISPLAY_STATE
    assert guid.Data1 == 0x6FE69556


def blob(flag: int) -> bytes:
    data = bytearray(43)
    data[18] = flag
    return bytes(data)


def test_windows_night_light_state():
    assert night_light_on_from_blob(blob(0x15)) is True
    assert night_light_on_from_blob(blob(0x13)) is False
    assert night_light_on_from_blob(blob(0x42)) is None  # an unknown format: don't guess
    assert night_light_on_from_blob(b"\x00" * 10) is None


def test_off_windows_these_report_nothing(monkeypatch):
    monkeypatch.setattr(winsystem.sys, "platform", "linux")
    assert winsystem.foreground_app() is None
    assert winsystem.running_apps() == []
    assert winsystem.windows_night_light_on() is None


@pytest.mark.parametrize(
    ("state", "fullscreen", "busy"),
    [
        (winsystem.QUNS_RUNNING_D3D_FULL_SCREEN, False, True),  # an exclusive-fullscreen game
        (winsystem.QUNS_PRESENTATION_MODE, False, True),
        (winsystem.QUNS_BUSY, False, True),
        (5, True, True),  # Windows doesn't know, but a borderless game is in front
        (5, False, False),  # QUNS_ACCEPTS_NOTIFICATIONS
        (None, False, False),
    ],
)
def test_busy(state, fullscreen, busy):
    assert winsystem.is_busy(state, fullscreen) is busy


def test_panel_brightness_message():
    from nightlight.hardware.winsystem import GUID_VIDEO_BRIGHTNESS, PANEL_BRIGHTNESS

    assert classify(winsystem.WM_POWERBROADCAST, winsystem.PBT_POWERSETTINGCHANGE, GUID_VIDEO_BRIGHTNESS, 40) == PANEL_BRIGHTNESS


def test_ticks_across_the_wraparound():
    assert winsystem.ticks_since(5000, 2000) == 3000
    assert winsystem.ticks_since(1000, 0xFFFFFFFF - 999) == 2000  # 49.7 days in


def test_idle_off_windows(monkeypatch):
    monkeypatch.setattr(winsystem.sys, "platform", "linux")
    assert winsystem.idle_seconds() == 0.0  # never "idle", so nothing dims
    assert winsystem.display_required() is False



# --- Windows' notifications as signals (from hardware/winevents.py) ----------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")


def test_power_setting_payload_is_read():
    data = winsystem._POWERBROADCAST_SETTING()
    data.PowerSetting = winsystem._GUID.from_uuid(GUID_CONSOLE_DISPLAY_STATE)
    data.DataLength, data.Data = 4, 2
    assert winsystem.read_setting(ctypes.addressof(data)) == (GUID_CONSOLE_DISPLAY_STATE, 2)
    assert winsystem.read_setting(0) == (None, None)


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


SIGNALS = ("suspending", "resumed", "displayOff", "displayOn", "locked", "unlocked", "displayChanged", "clockChanged", "endingSession")


def recorder(events):
    seen = []
    for name in SIGNALS:
        getattr(events, name).connect(lambda name=name: seen.append(name))
    events.panelBrightness.connect(lambda level: seen.append(("panelBrightness", level)))
    return seen


def test_repeats_are_merged():
    clock = Clock()
    events = winsystem.SystemEvents(clock=clock)  # not listening: offscreen tests have no real windows
    seen = recorder(events)
    events.handle(RESUMED)
    events.handle(RESUMED)  # Windows sends two kinds of resume
    clock.now += 60
    events.handle(winsystem.SUSPENDING)
    events.handle(RESUMED)
    assert seen == ["resumed", "suspending", "resumed"]


def test_display_state_reports_changes_only():
    events = winsystem.SystemEvents(clock=Clock())
    seen = recorder(events)
    events.handle(DISPLAY_ON)  # the current state, sent on registering
    events.handle(None)  # e.g. "dimmed"
    events.handle(DISPLAY_ON)
    events.handle(winsystem.DISPLAY_OFF)
    events.handle(winsystem.DISPLAY_OFF)
    events.handle(DISPLAY_ON)
    assert seen == ["displayOff", "displayOn"]


def test_every_kind_has_its_signal():
    events = winsystem.SystemEvents(clock=Clock())
    seen = recorder(events)
    for what in (winsystem.LOCKED, UNLOCKED, DISPLAY_CHANGED, winsystem.CLOCK_CHANGED, winsystem.ENDING_SESSION):
        events.handle(what)
    events.handle(winsystem.PANEL_BRIGHTNESS, 40)
    assert seen == ["locked", "unlocked", "displayChanged", "clockChanged", "endingSession", ("panelBrightness", 40)]
    events.close()  # harmless when not listening


REAL_WINDOW = r"""
import ctypes, uuid
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from nightlight.hardware import winsystem
app = QGuiApplication([])
events = winsystem.SystemEvents()
seen = []
for name in ("suspending", "resumed", "clockChanged", "displayOff", "displayOn"):
    getattr(events, name).connect(lambda name=name: seen.append(name))
events.panelBrightness.connect(lambda level: seen.append(f"panel {level}"))
user32 = ctypes.WinDLL("user32")
user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.PostMessageW.argtypes = user32.SendMessageW.argtypes
def setting(guid, value):
    data = winsystem._POWERBROADCAST_SETTING()
    data.PowerSetting = winsystem._GUID.from_uuid(guid)
    data.DataLength, data.Data = 4, value
    return data
listening = events.listening
QTimer.singleShot(200, lambda: None)  # let Windows' own current-state reports arrive first
app.processEvents()
on, off, panel = setting(winsystem.GUID_CONSOLE_DISPLAY_STATE, 1), setting(winsystem.GUID_CONSOLE_DISPLAY_STATE, 0), setting(winsystem.GUID_VIDEO_BRIGHTNESS, 35)
for data in (on, off, on, panel):
    user32.SendMessageW(events._hwnd, winsystem.WM_POWERBROADCAST, winsystem.PBT_POWERSETTINGCHANGE, ctypes.addressof(data))
user32.SendMessageW(events._hwnd, winsystem.WM_POWERBROADCAST, winsystem.PBT_APMSUSPEND, 0)
user32.SendMessageW(events._hwnd, winsystem.WM_POWERBROADCAST, winsystem.PBT_APMRESUMEAUTOMATIC, 0)
user32.PostMessageW(events._hwnd, winsystem.WM_POWERBROADCAST, winsystem.PBT_APMRESUMESUSPEND, 0)  # merged
user32.PostMessageW(events._hwnd, winsystem.WM_TIMECHANGE, 0, 0)  # posted: reaches the filter twice, counted once
QTimer.singleShot(300, app.quit)
app.exec()
events.close()
print(listening, events.listening, ",".join(seen))
"""


@windows_only
def test_a_real_window_receives_windows_broadcasts():
    # In a process of its own: the tests use Qt's offscreen platform, whose
    # windows have no real handles to receive Windows' messages.
    import os
    import subprocess
    from pathlib import Path

    env = {**os.environ, "QT_QPA_PLATFORM": "windows"}
    result = subprocess.run([sys.executable, "-c", REAL_WINDOW], capture_output=True, text=True, timeout=60, env=env,
                            cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0, result.stderr
    listening, closed, seen = result.stdout.strip().splitlines()[-1].split(" ", 2)
    assert (listening, closed) == ("True", "False")
    signals = seen.split(",")
    # The display state the first time is Windows' current one (ignored, if it hasn't sent its own already).
    assert signals[-6:] == ["displayOff", "displayOn", "panel 35", "suspending", "resumed", "clockChanged"]
