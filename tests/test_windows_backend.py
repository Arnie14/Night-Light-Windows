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
    # A few words under the slider, the advice in the detail. HDMI: maybe a TV.
    assert not tv.supported and tv.reason.startswith("DDC/CI didn't answer") and "TVs" in tv.reason
    assert "Turn DDC/CI on" in tv.detail and tv.status == "unsupported"
    assert not dock.supported and "USB" in dock.reason and "DisplayLink" in dock.detail
    assert sorted(ddc.destroyed) == [7, 8]  # handles of failed monitors are released


def test_displayport_monitor_without_ddc_gets_no_tv_hint(monkeypatch):
    FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x")], {}).install(monkeypatch, [target(r"\\.\DISPLAY1", "Office monitor", DP)])
    [monitor] = backend_with().list_monitors()
    assert "Turn DDC/CI on" in monitor.detail and "TVs" not in monitor.reason + monitor.detail


def test_power_state_comes_from_vcp_d6(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x"), PhysicalMonitor(r"\\.\DISPLAY2", 8, "y"), PhysicalMonitor(r"\\.\DISPLAY3", 9, "z")],
                  {7: (40, 100), 8: (40, 100), 9: (40, 100)})
    power = {7: (1, 5), 8: (4, 5)}  # on, off; 9 doesn't report it
    get_brightness = ddc.get_vcp

    def get_vcp(handle, code):
        if code == win32.VCP_POWER_MODE:
            result = power.get(handle, win32.ERROR_GRAPHICS_DDCCI_VCP_NOT_SUPPORTED)
            if isinstance(result, int):
                raise DdcError("Reading VCP", result)
            return result
        return get_brightness(handle, code)

    ddc.get_vcp = get_vcp
    ddc.install(monkeypatch, [target(rf"\\.\DISPLAY{i}", f"M{i}", DP) for i in (1, 2, 3)])
    backend = backend_with()
    backend.list_monitors()
    assert [backend.is_powered_on(i) for i in range(3)] == [True, False, None]


def test_laptop_panel_power_state_is_unknown(monkeypatch):
    FakeDdc([], {}).install(monkeypatch, [target(r"\\.\DISPLAY1", "", INTERNAL)])
    backend = backend_with(FakeWmi([("panel", 80, [])]))
    backend.list_monitors()
    assert backend.is_powered_on(0) is None


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
    assert (panel.key, panel.internal, panel.minimum) == ("BOE0812", True, 1)


def test_built_in_panel_is_not_asked_over_ddc(monkeypatch):
    path = r"\\?\DISPLAY#BOE0812#4&2a6&0&UID26#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 0, "Generic PnP Monitor")], {})
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "", INTERNAL, path=path)])
    asked = []
    monkeypatch.setattr(win32, "get_vcp", lambda handle, code: asked.append(handle))
    [panel] = backend_with(FakeWmi([(r"DISPLAY\BOE0812\4&2a6&0&UID26_0", 80, [])])).list_monitors()
    assert panel.supported and asked == [] and ddc.destroyed == [0]


def test_internal_panel_without_wmi_explains_why(monkeypatch):
    FakeDdc([], {}).install(monkeypatch, [target(r"\\.\DISPLAY1", "", INTERNAL)])
    backend = backend_with(FakeWmi([]))
    [panel] = backend.list_monitors()
    assert not panel.supported and panel.reason == windows.REASON_PANEL and "WMI" in panel.detail


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


# --- Software dimming and SDR brightness ------------------------------------

D1 = r"\\.\DISPLAY1"


def mid(gdi, name=D1, channel=0):
    return gdi.ramps[name][channel][128] / 65535


def test_dimming_and_night_light_share_one_ramp(monkeypatch):
    gdi = FakeGdi({D1: identity_ramp()}, min_peak=0.0)
    gdi.install(monkeypatch, [target(D1, "TV")])
    gamma = windows.WindowsGammaBackend()
    gamma.set_dimming(D1, 50)
    dimmed_red, dimmed_blue = mid(gdi), mid(gdi, channel=2)
    assert dimmed_red < 0.45  # darker than the identity's 0.5
    gamma.apply(4500)  # night light on top keeps the dimming
    assert mid(gdi) == pytest.approx(dimmed_red, abs=0.01)
    assert mid(gdi, channel=2) == pytest.approx(dimmed_blue * whitepoint(4500)[2], abs=0.01)


def test_night_light_off_keeps_dimming_and_quitting_undims(monkeypatch):
    gdi = FakeGdi({D1: identity_ramp()}, min_peak=0.0)
    gdi.install(monkeypatch, [target(D1, "TV")])
    gamma = windows.WindowsGammaBackend()
    gamma.set_dimming(D1, 50)
    gamma.apply(4500)
    gamma.reset()
    assert gamma.dimmed and mid(gdi) < 0.45
    assert gdi.ramps[D1][0] == gdi.ramps[D1][2]  # neutral again: no tint
    gamma.release()
    assert gdi.ramps[D1] == identity_ramp()


def test_dimming_comes_back_after_windows_resets_the_ramp(monkeypatch):
    gdi = FakeGdi({D1: identity_ramp()}, min_peak=0.0)
    gdi.install(monkeypatch, [target(D1, "TV")])
    gamma = windows.WindowsGammaBackend()
    gamma.set_dimming(D1, 50)
    gdi.ramps[D1] = identity_ramp()  # a display change resets it
    gamma.displays_changed()
    assert mid(gdi) < 0.45


def test_reset_leaves_displays_it_never_changed_alone(monkeypatch):
    gdi = FakeGdi({D1: identity_ramp()}, min_peak=0.0)
    gdi.install(monkeypatch, [target(D1, "A")])
    gamma = windows.WindowsGammaBackend()
    gamma.reset()
    assert gdi.sets == 0  # e.g. Windows Night Light keeps working while ours is off


def test_dimming_that_windows_refuses_settles_at_the_darkest_accepted(monkeypatch):
    gdi = FakeGdi({D1: identity_ramp()}, min_peak=0.9)  # stricter than the driver-safe 0.7 peak
    gdi.install(monkeypatch, [target(D1, "TV")])
    gamma = windows.WindowsGammaBackend()
    gamma.set_dimming(D1, 20)
    assert gdi.ramps[D1][0][-1] >= 0.9 * 65535
    assert gdi.ramps[D1] != identity_ramp()  # still dimmed, as far as Windows allows


def test_warmest_tint_is_found_again_when_dimming_changes(monkeypatch):
    # Dimming leaves less room for tinting before Windows refuses the ramp.
    gdi = FakeGdi({D1: identity_ramp()}, min_peak=0.6)
    gdi.install(monkeypatch, [target(D1, "TV")])
    gamma = windows.WindowsGammaBackend()
    gamma.apply(2000)
    undimmed = gamma._displays[D1].warmest
    gamma.set_dimming(D1, 20)
    assert gamma._displays[D1].warmest > undimmed
    gamma.set_dimming(D1, 100)
    gamma.apply(2000)
    assert gamma._displays[D1].warmest == undimmed


def test_hdr_displays_cannot_be_dimmed_in_software(monkeypatch):
    FakeGdi({D1: identity_ramp()}).install(monkeypatch, [target(D1, "HDR", hdr=True)])
    assert not windows.WindowsGammaBackend().can_dim(D1)


def install_sdr(monkeypatch, nits=240.0, accept=True):
    state = {"nits": nits, "sets": []}
    monkeypatch.setattr(win32, "get_sdr_white_level", lambda t: state["nits"])

    def set_level(t, value):
        state["sets"].append(value)
        if accept:
            state["nits"] = value
        return accept

    monkeypatch.setattr(win32, "set_sdr_white_level", set_level)
    return state


def test_hdr_display_without_ddc_gets_sdr_brightness(monkeypatch):
    FakeDdc([PhysicalMonitor(D1, 7, "x")], {}).install(monkeypatch, [target(D1, "HDR TV", hdr=True)])
    state = install_sdr(monkeypatch, nits=280)
    backend = backend_with()
    [info] = backend.list_monitors()
    assert (info.supported, info.control, info.reason) == (True, "sdr", "")
    assert backend.get_brightness(0) == 50  # 280 nits is halfway along 80-480
    backend.set_brightness(0, 100)
    assert state["sets"] == [480]


def test_refused_sdr_brightness_is_an_error(monkeypatch):
    FakeDdc([PhysicalMonitor(D1, 7, "x")], {}).install(monkeypatch, [target(D1, "HDR TV", hdr=True)])
    install_sdr(monkeypatch, accept=False)
    backend = backend_with()
    backend.list_monitors()
    with pytest.raises(OSError):
        backend.set_brightness(0, 30)


def test_ddc_wins_over_sdr_on_an_hdr_monitor(monkeypatch):
    FakeDdc([PhysicalMonitor(D1, 7, "x")], {7: (40, 100)}).install(monkeypatch, [target(D1, "HDR monitor", hdr=True)])
    install_sdr(monkeypatch)
    [info] = backend_with().list_monitors()
    assert info.control == "hardware"


def software_backend(monkeypatch, targets, ramps=None):
    gdi = FakeGdi(ramps or {t.gdi_name: identity_ramp() for t in targets}, min_peak=0.0)
    FakeDdc([PhysicalMonitor(t.gdi_name, i, "x") for i, t in enumerate(targets)], {}).install(monkeypatch, targets)
    monkeypatch.setattr(win32, "get_gamma_ramp", lambda name: [list(c) for c in gdi.ramps[name]])
    monkeypatch.setattr(win32, "set_gamma_ramp", gdi.set)
    gamma = windows.WindowsGammaBackend()
    backend = windows.WindowsBrightnessBackend(gamma)
    backend._wmi = FakeWmi([])
    return backend, gamma, gdi


def test_display_without_ddc_gets_software_dimming(monkeypatch):
    backend, gamma, gdi = software_backend(monkeypatch, [target(D1, "TV")])
    [info] = backend.list_monitors()
    assert (info.supported, info.control, info.minimum) == (True, "software", 20)
    assert backend.get_brightness(0) == 100
    backend.set_brightness(0, 40)
    assert gamma.dimming(D1) == 40 and mid(gdi) < 0.4
    assert backend.get_brightness(0) == 40
    assert backend.is_powered_on(0) is None


def test_software_dimming_turned_off(monkeypatch):
    backend, gamma, gdi = software_backend(monkeypatch, [target(D1, "TV")])
    backend.list_monitors()
    backend.set_brightness(0, 40)
    backend.set_software_dimming(False)
    assert gamma.dimming(D1) == 100 and gdi.ramps[D1] == identity_ramp()  # undimmed at once
    [info] = backend.list_monitors()
    assert not info.supported and "DDC/CI" in info.reason


def test_without_a_gamma_backend_there_is_no_software_dimming(monkeypatch):
    FakeDdc([PhysicalMonitor(D1, 7, "x")], {}).install(monkeypatch, [target(D1, "TV")])
    [info] = backend_with().list_monitors()
    assert not info.supported


# --- Identity, hangs, isolation, recovery --------------------------------------

import threading  # noqa: E402
import time  # noqa: E402

from nightlight.core import edid as edid_module  # noqa: E402


def with_edid(gdi, name, serial, tech=DP, product=0x41A2):
    data = edid_module.parse(edid_module.build("DEL", product, 0, name, serial))
    path = rf"\\?\DISPLAY#DEL{product:04X}#5&{serial or 'x'}&0&UID1#{{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}}"
    return DisplayTarget(gdi, name, path, tech, False, edid=data)


def test_identity_comes_from_the_edid_and_tells_twins_apart(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x"), PhysicalMonitor(r"\\.\DISPLAY2", 8, "y")], {7: (40, 100), 8: (60, 100)})
    ddc.install(monkeypatch, [with_edid(r"\\.\DISPLAY1", "DELL U2720Q", "AAA111"), with_edid(r"\\.\DISPLAY2", "DELL U2720Q", "BBB222")])
    left, right = backend_with().list_monitors()
    assert (left.key, right.key) == ("DEL41A2:AAA111", "DEL41A2:BBB222")  # not "#2" by order
    assert left.details.serial == "AAA111" and left.details.connector == "DisplayPort" and left.channel == "ddc"


class HangingDdc(FakeDdc):
    """Like FakeDdc, but reads from the given handles block until released."""

    def __init__(self, monitors, vcp, hang):
        super().__init__(monitors, vcp)
        self.hang = set(hang)
        self.release = threading.Event()
        self.reads = []

    def get_vcp(self, handle, code):
        self.reads.append(handle)
        if handle in self.hang:
            self.release.wait(5)
        return super().get_vcp(handle, code)


def test_a_hanging_monitor_is_left_alone_and_the_others_still_work(monkeypatch):
    monkeypatch.setattr(windows, "PROBE_TIMEOUT_S", 0.2)
    targets = [with_edid(r"\\.\DISPLAY1", "Hangs", "H1"), with_edid(r"\\.\DISPLAY2", "Fine", "F1", product=0x5000)]
    ddc = HangingDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x"), PhysicalMonitor(r"\\.\DISPLAY2", 8, "y")], {7: (40, 100), 8: (60, 100)}, hang={7})
    ddc.install(monkeypatch, targets)
    backend = backend_with()
    started = time.monotonic()
    hung, fine = backend.list_monitors()
    assert time.monotonic() - started < 1.0  # bounded by the probe deadline, probes side by side
    assert not hung.supported and hung.status == "not responding" and hung.reason == windows.REASON_NOT_RESPONDING
    assert fine.supported and backend.get_brightness(1) == 60
    assert 7 not in ddc.destroyed  # its probe still uses the handle
    # Detecting again doesn't ask it again while its call is stuck.
    ddc.monitors = [PhysicalMonitor(r"\\.\DISPLAY1", 17, "x"), PhysicalMonitor(r"\\.\DISPLAY2", 18, "y")]
    ddc.vcp = {17: (40, 100), 18: (60, 100)}
    reads = len(ddc.reads)
    hung, fine = backend.list_monitors()
    assert 17 not in ddc.reads[reads:] and not hung.supported and fine.supported
    assert 17 in ddc.destroyed  # its fresh handle was released unused
    # Once the stuck call returns, its handle is released and it is checked again.
    ddc.release.set()
    for _ in range(100):
        if 7 in ddc.destroyed and not backend.stuck:
            break
        time.sleep(0.01)
    assert 7 in ddc.destroyed and backend.stuck == {}
    ddc.hang.clear()
    ddc.monitors = [PhysicalMonitor(r"\\.\DISPLAY1", 27, "x"), PhysicalMonitor(r"\\.\DISPLAY2", 28, "y")]
    ddc.vcp = {27: (40, 100), 28: (60, 100)}
    hung, fine = backend.list_monitors()
    assert hung.supported


def test_a_monitor_that_answered_before_is_retrying_not_unsupported(monkeypatch):
    # After waking, monitors often don't answer for a while (Monitorian #50):
    # "turn DDC/CI on" would be the wrong advice.
    target_ = with_edid(r"\\.\DISPLAY1", "DELL", "S1")
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x")], {7: (40, 100)})
    ddc.install(monkeypatch, [target_])
    backend = backend_with()
    assert backend.list_monitors()[0].supported
    ddc.monitors, ddc.vcp = [PhysicalMonitor(r"\\.\DISPLAY1", 8, "x")], {8: win32.ERROR_GRAPHICS_I2C_ERROR_RECEIVING_DATA}
    [info] = backend.list_monitors()
    assert not info.supported and info.status == "retrying" and "Turn DDC/CI on" not in info.detail


def test_a_monitor_whose_signal_never_gets_through_is_not_asked_twice(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x")], {7: win32.ERROR_GRAPHICS_I2C_ERROR_TRANSMITTING_DATA},
                  high_level={7: (0, 50, 100)})
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "Behind a dock")])
    [info] = backend_with().list_monitors()
    assert not info.supported  # the high-level API wasn't tried: nothing gets through
    assert ddc.destroyed == [7]


def test_a_failure_detecting_one_display_does_not_affect_the_others(monkeypatch):
    panel_path = r"\\?\DISPLAY#BOE0812#4&2a6&0&UID26#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY2", 8, "y")], {8: (60, 100)})
    ddc.install(monkeypatch, [target(r"\\.\DISPLAY1", "", INTERNAL, path=panel_path), target(r"\\.\DISPLAY2", "External")])
    backend = backend_with(FakeWmi([]))

    def broken(*_args):
        raise RuntimeError("driver bug")

    monkeypatch.setattr(backend, "_panel", broken)
    panel, external = backend.list_monitors()
    assert not panel.supported and panel.reason == windows.REASON_CHECK_FAILED
    assert external.supported


def test_a_handle_in_use_is_released_only_when_its_call_returns():
    release, destroyed = threading.Event(), []

    class Api:
        def set_vcp(self, handle, code, value):
            release.wait(5)

        def destroy_physical_monitor(self, handle):
            destroyed.append(handle)

    control = windows._VcpControl(Api(), 7, 100)
    writer = threading.Thread(target=control.write, args=(50,))
    writer.start()
    time.sleep(0.05)
    assert control.busy_for is not None
    control.close()
    assert destroyed == []  # still in use: destroying it now could crash
    release.set()
    writer.join(2)
    assert destroyed == [7]
    with pytest.raises(MonitorGoneError):
        control.read()  # a closed control refuses calls: its display was re-detected


def test_detecting_again_while_a_call_is_stuck_keeps_away_from_that_display(monkeypatch):
    ddc = FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x")], {7: (40, 100)})
    ddc.install(monkeypatch, [with_edid(r"\\.\DISPLAY1", "DELL", "S1")])
    release = threading.Event()
    monkeypatch.setattr(win32, "set_vcp", lambda h, code, value: release.wait(5))
    backend = backend_with()
    backend.list_monitors()
    writer = threading.Thread(target=backend.set_brightness, args=(0, 50))
    writer.start()
    time.sleep(0.05)
    ddc.monitors = [PhysicalMonitor(r"\\.\DISPLAY1", 8, "x")]
    [info] = backend.list_monitors()  # e.g. after a display change
    assert not info.supported and info.status == "not responding"
    assert 7 not in ddc.destroyed and 8 in ddc.destroyed
    release.set()
    writer.join(2)
    assert 7 in ddc.destroyed and backend.stuck == {}


def test_switching_hdr_on_moves_a_display_from_dimming_to_sdr(monkeypatch):
    backend, gamma, gdi = software_backend(monkeypatch, [target(D1, "TV")])
    assert backend.list_monitors()[0].control == "software"
    targets = [target(D1, "TV", hdr=True)]
    monkeypatch.setattr(win32, "display_targets", lambda: targets)
    install_sdr(monkeypatch, nits=160)
    gamma.displays_changed()
    [info] = backend.list_monitors()
    assert (info.control, info.channel) == ("sdr", "sdr") and backend.get_brightness(0) == 20


def test_dimming_comes_back_when_hdr_is_switched_off(monkeypatch):
    gdi = FakeGdi({D1: identity_ramp()}, min_peak=0.0)
    targets = [target(D1, "TV")]
    gdi.install(monkeypatch, targets)
    monkeypatch.setattr(win32, "display_targets", lambda: targets)
    gamma = windows.WindowsGammaBackend()
    gamma.set_dimming(D1, 50)
    targets[0] = target(D1, "TV", hdr=True)
    gamma.displays_changed()
    gdi.ramps[D1] = identity_ramp()  # Windows reset it meanwhile
    targets[0] = target(D1, "TV")
    gamma.displays_changed()
    assert mid(gdi) < 0.45


def test_dimming_a_display_that_vanished_reports_it_gone(monkeypatch):
    FakeGdi({D1: identity_ramp()}).install(monkeypatch, [target(D1, "TV")])
    gamma = windows.WindowsGammaBackend()
    with pytest.raises(MonitorGoneError):
        gamma.set_dimming(r"\\.\DISPLAY9", 50)


def test_one_display_rejecting_its_ramp_does_not_stop_the_others(monkeypatch):
    gdi = FakeGdi({D1: identity_ramp(), r"\\.\DISPLAY2": identity_ramp()})
    gdi.install(monkeypatch, [target(D1, "A"), target(r"\\.\DISPLAY2", "B")])
    original = gdi.set

    def flaky(name, ramp):
        if name == D1:
            raise RuntimeError("driver bug")
        return original(name, ramp)

    monkeypatch.setattr(win32, "set_gamma_ramp", flaky)
    windows.WindowsGammaBackend().apply(4500)
    assert peak_blue(gdi, r"\\.\DISPLAY2") < 1


CALIBRATION = [[round(v * 0.95) for v in channel] for channel in identity_ramp()]


def test_after_a_crash_the_calibration_is_recovered(monkeypatch, tmp_path):
    gdi = FakeGdi({D1: [list(c) for c in CALIBRATION]})
    gdi.install(monkeypatch, [with_edid(D1, "Calibrated", "C1")])
    journal = tmp_path / "gamma.json"
    crashed = windows.WindowsGammaBackend(journal=windows.GammaJournal(journal))
    crashed.apply(4500)
    del crashed  # killed: no release, the tint stays on the display
    assert gdi.ramps[D1] != CALIBRATION
    restarted = windows.WindowsGammaBackend(journal=windows.GammaJournal(journal))
    restarted.reset()
    assert gdi.ramps[D1] == CALIBRATION  # not a flat ramp: the calibration came back
    restarted.apply(5000)
    assert gdi.ramps[D1] == gamma_ramp(whitepoint(5000), base=CALIBRATION)


def test_a_crash_mid_fade_is_recognised_too(monkeypatch, tmp_path):
    gdi = FakeGdi({D1: [list(c) for c in CALIBRATION]})
    gdi.install(monkeypatch, [with_edid(D1, "Calibrated", "C1")])
    crashed = windows.WindowsGammaBackend(journal=windows.GammaJournal(tmp_path / "gamma.json"))
    crashed.apply(5000)  # saved at once (clean -> tinted)
    crashed.apply(4800)  # within the write interval: not saved yet
    restarted = windows.WindowsGammaBackend(journal=windows.GammaJournal(tmp_path / "gamma.json"))
    restarted.reset()
    assert gdi.ramps[D1] == CALIBRATION


def test_a_stale_journal_entry_is_dropped(monkeypatch, tmp_path):
    gdi = FakeGdi({D1: [list(c) for c in CALIBRATION]})
    gdi.install(monkeypatch, [with_edid(D1, "Calibrated", "C1")])
    path = tmp_path / "gamma.json"
    windows.WindowsGammaBackend(journal=windows.GammaJournal(path)).apply(4500)  # then a crash...
    gdi.ramps[D1] = [list(c) for c in CALIBRATION]  # ...and Windows reloaded the calibration at sign-in
    restarted = windows.WindowsGammaBackend(journal=windows.GammaJournal(path))
    restarted.apply(5000)
    assert gdi.ramps[D1] == gamma_ramp(whitepoint(5000), base=CALIBRATION)
    assert windows.GammaJournal(path).entry("DEL41A2:C1") is not None  # tinted again now
    restarted.release()
    assert windows.GammaJournal(path).entry("DEL41A2:C1") is None  # clean exit: nothing to recover


def test_a_tint_from_another_app_is_not_mistaken_for_ours(monkeypatch, tmp_path):
    flux = gamma_ramp(whitepoint(3400))
    gdi = FakeGdi({D1: flux}, min_peak=0.0)
    gdi.install(monkeypatch, [with_edid(D1, "Calibrated", "C1")])
    gamma = windows.WindowsGammaBackend(journal=windows.GammaJournal(tmp_path / "gamma.json"))  # no entry
    gamma.reset()
    assert gdi.ramps[D1] == identity_ramp()  # neutral, as before
