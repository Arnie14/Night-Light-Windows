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
import time
from dataclasses import dataclass, field, replace

from nightlight.core.colortemp import NEUTRAL_KELVIN, gamma_ramp, identity_ramp, plausible_base, ramps_match, whitepoint
from nightlight.hardware import win32
from nightlight.hardware.brightness import MonitorGoneError, MonitorInfo, nearest_level, to_percent, to_raw, valid_range, with_unique_keys

log = logging.getLogger(__name__)

INDIRECT_REASON = "Connected through a USB (DisplayLink), wireless or virtual display adapter, which can't pass DDC/CI brightness commands"


@dataclass
class _Monitor:
    info: MonitorInfo
    method: str = "none"  # "vcp", "high-level", "wmi" or "none"
    handle: int | None = None  # physical monitor handle, for DDC/CI
    minimum: int = 0
    maximum: int = 100
    wmi_instance: str = ""
    levels: list[int] = field(default_factory=list)
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
    """External monitors over DDC/CI (dxva2) and built-in panels over WMI.

    Physical monitor handles stay open between calls, as Monitorian and Twinkle
    Tray keep them, and are released when monitors are detected again.
    """

    def __init__(self) -> None:
        self._monitors: list[_Monitor] = []
        self._wmi: _Wmi | None = None
        self._wmi_problem = ""

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
            responding = [m for m in map(self._probe, by_display.pop(gdi_name, [])) if m is not None]
            for target in group:
                name = target.friendly_name or ("Built-in display" if target.internal else f"{target.connector} display")
                if target.internal:
                    monitor = self._panel(name, target, panels)
                elif responding:
                    monitor = responding.pop(0)
                    monitor.info = replace(monitor.info, name=name)
                elif target.output_technology in win32.INDIRECT_TECHNOLOGIES:
                    monitor = _Monitor(MonitorInfo(0, name, False, INDIRECT_REASON))
                else:
                    monitor = _Monitor(MonitorInfo(0, name, False))
                monitor.info = replace(monitor.info, key=target.hardware_id, internal=target.internal)
                monitors.append(monitor)
            monitors += responding  # more answering monitors than targets; shouldn't happen

        # Displays the display configuration didn't describe (or it failed).
        for physicals in by_display.values():
            for physical in physicals:
                monitors.append(self._probe(physical) or _Monitor(MonitorInfo(0, physical.description, False)))
        for instance, current, levels in panels:
            info = MonitorInfo(0, "Built-in display", True, key="internal", internal=True)
            monitors.append(_Monitor(info, "wmi", wmi_instance=instance, levels=levels, detected=current))

        for monitor, info in zip(monitors, with_unique_keys([replace(m.info, index=i) for i, m in enumerate(monitors)])):
            monitor.info = info
            log.info("Monitor %d: %s [%s] (%s)", info.index, info.name, info.key, monitor.method if info.supported else "not supported")
        self._monitors = monitors
        return [m.info for m in monitors]

    def get_brightness(self, index: int) -> int | None:
        monitor = self._monitors[index]
        if monitor.detected is not None:
            value, monitor.detected = monitor.detected, None
            return value
        try:
            if monitor.method == "vcp":
                current, maximum = win32.get_vcp(monitor.handle, win32.VCP_LUMINANCE)
                if valid_range(0, current, maximum):
                    monitor.maximum = maximum
                    return to_percent(current, 0, maximum)
            elif monitor.method == "high-level":
                minimum, current, maximum = win32.get_monitor_brightness(monitor.handle)
                if valid_range(minimum, current, maximum):
                    monitor.minimum, monitor.maximum = minimum, maximum
                    return to_percent(current, minimum, maximum)
            elif monitor.method == "wmi":
                return self._get_wmi().get(monitor.wmi_instance)
        except win32.DdcError as exc:
            if exc.monitor_gone:
                raise MonitorGoneError(monitor.info.name) from exc
            log.warning("Failed to read brightness of %s: %s", monitor.info.name, exc)
        except MonitorGoneError:
            raise
        except Exception:
            log.warning("Failed to read brightness of %s", monitor.info.name, exc_info=True)
        return None

    def set_brightness(self, index: int, value: int) -> None:
        monitor = self._monitors[index]
        try:
            if monitor.method == "vcp":
                win32.set_vcp(monitor.handle, win32.VCP_LUMINANCE, to_raw(value, 0, monitor.maximum))
            elif monitor.method == "high-level":
                win32.set_monitor_brightness(monitor.handle, to_raw(value, monitor.minimum, monitor.maximum))
            elif monitor.method == "wmi":
                self._get_wmi().set(monitor.wmi_instance, nearest_level(monitor.levels, value))
            else:
                raise ValueError(f"{monitor.info.name} has no brightness control")
        except win32.DdcError as exc:
            if exc.monitor_gone:
                raise MonitorGoneError(monitor.info.name) from exc
            raise

    def close(self) -> None:
        for monitor in self._monitors:
            if monitor.handle is not None:
                win32.destroy_physical_monitor(monitor.handle)
        self._monitors = []

    def _probe(self, physical: win32.PhysicalMonitor) -> _Monitor | None:
        """A monitor that answers DDC/CI brightness requests, or None (and the handle is released).

        Like Twinkle Tray's "fast" check, this reads VCP 0x10 directly rather
        than requesting the capabilities string first, which is slow and on
        some monitors and drivers hangs or returns garbage.
        """
        name = physical.description or physical.gdi_name
        info = MonitorInfo(0, name, True)
        try:
            current, maximum = win32.get_vcp(physical.handle, win32.VCP_LUMINANCE)
            if valid_range(0, current, maximum):
                return _Monitor(info, "vcp", physical.handle, 0, maximum, detected=to_percent(current, 0, maximum))
            log.info("%s on %s reported brightness %d of %d; ignoring it", name, physical.gdi_name, current, maximum)
        except win32.DdcError as exc:
            log.info("%s on %s: no DDC/CI brightness (%s)", name, physical.gdi_name, exc)
            if exc.monitor_gone:
                win32.destroy_physical_monitor(physical.handle)
                return None
        try:
            minimum, current, maximum = win32.get_monitor_brightness(physical.handle)
            if valid_range(minimum, current, maximum):
                return _Monitor(info, "high-level", physical.handle, minimum, maximum, detected=to_percent(current, minimum, maximum))
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
        return _Monitor(MonitorInfo(0, name, True), "wmi", wmi_instance=instance, levels=levels, detected=current)

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


@dataclass
class _GammaDisplay:
    name: str  # GDI name, "\\.\DISPLAY1"
    hdr: bool = False
    base: win32.GammaRamp | None = None  # the ramp to tint, None for identity
    last_set: win32.GammaRamp | None = None
    warmest: int = 0  # warmest temperature Windows accepted here, once it refused one


class WindowsGammaBackend:
    """Sets each display's gamma ramp with GDI's SetDeviceGammaRamp.

    The display's own ramp (usually its calibration) is kept as the base and
    tinted, rather than replaced with a flat one, and is put back by reset().
    """

    # How long the display list and their HDR state are trusted.
    DISPLAYS_TTL = 10.0

    def __init__(self) -> None:
        self._displays: dict[str, _GammaDisplay] = {}
        self._listed_at: float | None = None
        self._warned: set[tuple[str, str]] = set()

    def displays_changed(self) -> None:
        """Displays were added, removed or reconfigured: look at them again."""
        self._listed_at = None
        for display in self._displays.values():
            display.warmest = 0

    def apply(self, kelvin: int) -> None:
        for display in self._current_displays():
            self._apply(display, kelvin)

    def reset(self) -> None:
        for display in self._current_displays():
            if not display.hdr:
                self._set(display, display.base or identity_ramp(win32.RAMP_SIZE))

    def _apply(self, display: _GammaDisplay, kelvin: int) -> None:
        if display.hdr:
            self._warn_once(
                display.name,
                "hdr",
                "HDR is on for %s. Windows doesn't apply gamma ramps to HDR displays, so night light skips it",
                display.name,
            )
            return
        target = max(kelvin, display.warmest)
        if self._set(display, gamma_ramp(whitepoint(target), win32.RAMP_SIZE, display.base)) or target >= NEUTRAL_KELVIN:
            return
        # Windows rejects ramps far from identity unless the GdiIcmGammaRange
        # registry value is raised; settle for the warmest one it accepts.
        for warmer in range((target // 100 + 1) * 100, NEUTRAL_KELVIN + 1, 100):
            if self._set(display, gamma_ramp(whitepoint(warmer), win32.RAMP_SIZE, display.base)):
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
        self._warn_once(display.name, "rejected", "Windows rejected every gamma ramp for %s", display.name)

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
