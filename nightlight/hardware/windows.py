# SPDX-License-Identifier: GPL-3.0-only
"""Windows backends: DDC/CI and WMI brightness, and gamma-ramp night light.

Discovery follows Monitorian: every active display in Windows' display
configuration is listed once, by its EDID name. A built-in laptop panel is
driven through WMI and other displays through DDC/CI; a display reachable by
neither is listed as unsupported, with the reason when it is known. See
docs/windows-notes.md for the quirks behind the details here.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace

from nightlight.core.colortemp import (
    NEUTRAL_KELVIN,
    SOFTWARE_DIM_MIN,
    dimmed_ramp,
    gamma_ramp,
    identity_ramp,
    plausible_base,
    ramps_match,
    whitepoint,
)
from nightlight.hardware import win32
from nightlight.hardware.brightness import MonitorGoneError, MonitorInfo, nearest_level, to_percent, to_raw, valid_range, with_unique_keys
from nightlight.safe_mode import SKIPPED_REASON, SafeMode

log = logging.getLogger(__name__)

INDIRECT_REASON = "Connected through a USB (DisplayLink), wireless or virtual display adapter, which can't pass DDC/CI brightness commands"
# Why an external display didn't answer, in the order to try things: the
# setting is off by default on many monitors, and adapters, hubs, MST and
# docks often don't pass DDC/CI (Monitorian's and Twinkle Tray's FAQs).
NO_DDC_REASON = (
    "Didn't answer over DDC/CI. Turn DDC/CI on in the display's own menu; if it is on, "
    "try another cable or port without adapters, hubs or docks"
)
TV_HINT = " (most TVs don't support it)"


class _Control:
    """How one display's brightness is read and changed. Like Monitorian's
    monitor item classes (DDC/CI, WMI, HDR, unreachable), each way is its own
    class behind one interface, so the backend never branches on the kind.
    DDC/CI errors pass through as win32.DdcError."""

    kind = "none"  # for the log

    def read(self) -> int | None:
        return None

    def write(self, value: int) -> None:
        raise ValueError("this display has no brightness control")

    def powered_on(self) -> bool | None:
        """Whether the display says it is awake; None if it can't tell."""
        return None

    def close(self) -> None:
        pass


class _VcpControl(_Control):
    """DDC/CI, reading and writing VCP 0x10 directly (Twinkle Tray's "fast" way)."""

    kind = "vcp"

    def __init__(self, handle: int, maximum: int) -> None:
        self.handle = handle
        self.maximum = maximum

    def read(self) -> int | None:
        current, maximum = win32.get_vcp(self.handle, win32.VCP_LUMINANCE)
        if not valid_range(0, current, maximum):
            return None
        self.maximum = maximum
        return to_percent(current, 0, maximum)

    def write(self, value: int) -> None:
        win32.set_vcp(self.handle, win32.VCP_LUMINANCE, to_raw(value, 0, self.maximum))

    def powered_on(self) -> bool | None:
        try:
            mode, _maximum = win32.get_vcp(self.handle, win32.VCP_POWER_MODE)
        except win32.DdcError as exc:
            if exc.monitor_gone:
                raise
            return None  # many monitors don't report it
        return mode == win32.POWER_ON

    def close(self) -> None:
        win32.destroy_physical_monitor(self.handle)


class _HighLevelControl(_VcpControl):
    """DDC/CI through GetMonitorBrightness, for monitors that reject plain VCP 0x10."""

    kind = "high-level"

    def __init__(self, handle: int, minimum: int, maximum: int) -> None:
        super().__init__(handle, maximum)
        self.minimum = minimum

    def read(self) -> int | None:
        minimum, current, maximum = win32.get_monitor_brightness(self.handle)
        if not valid_range(minimum, current, maximum):
            return None
        self.minimum, self.maximum = minimum, maximum
        return to_percent(current, minimum, maximum)

    def write(self, value: int) -> None:
        win32.set_monitor_brightness(self.handle, to_raw(value, self.minimum, self.maximum))


class _WmiControl(_Control):
    """A laptop's built-in panel through WMI."""

    kind = "wmi"

    def __init__(self, wmi: Callable[[], _Wmi], instance: str, levels: list[int]) -> None:
        self._wmi = wmi
        self.instance = instance
        self.levels = levels

    def read(self) -> int | None:
        return self._wmi().get(self.instance)

    def write(self, value: int) -> None:
        self._wmi().set(self.instance, nearest_level(self.levels, value))


class _SdrControl(_Control):
    """Windows' SDR content brightness, for a display with HDR on."""

    kind = "sdr"

    def __init__(self, target: win32.DisplayTarget) -> None:
        self.target = target

    def read(self) -> int | None:
        nits = win32.get_sdr_white_level(self.target)
        return None if nits is None else _sdr_percent(nits)

    def write(self, value: int) -> None:
        nits = win32.SDR_MIN_NITS + value * (win32.SDR_MAX_NITS - win32.SDR_MIN_NITS) / 100
        if not win32.set_sdr_white_level(self.target, nits):
            raise OSError("Windows refused the SDR brightness (is HDR still on?)")


class _SoftwareControl(_Control):
    """Dimming the display's gamma ramp, together with night light."""

    kind = "software"

    def __init__(self, gamma: WindowsGammaBackend, gdi_name: str) -> None:
        self.gamma = gamma
        self.gdi_name = gdi_name

    def read(self) -> int | None:
        return self.gamma.dimming(self.gdi_name)

    def write(self, value: int) -> None:
        self.gamma.set_dimming(self.gdi_name, value)


@dataclass
class _Monitor:
    info: MonitorInfo
    control: _Control = field(default_factory=_Control)
    detected: int | None = None  # brightness read while detecting, handed out once


class _Wmi:
    """WMI's WmiMonitorBrightness, which laptops provide for the built-in panel.

    COM must be initialised on the thread that uses it, and the brightness
    backend runs on the monitor worker thread, so this is created there.
    """

    def __init__(self) -> None:
        import pythoncom
        import wmi

        pythoncom.CoInitialize()
        self._conn = wmi.WMI(namespace="wmi")

    def panels(self) -> list[tuple[str, int, list[int]]]:
        """(instance name, current brightness, supported levels) per panel."""
        return [(item.InstanceName, int(item.CurrentBrightness), sorted(set(item.Level or ()))) for item in self._conn.WmiMonitorBrightness()]

    def get(self, instance: str) -> int:
        for item in self._conn.WmiMonitorBrightness():
            if item.InstanceName == instance:
                return int(item.CurrentBrightness)
        raise MonitorGoneError(instance)

    def set(self, instance: str, value: int) -> None:
        for item in self._conn.WmiMonitorBrightnessMethods():
            if item.InstanceName == instance:
                item.WmiSetBrightness(Brightness=value, Timeout=0)
                return
        raise MonitorGoneError(instance)


class WindowsBrightnessBackend:
    """Each display's brightness by the best means it has, in Monitorian's
    order with Twinkle Tray's software fallback:

    1. DDC/CI (dxva2) for external monitors, WMI for built-in panels: the
       backlight itself.
    2. Windows' SDR content brightness, for displays with HDR on.
    3. Software dimming of the gamma ramp, shared with night light (see
       WindowsGammaBackend), for the rest, such as most TVs. Needs `gamma`,
       and can be turned off.

    Physical monitor handles stay open between calls, as Monitorian and Twinkle
    Tray keep them, and are released when monitors are detected again.
    """

    def __init__(self, gamma: WindowsGammaBackend | None = None) -> None:
        self._monitors: list[_Monitor] = []
        self._wmi: _Wmi | None = None
        self._wmi_problem = ""
        self._gamma = gamma
        self.software_dimming = gamma is not None
        self.safe_mode: SafeMode | None = None  # skips displays whose DDC/CI crashed or hung us

    def retry_skipped(self) -> None:
        if self.safe_mode is not None:
            self.safe_mode.clear()

    def set_software_dimming(self, enabled: bool) -> None:
        """Takes effect at the next detection; turning it off undims at once."""
        self.software_dimming = enabled and self._gamma is not None
        if not self.software_dimming:
            for monitor in self._monitors:
                if isinstance(monitor.control, _SoftwareControl):
                    monitor.control.write(100)

    def list_monitors(self) -> list[MonitorInfo]:
        self.close()
        targets = win32.display_targets()
        by_display: dict[str, list[win32.PhysicalMonitor]] = {}
        for physical in win32.physical_monitors():
            by_display.setdefault(physical.gdi_name, []).append(physical)
        panels = self._wmi_panels() if not targets or any(t.internal for t in targets) else []

        # Cloned displays share one source (GDI name). Windows documents no
        # order between a source's physical monitors and its targets, so the
        # monitors that answer DDC/CI go to its external targets in order.
        groups: dict[str, list[win32.DisplayTarget]] = {}
        for target in targets:
            groups.setdefault(target.gdi_name, []).append(target)

        monitors: list[_Monitor] = []
        for gdi_name, group in groups.items():
            physicals = by_display.pop(gdi_name, [])
            # A display whose DDC/CI crashed or hung the app last time isn't probed.
            skipped = self.safe_mode is not None and self.safe_mode.is_skipped(gdi_name)
            if skipped:
                for physical in physicals:
                    win32.destroy_physical_monitor(physical.handle)
            responding = [] if skipped else [m for m in map(self._probe, physicals) if m is not None]
            for target in group:
                name = target.friendly_name or ("Built-in display" if target.internal else f"{target.connector} display")
                if target.internal:
                    monitor = self._panel(name, target, panels)
                elif responding:
                    monitor = responding.pop(0)
                    monitor.info = replace(monitor.info, name=name)
                elif target.output_technology in win32.INDIRECT_TECHNOLOGIES:
                    monitor = _Monitor(MonitorInfo(0, name, False, INDIRECT_REASON))
                elif skipped:
                    monitor = _Monitor(MonitorInfo(0, name, False, SKIPPED_REASON))
                else:
                    hdmi = target.output_technology == win32.OUTPUT_HDMI
                    monitor = _Monitor(MonitorInfo(0, name, False, NO_DDC_REASON + (TV_HINT if hdmi else "")))
                if not monitor.info.supported and not skipped:
                    monitor = self._fallback(monitor, target)
                monitor.info = replace(monitor.info, key=target.hardware_id, internal=target.internal)
                monitors.append(monitor)
            monitors += responding  # more answering monitors than targets; shouldn't happen

        # Displays the display configuration didn't describe (or it failed).
        for physicals in by_display.values():
            for physical in physicals:
                monitors.append(self._probe(physical) or _Monitor(MonitorInfo(0, physical.description, False)))
        for instance, current, levels in panels:
            info = MonitorInfo(0, "Built-in display", True, key="internal", internal=True, minimum=1)
            monitors.append(_Monitor(info, _WmiControl(self._get_wmi, instance, levels), current))

        for monitor, info in zip(monitors, with_unique_keys([replace(m.info, index=i) for i, m in enumerate(monitors)])):
            monitor.info = info
            log.info("Monitor %d: %s [%s] (%s)", info.index, info.name, info.key, monitor.control.kind if info.supported else "not supported")
        self._monitors = monitors
        return [m.info for m in monitors]

    def _fallback(self, monitor: _Monitor, target: win32.DisplayTarget) -> _Monitor:
        """SDR brightness or software dimming for a display without hardware control."""
        info = monitor.info
        if target.hdr:
            try:
                nits = win32.get_sdr_white_level(target)
            except OSError:
                nits = None
            if nits is not None:
                return _Monitor(replace(info, supported=True, reason="", control="sdr"), _SdrControl(target), _sdr_percent(nits))
        elif self.software_dimming and self._gamma is not None and self._gamma.can_dim(target.gdi_name):
            info = replace(info, supported=True, reason="", control="software", minimum=SOFTWARE_DIM_MIN)
            return _Monitor(info, _SoftwareControl(self._gamma, target.gdi_name))
        return monitor

    def get_brightness(self, index: int) -> int | None:
        monitor = self._monitors[index]
        if monitor.detected is not None:
            value, monitor.detected = monitor.detected, None
            return value
        try:
            with self._gone_if_unplugged(monitor):
                return monitor.control.read()
        except MonitorGoneError:
            raise
        except win32.DdcError as exc:
            log.warning("Failed to read brightness of %s: %s", monitor.info.name, exc)
        except Exception:
            log.warning("Failed to read brightness of %s", monitor.info.name, exc_info=True)
        return None

    def set_brightness(self, index: int, value: int) -> None:
        monitor = self._monitors[index]
        with self._gone_if_unplugged(monitor):
            monitor.control.write(value)

    def is_powered_on(self, index: int) -> bool | None:
        """Whether a DDC/CI monitor says it is on (VCP 0xD6). None when it
        can't tell: no answer, or not DDC/CI (a laptop panel is on while
        Windows runs; SDR and software control don't talk to the display)."""
        monitor = self._monitors[index]
        with self._gone_if_unplugged(monitor):
            return monitor.control.powered_on()

    def close(self) -> None:
        for monitor in self._monitors:
            monitor.control.close()
        self._monitors = []

    @staticmethod
    @contextmanager
    def _gone_if_unplugged(monitor: _Monitor) -> Iterator[None]:
        """Turn DDC/CI's "monitor no longer exists" errors into MonitorGoneError."""
        try:
            yield
        except win32.DdcError as exc:
            if exc.monitor_gone:
                raise MonitorGoneError(monitor.info.name) from exc
            raise

    def _probe(self, physical: win32.PhysicalMonitor) -> _Monitor | None:
        """A monitor that answers DDC/CI brightness requests, or None (and the handle is released).

        Like Twinkle Tray's "fast" check, this reads VCP 0x10 directly rather
        than requesting the capabilities string first, which is slow and on
        some monitors and drivers hangs or returns garbage.
        """
        name = physical.description or physical.gdi_name
        info = MonitorInfo(0, name, True)
        if self.safe_mode is not None:
            with self.safe_mode.risky(physical.gdi_name, f"detecting {name} on {physical.gdi_name}"):
                return self._probe_ddc(physical, name, info)
        return self._probe_ddc(physical, name, info)

    def _probe_ddc(self, physical: win32.PhysicalMonitor, name: str, info: MonitorInfo) -> _Monitor | None:
        try:
            current, maximum = win32.get_vcp(physical.handle, win32.VCP_LUMINANCE)
            if valid_range(0, current, maximum):
                return _Monitor(info, _VcpControl(physical.handle, maximum), to_percent(current, 0, maximum))
            log.info("%s on %s reported brightness %d of %d; ignoring it", name, physical.gdi_name, current, maximum)
        except win32.DdcError as exc:
            log.info("%s on %s: no DDC/CI brightness (%s)", name, physical.gdi_name, exc)
            if exc.monitor_gone:
                win32.destroy_physical_monitor(physical.handle)
                return None
        try:
            minimum, current, maximum = win32.get_monitor_brightness(physical.handle)
            if valid_range(minimum, current, maximum):
                return _Monitor(info, _HighLevelControl(physical.handle, minimum, maximum), to_percent(current, minimum, maximum))
        except win32.DdcError as exc:
            log.debug("%s on %s: GetMonitorBrightness failed too (%s)", name, physical.gdi_name, exc)
        win32.destroy_physical_monitor(physical.handle)
        return None

    def _panel(self, name: str, target: win32.DisplayTarget, panels: list[tuple[str, int, list[int]]]) -> _Monitor:
        """The WMI panel for a built-in display, removing it from `panels`."""
        instance_id = target.instance_id.lower()
        match = next((p for p in panels if instance_id and p[0].lower().startswith(instance_id)), None)
        if match is None and len(panels) == 1:
            match = panels[0]  # a laptop has one panel; the ids may just be formatted differently
        if match is None:
            reason = f"Windows offers no brightness control for this display ({self._wmi_problem or 'no WMI brightness'})"
            return _Monitor(MonitorInfo(0, name, False, reason))
        panels.remove(match)
        instance, current, levels = match
        return _Monitor(MonitorInfo(0, name, True, minimum=1), _WmiControl(self._get_wmi, instance, levels), current)

    def _get_wmi(self) -> _Wmi:
        if self._wmi is None:
            self._wmi = _Wmi()
        return self._wmi

    def _wmi_panels(self) -> list[tuple[str, int, list[int]]]:
        try:
            panels = self._get_wmi().panels()
        except ImportError:
            self._wmi_problem = "the 'wmi' package is not installed"
            log.warning("Built-in display brightness needs the 'wmi' package: pip install wmi")
            return []
        except Exception:
            # Often temporary: WMI is busy at sign-in and right after resume.
            # Detection runs again after those, so just try again then.
            self._wmi, self._wmi_problem = None, "the WMI query failed"
            log.info("Querying WMI brightness failed", exc_info=True)
            return []
        self._wmi_problem = "" if panels else "no WMI brightness"
        return panels


def _sdr_percent(nits: float) -> int:
    """SDR white level to 0-100 across Windows' 80-480 nit slider (as Monitorian)."""
    span = win32.SDR_MAX_NITS - win32.SDR_MIN_NITS
    return max(0, min(100, round((nits - win32.SDR_MIN_NITS) * 100 / span)))


@dataclass
class _GammaDisplay:
    name: str  # GDI name, "\\.\DISPLAY1"
    hdr: bool = False
    base: win32.GammaRamp | None = None  # the ramp to tint, None for identity
    last_set: win32.GammaRamp | None = None
    warmest: int = 0  # warmest temperature Windows accepted here, once it refused one
    dim: int = 100  # software dimming, percent (100: none)
    dim_floor: int = 0  # darkest dimming Windows accepted here, once it refused one
    leftover: bool = False  # found tinted (a crash, f.lux): put neutral back even if we never set it
    kelvin_override: int | None = None  # this display's own temperature (night light paused for an app)


class WindowsGammaBackend:
    """Sets each display's gamma ramp with GDI's SetDeviceGammaRamp.

    The display's own ramp (usually its calibration) is kept as the base and
    tinted, rather than replaced with a flat one, and is put back by reset().

    Night light and software dimming share the one ramp, so, like KWin's
    ColorDevice (brightness x temperature), each display keeps its state
    (base ramp, dimming) and every change composes the whole ramp again:
    base, dimmed, then tinted. Night light is set from the UI thread and
    dimming from the monitor thread, so a lock guards the state.
    """

    # How long the display list and their HDR state are trusted.
    DISPLAYS_TTL = 10.0

    def __init__(self) -> None:
        self._displays: dict[str, _GammaDisplay] = {}
        self._listed_at: float | None = None
        self._warned: set[tuple[str, str]] = set()
        self._kelvin = NEUTRAL_KELVIN
        self._lock = threading.RLock()

    @property
    def dimmed(self) -> bool:
        """Some display is dimmed, so its ramp must be kept even while night light is off."""
        return any(d.dim < 100 for d in self._displays.values())

    def displays_changed(self) -> None:
        """Displays were added, removed or reconfigured: look at them again.
        Windows resets gamma ramps then, so dimmed displays are dimmed again."""
        with self._lock:
            self._listed_at = None
            for display in self._displays.values():
                display.warmest = 0
                display.dim_floor = 0
            for display in self._current_displays():
                if display.dim < 100:
                    self._apply(display)

    def apply(self, kelvin: int) -> None:
        with self._lock:
            self._kelvin = kelvin
            for display in self._current_displays():
                self._apply(display)

    def reset(self) -> None:
        """Night light off: neutral colors, keeping any dimming. Displays the
        app hasn't changed are left alone, so other color tools aren't fought."""
        with self._lock:
            self._kelvin = NEUTRAL_KELVIN
            for display in self._current_displays():
                if display.dim < 100 or display.kelvin_override is not None:
                    self._apply(display)
                elif self._owned(display):
                    base = display.base or identity_ramp(win32.RAMP_SIZE)
                    if display.leftover or not ramps_match(display.last_set, base):
                        self._set(display, base)

    def release(self) -> None:
        """Quitting: give every display back its own ramp, undimmed."""
        with self._lock:
            for display in self._current_displays():
                display.kelvin_override = None
                if self._owned(display):
                    self._set(display, display.base or identity_ramp(win32.RAMP_SIZE))

    @staticmethod
    def _owned(display: _GammaDisplay) -> bool:
        """Whether resetting this display's ramp is ours to do."""
        return not display.hdr and (display.last_set is not None or display.leftover)

    def set_override(self, gdi_name: str, kelvin: int | None) -> None:
        """Give one display its own temperature (None: follow night light), used
        to pause night light on the display showing an exempt app."""
        with self._lock:
            display = next((d for d in self._current_displays() if d.name == gdi_name), None)
            if display is None or display.kelvin_override == kelvin:
                return
            display.kelvin_override = kelvin
            self._apply(display)

    def verify(self) -> bool:
        """Put our ramps back where something reset them: Windows does on the
        lock screen, after sleep and on display changes, often before any event
        arrives. Cheap (one GetDeviceGammaRamp per display), so it can run every
        second or two, and right after those events. True if any was re-applied."""
        reapplied = False
        with self._lock:
            for display in self._current_displays():
                if display.hdr or display.last_set is None:
                    continue
                try:
                    current = win32.get_gamma_ramp(display.name)
                except OSError:
                    continue
                if current is None or ramps_match(current, display.last_set):
                    continue
                log.debug("The gamma ramp of %s was reset; applying it again", display.name)
                self._sync_base(display)
                active = self._kelvin < NEUTRAL_KELVIN or display.dim < 100 or display.kelvin_override is not None
                if active:
                    self._apply(display)
                elif display.leftover:
                    self._set(display, display.base or identity_ramp(win32.RAMP_SIZE))
                reapplied = reapplied or active
        return reapplied

    def can_dim(self, gdi_name: str) -> bool:
        """Whether software dimming can work here: an SDR display whose ramp can be read."""
        with self._lock:
            display = next((d for d in self._current_displays() if d.name == gdi_name), None)
            if display is None or display.hdr:
                return False
            try:
                return win32.get_gamma_ramp(gdi_name) is not None
            except OSError:
                return False

    def dimming(self, gdi_name: str) -> int:
        with self._lock:
            display = self._displays.get(gdi_name)
            return display.dim if display else 100

    def set_dimming(self, gdi_name: str, percent: int) -> int:
        """Dim a display to `percent`; returns the level it ended up at, which
        is higher if Windows refused darker ramps."""
        with self._lock:
            display = next((d for d in self._current_displays() if d.name == gdi_name), None)
            if display is None:
                return 100
            display.dim = max(SOFTWARE_DIM_MIN, min(100, percent)) if percent < 100 else 100
            self._apply(display)
            return display.dim

    def _apply(self, display: _GammaDisplay) -> None:
        if display.hdr:
            self._warn_once(
                display.name,
                "hdr",
                "HDR is on for %s. Windows doesn't apply gamma ramps to HDR displays, so night light skips it",
                display.name,
            )
            return
        own = display.kelvin_override if display.kelvin_override is not None else self._kelvin
        kelvin = max(own, display.warmest)
        dim = max(display.dim, display.dim_floor)
        if self._try(display, kelvin, dim) or (kelvin >= NEUTRAL_KELVIN and dim >= 100):
            return
        # Windows rejects ramps far from identity unless the GdiIcmGammaRange
        # registry value is raised: keep the dimming and settle for the
        # warmest tint it accepts, then, if needed, dim less.
        for warmer in range((kelvin // 100 + 1) * 100, NEUTRAL_KELVIN + 1, 100):
            if self._try(display, warmer, dim):
                display.warmest = warmer
                self._warn_once(
                    display.name,
                    "limit",
                    "Windows refused gamma ramps warmer than %d K for %s, so night light stops there. "
                    "Raising GdiIcmGammaRange in the registry lifts the limit; see docs/windows-notes.md",
                    warmer,
                    display.name,
                )
                return
        for lighter in range(dim + 5, 101, 5):
            if self._try(display, kelvin, lighter):
                display.dim_floor = lighter
                self._warn_once(display.name, "dim", "Windows refused to dim %s below %d%%", display.name, lighter)
                return
        self._warn_once(display.name, "rejected", "Windows rejected every gamma ramp for %s", display.name)

    def _try(self, display: _GammaDisplay, kelvin: int, dim: int) -> bool:
        base = display.base if dim >= 100 else dimmed_ramp(dim, win32.RAMP_SIZE, display.base)
        return self._set(display, gamma_ramp(whitepoint(kelvin), win32.RAMP_SIZE, base))

    def _set(self, display: _GammaDisplay, ramp: win32.GammaRamp) -> bool:
        try:
            if not win32.set_gamma_ramp(display.name, ramp):
                return False
            applied = win32.get_gamma_ramp(display.name)
        except OSError as exc:
            self._warn_once(display.name, "dc", "Could not change the gamma ramp of %s: %s", display.name, exc)
            return False
        if applied is not None and not ramps_match(ramp, applied):
            # Some drivers report success without applying the ramp
            # (Twinkle Tray checks for this too).
            self._warn_once(
                display.name,
                "ignored",
                "%s accepted the gamma ramp but shows a different one; its driver or another program may control its colors",
                display.name,
            )
            ramp = applied
        display.last_set = ramp
        display.leftover = False
        return True

    def _current_displays(self) -> list[_GammaDisplay]:
        now = time.monotonic()
        if self._listed_at is not None and now - self._listed_at < self.DISPLAYS_TTL:
            return list(self._displays.values())
        self._listed_at = now
        sources: dict[str, bool] = {}  # GDI name -> HDR
        if targets := win32.display_targets():
            for target in targets:
                sources[target.gdi_name] = sources.get(target.gdi_name, False) or target.hdr
        else:
            sources = dict.fromkeys(win32.attached_display_names(), False)
        displays = {}
        for name, hdr in sources.items():
            display = self._displays.get(name) or _GammaDisplay(name)
            display.hdr = hdr
            self._sync_base(display)
            displays[name] = display
        self._displays = displays
        return list(displays.values())

    def _sync_base(self, display: _GammaDisplay) -> None:
        """Re-read the display's base ramp if something other than us set its ramp."""
        try:
            current = win32.get_gamma_ramp(display.name)
        except OSError:
            return
        if current is None:
            return
        expected = display.last_set if display.last_set is not None else (display.base or identity_ramp(win32.RAMP_SIZE))
        if ramps_match(current, expected):
            return
        if display.last_set is not None:
            # Windows resets ramps on display changes and when it reloads a
            # calibration; another color tool may have changed it too.
            log.info("The gamma ramp of %s was changed by something else; using it as the new base", display.name)
        display.last_set = None
        if not plausible_base(current):
            display.base = None
            display.leftover = True
            self._warn_once(
                display.name,
                "tinted",
                "%s already had a tinted gamma ramp (left over from a crash, or another app such as f.lux); starting from a neutral one",
                display.name,
            )
        elif ramps_match(current, identity_ramp(win32.RAMP_SIZE)):
            display.base = None
        else:
            display.base = current
            log.info("Keeping the existing gamma ramp of %s (its calibration) as the base for night light", display.name)

    def _warn_once(self, display: str, kind: str, message: str, *args) -> None:
        if (display, kind) not in self._warned:
            self._warned.add((display, kind))
            log.warning(message, *args)
