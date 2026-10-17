# SPDX-License-Identifier: GPL-3.0-only
"""Layouts of the Windows API structures, and smoke tests that only run on Windows."""

import ctypes
import sys

import pytest

from nightlight.hardware import win32

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")


def test_instance_id_from_device_path():
    path = r"\\?\DISPLAY#DEL41A2#5&2a6d2d4a&0&UID4352#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
    assert win32.instance_id_from_device_path(path) == r"DISPLAY\DEL41A2\5&2a6d2d4a&0&UID4352"
    assert win32.instance_id_from_device_path("") == ""


def test_layouts_without_strings():
    # Sizes from the Windows SDK headers; a wrong size makes the call fail or corrupt memory.
    assert ctypes.sizeof(win32.DISPLAYCONFIG_PATH_INFO) == 72
    assert ctypes.sizeof(win32.DISPLAYCONFIG_MODE_INFO) == 64
    assert ctypes.sizeof(win32.DISPLAYCONFIG_DEVICE_INFO_HEADER) == 20
    assert ctypes.sizeof(win32.DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO) == 32
    assert ctypes.sizeof(win32.DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO_2) == 36


@windows_only
def test_layouts_with_strings():
    assert ctypes.sizeof(win32.DISPLAYCONFIG_SOURCE_DEVICE_NAME) == 84
    assert ctypes.sizeof(win32.DISPLAYCONFIG_TARGET_DEVICE_NAME) == 420
    assert ctypes.sizeof(win32.MONITORINFOEXW) == 104
    assert ctypes.sizeof(win32.PHYSICAL_MONITOR) == ctypes.sizeof(ctypes.c_void_p) + 256
    assert ctypes.sizeof(win32.DISPLAY_DEVICEW) == 840


def test_ddc_error_classification():
    assert win32.DdcError("x", win32.ERROR_GRAPHICS_MONITOR_NO_LONGER_EXISTS).monitor_gone
    assert not win32.DdcError("x", win32.ERROR_GRAPHICS_DDCCI_VCP_NOT_SUPPORTED).monitor_gone
    # GetLastError values can come back sign-extended.
    assert win32.DdcError("x", -1071241843).code == win32.ERROR_GRAPHICS_MONITOR_NO_LONGER_EXISTS


@windows_only
def test_display_configuration_lists_this_machines_displays():
    targets = win32.display_targets()
    assert targets, "QueryDisplayConfig found no active displays"
    assert all(t.gdi_name.startswith("\\\\.\\DISPLAY") for t in targets)
    assert {t.gdi_name for t in targets} <= set(win32.attached_display_names())


@windows_only
def test_gamma_ramps_can_be_read():
    for name in win32.attached_display_names():
        ramp = win32.get_gamma_ramp(name)
        assert ramp is not None and [len(c) for c in ramp] == [256, 256, 256]


@windows_only
def test_physical_monitor_handles_open_and_close():
    for monitor in win32.physical_monitors():
        win32.destroy_physical_monitor(monitor.handle)
