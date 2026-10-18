# SPDX-License-Identifier: GPL-3.0-only
"""Windows' power, display and session notifications, as Qt signals.

Sleep and hibernation cause most of the trouble in apps like this one:
monitors vanish and come back, handles go stale, monitors don't answer for
a while after waking (Monitorian issue #50), and DDC/CI sent to a sleeping
monitor can wake it or hang its firmware. Monitorian's watchers listen for
exactly these, so this does too:

- WM_POWERBROADCAST: suspending, and resuming (Windows sends both
  PBT_APMRESUMEAUTOMATIC and PBT_APMRESUMESUSPEND; they're merged).
- Power setting GUID_CONSOLE_DISPLAY_STATE: the displays turned off, on or
  dimmed (Windows' screen timeout, while the PC stays awake).
- WM_WTSSESSION_CHANGE: locked and unlocked.
- WM_DISPLAYCHANGE: resolution, layout or HDR changed.
- WM_TIMECHANGE: the clock or time zone changed (schedules re-evaluate).
- WM_ENDSESSION: signing out or shutting down (clean up now; there may be
  no event loop left later).

A hidden Qt window receives them through a native event filter. Decoding is
a pure function, so it is tested without Windows.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
import uuid
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtGui import QGuiApplication, QWindow

log = logging.getLogger(__name__)

WM_ENDSESSION = 0x0016
WM_TIMECHANGE = 0x001E
WM_DISPLAYCHANGE = 0x007E
WM_POWERBROADCAST = 0x0218
WM_WTSSESSION_CHANGE = 0x02B1
PBT_APMSUSPEND = 0x4
PBT_APMRESUMESUSPEND = 0x7
PBT_APMRESUMEAUTOMATIC = 0x12
PBT_POWERSETTINGCHANGE = 0x8013
WTS_CONSOLE_CONNECT = 0x1
WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
GUID_CONSOLE_DISPLAY_STATE = uuid.UUID("6fe69556-704a-47a0-8f24-c28d936fda47")
DISPLAY_STATES = {0: "display_off", 1: "display_on", 2: "display_dimmed"}

# Windows sends resume twice, and posted messages can reach the filter twice.
RESUME_MERGE_S = 10.0


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", ctypes.c_byte * 16), ("DataLength", ctypes.c_uint32), ("Data", ctypes.c_ubyte * 4)]


def decode(message: int, wparam: int, setting: tuple[uuid.UUID, int] | None = None) -> str | None:
    """The event a window message stands for, or None. `setting` is the
    (GUID, value) carried by a PBT_POWERSETTINGCHANGE."""
    if message == WM_POWERBROADCAST:
        if wparam == PBT_APMSUSPEND:
            return "suspend"
        if wparam in (PBT_APMRESUMESUSPEND, PBT_APMRESUMEAUTOMATIC):
            return "resume"
        if wparam == PBT_POWERSETTINGCHANGE and setting is not None and setting[0] == GUID_CONSOLE_DISPLAY_STATE:
            return DISPLAY_STATES.get(setting[1])
        return None
    if message == WM_WTSSESSION_CHANGE:
        return {WTS_SESSION_LOCK: "lock", WTS_SESSION_UNLOCK: "unlock", WTS_CONSOLE_CONNECT: "unlock"}.get(wparam)
    if message == WM_DISPLAYCHANGE:
        return "display_change"
    if message == WM_TIMECHANGE:
        return "time_change"
    if message == WM_ENDSESSION:
        return "end_session" if wparam else None
    return None


def read_setting(lparam: int) -> tuple[uuid.UUID, int] | None:
    """The GUID and first data word of a POWERBROADCAST_SETTING."""
    if not lparam:
        return None
    setting = POWERBROADCAST_SETTING.from_address(lparam)
    data = int.from_bytes(bytes(setting.Data[: min(4, max(1, setting.DataLength))]), "little")
    return uuid.UUID(bytes_le=bytes(setting.PowerSetting)), data


class SystemEvents(QObject):
    """The notifications above. Does nothing (and emits nothing) off Windows."""

    suspending = Signal()
    resumed = Signal()
    displaysOff = Signal()
    displaysOn = Signal()
    locked = Signal()
    unlocked = Signal()
    displaysChanged = Signal()
    clockChanged = Signal()
    endingSession = Signal()

    def __init__(self, parent: QObject | None = None, clock=time.monotonic) -> None:
        super().__init__(parent)
        self._clock = clock
        self._resumed_at: float | None = None
        self._display_state: str | None = None
        self._last_message: tuple | None = None
        self.listening = False
        self._window: QWindow | None = None
        self._filter = None
        self._power_handle = None
        # Only Qt's "windows" platform has real window handles (not "offscreen").
        if sys.platform == "win32" and QGuiApplication.instance() is not None and QGuiApplication.platformName() == "windows":
            try:
                self._listen()
            except Exception:
                log.warning("Could not listen for sleep, display and session notifications; waking is noticed from the clock instead", exc_info=True)

    def handle(self, event: str | None) -> None:
        """Turn a decoded event into a signal, merging repeats."""
        if event is None:
            return
        now = self._clock()
        if event == "resume":
            if self._resumed_at is not None and now - self._resumed_at < RESUME_MERGE_S:
                return
            self._resumed_at = now
        elif event == "suspend":
            self._resumed_at = None
        elif event.startswith("display_"):
            if event == "display_dimmed":
                return  # still on; nothing to do
            if self._display_state is None:
                self._display_state = event  # Windows reports the current state on registering
                return
            if event == self._display_state:
                return
            self._display_state = event
        log.info("Windows: %s", event.replace("_", " "))
        signal = {
            "suspend": self.suspending,
            "resume": self.resumed,
            "display_off": self.displaysOff,
            "display_on": self.displaysOn,
            "lock": self.locked,
            "unlock": self.unlocked,
            "display_change": self.displaysChanged,
            "time_change": self.clockChanged,
            "end_session": self.endingSession,
        }[event]
        signal.emit()

    def close(self) -> None:
        if not self.listening:
            return
        app = QGuiApplication.instance()
        if app is not None and self._filter is not None:
            app.removeNativeEventFilter(self._filter)
        try:
            user32 = ctypes.WinDLL("user32")
            if self._power_handle:
                user32.UnregisterPowerSettingNotification(wintypes.HANDLE(self._power_handle))
            ctypes.WinDLL("wtsapi32").WTSUnRegisterSessionNotification(wintypes.HWND(self._hwnd))
        except OSError:
            pass
        self.listening = False

    def _listen(self) -> None:
        self._window = QWindow()  # never shown; its handle receives the broadcasts
        self._hwnd = int(self._window.winId())
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
        guid = (ctypes.c_byte * 16).from_buffer_copy(GUID_CONSOLE_DISPLAY_STATE.bytes_le)
        self._guid = guid  # kept alive while registered
        self._power_handle = user32.RegisterPowerSettingNotification(self._hwnd, ctypes.byref(guid), 0)  # DEVICE_NOTIFY_WINDOW_HANDLE
        if not self._power_handle:
            log.info("RegisterPowerSettingNotification failed (%d); display on/off isn't watched", ctypes.get_last_error())
        wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
        wtsapi32.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
        if not wtsapi32.WTSRegisterSessionNotification(self._hwnd, 0):  # NOTIFY_FOR_THIS_SESSION
            log.info("WTSRegisterSessionNotification failed (%d); lock and unlock aren't watched", ctypes.get_last_error())
        self._filter = _Filter(self)
        QGuiApplication.instance().installNativeEventFilter(self._filter)
        self.listening = True

    def _on_message(self, msg: wintypes.MSG) -> None:
        # Broadcasts reach every top-level window of the app: take only ours.
        if msg.hWnd != self._hwnd:
            return
        identity = (msg.message, msg.wParam, msg.lParam, msg.time)
        if identity == self._last_message:
            return  # a posted message seen a second time
        self._last_message = identity
        setting = read_setting(msg.lParam) if msg.message == WM_POWERBROADCAST and msg.wParam == PBT_POWERSETTINGCHANGE else None
        self.handle(decode(msg.message, msg.wParam, setting))


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, events: SystemEvents) -> None:
        super().__init__()
        self._events = events

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            try:
                self._events._on_message(wintypes.MSG.from_address(int(message)))
            except Exception:
                log.exception("Handling a Windows message failed")
        return False, 0
