"""Gamma backends: apply a night light whitepoint to the displays.

Night light is global, as in KDE: the same ramp goes to every display.
"""

from __future__ import annotations

import logging
import sys
from typing import Protocol

from nightlight.core.colortemp import NEUTRAL_WHITEPOINT, Whitepoint, gamma_ramp

log = logging.getLogger(__name__)


class GammaBackend(Protocol):
    def apply(self, whitepoint: Whitepoint) -> None: ...
    def reset(self) -> None: ...


class FakeGammaBackend:
    """Remembers and logs the whitepoint instead of touching any display."""

    def __init__(self) -> None:
        self.whitepoint: Whitepoint = NEUTRAL_WHITEPOINT

    def apply(self, whitepoint: Whitepoint) -> None:
        if whitepoint != self.whitepoint:
            log.info("Fake gamma -> r=%.3f g=%.3f b=%.3f", *whitepoint)
        self.whitepoint = whitepoint

    def reset(self) -> None:
        self.apply(NEUTRAL_WHITEPOINT)


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128),
            ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128),
            ("DeviceKey", wintypes.WCHAR * 128),
        ]

    _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
    _RAMP_SIZE = 256

    _user32 = ctypes.WinDLL("user32")
    _gdi32 = ctypes.WinDLL("gdi32")
    _user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(_DISPLAY_DEVICEW), wintypes.DWORD]
    _user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    # Explicit restypes matter: HDCs are pointers and would be truncated as ints.
    _gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    _gdi32.CreateDCW.restype = wintypes.HDC
    _gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
    _gdi32.SetDeviceGammaRamp.restype = wintypes.BOOL
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _gdi32.DeleteDC.restype = wintypes.BOOL

    class WindowsGammaBackend:
        """Sets each display's gamma ramp with GDI's SetDeviceGammaRamp."""

        def __init__(self) -> None:
            self._warned: set[str] = set()

        def apply(self, whitepoint: Whitepoint) -> None:
            ramp = ((wintypes.WORD * _RAMP_SIZE) * 3)()
            for c, channel in enumerate(gamma_ramp(whitepoint, _RAMP_SIZE)):
                for i, value in enumerate(channel):
                    ramp[c][i] = value
            for name in self._display_names():
                hdc = _gdi32.CreateDCW(name, None, None, None)
                if not hdc:
                    self._warn_once(name, "Could not open a device context for %s", name)
                    continue
                try:
                    if not _gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
                        # Windows rejects ramps far from identity unless the
                        # GdiIcmGammaRange registry value is raised.
                        self._warn_once(name, "Windows rejected the gamma ramp for %s (temperature too warm?)", name)
                finally:
                    _gdi32.DeleteDC(hdc)

        def reset(self) -> None:
            self.apply(NEUTRAL_WHITEPOINT)

        def _warn_once(self, key: str, message: str, *args) -> None:
            if key not in self._warned:
                self._warned.add(key)
                log.warning(message, *args)

        @staticmethod
        def _display_names() -> list[str]:
            names = []
            i = 0
            while True:
                device = _DISPLAY_DEVICEW()
                device.cb = ctypes.sizeof(device)
                if not _user32.EnumDisplayDevicesW(None, i, ctypes.byref(device), 0):
                    return names
                if device.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
                    names.append(device.DeviceName)
                i += 1
