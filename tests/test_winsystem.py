# SPDX-License-Identifier: GPL-3.0-only
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
        (winsystem.WM_WTSSESSION_CHANGE, 0x7, None, None, None),  # locking: nothing to do
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_APMRESUMEAUTOMATIC, None, None, RESUMED),
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_POWERSETTINGCHANGE, GUID_CONSOLE_DISPLAY_STATE, 1, DISPLAY_ON),
        (winsystem.WM_POWERBROADCAST, winsystem.PBT_POWERSETTINGCHANGE, GUID_CONSOLE_DISPLAY_STATE, 0, None),  # turning off
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
