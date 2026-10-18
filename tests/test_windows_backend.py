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
    assert not tv.supported and "Turn DDC/CI on" in tv.reason and "TVs" in tv.reason  # HDMI: maybe a TV
    assert not dock.supported and "DisplayLink" in dock.reason
    assert sorted(ddc.destroyed) == [7, 8]  # handles of failed monitors are released


def test_displayport_monitor_without_ddc_gets_no_tv_hint(monkeypatch):
    FakeDdc([PhysicalMonitor(r"\\.\DISPLAY1", 7, "x")], {}).install(monkeypatch, [target(r"\\.\DISPLAY1", "Office monitor", DP)])
    [monitor] = backend_with().list_monitors()
    assert "Turn DDC/CI on" in monitor.reason and "TVs" not in monitor.reason


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


def test_verify_puts_back_a_ramp_windows_reset(monkeypatch):
    gdi = FakeGdi({D1: identity_ramp()}, min_peak=0.0)
    gdi.install(monkeypatch, [target(D1, "A")])
    gamma = windows.WindowsGammaBackend()
    gamma.apply(4500)
    tinted = gdi.ramps[D1]
    assert gamma.verify() is False  # nothing changed: nothing to do
    gdi.ramps[D1] = identity_ramp()  # the lock screen reset it
    assert gamma.verify() is True
    assert gdi.ramps[D1] == tinted


def test_verify_leaves_untouched_displays_alone(monkeypatch):
    gdi = FakeGdi({D1: identity_ramp()}, min_peak=0.0)
    gdi.install(monkeypatch, [target(D1, "A")])
    gamma = windows.WindowsGammaBackend()
    gdi.ramps[D1] = gamma_ramp(whitepoint(5000))  # Windows Night Light, while ours never ran
    assert gamma.verify() is False and gdi.sets == 0


def test_a_display_can_pause_night_light_on_its_own(monkeypatch):
    d2 = r"\\.\DISPLAY2"
    gdi = FakeGdi({D1: identity_ramp(), d2: identity_ramp()}, min_peak=0.0)
    gdi.install(monkeypatch, [target(D1, "A"), target(d2, "B")])
    gamma = windows.WindowsGammaBackend()
    gamma.apply(4000)
    gamma.set_override(d2, 6500)  # a fullscreen game on display 2
    assert peak_blue(gdi, D1) < 0.8 and peak_blue(gdi, d2) == 1.0
    gamma.apply(3800)  # night light keeps changing on the other display only
    assert peak_blue(gdi, d2) == 1.0
    gamma.set_override(d2, None)
    assert peak_blue(gdi, d2) == pytest.approx(peak_blue(gdi, D1))
