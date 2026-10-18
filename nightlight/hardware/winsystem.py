# SPDX-License-Identifier: GPL-3.0-only
"""Windows system state outside the displays themselves:

- which app is in the foreground, on which display, and whether it is
  fullscreen (for pausing night light there, as f.lux does);
- session and power events (unlocking, waking, a display turning on), so
  night light is put back the moment Windows resets the gamma ramps;
- whether Windows' own Night Light is on, which would stack with ours.

The decisions are pure functions, testable anywhere; the ctypes calls only
run on Windows.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import uuid
from dataclasses import dataclass
from pathlib import PureWindowsPath

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

log = logging.getLogger(__name__)


# --- The foreground app -------------------------------------------------------


@dataclass(frozen=True)
class ForegroundApp:
    exe: str  # lower-case executable name, e.g. "game.exe"
    display: str  # GDI name of the display it is mostly on, e.g. "\\.\DISPLAY2"
    fullscreen: bool


# The desktop and taskbar cover a whole display too, but aren't "fullscreen apps".
SHELL_CLASSES = frozenset({"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"})


def is_fullscreen(window: tuple[int, int, int, int], monitor: tuple[int, int, int, int], class_name: str) -> bool:
    """Whether a window (left, top, right, bottom) covers its whole monitor:
    borderless and exclusive fullscreen games, videos and presentations."""
    if class_name in SHELL_CLASSES:
        return False
    left, top, right, bottom = window
    m_left, m_top, m_right, m_bottom = monitor
    return left <= m_left and top <= m_top and right >= m_right and bottom >= m_bottom


def foreground_app() -> ForegroundApp | None:
    """The app in the foreground, or None (nothing, or Windows wouldn't say)."""
    if sys.platform != "win32":
        return None
    try:
        return _foreground_app()
    except OSError:
        log.debug("Could not read the foreground window", exc_info=True)
        return None


def _foreground_app() -> ForegroundApp | None:
    from ctypes import wintypes

    from nightlight.hardware.win32 import MONITORINFOEXW

    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    class_name = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, class_name, len(class_name))

    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    process = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if process:
        try:
            path = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(path))
            if _kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                exe = PureWindowsPath(path.value).name.lower()
        finally:
            _kernel32.CloseHandle(process)

    # The visible bounds, without the invisible resize borders GetWindowRect includes.
    rect = wintypes.RECT()
    if _dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(info)
    if not monitor or not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    m = info.rcMonitor
    fullscreen = is_fullscreen((rect.left, rect.top, rect.right, rect.bottom), (m.left, m.top, m.right, m.bottom), class_name.value)
    return ForegroundApp(exe, info.szDevice, fullscreen)


# SHQueryUserNotificationState: Windows' own "don't interrupt" signal.
QUNS_BUSY = 2  # a fullscreen app is running, or presentation settings are on
QUNS_RUNNING_D3D_FULL_SCREEN = 3  # an exclusive-fullscreen Direct3D game
QUNS_PRESENTATION_MODE = 4
_DO_NOT_INTERRUPT = {QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE}


def is_busy(notification_state: int | None, foreground_fullscreen: bool) -> bool:
    """Whether opening a window now would interrupt a game, video or presentation."""
    return notification_state in _DO_NOT_INTERRUPT or foreground_fullscreen


def user_busy() -> bool:
    """Windows says not to interrupt, or the app in front is fullscreen.
    Opening a window then could minimize an exclusive-fullscreen game."""
    if sys.platform != "win32":
        return False
    state = ctypes.c_int()
    try:
        notification_state = state.value if _shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0 else None
    except OSError:
        notification_state = None
    app = foreground_app()
    return is_busy(notification_state, bool(app and app.fullscreen))


def running_apps() -> list[str]:
    """Executable names of apps with a visible window, for picking exemptions."""
    if sys.platform != "win32":
        return []
    from ctypes import wintypes

    names: set[str] = set()
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]

    def visit(hwnd, _lparam):
        if _user32.IsWindowVisible(hwnd) and _user32.GetWindowTextLengthW(hwnd) > 0:
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if process:
                try:
                    path = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(len(path))
                    if _kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                        names.add(PureWindowsPath(path.value).name.lower())
                finally:
                    _kernel32.CloseHandle(process)
        return True

    _user32.EnumWindows(enum_proc(visit), 0)
    return sorted(names - {"explorer.exe", "applicationframehost.exe", "textinputhost.exe", "winlight.exe"})


# --- Session and power events -------------------------------------------------

WM_DISPLAYCHANGE = 0x007E
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013
WTS_CONSOLE_CONNECT = 0x1
WTS_SESSION_LOGON = 0x5
WTS_SESSION_UNLOCK = 0x8
# GUID_CONSOLE_DISPLAY_STATE: the console display turned off (0), on (1) or dimmed (2).
GUID_CONSOLE_DISPLAY_STATE = uuid.UUID("6fe69556-704a-47a0-8f24-c28d936fda47")
# The power setting for display brightness (video subgroup); Windows reports
# the built-in panel's level in percent when it changes, e.g. by the keys.
GUID_VIDEO_BRIGHTNESS = uuid.UUID("aded5e82-b909-4619-9949-f5d71dac0bcb")

UNLOCKED, RESUMED, DISPLAY_ON, DISPLAY_CHANGED = "unlocked", "resumed", "display on", "display changed"
PANEL_BRIGHTNESS = "panel brightness"


def classify(message: int, wparam: int, setting: uuid.UUID | None = None, data: int | None = None) -> str | None:
    """What a window message means for us, if anything."""
    if message == WM_WTSSESSION_CHANGE and wparam in (WTS_SESSION_UNLOCK, WTS_CONSOLE_CONNECT, WTS_SESSION_LOGON):
        return UNLOCKED
    if message == WM_POWERBROADCAST:
        if wparam in (PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND):
            return RESUMED
        if wparam == PBT_POWERSETTINGCHANGE and setting == GUID_CONSOLE_DISPLAY_STATE and data == 1:
            return DISPLAY_ON
        if wparam == PBT_POWERSETTINGCHANGE and setting == GUID_VIDEO_BRIGHTNESS and data is not None:
            return PANEL_BRIGHTNESS
    if message == WM_DISPLAYCHANGE:
        return DISPLAY_CHANGED
    return None


class SystemEvents(QObject):
    """Unlocking, waking and displays turning on, straight from Windows.

    Windows resets gamma ramps on the lock screen and after sleep; hearing it
    from Windows lets night light come back at once instead of at the next
    check. Messages arrive through Qt's native event filter, on a hidden
    window registered for session and display-state notifications (as
    Monitorian's PowerManagement does).
    """

    unlocked = Signal()
    resumed = Signal()
    displayOn = Signal()
    displayChanged = Signal()
    panelBrightness = Signal(int)  # the built-in panel's level changed (keys, Windows' slider)

    def __init__(self, app, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._filter = None
        if sys.platform != "win32":
            return
        from PySide6.QtWidgets import QWidget

        self._window = QWidget()  # never shown; only its native handle is needed
        hwnd = int(self._window.winId())
        try:
            if not _wtsapi32.WTSRegisterSessionNotification(hwnd, 0):  # NOTIFY_FOR_THIS_SESSION
                log.info("Could not register for session notifications")
            self._power = [
                _user32.RegisterPowerSettingNotification(hwnd, ctypes.byref(_GUID.from_uuid(guid)), 0)  # DEVICE_NOTIFY_WINDOW_HANDLE
                for guid in (GUID_CONSOLE_DISPLAY_STATE, GUID_VIDEO_BRIGHTNESS)
            ]
        except OSError:
            log.info("Could not register for power notifications", exc_info=True)
        self._filter = _Filter(self._on_event)
        app.installNativeEventFilter(self._filter)

    def _on_event(self, what: str, data: int | None = None) -> None:
        if what == PANEL_BRIGHTNESS:
            log.debug("Windows: built-in display brightness %s%%", data)
            self.panelBrightness.emit(int(data))
            return
        log.info("Windows: %s", what)
        {UNLOCKED: self.unlocked, RESUMED: self.resumed, DISPLAY_ON: self.displayOn, DISPLAY_CHANGED: self.displayChanged}[what].emit()


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, callback) -> None:
        super().__init__()
        self._callback = callback

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            setting = data = None
            if msg.message == WM_POWERBROADCAST and msg.wParam == PBT_POWERSETTINGCHANGE and msg.lParam:
                broadcast = _POWERBROADCAST_SETTING.from_address(msg.lParam)
                setting = broadcast.PowerSetting.to_uuid()
                raw = ctypes.string_at(msg.lParam + _POWERBROADCAST_SETTING.Data.offset, min(broadcast.DataLength, 4))
                data = int.from_bytes(raw, "little") if raw else None
            if what := classify(msg.message, msg.wParam, setting, data):
                self._callback(what, data)
        return False, 0


# --- Windows Night Light ------------------------------------------------------

# Windows keeps Night Light's state in a CloudStore blob; there is no API.
# Its location moved over Windows versions, so both are tried.
_NIGHT_LIGHT_KEYS = (
    r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current"
    r"\default$windows.data.bluelightreduction.bluelightreductionstate"
    r"\windows.data.bluelightreduction.bluelightreductionstate",
    r"Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\Cache\DefaultAccount"
    r"\$$windows.data.bluelightreduction.bluelightreductionstate\Current",
)
WINDOWS_NIGHT_LIGHT_SETTINGS = "ms-settings:nightlight"


def night_light_on_from_blob(data: bytes) -> bool | None:
    """Whether the state blob says Night Light is on now. The format is
    undocumented; scripts that toggle Night Light read byte 18 as 0x15 when
    it is on and 0x13 when off. Anything else: None (don't guess)."""
    if len(data) <= 18:
        return None
    return {0x15: True, 0x13: False}.get(data[18])


def windows_night_light_on() -> bool | None:
    """Whether Windows' own Night Light is on; None if it can't tell."""
    if sys.platform != "win32":
        return None
    import winreg

    for key_path in _NIGHT_LIGHT_KEYS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                data, _kind = winreg.QueryValueEx(key, "Data")
        except OSError:
            continue
        if isinstance(data, bytes) and (state := night_light_on_from_blob(data)) is not None:
            return state
    return None


# --- Bindings -----------------------------------------------------------------

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> _GUID:
        return cls.from_buffer_copy(value.bytes_le)

    def to_uuid(self) -> uuid.UUID:
        return uuid.UUID(bytes_le=bytes(self))


class _POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", _GUID), ("DataLength", ctypes.c_uint32), ("Data", ctypes.c_ubyte)]


if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _dwmapi = ctypes.WinDLL("dwmapi")
    _wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32")
    _shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
    _shell32.SHQueryUserNotificationState.restype = ctypes.c_long

    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HMONITOR
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(_GUID), wintypes.DWORD]
    _user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
    _wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
    _wtsapi32.WTSRegisterSessionNotification.restype = wintypes.BOOL
