# SPDX-License-Identifier: GPL-3.0-only
"""Layouts of the Windows API structures, and smoke tests that only run on Windows."""

import ctypes
import time
import os
import sys

import pytest

from nightlight.hardware import win32

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs Windows")
# Build machines (GitHub Actions sets CI) run without a desktop or monitors.
real_display = pytest.mark.skipif(bool(os.environ.get("CI")), reason="needs a real display, not a CI runner")


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
    assert ctypes.sizeof(win32.DISPLAYCONFIG_SDR_WHITE_LEVEL) == 24
    assert ctypes.sizeof(win32.DISPLAYCONFIG_SET_SDR_WHITE_LEVEL) == 28  # 25 bytes, padded like the C struct


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
@real_display
def test_display_configuration_lists_this_machines_displays():
    targets = win32.display_targets()
    assert targets, "QueryDisplayConfig found no active displays"
    assert all(t.gdi_name.startswith("\\\\.\\DISPLAY") for t in targets)
    assert {t.gdi_name for t in targets} <= set(win32.attached_display_names())


@windows_only
@real_display
def test_gamma_ramps_can_be_read():
    for name in win32.attached_display_names():
        ramp = win32.get_gamma_ramp(name)
        assert ramp is not None and [len(c) for c in ramp] == [256, 256, 256]


@windows_only
@real_display
def test_physical_monitor_handles_open_and_close():
    for monitor in win32.physical_monitors():
        win32.destroy_physical_monitor(monitor.handle)


def test_every_structure_matches_the_sdk_layout():
    assert win32.layout_problems() == []
    assert win32.self_check() == []


def test_error_codes_are_explained():
    text = win32.describe_error(win32.ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_COMMAND)
    assert text.startswith("0xC0262589") and "DDC/CI may be off" in text
    assert "disconnected" in win32.describe_error(win32.ERROR_GRAPHICS_MONITOR_NO_LONGER_EXISTS)
    assert "Remote Desktop" in win32.describe_error(win32.ERROR_ACCESS_DENIED)
    assert "without an error code" in win32.describe_error(0)  # some drivers fail and leave no code


def test_ddc_errors_are_classified_like_monitorian():
    assert win32.DdcError("x", win32.ERROR_GRAPHICS_DDCCI_VCP_NOT_SUPPORTED).status == "not supported"
    assert win32.DdcError("x", win32.ERROR_GRAPHICS_I2C_ERROR_TRANSMITTING_DATA).status == "transmission failed"
    assert win32.DdcError("x", win32.ERROR_GRAPHICS_DDCCI_INVALID_MESSAGE_CHECKSUM).transient
    assert not win32.DdcError("x", win32.ERROR_GRAPHICS_DDCCI_VCP_NOT_SUPPORTED).transient


class _FakeDxva2:
    def __init__(self, times):
        self.times = times

    def SetVCPFeature(self, handle, code, value):
        self.times.append(time.monotonic())
        return True


def test_messages_to_one_monitor_are_spaced_out(monkeypatch):
    times = []
    monkeypatch.setattr(win32, "_dxva2", _FakeDxva2(times), raising=False)
    win32.forget_handle(99)
    for _ in range(3):
        win32.set_vcp(99, win32.VCP_LUMINANCE, 50)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(gap >= win32.DDC_MIN_INTERVAL - 0.005 for gap in gaps), gaps


def test_out_of_range_values_never_reach_windows():
    with pytest.raises(ValueError):
        win32.set_vcp(1, 0x10, 70000)
    with pytest.raises(ValueError):
        win32.get_vcp(1, 300)


@windows_only
def test_gamma_ramps_must_have_the_right_shape():
    with pytest.raises(ValueError):
        win32.set_gamma_ramp("\\\\.\\DISPLAY1", [[0] * 10] * 3)


@windows_only
def test_capabilities_are_detected():
    caps = win32.capabilities()
    assert caps["windows"] and caps["build"] > 0 and caps["layout problems"] == "none"


@windows_only
@real_display
def test_each_display_has_a_stable_identity_from_its_edid():
    for target in win32.display_targets():
        assert target.edid is not None, f"no EDID in the registry for {target.instance_id}"
        assert target.key.startswith(target.edid.hardware_id)
        assert target.hardware_id == target.edid.hardware_id  # the registry and the device path agree


@windows_only
@real_display
def test_an_invalid_handle_is_reported_as_a_vanished_monitor():
    if not win32.ddc_available():
        pytest.skip("no dxva2")
    with pytest.raises(win32.DdcError) as raised:
        win32.get_vcp(0x7FFF1234, win32.VCP_LUMINANCE)
    assert raised.value.monitor_gone
