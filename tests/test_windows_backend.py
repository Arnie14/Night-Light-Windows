# SPDX-License-Identifier: GPL-3.0-only
"""The Windows backends' logic, against a fake of the Windows APIs (runs anywhere)."""

import pytest

from nightlight.core.colortemp import gamma_ramp, identity_ramp, whitepoint
from nightlight.hardware import win32, windows
from nightlight.hardware.brightness import MonitorGoneError
from nightlight.hardware.win32 import DdcError, DisplayTarget, PhysicalMonitor

HDMI, DP, INTERNAL, DISPLAYLINK = 5, 10, 11, 16


def target(gdi, name, tech=HDMI, hdr=False, path=""):
    return DisplayTarget(gdi, name, path, tech, hdr)


class FakeDdc:
    """Physical monitors keyed by handle: (current, maximum) for VCP 0x10, or an error code."""

    def __init__(self, monitors, vcp, high_level=None):
        self.monitors = monitors
        self.vcp = vcp
        self.high_level = high_level or {}
        self.writes = []
        self.destroyed = []

    def install(self, monkeypatch, targets):
        monkeypatch.setattr(win32, "display_targets", lambda: targets)
        monkeypatch.setattr(win32, "physical_monitors", lambda: list(self.monitors))
        monkeypatch.setattr(win32, "destroy_physical_monitor", self.destroyed.append)
        monkeypatch.setattr(win32, "get_vcp", self.get_vcp)
        monkeypatch.setattr(win32, "set_vcp", lambda h, code, value: self.writes.append((h, value)))
        monkeypatch.setattr(win32, "get_monitor_brightness", self.get_high_level)
        monkeypatch.setattr(win32, "set_monitor_brightness", lambda h, value: self.writes.append((h, value)))

    def get_vcp(self, handle, code):
        result = self.vcp.get(handle, win32.ERROR_GRAPHICS_DDCCI_VCP_NOT_SUPPORTED)
        if isinstance(result, int):
            raise DdcError("Reading VCP", result)
        return result

    def get_high_level(self, handle):
        result = self.high_level.get(handle, win32.ERROR_GRAPHICS_DDCCI_VCP_NOT_SUPPORTED)
        if isinstance(result, int):
            raise DdcError("GetMonitorBrightness", result)
        return result


class FakeWmi:
    def __init__(self, panels):
        self._panels = panels
        self.set_calls = []

    def panels(self):
        return list(self._panels)

    def get(self, instance):
        return 50

    def set(self, instance, value):
        self.set_calls.append((instance, value))


def backend_with(wmi=None):
    backend = windows.WindowsBrightnessBackend()
    backend._wmi = wmi
    return backend


def test_names_come_from_the_display_configuration(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "Generic PnP Monitor")], {7: (40, 100)})
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "DELL U2720Q")])
    backend = backend_with()
    [info] = backend.list_monitors()
    assert (info.name, info.supported) == ("DELL U2720Q", True)
    assert backend.get_brightness(0) == 40  # read during detection, not again


def test_brightness_is_scaled_to_the_monitors_range(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x")], {7: (25, 50)})
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "Odd range")])
    backend = backend_with()
    backend.list_monitors()
    assert backend.get_brightness(0) == 50
    backend.set_brightness(0, 100)
    assert ddc.writes == [(7, 50)]


def test_high_level_api_is_the_fallback(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x")], {}, high_level={7: (10, 60, 110)})
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "Picky")])
    backend = backend_with()
    backend.list_monitors()
    assert backend.get_brightness(0) == 50
    backend.set_brightness(0, 0)
    assert ddc.writes == [(7, 10)]


def test_unresponsive_and_indirect_displays_are_listed_as_unsupported(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x"), PhysicalMonitor(r"\\.\DISPLAY2", 8, "y")], {})
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "Old TV"), target(r"\\.\DISPLAY2", "Dock screen", DISPLAYLINK)])
    tv, dock = backend_with().list_monitors()
    assert not tv.supported and tv.reason == ""
    assert not dock.supported and "DisplayLink" in dock.reason
    assert sorted(ddc.destroyed) == [7, 8]  # handles of failed monitors are released


def test_cloned_displays_get_the_handle_that_answers(monkeypatch):
    # A laptop panel duplicated onto an external monitor: one source, two
    # targets, and the panel's physical monitor listed first.
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 1, "panel"), PhysicalMonitor(r"\\.\DISPLAY1", 2, "external")], {2: (30, 100)})
    path = r"\\?\DISPLAY#BOE0812#4&2a6&0&UID26#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "", INTERNAL, path=path), target(r"\\.\DISPLAY1", "LG 27GL850", DP)])
    wmi = FakeWmi([(r"DISPLAY\BOE0812\4&2a6&0&UID26_0", 80, [0, 25, 50, 75, 100])])
    backend = backend_with(wmi)
    panel, external = backend.list_monitors()
    assert (panel.name, panel.supported) == ("Built-in display", True)
    assert (external.name, external.supported) == ("LG 27GL850", True)
    assert backend.get_brightness(0) == 80
    assert backend.get_brightness(1) == 30
    backend.set_brightness(0, 60)
    assert wmi.set_calls == [(r"DISPLAY\BOE0812\4&2a6&0&UID26_0", 50)]  # nearest supported level


def test_keys_come_from_the_edid_and_survive_a_port_change(monkeypatch):
    def dell(uid):  # the same monitor on another port gets another instance id
        return rf"\\?\DISPLAY#DEL41A2#5&1a2b&0&UID{uid}#{{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}}"

    keys = []
    for uid in ("4352", "8448"):
        ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x"), PhysicalMonitor(r"\\.\DISPLAY2", 8, "y")], {7: (40, 100), 8: (40, 100)})
        # Two identical monitors: the second is told apart by order.
        ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "DELL", path=dell(uid)), target(r"\\.\DISPLAY2", "DELL", path=dell("1"))])
        keys.append([m.key for m in backend_with().list_monitors()])
    assert keys == [["DEL41A2", "DEL41A2#2"]] * 2


def test_built_in_panel_is_marked_internal(monkeypatch):
    path = r"\\?\DISPLAY#BOE0812#4&2a6&0&UID26#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
    FakeDdc([], {}).install(monkeypatch, [target(r"\\.\DISPLAY1", "", INTERNAL, path=path)])
    [panel] = backend_with(FakeWmi([(r"DISPLAY\BOE0812\4&2a6&0&UID26_0", 80, [])])).list_monitors()
    assert (panel.key, panel.internal) == ("BOE0812", True)


def test_internal_panel_without_wmi_explains_why(monkeypatch):
    FakeDdc([], {}).install(monkeypatch, [target(r"\\.\DISPLAY1", "", INTERNAL)])
    backend = backend_with(FakeWmi([]))
    [panel] = backend.list_monitors()
    assert not panel.supported and "WMI" in panel.reason


def test_monitors_without_display_configuration_still_work(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "Generic PnP Monitor")], {7: (70, 100)})
    ddc.install(monkeypatch, [])  # QueryDisplayConfig failed
    [info] = backend_with(FakeWmi([])).list_monitors()
    assert (info.name, info.supported) == ("Generic PnP Monitor", True)


def test_unplugged_monitor_raises_monitor_gone(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x")], {7: (40, 100)})
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "Gone soon")])
    backend = backend_with()
    backend.list_monitors()

    def gone(*_args):
        raise DdcError("Setting VCP", win32.ERROR_GRAPHICS_MONITOR_NO_LONGER_EXISTS)

    monkeypatch.setattr(win32, "set_vcp", gone)
    with pytest.raises(MonitorGoneError):
        backend.set_brightness(0, 10)


def test_detecting_again_releases_old_handles(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x")], {7: (40, 100)})
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "A")])
    backend = backend_with()
    backend.list_monitors()
    backend.list_monitors()
    assert ddc.destroyed == [7]


class FakeGdi:
    """Gamma ramps per display. Like Windows' default GdiIcmGammaRange, it
    rejects ramps whose peak drops below `min_peak` of full output."""

    def __init__(self, ramps, min_peak=0.7, ignore=()):
        self.ramps = ramps
        self.min_peak = min_peak
        self.ignore = set(ignore)
        self.sets = 0

    def install(self, monkeypatch, targets):
        monkeypatch.setattr(win32, "display_targets", lambda: targets)
        monkeypatch.setattr(win32, "get_gamma_ramp", lambda name: [list(c) for c in self.ramps[name]])
        monkeypatch.setattr(win32, "set_gamma_ramp", self.set)

    def set(self, name, ramp):
        self.sets += 1
        if min(channel[-1] for channel in ramp) < self.min_peak * 65535:
            return False
        if name not in self.ignore:
            self.ramps[name] = [list(c) for c in ramp]
        return True


def peak_blue(gdi, name=r"\\.\DISPLAY1"):
    return gdi.ramps[name][2][-1] / 65535


def test_night_light_tints_and_reset_restores(monkeypatch):
    gdi = FakeGdi({r"\\.\DISPLAY1": identity_ramp()})
    gdi.install(monkeypatch, [target(r"\\.\DISPLAY1", "A")])
    gamma = windows.WindowsGammaBackend()
    gamma.apply(4500)
    assert peak_blue(gdi) == pytest.approx(whitepoint(4500)[2], abs=1e-4)
    gamma.reset()
    assert gdi.ramps[r"\\.\DISPLAY1"] == identity_ramp()


def test_calibration_is_kept_as_the_base(monkeypatch):
    calibration = [[round(v * 0.95) for v in channel] for channel in identity_ramp()]
    gdi = FakeGdi({r"\\.\DISPLAY1": calibration})
    gdi.install(monkeypatch, [target(r"\\.\DISPLAY1", "Calibrated")])
    gamma = windows.WindowsGammaBackend()
    gamma.apply(5000)
    assert gdi.ramps[r"\\.\DISPLAY1"] == gamma_ramp(whitepoint(5000), base=calibration)
    gamma.reset()
    assert gdi.ramps[r"\\.\DISPLAY1"] == calibration


def test_leftover_tint_is_not_kept_as_the_base(monkeypatch):
    leftover = gamma_ramp(whitepoint(3500))  # e.g. from a crashed session
    gdi = FakeGdi({r"\\.\DISPLAY1": leftover}, min_peak=0.0)
    gdi.install(monkeypatch, [target(r"\\.\DISPLAY1", "A")])
    gamma = windows.WindowsGammaBackend()
    gamma.reset()
    assert gdi.ramps[r"\\.\DISPLAY1"] == identity_ramp()


def test_too_warm_falls_back_to_the_warmest_accepted(monkeypatch):
    gdi = FakeGdi({r"\\.\DISPLAY1": identity_ramp()})
    gdi.install(monkeypatch, [target(r"\\.\DISPLAY1", "A")])
    gamma = windows.WindowsGammaBackend()
    gamma.apply(2000)
    limit = gamma._displays[r"\\.\DISPLAY1"].warmest
    assert whitepoint(limit)[2] >= 0.7 > whitepoint(limit - 100)[2]
    assert peak_blue(gdi) == pytest.approx(whitepoint(limit)[2], abs=1e-4)
    sets = gdi.sets
    gamma.apply(1900)  # now clamped straight away, without searching again
    assert gdi.sets == sets + 1


def test_hdr_displays_are_skipped(monkeypatch):
    gdi = FakeGdi({r"\\.\DISPLAY1": identity_ramp(), r"\\.\DISPLAY2": identity_ramp()})
    gdi.install(monkeypatch, [target(r"\\.\DISPLAY1", "SDR"), target(r"\\.\DISPLAY2", "HDR", hdr=True)])
    windows.WindowsGammaBackend().apply(4500)
    assert peak_blue(gdi) < 1
    assert gdi.ramps[r"\\.\DISPLAY2"] == identity_ramp()


def test_ramp_reset_by_windows_becomes_the_new_base(monkeypatch):
    gdi = FakeGdi({r"\\.\DISPLAY1": identity_ramp()})
    gdi.install(monkeypatch, [target(r"\\.\DISPLAY1", "A")])
    gamma = windows.WindowsGammaBackend()
    gamma.apply(4500)
    # A mode change: Windows puts the calibration back.
    calibration = [[round(v * 0.97) for v in channel] for channel in identity_ramp()]
    gdi.ramps[r"\\.\DISPLAY1"] = calibration
    gamma.displays_changed()
    gamma.apply(4500)
    assert gdi.ramps[r"\\.\DISPLAY1"] == gamma_ramp(whitepoint(4500), base=calibration)


def test_driver_that_ignores_ramps_does_not_cause_rebasing(monkeypatch):
    gdi = FakeGdi({r"\\.\DISPLAY1": identity_ramp()}, ignore={r"\\.\DISPLAY1"})
    gdi.install(monkeypatch, [target(r"\\.\DISPLAY1", "Stubborn")])
    gamma = windows.WindowsGammaBackend()
    gamma.apply(4500)
    gamma.displays_changed()
    gamma.apply(4000)
    assert gamma._displays[r"\\.\DISPLAY1"].base is None
