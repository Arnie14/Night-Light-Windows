# SPDX-License-Identifier: GPL-3.0-only
"""Layouts of the Windows API structures, and smoke tests that only run on Windows."""

import ctypes
import itertools
import os
import pathlib
import sys
import time

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
    gaps = [b - a for a, b in itertools.pairwise(times)]
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


def test_monitor_handles_are_closed_if_opening_the_rest_fails(monkeypatch):
    """physical_monitors() hands its handles to the caller only on success;
    a failure partway through would have leaked the ones already open."""

    class User32:
        @staticmethod
        def EnumDisplayMonitors(_hdc, _rect, callback, _data):
            for hmonitor in (1, 2):
                callback(hmonitor, None, None, 0)
            return True

    opened = {1: [win32.PhysicalMonitor(r"\\.\DISPLAY1", 101, "first")]}

    def physical_monitors_of(hmonitor):
        if hmonitor in opened:
            return opened[hmonitor]
        raise OSError("the driver failed")

    closed = []
    monkeypatch.setattr(win32, "ddc_available", lambda: True)
    monkeypatch.setattr(win32, "_user32", User32, raising=False)
    monkeypatch.setattr(win32, "_MONITORENUMPROC", lambda function: function, raising=False)
    monkeypatch.setattr(win32, "_physical_monitors_of", physical_monitors_of)
    monkeypatch.setattr(win32, "destroy_physical_monitor", closed.append)
    with pytest.raises(OSError):
        win32.physical_monitors()
    assert closed == [101]


# --- Which color profile a display uses (Reload calibrations) -----------------


class FakeColorSystem:
    """Windows Color System's answers for one monitor: whether it uses
    per-user profiles, and the profile chosen per scope (0 system, 1 user)."""

    def __init__(self, per_user, chosen):
        self.per_user, self.chosen, self.asked = per_user, chosen, []

    def EnumDisplayDevicesW(self, gdi_name, index, monitor, flags):
        monitor._obj.DeviceKey = r"\Registry\Machine\...\0007"
        return True

    def WcsGetUsePerUserProfiles(self, key, device_class, result):
        result._obj.value = self.per_user
        return True

    def WcsGetDefaultColorProfileSize(self, scope, key, kind, subtype, profile_id, size):
        self.asked.append(scope)
        name = self.chosen.get(scope)
        if name is None:
            return False  # ERROR_FILE_NOT_FOUND: none chosen
        size._obj.value = 2 * (len(name) + 1)
        return True

    def WcsGetDefaultColorProfile(self, scope, key, kind, subtype, profile_id, size, buffer):
        buffer.value = self.chosen[scope]
        return True

    def GetColorDirectoryW(self, machine, folder, size):
        folder.value = r"C:\WINDOWS\system32\spool\drivers\color"
        return True


@pytest.mark.parametrize(("per_user", "chosen", "expected", "asked"), [
    (True, {1: "DELL calibrated.icc", 0: "sRGB.icm"}, "DELL calibrated.icc", [1]),  # the user's own choice
    (False, {1: "ignored.icc", 0: "Office.icm"}, "Office.icm", [0]),  # the system's, when the user has none of their own
    (True, {}, "fallback", [1]),  # none chosen: the device context's (sRGB)
])
def test_the_profile_chosen_for_the_monitor_comes_first(monkeypatch, per_user, chosen, expected, asked):
    from pathlib import Path

    fake = FakeColorSystem(per_user, chosen)
    monkeypatch.setattr(win32, "_mscms", fake, raising=False)
    monkeypatch.setattr(win32, "_user32", fake, raising=False)
    monkeypatch.setattr(win32, "_device_context_profile", lambda gdi_name: "fallback")
    profile = win32.display_profile(r"\.\DISPLAY1")
    assert fake.asked == asked
    assert profile == (expected if expected == "fallback" else str(Path(r"C:\WINDOWS\system32\spool\drivers\color") / expected))


# --- Unlocking warmer colors (GdiIcmGammaRange, as LightBulb sets it) ---------


@windows_only
@pytest.mark.parametrize(("exit_code", "set_after", "expected"), [
    (0, True, "done"),
    (None, False, "cancelled"),  # the administrator prompt was declined: nothing changed, no error
    (5, False, "failed"),
    (0, False, "failed"),  # reg.exe said yes, but the value isn't there
])
def test_unlocking_asks_reg_exe_through_the_administrator_prompt(monkeypatch, exit_code, set_after, expected):
    import winreg

    def not_administrator(*args):
        raise PermissionError("Access is denied")

    asked = []
    monkeypatch.setattr(winreg, "CreateKeyEx", not_administrator)
    monkeypatch.setattr(win32, "run_elevated", lambda path, arguments, parent=0: asked.append((path, arguments)) or exit_code)
    monkeypatch.setattr(win32, "gamma_range_expanded", lambda: set_after)
    assert win32.expand_gamma_range() == expected
    [(path, arguments)] = asked
    assert path.lower().endswith(r"\system32\reg.exe")  # by its full path, not whatever PATH finds
    assert arguments == r'add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM" /v GdiIcmGammaRange /t REG_DWORD /d 256 /f'


@windows_only
def test_elevated_programs_report_their_exit_code():
    """The ShellExecuteEx plumbing, for real, without the prompt ("open")."""
    reg = str(pathlib.Path(os.environ["SYSTEMROOT"]) / "System32" / "reg.exe")
    assert win32.run_elevated(reg, rf'query "HKLM\{win32.GAMMA_RANGE_KEY}"', verb="open") == 0
    assert win32.run_elevated(reg, rf'query "HKLM\{win32.GAMMA_RANGE_KEY}" /v NoSuchValue', verb="open") == 1
