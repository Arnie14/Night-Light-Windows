# SPDX-License-Identifier: GPL-3.0-only
"""Thin ctypes bindings for the Windows display APIs the Windows backends need.

- QueryDisplayConfig: each active display's friendly name ("DELL U2720Q"),
  connector, whether it is the built-in panel, and whether HDR is on.
- dxva2 (Monitor Configuration API): DDC/CI brightness of external monitors.
- GDI: gamma ramps, used for night light.

The approach follows Monitorian and Twinkle Tray (both MIT), which drive these
same APIs; see docs/windows-notes.md. Structures are defined on every platform
so their layout can be unit-tested; the functions need Windows.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

# --- QueryDisplayConfig -----------------------------------------------------

QDC_ONLY_ACTIVE_PATHS = 0x2
ERROR_SUCCESS = 0
ERROR_INSUFFICIENT_BUFFER = 122

DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME = 1
DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME = 2
DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO = 9
DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO_2 = 15  # Windows 11 24H2+
DISPLAYCONFIG_ADVANCED_COLOR_MODE_HDR = 2

# DISPLAYCONFIG_VIDEO_OUTPUT_TECHNOLOGY values.
OUTPUT_TECHNOLOGIES = {
    0: "VGA",
    1: "S-Video",
    2: "Composite",
    3: "Component",
    4: "DVI",
    5: "HDMI",
    6: "LVDS (built-in)",
    8: "D-JPN",
    9: "SDI",
    10: "DisplayPort",
    11: "DisplayPort (built-in)",
    12: "UDI",
    13: "UDI (built-in)",
    14: "SDTV dongle",
    15: "Miracast",
    16: "Indirect (USB/DisplayLink)",
    17: "Indirect (virtual)",
    18: "DisplayPort over USB4",
    0x80000000: "Internal",
    0xFFFFFFFF: "Other",
}
_INTERNAL_TECHNOLOGIES = {6, 11, 13, 0x80000000}
# Displays that are drawn by a driver rather than a GPU output (DisplayLink
# docks, wireless displays, remote/virtual displays) have no DDC/CI channel.
INDIRECT_TECHNOLOGIES = {15, 16, 17}


# Fixed-width types rather than wintypes.DWORD/LONG/BOOL, which are 8 bytes
# on 64-bit Linux: the layouts are then right everywhere and testable anywhere.
class LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_uint32), ("HighPart", ctypes.c_int32)]


class DISPLAYCONFIG_RATIONAL(ctypes.Structure):
    _fields_ = [("Numerator", ctypes.c_uint32), ("Denominator", ctypes.c_uint32)]


class DISPLAYCONFIG_PATH_SOURCE_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx", ctypes.c_uint32),
        ("statusFlags", ctypes.c_uint32),
    ]


class DISPLAYCONFIG_PATH_TARGET_INFO(ctypes.Structure):
    _fields_ = [
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
        ("modeInfoIdx", ctypes.c_uint32),
        ("outputTechnology", ctypes.c_uint32),
        ("rotation", ctypes.c_uint32),
        ("scaling", ctypes.c_uint32),
        ("refreshRate", DISPLAYCONFIG_RATIONAL),
        ("scanLineOrdering", ctypes.c_uint32),
        ("targetAvailable", ctypes.c_int32),  # BOOL
        ("statusFlags", ctypes.c_uint32),
    ]


class DISPLAYCONFIG_PATH_INFO(ctypes.Structure):
    _fields_ = [
        ("sourceInfo", DISPLAYCONFIG_PATH_SOURCE_INFO),
        ("targetInfo", DISPLAYCONFIG_PATH_TARGET_INFO),
        ("flags", ctypes.c_uint32),
    ]


class DISPLAYCONFIG_MODE_INFO(ctypes.Structure):
    # The mode itself is a union that is never read here; only its size matters.
    _fields_ = [
        ("infoType", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
        ("adapterId", LUID),
        ("mode", ctypes.c_uint64 * 6),
    ]


class DISPLAYCONFIG_DEVICE_INFO_HEADER(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("size", ctypes.c_uint32),
        ("adapterId", LUID),
        ("id", ctypes.c_uint32),
    ]


class DISPLAYCONFIG_SOURCE_DEVICE_NAME(ctypes.Structure):
    _fields_ = [("header", DISPLAYCONFIG_DEVICE_INFO_HEADER), ("viewGdiDeviceName", wintypes.WCHAR * 32)]


class DISPLAYCONFIG_TARGET_DEVICE_NAME(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("flags", ctypes.c_uint32),
        ("outputTechnology", ctypes.c_uint32),
        ("edidManufactureId", ctypes.c_uint16),
        ("edidProductCodeId", ctypes.c_uint16),
        ("connectorInstance", ctypes.c_uint32),
        ("monitorFriendlyDeviceName", wintypes.WCHAR * 64),
        ("monitorDevicePath", wintypes.WCHAR * 128),
    ]


class DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("value", ctypes.c_uint32),  # bit 0: supported, bit 1: enabled
        ("colorEncoding", ctypes.c_uint32),
        ("bitsPerColorChannel", ctypes.c_uint32),
    ]


class DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO_2(ctypes.Structure):
    _fields_ = [
        ("header", DISPLAYCONFIG_DEVICE_INFO_HEADER),
        ("value", ctypes.c_uint32),
        ("colorEncoding", ctypes.c_uint32),
        ("bitsPerColorChannel", ctypes.c_uint32),
        ("activeColorMode", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class DisplayTarget:
    """One active display, as Windows' display configuration describes it."""

    gdi_name: str  # "\\.\DISPLAY1": the source; cloned displays share one
    friendly_name: str  # from the monitor's EDID, e.g. "DELL U2720Q"; may be ""
    device_path: str  # "\\?\DISPLAY#DEL41A2#5&...#{e6f07b5f-...}"
    output_technology: int
    hdr: bool

    @property
    def internal(self) -> bool:
        return self.output_technology in _INTERNAL_TECHNOLOGIES

    @property
    def connector(self) -> str:
        return OUTPUT_TECHNOLOGIES.get(self.output_technology, f"type {self.output_technology}")

    @property
    def instance_id(self) -> str:
        return instance_id_from_device_path(self.device_path)


def instance_id_from_device_path(path: str) -> str:
    r"""Turn a monitor device path into its PnP instance id, which WMI's
    InstanceName starts with: "\\?\DISPLAY#BOE0812#4&2a6&0&UID26#{guid}"
    becomes "DISPLAY\BOE0812\4&2a6&0&UID26"."""
    parts = path.removeprefix("\\\\?\\").split("#")
    return "\\".join(parts[:3]) if len(parts) >= 3 else ""


# --- dxva2 (DDC/CI) ---------------------------------------------------------

VCP_LUMINANCE = 0x10

# winerror.h. Monitorian and Twinkle Tray retry the transient ones.
ERROR_GRAPHICS_I2C_ERROR_TRANSMITTING_DATA = 0xC0262582
ERROR_GRAPHICS_I2C_ERROR_RECEIVING_DATA = 0xC0262583
ERROR_GRAPHICS_DDCCI_VCP_NOT_SUPPORTED = 0xC0262584
ERROR_GRAPHICS_DDCCI_INVALID_DATA = 0xC0262585
ERROR_GRAPHICS_DDCCI_MONITOR_RETURNED_INVALID_TIMING_STATUS_BYTE = 0xC0262586
ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_COMMAND = 0xC0262589
ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_LENGTH = 0xC026258A
ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_CHECKSUM = 0xC026258B
ERROR_GRAPHICS_INVALID_PHYSICAL_MONITOR_HANDLE = 0xC026258C
ERROR_GRAPHICS_MONITOR_NO_LONGER_EXISTS = 0xC026258D

_TRANSIENT_ERRORS = {
    ERROR_GRAPHICS_I2C_ERROR_TRANSMITTING_DATA,
    ERROR_GRAPHICS_I2C_ERROR_RECEIVING_DATA,
    ERROR_GRAPHICS_DDCCI_MONITOR_RETURNED_INVALID_TIMING_STATUS_BYTE,
    ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_COMMAND,
    ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_LENGTH,
    ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_CHECKSUM,
}
_GONE_ERRORS = {ERROR_GRAPHICS_INVALID_PHYSICAL_MONITOR_HANDLE, ERROR_GRAPHICS_MONITOR_NO_LONGER_EXISTS}
_DDC_ATTEMPTS = 3
_DDC_RETRY_DELAY = 0.05  # DDC/CI asks for at least 50 ms between messages


class DdcError(OSError):
    def __init__(self, what: str, code: int) -> None:
        self.code = code & 0xFFFFFFFF
        super().__init__(f"{what} failed: 0x{self.code:08X} {_format_error(self.code)}".rstrip())

    @property
    def monitor_gone(self) -> bool:
        """The handle no longer works: the monitor was unplugged or the displays were reconfigured."""
        return self.code in _GONE_ERRORS


class PHYSICAL_MONITOR(ctypes.Structure):
    _pack_ = 1  # declared under #pragma pack(1) in PhysicalMonitorEnumerationAPI.h
    _layout_ = "ms"
    _fields_ = [("hPhysicalMonitor", wintypes.HANDLE), ("szPhysicalMonitorDescription", wintypes.WCHAR * 128)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


@dataclass(frozen=True)
class PhysicalMonitor:
    gdi_name: str  # the display (HMONITOR) it belongs to
    handle: int
    description: str  # usually "Generic PnP Monitor"


# --- GDI gamma ramps --------------------------------------------------------

RAMP_SIZE = 256
GammaRamp = list[list[int]]  # three channels (R, G, B) of RAMP_SIZE 16-bit values
_GammaRampArray = (wintypes.WORD * RAMP_SIZE) * 3

_DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        _dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)
    except OSError:  # missing on some stripped-down Windows editions
        _dxva2 = None

    # Explicit types everywhere: handles are pointers and would be truncated
    # to 32 bits if ctypes guessed int.
    _user32.GetDisplayConfigBufferSizes.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32)]
    _user32.GetDisplayConfigBufferSizes.restype = wintypes.LONG
    _user32.QueryDisplayConfig.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(DISPLAYCONFIG_PATH_INFO),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(DISPLAYCONFIG_MODE_INFO),
        ctypes.c_void_p,
    ]
    _user32.QueryDisplayConfig.restype = wintypes.LONG
    _user32.DisplayConfigGetDeviceInfo.argtypes = [ctypes.POINTER(DISPLAYCONFIG_DEVICE_INFO_HEADER)]
    _user32.DisplayConfigGetDeviceInfo.restype = wintypes.LONG

    _MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    _user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), _MONITORENUMPROC, wintypes.LPARAM]
    _user32.EnumDisplayMonitors.restype = wintypes.BOOL
    _user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    _user32.GetMonitorInfoW.restype = wintypes.BOOL
    _user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    _user32.EnumDisplayDevicesW.restype = wintypes.BOOL

    _gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    _gdi32.CreateDCW.restype = wintypes.HDC
    _gdi32.DeleteDC.argtypes = [wintypes.HDC]
    _gdi32.DeleteDC.restype = wintypes.BOOL
    _gdi32.GetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
    _gdi32.GetDeviceGammaRamp.restype = wintypes.BOOL
    _gdi32.SetDeviceGammaRamp.argtypes = [wintypes.HDC, ctypes.c_void_p]
    _gdi32.SetDeviceGammaRamp.restype = wintypes.BOOL

    _kernel32.FormatMessageW.argtypes = [
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    _kernel32.FormatMessageW.restype = wintypes.DWORD

    if _dxva2 is not None:
        _dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, ctypes.POINTER(wintypes.DWORD)]
        _dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
        _dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, wintypes.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
        _dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL
        _dxva2.DestroyPhysicalMonitor.argtypes = [wintypes.HANDLE]
        _dxva2.DestroyPhysicalMonitor.restype = wintypes.BOOL
        _dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [
            wintypes.HANDLE,
            ctypes.c_ubyte,  # wintypes.BYTE is signed
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        _dxva2.GetVCPFeatureAndVCPFeatureReply.restype = wintypes.BOOL
        _dxva2.SetVCPFeature.argtypes = [wintypes.HANDLE, ctypes.c_ubyte, wintypes.DWORD]
        _dxva2.SetVCPFeature.restype = wintypes.BOOL
        _dxva2.GetMonitorBrightness.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        _dxva2.GetMonitorBrightness.restype = wintypes.BOOL
        _dxva2.SetMonitorBrightness.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        _dxva2.SetMonitorBrightness.restype = wintypes.BOOL


def _format_error(code: int) -> str:
    if sys.platform != "win32":
        return ""
    FORMAT_MESSAGE_FROM_SYSTEM = 0x1000
    FORMAT_MESSAGE_IGNORE_INSERTS = 0x200
    buffer = ctypes.create_unicode_buffer(512)
    if _kernel32.FormatMessageW(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, None, code, 0, buffer, len(buffer), None):
        return buffer.value.strip()
    return ""


# --- Display configuration --------------------------------------------------


def display_targets() -> list[DisplayTarget]:
    """All active displays, in Windows' path order. Empty if the query fails,
    so callers fall back to less detailed APIs instead of failing."""
    try:
        paths = _query_active_paths()
    except OSError:
        log.warning("QueryDisplayConfig failed", exc_info=True)
        return []
    targets = []
    for path in paths:
        source = DISPLAYCONFIG_SOURCE_DEVICE_NAME()
        _fill_header(source.header, DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME, source, path.sourceInfo.adapterId, path.sourceInfo.id)
        target = DISPLAYCONFIG_TARGET_DEVICE_NAME()
        _fill_header(target.header, DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME, target, path.targetInfo.adapterId, path.targetInfo.id)
        if _device_info(source.header) or _device_info(target.header):
            # Twinkle Tray: a partial list could pair handles with the wrong
            # monitors, so give up and let callers use the fallback.
            log.warning("DisplayConfigGetDeviceInfo failed; display names unavailable")
            return []
        targets.append(
            DisplayTarget(
                gdi_name=source.viewGdiDeviceName,
                friendly_name=target.monitorFriendlyDeviceName.strip(),
                device_path=target.monitorDevicePath,
                output_technology=path.targetInfo.outputTechnology,
                hdr=_hdr_enabled(path.targetInfo.adapterId, path.targetInfo.id),
            )
        )
    return targets


def _query_active_paths() -> list[DISPLAYCONFIG_PATH_INFO]:
    # The configuration can change between sizing and querying; retry then.
    for _ in range(5):
        num_paths, num_modes = ctypes.c_uint32(), ctypes.c_uint32()
        result = _user32.GetDisplayConfigBufferSizes(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(num_paths), ctypes.byref(num_modes))
        if result != ERROR_SUCCESS:
            raise ctypes.WinError(result)
        paths = (DISPLAYCONFIG_PATH_INFO * num_paths.value)()
        modes = (DISPLAYCONFIG_MODE_INFO * num_modes.value)()
        result = _user32.QueryDisplayConfig(QDC_ONLY_ACTIVE_PATHS, ctypes.byref(num_paths), paths, ctypes.byref(num_modes), modes, None)
        if result == ERROR_SUCCESS:
            return list(paths[: num_paths.value])
        if result != ERROR_INSUFFICIENT_BUFFER:
            raise ctypes.WinError(result)
    raise ctypes.WinError(ERROR_INSUFFICIENT_BUFFER)


def _fill_header(header, kind: int, packet, adapter: LUID, target_id: int) -> None:
    header.type = kind
    header.size = ctypes.sizeof(packet)
    header.adapterId = adapter
    header.id = target_id


def _device_info(header: DISPLAYCONFIG_DEVICE_INFO_HEADER) -> int:
    return _user32.DisplayConfigGetDeviceInfo(ctypes.byref(header))


def _hdr_enabled(adapter: LUID, target_id: int) -> bool:
    # Windows 11 24H2 can report "advanced color" for SDR displays (auto color
    # management), so prefer the newer query, which states HDR explicitly.
    info2 = DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO_2()
    _fill_header(info2.header, DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO_2, info2, adapter, target_id)
    if _device_info(info2.header) == ERROR_SUCCESS:
        return info2.activeColorMode == DISPLAYCONFIG_ADVANCED_COLOR_MODE_HDR
    info = DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO()
    _fill_header(info.header, DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO, info, adapter, target_id)
    if _device_info(info.header) == ERROR_SUCCESS:
        return bool(info.value & 0x2)
    return False


def attached_display_names() -> list[str]:
    """GDI names of displays on the desktop, from the older EnumDisplayDevices API."""
    names = []
    i = 0
    while True:
        device = DISPLAY_DEVICEW()
        device.cb = ctypes.sizeof(device)
        if not _user32.EnumDisplayDevicesW(None, i, ctypes.byref(device), 0):
            return names
        if device.StateFlags & _DISPLAY_DEVICE_ATTACHED_TO_DESKTOP:
            names.append(device.DeviceName)
        i += 1


# --- DDC/CI -----------------------------------------------------------------


def ddc_available() -> bool:
    return sys.platform == "win32" and _dxva2 is not None


def physical_monitors() -> list[PhysicalMonitor]:
    """Open a handle to every physical monitor. The caller owns the handles
    and must pass each to destroy_physical_monitor()."""
    if not ddc_available():
        return []
    monitors: list[PhysicalMonitor] = []

    def collect(hmonitor, _hdc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        name = info.szDevice if _user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)) else ""
        count = wintypes.DWORD()
        # Virtual, remote and some USB displays have no physical monitors to
        # give; skip them rather than failing every other display.
        if not _dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmonitor, ctypes.byref(count)) or count.value == 0:
            log.debug("No physical monitors for %s", name or hmonitor)
            return True
        array = (PHYSICAL_MONITOR * count.value)()
        if not _dxva2.GetPhysicalMonitorsFromHMONITOR(hmonitor, count.value, array):
            log.debug("GetPhysicalMonitorsFromHMONITOR failed for %s", name)
            return True
        for entry in array:
            monitors.append(PhysicalMonitor(name, entry.hPhysicalMonitor or 0, entry.szPhysicalMonitorDescription))
        return True

    callback = _MONITORENUMPROC(collect)  # kept referenced until the call returns
    _user32.EnumDisplayMonitors(None, None, callback, 0)
    return monitors


def destroy_physical_monitor(handle: int) -> None:
    if ddc_available():
        _dxva2.DestroyPhysicalMonitor(handle)


def _ddc_call(what: str, call) -> None:
    for attempt in range(_DDC_ATTEMPTS):
        if call():
            return
        code = ctypes.get_last_error() & 0xFFFFFFFF
        if code not in _TRANSIENT_ERRORS or attempt == _DDC_ATTEMPTS - 1:
            raise DdcError(what, code)
        log.debug("%s: transient DDC/CI error 0x%08X, retrying", what, code)
        time.sleep(_DDC_RETRY_DELAY)


def get_vcp(handle: int, code: int) -> tuple[int, int]:
    """(current, maximum) of a VCP feature, e.g. VCP_LUMINANCE."""
    current, maximum = wintypes.DWORD(), wintypes.DWORD()
    _ddc_call(
        f"Reading VCP 0x{code:02X}",
        lambda: _dxva2.GetVCPFeatureAndVCPFeatureReply(handle, code, None, ctypes.byref(current), ctypes.byref(maximum)),
    )
    return current.value, maximum.value


def set_vcp(handle: int, code: int, value: int) -> None:
    _ddc_call(f"Setting VCP 0x{code:02X}", lambda: _dxva2.SetVCPFeature(handle, code, value))


def get_monitor_brightness(handle: int) -> tuple[int, int, int]:
    """(minimum, current, maximum) from the "high-level" brightness API.

    It reads the monitor's capabilities string first, which is slow and
    unreliable, so it is only a fallback for monitors that reject VCP 0x10."""
    minimum, current, maximum = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    _ddc_call(
        "GetMonitorBrightness",
        lambda: _dxva2.GetMonitorBrightness(handle, ctypes.byref(minimum), ctypes.byref(current), ctypes.byref(maximum)),
    )
    return minimum.value, current.value, maximum.value


def set_monitor_brightness(handle: int, value: int) -> None:
    # Monitorian: this can report success even when the monitor ignored it.
    _ddc_call("SetMonitorBrightness", lambda: _dxva2.SetMonitorBrightness(handle, value))


# --- Gamma ramps ------------------------------------------------------------


class _DisplayDC:
    """A device context for one display, deleted on exit."""

    def __init__(self, gdi_name: str) -> None:
        self._name = gdi_name
        self._hdc = None

    def __enter__(self):
        self._hdc = _gdi32.CreateDCW(self._name, None, None, None)
        if not self._hdc:
            raise ctypes.WinError(ctypes.get_last_error(), f"Could not open a device context for {self._name}")
        return self._hdc

    def __exit__(self, *_exc) -> None:
        _gdi32.DeleteDC(self._hdc)


def get_gamma_ramp(gdi_name: str) -> GammaRamp | None:
    array = _GammaRampArray()
    with _DisplayDC(gdi_name) as hdc:
        if not _gdi32.GetDeviceGammaRamp(hdc, ctypes.byref(array)):
            return None
    return [list(channel) for channel in array]


def set_gamma_ramp(gdi_name: str, ramp: GammaRamp) -> bool:
    """False when Windows rejects the ramp. Its GDI refuses ramps too far
    from identity (very warm temperatures) unless the GdiIcmGammaRange
    registry value is raised; see docs/windows-notes.md."""
    array = _GammaRampArray()
    for c, channel in enumerate(ramp):
        for i, value in enumerate(channel):
            array[c][i] = value
    with _DisplayDC(gdi_name) as hdc:
        return bool(_gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(array)))
