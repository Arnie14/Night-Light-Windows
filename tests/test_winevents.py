# SPDX-License-Identifier: GPL-3.0-only
"""Windows' sleep, display and session notifications."""

import ctypes
import sys
import uuid

import pytest

from nightlight.hardware import winevents
from nightlight.hardware.winevents import GUID_CONSOLE_DISPLAY_STATE, SystemEvents, decode

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")


def test_messages_are_decoded():
    assert decode(winevents.WM_POWERBROADCAST, winevents.PBT_APMSUSPEND) == "suspend"
    assert decode(winevents.WM_POWERBROADCAST, winevents.PBT_APMRESUMEAUTOMATIC) == "resume"
    assert decode(winevents.WM_POWERBROADCAST, winevents.PBT_APMRESUMESUSPEND) == "resume"
    setting = winevents.PBT_POWERSETTINGCHANGE
    assert decode(winevents.WM_POWERBROADCAST, setting, (GUID_CONSOLE_DISPLAY_STATE, 0)) == "display_off"
    assert decode(winevents.WM_POWERBROADCAST, setting, (GUID_CONSOLE_DISPLAY_STATE, 1)) == "display_on"
    assert decode(winevents.WM_POWERBROADCAST, setting, (uuid.uuid4(), 1)) is None  # another setting
    assert decode(winevents.WM_WTSSESSION_CHANGE, winevents.WTS_SESSION_LOCK) == "lock"
    assert decode(winevents.WM_WTSSESSION_CHANGE, winevents.WTS_SESSION_UNLOCK) == "unlock"
    assert decode(winevents.WM_DISPLAYCHANGE, 0) == "display_change"
    assert decode(winevents.WM_TIMECHANGE, 0) == "time_change"
    assert decode(winevents.WM_ENDSESSION, 1) == "end_session"
    assert decode(winevents.WM_ENDSESSION, 0) is None  # sign-out was cancelled
    assert decode(0x0001, 0) is None


def test_power_setting_payload_is_read():
    data = winevents.POWERBROADCAST_SETTING()
    ctypes.memmove(data.PowerSetting, GUID_CONSOLE_DISPLAY_STATE.bytes_le, 16)
    data.DataLength, data.Data[0] = 4, 2
    assert winevents.read_setting(ctypes.addressof(data)) == (GUID_CONSOLE_DISPLAY_STATE, 2)
    assert winevents.read_setting(0) is None


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


def recorder(events):
    seen = []
    for name in ("suspending", "resumed", "displaysOff", "displaysOn", "locked", "unlocked", "displaysChanged", "clockChanged", "endingSession"):
        getattr(events, name).connect(lambda name=name: seen.append(name))
    return seen


def test_repeats_are_merged():
    clock = Clock()
    events = SystemEvents(clock=clock)
    events.close()  # only the decoding logic here
    seen = recorder(events)
    events.handle("resume")
    events.handle("resume")  # Windows sends two kinds of resume
    clock.now += 60
    events.handle("suspend")
    events.handle("resume")
    assert seen == ["resumed", "suspending", "resumed"]


def test_display_state_reports_changes_only():
    events = SystemEvents(clock=Clock())
    events.close()
    seen = recorder(events)
    events.handle("display_on")  # the current state, sent on registering
    events.handle("display_dimmed")
    events.handle("display_on")
    events.handle("display_off")
    events.handle("display_off")
    events.handle("display_on")
    assert seen == ["displaysOff", "displaysOn"]


REAL_WINDOW = r"""
import ctypes, sys
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from nightlight.hardware import winevents
app = QGuiApplication([])
events = winevents.SystemEvents()
seen = []
for name in ("suspending", "resumed", "clockChanged"):
    getattr(events, name).connect(lambda name=name: seen.append(name))
user32 = ctypes.WinDLL("user32")
user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.PostMessageW.argtypes = user32.SendMessageW.argtypes
user32.SendMessageW(events._hwnd, winevents.WM_POWERBROADCAST, winevents.PBT_APMSUSPEND, 0)
user32.SendMessageW(events._hwnd, winevents.WM_POWERBROADCAST, winevents.PBT_APMRESUMEAUTOMATIC, 0)
user32.PostMessageW(events._hwnd, winevents.WM_POWERBROADCAST, winevents.PBT_APMRESUMESUSPEND, 0)  # merged
user32.PostMessageW(events._hwnd, winevents.WM_TIMECHANGE, 0, 0)  # posted: reaches the filter twice, counted once
QTimer.singleShot(300, app.quit)
app.exec()
events.close()
print(events.listening, ",".join(seen))
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
    assert result.stdout.strip().splitlines()[-1] == "False suspending,resumed,clockChanged"
