# SPDX-License-Identifier: GPL-3.0-only
"""Windows backends: DDC/CI and WMI brightness, and gamma-ramp night light.

Discovery follows Monitorian: every active display in Windows' display
configuration is listed once, by its EDID name. A built-in laptop panel is
driven through WMI and other displays through DDC/CI; a display reachable by
neither is listed as unsupported, with the reason when it is known. See
docs/windows-notes.md for the quirks behind the details here.

Reliability rules, learnt from Monitorian's and Twinkle Tray's bug reports:

- Every Windows call goes through an adapter (DisplayApi; win32.py in the
  app, a fake in tests).
- One display's failure never affects another: each is detected in its own
  try block, and DDC/CI probes run side by side under one deadline, so a
  monitor that hangs is listed as "not responding" while the rest work.
- A display with a call still stuck in it is never asked again until that
  call returns (at most one stuck thread per display), and its handle is
  only destroyed once the call is done with it.
- DDC/CI is used sparingly: one read of VCP 0x10 per monitor per detection,
  never the capabilities string, never for laptop panels.
- A monitor that answered earlier but not now is "not answering yet", not
  "has no DDC/CI": after waking from sleep, monitors take a while (Monitorian
  issue #50), and telling the user to turn DDC/CI on would be wrong.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

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
from nightlight.files import write_atomic
from nightlight.hardware import win32
from nightlight.hardware.brightness import (
    DisplayDetails,
    MonitorGoneError,
    MonitorInfo,
    nearest_level,
    to_percent,
    to_raw,
    valid_range,
    with_unique_keys,
)
from nightlight.hardware.calls import CallTimeout, start_side_call
from nightlight.safe_mode import SKIPPED_REASON, SKIPPED_SHORT, SafeMode

log = logging.getLogger(__name__)

if sys.platform == "win32":
    try:
        # The wmi package makes a COM object when first imported, on the
        # importing thread. Import it here, on the main thread, whose COM
        # lasts until exit; first imported on a worker thread, that object
        # would outlive the thread's COM and fail when released at exit.
        import wmi  # noqa: F401
    except ImportError:
        pass

# How long detection waits for the DDC/CI probes, which run side by side. A
# healthy monitor answers within a second, even with retries.
PROBE_TIMEOUT_S = 4.0
# Another program (f.lux, a GPU color tool, a game) setting the gamma ramp
# too: this many changes within the window means it keeps doing so, and
# putting night light back each time would make the screen flicker between
# the two forever. Winlight backs off from that display for a while, longer
# each time it happens again.
CONTEST_CHANGES = 3
CONTEST_WINDOW_S = 30.0
CONTEST_BACKOFF_S = 120.0
CONTEST_BACKOFF_MAX_S = 1800.0

# Short reasons, shown under a disabled slider, and the longer advice that
# goes with them (tooltip and compatibility report).
REASON_NO_DDC = "DDC/CI didn't answer"
NO_DDC_REASON = (
    "Didn't answer over DDC/CI. Turn DDC/CI on in the display's own menu; if it is on, "
    "try another cable or port without adapters, hubs or docks"
)
TV_HINT = " (most TVs don't support it)"
REASON_INDIRECT = "USB or wireless display: no DDC/CI"
INDIRECT_REASON = "Connected through a USB (DisplayLink), wireless or virtual display adapter, which can't pass DDC/CI brightness commands"
REASON_PANEL = "Windows offers no brightness control"
REASON_NOT_RESPONDING = "Not responding"
NOT_RESPONDING_DETAIL = (
    "A DDC/CI call to this display didn't return, so Winlight stopped talking to it and tries again once that call "
    "finishes. If this keeps happening, turn DDC/CI off in the display's menu, or connect it without an adapter or dock."
)
REASON_RETRYING = "Not answering yet, retrying"
RETRYING_DETAIL = (
    "It answered over DDC/CI earlier. Displays often don't answer for a while after waking from sleep or being "
    "reconnected; Winlight checks again shortly."
)
REASON_CHECK_FAILED = "Couldn't be checked"


class DisplayApi(Protocol):
    """The Windows calls the backends make; win32.py is the real one."""

    def display_targets(self) -> list[win32.DisplayTarget]: ...
    def attached_display_names(self) -> list[str]: ...
    def physical_monitors(self) -> list[win32.PhysicalMonitor]: ...
    def destroy_physical_monitor(self, handle: int) -> None: ...
    def get_vcp(self, handle: int, code: int) -> tuple[int, int]: ...
    def set_vcp(self, handle: int, code: int, value: int) -> None: ...
    def get_monitor_brightness(self, handle: int) -> tuple[int, int, int]: ...
    def set_monitor_brightness(self, handle: int, value: int) -> None: ...
    def get_sdr_white_level(self, target: win32.DisplayTarget) -> float | None: ...
    def set_sdr_white_level(self, target: win32.DisplayTarget, nits: float) -> bool: ...
    def get_gamma_ramp(self, gdi_name: str) -> win32.GammaRamp | None: ...
    def set_gamma_ramp(self, gdi_name: str, ramp: win32.GammaRamp) -> bool: ...


class _Control:
    """How one display's brightness is read and changed. Like Monitorian's
    monitor item classes (DDC/CI, WMI, HDR, unreachable), each way is its own
    class behind one interface, so the backend never branches on the kind.
    DDC/CI errors pass through as win32.DdcError.

    Calls in progress are counted, so closing a control while a call on
    another thread is stuck in it waits for that call to return before
    releasing what it holds (destroying a handle in use is unsafe)."""

    kind = "none"  # for the log
    channel = ""  # MonitorInfo.channel

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = 0
        self._busy_since = 0.0
        self._closing = False
        self._closed = False
        self.on_released: Callable[[], None] | None = None

    def read(self) -> int | None:
        with self._use():
            return self._read()

    def write(self, value: int) -> None:
        with self._use():
            self._write(value)

    def powered_on(self) -> bool | None:
        """Whether the display says it is awake; None if it can't tell."""
        with self._use():
            return self._powered_on()

    @property
    def busy_for(self) -> float | None:
        """How long the call in progress has been running; None when idle."""
        with self._lock:
            return time.monotonic() - self._busy_since if self._busy else None

    def close(self) -> None:
        with self._lock:
            if self._closed or self._closing:
                return
            if self._busy:
                self._closing = True  # released when the call in progress returns
                return
            self._closed = True
        self._finish_release()

    @contextmanager
    def _use(self) -> Iterator[None]:
        with self._lock:
            if self._closed or self._closing:
                # Detection replaced this control; the caller holds a stale reference.
                raise MonitorGoneError(f"{self.kind} control was closed")
            if not self._busy:
                self._busy_since = time.monotonic()
            self._busy += 1
        try:
            yield
        finally:
            with self._lock:
                self._busy -= 1
                release = self._busy == 0 and self._closing
                if release:
                    self._closed = True
            if release:
                self._finish_release()

    def _finish_release(self) -> None:
        try:
            self._release()
        finally:
            if self.on_released is not None:
                self.on_released()

    def _read(self) -> int | None:
        return None

    def _write(self, value: int) -> None:
        raise ValueError("this display has no brightness control")

    def _powered_on(self) -> bool | None:
        return None

    def _release(self) -> None:
        pass


class _VcpControl(_Control):
    """DDC/CI, reading and writing VCP 0x10 directly (Twinkle Tray's "fast" way)."""

    kind = "vcp"
    channel = "ddc"

    def __init__(self, api: DisplayApi, handle: int, maximum: int) -> None:
        super().__init__()
        self.api = api
        self.handle = handle
        self.maximum = maximum

    def _read(self) -> int | None:
        current, maximum = self.api.get_vcp(self.handle, win32.VCP_LUMINANCE)
        if not valid_range(0, current, maximum):
            return None
        self.maximum = maximum
        return to_percent(current, 0, maximum)

    def _write(self, value: int) -> None:
        self.api.set_vcp(self.handle, win32.VCP_LUMINANCE, to_raw(value, 0, self.maximum))

    def _powered_on(self) -> bool | None:
        try:
            mode, _maximum = self.api.get_vcp(self.handle, win32.VCP_POWER_MODE)
        except win32.DdcError as exc:
            if exc.monitor_gone:
                raise
            return None  # many monitors don't report it
        return mode == win32.POWER_ON

    def _release(self) -> None:
        self.api.destroy_physical_monitor(self.handle)


class _HighLevelControl(_VcpControl):
    """DDC/CI through GetMonitorBrightness, for monitors that reject plain VCP 0x10."""

    kind = "high-level"
    channel = "ddc-high-level"

    def __init__(self, api: DisplayApi, handle: int, minimum: int, maximum: int) -> None:
        super().__init__(api, handle, maximum)
        self.minimum = minimum

    def _read(self) -> int | None:
        minimum, current, maximum = self.api.get_monitor_brightness(self.handle)
        if not valid_range(minimum, current, maximum):
            return None
        self.minimum, self.maximum = minimum, maximum
        return to_percent(current, minimum, maximum)

    def _write(self, value: int) -> None:
        self.api.set_monitor_brightness(self.handle, to_raw(value, self.minimum, self.maximum))


class _WmiControl(_Control):
    """A laptop's built-in panel through WMI."""

    kind = "wmi"
    channel = "wmi"

    def __init__(self, wmi: Callable[[], _Wmi], instance: str, levels: list[int]) -> None:
        super().__init__()
        self._wmi = wmi
        self.instance = instance
        self.levels = levels

    def _read(self) -> int | None:
        return self._wmi().get(self.instance)

    def _write(self, value: int) -> None:
        self._wmi().set(self.instance, nearest_level(self.levels, value))


class _SdrControl(_Control):
    """Windows' SDR content brightness, for a display with HDR on."""

    kind = "sdr"
    channel = "sdr"

    def __init__(self, api: DisplayApi, target: win32.DisplayTarget) -> None:
        super().__init__()
        self.api = api
        self.target = target

    def _read(self) -> int | None:
        nits = self.api.get_sdr_white_level(self.target)
        return None if nits is None else _sdr_percent(nits)

    def _write(self, value: int) -> None:
        nits = win32.SDR_MIN_NITS + value * (win32.SDR_MAX_NITS - win32.SDR_MIN_NITS) / 100
        if not self.api.set_sdr_white_level(self.target, nits):
            raise OSError("Windows refused the SDR brightness (is HDR still on?)")


class _SoftwareControl(_Control):
    """Dimming the display's gamma ramp, together with night light."""

    kind = "software"
    channel = "software"

    def __init__(self, gamma: WindowsGammaBackend, gdi_name: str) -> None:
        super().__init__()
        self.gamma = gamma
        self.gdi_name = gdi_name

    def _read(self) -> int | None:
        return self.gamma.dimming(self.gdi_name)

    def _write(self, value: int) -> None:
        self.gamma.set_dimming(self.gdi_name, value)


@dataclass
class _Monitor:
    info: MonitorInfo
    control: _Control = field(default_factory=_Control)
    detected: int | None = None  # brightness read while detecting, handed out once
    ident: str = ""  # the display's identity before with_unique_keys numbered repeats


_HUNG = object()  # a probe that didn't return in time


class _Wmi:
    """WMI's WmiMonitorBrightness, which laptops provide for the built-in panel.

    COM must be initialised on the thread that uses it, and calls can move to
    a fresh thread after a stuck one (see calls.CallRunner), so the backend
    keeps one connection per thread.
    """

    def __init__(self) -> None:
        import pythoncom
        import wmi

        pythoncom.CoInitialize()
        self._pythoncom = pythoncom
        self._conn = wmi.WMI(namespace="wmi")

    def release(self) -> None:
        """Drop the COM objects on the thread that made them (at exit they
        would be released from another thread, which COM complains about)."""
        self._conn = None
        import gc

        gc.collect()  # the wmi package's wrappers sit in reference cycles
        try:
            self._pythoncom.CoUninitialize()
        except Exception:
            pass

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

    def __init__(self, gamma: WindowsGammaBackend | None = None, api: DisplayApi | None = None) -> None:
        self._api: DisplayApi = api or win32
        self._monitors: list[_Monitor] = []
        self._wmi: _Wmi | None = None  # set by tests; otherwise one connection per thread
        self._wmi_local = threading.local()
        self._wmi_problem = ""
        self._gamma = gamma
        self.software_dimming = gamma is not None
        # Displays with a call still stuck in them, by key: never probed until it returns.
        self._stuck: dict[str, str] = {}
        self._stuck_lock = threading.Lock()
        # Displays that answered DDC/CI earlier in this session, by key.
        self._answered_ddc: set[str] = set()
        # Skips displays whose DDC/CI crashed or hung the app in an earlier run
        # (a crash can't be caught by time limits, only learnt from).
        self.safe_mode: SafeMode | None = None

    def retry_skipped(self) -> None:
        """The user asked to try again (⟳): displays skipped after a crash are probed again."""
        if self.safe_mode is not None:
            self.safe_mode.clear()

    def set_software_dimming(self, enabled: bool) -> None:
        """Takes effect at the next detection; turning it off undims at once."""
        self.software_dimming = enabled and self._gamma is not None
        if not self.software_dimming:
            for monitor in self._monitors:
                if isinstance(monitor.control, _SoftwareControl):
                    monitor.control.write(100)

    @property
    def stuck(self) -> dict[str, str]:
        with self._stuck_lock:
            return dict(self._stuck)

    def list_monitors(self) -> list[MonitorInfo]:
        self.close()
        api = self._api
        targets = api.display_targets()
        by_display: dict[str, list[win32.PhysicalMonitor]] = {}
        for physical in api.physical_monitors():
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
            try:
                monitors += self._detect_source(group, physicals, panels)
            except Exception:
                # One display's failure must not take the others down.
                log.exception("Detecting the display(s) on %s failed", gdi_name)
                for physical in physicals:
                    self._destroy_quietly(physical.handle)
                for target in group:
                    info = MonitorInfo(0, _name_of(target), False, REASON_CHECK_FAILED, detail="Detecting this display failed; the log has the details.",
                                       status="unsupported")
                    monitors.append(_Monitor(replace(info, key=target.key, internal=target.internal, details=_details_of(target),
                                                     display=target.gdi_name, hdr=target.hdr)))

        # Displays the display configuration didn't describe (or it failed).
        leftovers = [physical for physicals in by_display.values() for physical in physicals]
        if self.safe_mode is not None:
            for physical in [p for p in leftovers if self.safe_mode.is_skipped(p.gdi_name)]:
                self._destroy_quietly(physical.handle)
                leftovers.remove(physical)
                info = MonitorInfo(0, physical.description, False, SKIPPED_SHORT, detail=SKIPPED_REASON, status="skipped", display=physical.gdi_name)
                monitors.append(_Monitor(info))
        for physical, probed in zip(leftovers, self._probe_all(leftovers)):
            if isinstance(probed, _Monitor):
                probed.info = replace(probed.info, display=physical.gdi_name)
                monitors.append(probed)
            else:
                monitors.append(_Monitor(MonitorInfo(0, physical.description, False, REASON_NO_DDC, detail=NO_DDC_REASON, status="unsupported",
                                                     display=physical.gdi_name)))
        for instance, current, levels in panels:
            info = MonitorInfo(0, "Built-in display", True, key="internal", internal=True, minimum=1, channel="wmi")
            monitors.append(_Monitor(info, self._wmi_control(instance, levels), current))

        for monitor, info in zip(monitors, with_unique_keys([replace(m.info, index=i) for i, m in enumerate(monitors)])):
            monitor.info = info
            state = monitor.control.kind if info.supported else f"not supported: {info.reason}"
            log.info("Monitor %d: %s [%s] (%s)", info.index, info.name, info.key, state)
        self._monitors = monitors
        return [m.info for m in monitors]

    def _detect_source(self, group: list[win32.DisplayTarget], physicals: list[win32.PhysicalMonitor], panels) -> list[_Monitor]:
        external = [t for t in group if not t.internal]
        stuck = self.stuck
        # A display whose DDC/CI crashed or hung the app last time isn't probed
        # (safe mode); in a clone group, none of its monitors are, since which
        # handle belongs to which display isn't known.
        skipped = self.safe_mode is not None and any(self.safe_mode.is_skipped(t.key, t.gdi_name) for t in external)
        if not external or skipped or all(t.key in stuck for t in external):
            # A laptop panel is driven through WMI and never gets a DDC/CI
            # handle; asking it anyway costs half a second of retried errors
            # at every detection. A display with a stuck call isn't asked again.
            for physical in physicals:
                self._destroy_quietly(physical.handle)
            physicals = []
        probed = self._probe_all(physicals, [t.key for t in external if t.key not in stuck])
        responding = [p for p in probed if isinstance(p, _Monitor)]
        hung = sum(1 for p in probed if p is _HUNG)

        monitors = []
        for target in group:
            name = _name_of(target)
            if target.internal:
                monitor = self._panel(name, target, panels)
            elif skipped:
                monitor = _Monitor(MonitorInfo(0, name, False, SKIPPED_SHORT, detail=SKIPPED_REASON, status="skipped"))
            elif target.key in stuck:
                monitor = _Monitor(MonitorInfo(0, name, False, REASON_NOT_RESPONDING, detail=NOT_RESPONDING_DETAIL, status="not responding"))
            elif responding:
                monitor = responding.pop(0)
                monitor.info = replace(monitor.info, name=name)
                self._answered_ddc.add(target.key)
            elif hung:
                hung -= 1
                monitor = _Monitor(MonitorInfo(0, name, False, REASON_NOT_RESPONDING, detail=NOT_RESPONDING_DETAIL, status="not responding"))
            elif target.key in self._answered_ddc:
                monitor = _Monitor(MonitorInfo(0, name, False, REASON_RETRYING, detail=RETRYING_DETAIL, status="retrying"))
            elif target.output_technology in win32.INDIRECT_TECHNOLOGIES:
                monitor = _Monitor(MonitorInfo(0, name, False, REASON_INDIRECT, detail=INDIRECT_REASON, status="unsupported"))
            else:
                hdmi = target.output_technology == win32.OUTPUT_HDMI
                reason = REASON_NO_DDC + (TV_HINT if hdmi else "")
                monitor = _Monitor(MonitorInfo(0, name, False, reason, detail=NO_DDC_REASON + (TV_HINT if hdmi else ""), status="unsupported"))
            if not monitor.info.supported and monitor.info.status == "unsupported":
                monitor = self._fallback(monitor, target)
            monitor.info = replace(monitor.info, key=target.key, internal=target.internal, details=_details_of(target),
                                   display=target.gdi_name, hdr=target.hdr)
            monitor.ident = target.key
            monitors.append(monitor)
        return monitors + responding  # more answering monitors than targets; shouldn't happen

    def _fallback(self, monitor: _Monitor, target: win32.DisplayTarget) -> _Monitor:
        """SDR brightness or software dimming for a display without hardware control."""
        info = monitor.info
        if target.hdr:
            try:
                nits = self._api.get_sdr_white_level(target)
            except OSError:
                nits = None
            if nits is not None:
                info = replace(info, supported=True, reason="", detail="", control="sdr", channel="sdr", status="ok")
                return _Monitor(info, _SdrControl(self._api, target), _sdr_percent(nits))
        elif self.software_dimming and self._gamma is not None and self._gamma.can_dim(target.gdi_name):
            info = replace(info, supported=True, reason="", detail="", control="software", channel="software", minimum=SOFTWARE_DIM_MIN, status="ok")
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
            if monitor.control.busy_for is not None:
                # A call is stuck in this display: keep away from it until it returns.
                key, name = monitor.ident or monitor.info.key, monitor.info.name
                with self._stuck_lock:
                    self._stuck[key] = f"a call to {name}"
                monitor.control.on_released = lambda key=key, name=name: self._unstuck(key, name)
                log.warning("%s still has a call in progress; not asking it again until that returns", name)
            monitor.control.close()
        self._monitors = []

    def release_thread_resources(self) -> None:
        """Release this thread's WMI connection; call on the thread that used it."""
        conn = getattr(self._wmi_local, "conn", None)
        self._wmi_local.conn = None
        if conn is not None:
            conn.release()

    def _unstuck(self, key: str, name: str) -> None:
        with self._stuck_lock:
            if self._stuck.pop(key, None) is not None:
                log.info("The stuck call to %s returned; it will be checked again at the next detection", name)

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

    def _probe_all(self, physicals: list[win32.PhysicalMonitor], keys: list[str] | None = None) -> list:
        """Probe monitors side by side, all within PROBE_TIMEOUT_S. Each result
        is a _Monitor, None (no DDC/CI) or _HUNG (didn't answer in time; its
        handle is released when the probe finally returns)."""
        if not physicals:
            return []
        keys = keys or []
        calls = []
        returned_late: set[str] = set()  # keys whose probe returned after its deadline; guarded by _stuck_lock
        for i, physical in enumerate(physicals):
            key = keys[i] if i < len(keys) else ""
            calls.append((physical, key, start_side_call(lambda p=physical, k=key: self._probe_marked(p, k),
                                                          f"probing {physical.description} on {physical.gdi_name}",
                                                          on_late=lambda p=physical, k=key: self._late_probe(p, k, returned_late))))
        deadline = time.monotonic() + PROBE_TIMEOUT_S
        results = []
        for physical, key, call in calls:
            try:
                results.append(call.result(deadline - time.monotonic()))
            except CallTimeout:
                log.warning("%s on %s didn't answer within %g s; leaving it alone until it does", physical.description, physical.gdi_name, PROBE_TIMEOUT_S)
                if key:
                    with self._stuck_lock:
                        # Unless the probe returned in the meantime (its clean-up already ran).
                        if key not in returned_late:
                            self._stuck[key] = f"probing {physical.description}"
                results.append(_HUNG)
            except Exception:
                log.exception("Probing %s on %s failed", physical.description, physical.gdi_name)
                self._destroy_quietly(physical.handle)
                results.append(None)
        return results

    def _probe_marked(self, physical: win32.PhysicalMonitor, key: str) -> _Monitor | None:
        """A probe inside safe mode's marker: if the app crashes during it, or
        quits while it hangs, the display is skipped at the next start."""
        if self.safe_mode is None:
            return self._probe(physical)
        name = physical.description or physical.gdi_name
        with self.safe_mode.risky(key or physical.gdi_name, f"detecting {name} on {physical.gdi_name}"):
            return self._probe(physical)

    def _late_probe(self, physical: win32.PhysicalMonitor, key: str, returned_late: set[str]) -> None:
        """A probe that was given up on returned: release its handle, and its display."""
        self._destroy_quietly(physical.handle)
        if key:
            with self._stuck_lock:
                returned_late.add(key)
                self._stuck.pop(key, None)

    def _probe(self, physical: win32.PhysicalMonitor) -> _Monitor | None:
        """A monitor that answers DDC/CI brightness requests, or None (and the handle is released).

        Like Twinkle Tray's "fast" check, this reads VCP 0x10 directly rather
        than requesting the capabilities string first, which is slow and on
        some monitors and drivers hangs or returns garbage.
        """
        name = physical.description or physical.gdi_name
        info = MonitorInfo(0, name, True)
        api = self._api
        try:
            current, maximum = api.get_vcp(physical.handle, win32.VCP_LUMINANCE)
            if valid_range(0, current, maximum):
                return _Monitor(replace(info, channel="ddc"), _VcpControl(api, physical.handle, maximum), to_percent(current, 0, maximum))
            log.info("%s on %s reported brightness %d of %d; ignoring it", name, physical.gdi_name, current, maximum)
        except win32.DdcError as exc:
            log.info("%s on %s: no DDC/CI brightness (%s)", name, physical.gdi_name, exc)
            if exc.monitor_gone or exc.status == "transmission failed":
                # Gone, or nothing gets through at all: the high-level API can't do better.
                self._destroy_quietly(physical.handle)
                return None
        try:
            minimum, current, maximum = api.get_monitor_brightness(physical.handle)
            if valid_range(minimum, current, maximum):
                control = _HighLevelControl(api, physical.handle, minimum, maximum)
                return _Monitor(replace(info, channel="ddc-high-level"), control, to_percent(current, minimum, maximum))
        except win32.DdcError as exc:
            log.debug("%s on %s: GetMonitorBrightness failed too (%s)", name, physical.gdi_name, exc)
        self._destroy_quietly(physical.handle)
        return None

    def _destroy_quietly(self, handle: int) -> None:
        try:
            self._api.destroy_physical_monitor(handle)
        except Exception:
            log.debug("Destroying physical monitor handle %s failed", handle, exc_info=True)

    def _panel(self, name: str, target: win32.DisplayTarget, panels: list[tuple[str, int, list[int]]]) -> _Monitor:
        """The WMI panel for a built-in display, removing it from `panels`."""
        instance_id = target.instance_id.lower()
        match = next((p for p in panels if instance_id and p[0].lower().startswith(instance_id)), None)
        if match is None and len(panels) == 1:
            match = panels[0]  # a laptop has one panel; the ids may just be formatted differently
        if match is None:
            detail = f"Windows offers no brightness control for this display ({self._wmi_problem or 'no WMI brightness'})"
            return _Monitor(MonitorInfo(0, name, False, REASON_PANEL, detail=detail, status="unsupported"))
        panels.remove(match)
        instance, current, levels = match
        return _Monitor(MonitorInfo(0, name, True, minimum=1, channel="wmi"), self._wmi_control(instance, levels), current)

    def _wmi_control(self, instance: str, levels: list[int]) -> _WmiControl:
        return _WmiControl(self._get_wmi, instance, levels)

    def _get_wmi(self) -> _Wmi:
        if self._wmi is not None:
            return self._wmi
        conn = getattr(self._wmi_local, "conn", None)
        if conn is None:
            conn = self._wmi_local.conn = _Wmi()
        return conn

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
            self._wmi_local.conn = None
            self._wmi_problem = "the WMI query failed"
            log.info("Querying WMI brightness failed", exc_info=True)
            return []
        self._wmi_problem = "" if panels else "no WMI brightness"
        return panels


def _name_of(target: win32.DisplayTarget) -> str:
    return target.model or ("Built-in display" if target.internal else f"{target.connector} display")


def _details_of(target: win32.DisplayTarget) -> DisplayDetails:
    edid = target.edid
    return DisplayDetails(
        manufacturer=edid.manufacturer if edid else target.hardware_id[:3],
        model=target.model,
        serial=edid.serial if edid else "",
        connector=target.connector,
        hdr=target.hdr,
        year=edid.year if edid else 0,
        diagonal_inches=edid.diagonal_inches if edid else 0.0,
        source=target.gdi_name,
    )


def _sdr_percent(nits: float) -> int:
    """SDR white level to 0-100 across Windows' 80-480 nit slider (as Monitorian)."""
    span = win32.SDR_MAX_NITS - win32.SDR_MIN_NITS
    return max(0, min(100, round((nits - win32.SDR_MIN_NITS) * 100 / span)))


class GammaJournal:
    """Remembers each display's own ramp (its calibration) while the app has
    it tinted or dimmed, so a crash can be recovered from.

    Gamma ramps outlive the app. After a crash, the next start finds a tinted
    ramp and can't tell the calibration underneath; without this, it would
    fall back to a neutral ramp and the calibration would stay lost until
    Windows reloads it. Entries are kept per display identity (EDID), not
    per GDI name, which can change between sessions. Stale entries (the
    ramp was reset by Windows meanwhile) are recognised and dropped.
    """

    WRITE_INTERVAL_S = 2.0  # while a ramp keeps changing (fades), save at most this often

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self._entries: dict[str, dict] = self._load()
        self._written_at = 0.0
        self._unsaved = False

    def _load(self) -> dict[str, dict]:
        if self.path is None:
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("displays", {}) if isinstance(raw, dict) else {}
            return {k: v for k, v in entries.items() if isinstance(v, dict)}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.warning("Could not read %s; crash recovery of display calibrations starts afresh", self.path, exc_info=True)
            return {}

    def entry(self, key: str) -> dict | None:
        entry = self._entries.get(key)
        return entry if entry and entry.get("dirty") else None

    def changed(self, key: str, base: win32.GammaRamp | None, ramp: win32.GammaRamp) -> None:
        """The app set `ramp` on a display whose own ramp is `base` (None: identity)."""
        clean = ramps_match(ramp, base or identity_ramp(win32.RAMP_SIZE))
        previous = self._entries.get(key)
        was_dirty = bool(previous and previous.get("dirty"))
        self._entries[key] = {"dirty": not clean, "base": base, "last": None if clean else ramp}
        if clean != (not was_dirty) or time.monotonic() - self._written_at >= self.WRITE_INTERVAL_S:
            self.save()  # at every switch between tinted and clean, and periodically while tinted
        else:
            self._unsaved = True

    def forget(self, key: str) -> None:
        if self._entries.pop(key, None) is not None:
            self.save()

    def flush(self) -> None:
        if self._unsaved:
            self.save()

    def save(self) -> None:
        self._unsaved = False
        self._written_at = time.monotonic()
        if self.path is None:
            return
        try:
            write_atomic(self.path, json.dumps({"version": 1, "displays": self._entries}))
        except OSError:
            log.warning("Could not save %s", self.path, exc_info=True)

    def recognises(self, key: str, ramp: win32.GammaRamp) -> win32.GammaRamp | None:
        """If `ramp` is what the app left on this display, the display's own
        ramp underneath (or the identity); else None."""
        entry = self.entry(key)
        if entry is None:
            return None
        base = entry.get("base") or identity_ramp(win32.RAMP_SIZE)
        if entry.get("last") and ramps_match(ramp, entry["last"]):
            return base
        # Saved a moment before the crash, mid-fade: any tint of the base.
        for kelvin in range(1000, NEUTRAL_KELVIN + 1, 50):
            if ramps_match(ramp, gamma_ramp(whitepoint(kelvin), win32.RAMP_SIZE, base)):
                return base
        return None


@dataclass
class _GammaDisplay:
    name: str  # GDI name, "\\.\DISPLAY1"
    hdr: bool = False
    key: str = ""  # identity, for the journal
    base: win32.GammaRamp | None = None  # the ramp to tint, None for identity
    last_set: win32.GammaRamp | None = None
    warmest: int = 0  # warmest temperature Windows accepted here, once it refused one
    dim: int = 100  # software dimming, percent (100: none)
    idle_dim: int = 100  # dimming while the PC is left alone, a separate layer; the darker of the two wins
    dim_floor: int = 0  # darkest dimming Windows accepted here, once it refused one
    leftover: bool = False  # found tinted (a crash, f.lux): put neutral back even if we never set it
    kelvin_override: int | None = None  # this display's own temperature (night light paused for an app)
    foreign: list[float] = field(default_factory=list)  # when another program changed the ramp lately
    contested: bool = False  # backing off from another program changing the ramp
    contested_until: float = 0.0
    backoff: float = 0.0  # the last back-off, doubled if it happens again

    @property
    def effective_dim(self) -> int:
        return min(self.dim, self.idle_dim)


class WindowsGammaBackend:
    """Sets each display's gamma ramp with GDI's SetDeviceGammaRamp.

    The display's own ramp (usually its calibration) is kept as the base and
    tinted, rather than replaced with a flat one, and is put back by reset().

    Night light and software dimming share the one ramp, so, like KWin's
    ColorDevice (brightness x temperature), each display keeps its state
    (base ramp, dimming, idle dimming, its own temperature while night light
    is paused for an app) and every change composes the whole ramp again:
    base, dimmed, then tinted. Night light is set from the UI thread and
    dimming from the monitor thread, so a lock guards the state.
    """

    # How long the display list and their HDR state are trusted.
    DISPLAYS_TTL = 10.0

    def __init__(self, api: DisplayApi | None = None, journal: GammaJournal | None = None) -> None:
        self._api: DisplayApi = api or win32
        self._journal = journal or GammaJournal()
        self._displays: dict[str, _GammaDisplay] = {}
        self._listed_at: float | None = None
        self._reapplied = False  # set when refreshing the list put back a reset ramp
        self._warned: set[tuple[str, str]] = set()
        self._kelvin = NEUTRAL_KELVIN
        self._lock = threading.RLock()

    def set_journal(self, path: Path) -> None:
        """Where to keep the crash-recovery journal; call before the first change."""
        with self._lock:
            self._journal = GammaJournal(path)
            self._listed_at = None  # look at the displays again with it

    @property
    def dimmed(self) -> bool:
        """Some display is dimmed, so its ramp must be kept even while night light is off."""
        with self._lock:
            return any(d.effective_dim < 100 for d in self._displays.values())

    def set_idle_dim(self, levels: dict[str, int]) -> None:
        """Dim these displays (GDI name -> percent) for idle, undim the rest.
        Software only: no DDC/CI writes. HDR displays are skipped, since
        Windows ignores gamma ramps there."""
        with self._lock:
            for display in self._current_displays():
                level = 100 if display.hdr else max(SOFTWARE_DIM_MIN, min(100, levels.get(display.name, 100)))
                if level != display.idle_dim:
                    display.idle_dim = level
                    display.warmest = 0  # the room for tinting depends on the dimming
                    self._isolated(display, self._apply)

    def set_override(self, gdi_name: str, kelvin: int | None) -> None:
        """Give one display its own temperature (None: follow night light), used
        to pause night light on the display showing an exempt app."""
        with self._lock:
            display = next((d for d in self._current_displays() if d.name == gdi_name), None)
            if display is None or display.kelvin_override == kelvin:
                return
            display.kelvin_override = kelvin
            self._isolated(display, self._apply)

    def verify(self) -> bool:
        """Put our ramps back where something reset them: Windows does on the
        lock screen, after sleep and on display changes, often before any event
        arrives. Cheap (one GetDeviceGammaRamp per display), so it can run every
        second or two, and right after those events. True if any was re-applied."""
        with self._lock:
            self._reapplied = False
            displays = self._current_displays()
            reapplied = self._reapplied  # found reset while the list was refreshed (and already re-applied)
            now = time.monotonic()
            for display in displays:
                if display.contested and now >= display.contested_until:
                    display.contested = False
                    log.info("No other program changed the colors of %s for a while; putting night light back", display.name)
                    if not display.hdr and self._active(display):
                        self._isolated(display, self._apply)
                        reapplied = True
                    continue
                if display.hdr or display.last_set is None:
                    continue
                try:
                    current = self._api.get_gamma_ramp(display.name)
                except OSError:
                    continue
                if current is None or ramps_match(current, display.last_set):
                    continue
                log.debug("The gamma ramp of %s was reset; applying it again", display.name)
                self._isolated(display, self._sync_base)
                active = self._active(display)
                if active:
                    self._isolated(display, self._apply)
                elif display.leftover:
                    self._set(display, display.base or identity_ramp(win32.RAMP_SIZE))
                reapplied = reapplied or (active and not display.contested)
        return reapplied

    def warmest_accepted(self) -> dict[str, int]:
        """GDI name -> the warmest temperature Windows has accepted, where it refused warmer."""
        with self._lock:
            return {d.name: d.warmest for d in self._displays.values() if d.warmest}

    def hdr_displays(self) -> set[str]:
        with self._lock:
            return {d.name for d in self._displays.values() if d.hdr}

    def displays_changed(self) -> None:
        """Displays were added, removed or reconfigured: look at them again.
        Windows resets gamma ramps then, so dimmed displays are dimmed again."""
        with self._lock:
            self._listed_at = None
            for display in self._displays.values():
                display.warmest = 0
                display.dim_floor = 0
            for display in self._current_displays():
                if display.effective_dim < 100 or display.kelvin_override is not None:
                    self._isolated(display, self._apply)

    def apply(self, kelvin: int) -> None:
        with self._lock:
            self._kelvin = kelvin
            for display in self._current_displays():
                self._isolated(display, self._apply)

    def reset(self) -> None:
        """Night light off: neutral colors, keeping any dimming. Displays the
        app hasn't changed are left alone, so other color tools aren't fought."""
        with self._lock:
            self._kelvin = NEUTRAL_KELVIN
            for display in self._current_displays():
                if display.effective_dim < 100 or display.kelvin_override is not None:
                    self._isolated(display, self._apply)
                elif self._owned(display):
                    base = display.base or identity_ramp(win32.RAMP_SIZE)
                    if display.leftover or not ramps_match(display.last_set, base):
                        self._set(display, base)

    def release(self) -> None:
        """Quitting: give every display back its own ramp, undimmed."""
        with self._lock:
            for display in self._current_displays():
                display.kelvin_override = None
                display.idle_dim = 100
                if self._owned(display):
                    self._set(display, display.base or identity_ramp(win32.RAMP_SIZE))
                display.dim = 100
            self._journal.flush()

    @staticmethod
    def _owned(display: _GammaDisplay) -> bool:
        """Whether resetting this display's ramp is ours to do."""
        return not display.hdr and (display.last_set is not None or display.leftover)

    def can_dim(self, gdi_name: str) -> bool:
        """Whether software dimming can work here: an SDR display whose ramp can be read."""
        with self._lock:
            display = next((d for d in self._current_displays() if d.name == gdi_name), None)
            if display is None or display.hdr:
                return False
            try:
                return self._api.get_gamma_ramp(gdi_name) is not None
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
                raise MonitorGoneError(gdi_name)
            dim = max(SOFTWARE_DIM_MIN, min(100, percent)) if percent < 100 else 100
            if dim != display.dim:
                # Dimming leaves less room for tinting, so the warmest
                # temperature Windows accepts depends on it: find it again.
                display.warmest = 0
            display.dim = dim
            self._apply(display)
            return max(display.dim, display.dim_floor)

    def idle_dimming(self) -> dict[str, int]:
        """GDI name -> idle dimming level, where dimmed (for diagnostics)."""
        with self._lock:
            return {d.name: d.idle_dim for d in self._displays.values() if d.idle_dim < 100}

    def _isolated(self, display: _GammaDisplay, step: Callable[[_GammaDisplay], None]) -> None:
        """One display's failure must not stop the others from being set."""
        try:
            step(display)
        except Exception:
            log.exception("Changing the gamma ramp of %s failed", display.name)

    def _apply(self, display: _GammaDisplay) -> None:
        if display.contested and time.monotonic() < display.contested_until:
            return  # another program keeps changing it; see _foreign_change
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
        dim = max(display.effective_dim, display.dim_floor)
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
            if not self._api.set_gamma_ramp(display.name, ramp):
                return False
            applied = self._api.get_gamma_ramp(display.name)
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
        self._journal.changed(display.key or display.name, display.base, ramp)
        return True

    def _current_displays(self) -> list[_GammaDisplay]:
        now = time.monotonic()
        if self._listed_at is not None and now - self._listed_at < self.DISPLAYS_TTL:
            return list(self._displays.values())
        self._listed_at = now
        sources: dict[str, tuple[bool, str]] = {}  # GDI name -> (HDR, identity)
        if targets := self._api.display_targets():
            for target in targets:
                hdr, key = sources.get(target.gdi_name, (False, ""))
                sources[target.gdi_name] = (hdr or target.hdr, key or target.key)
        else:
            sources = {name: (False, "") for name in self._api.attached_display_names()}
        displays = {}
        for name, (hdr, key) in sources.items():
            display = self._displays.get(name) or _GammaDisplay(name)
            if display.hdr and not hdr:
                log.info("HDR was turned off for %s; night light and dimming apply to it again", name)
            display.hdr = hdr
            display.key = key
            ours = display.last_set is not None
            self._isolated(display, self._sync_base)
            if ours and display.last_set is None and not hdr and self._active(display):
                # Our ramp was reset (the lock screen, waking) and is now the
                # new base; put night light and dimming back over it at once,
                # as verify() would have.
                log.debug("The gamma ramp of %s was reset; applying it again", name)
                self._isolated(display, self._apply)
                self._reapplied = True
            displays[name] = display
        self._displays = displays
        return list(displays.values())

    def _active(self, display: _GammaDisplay) -> bool:
        """Whether this display should show something of ours (tint, dimming, a pause)."""
        return self._kelvin < NEUTRAL_KELVIN or display.effective_dim < 100 or display.kelvin_override is not None

    def _sync_base(self, display: _GammaDisplay) -> None:
        """Re-read the display's base ramp if something other than us set its ramp."""
        try:
            current = self._api.get_gamma_ramp(display.name)
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
            self._foreign_change(display)
        display.last_set = None
        key = display.key or display.name
        if not plausible_base(current):
            recovered = self._journal.recognises(key, current)
            display.base = None if recovered is None or ramps_match(recovered, identity_ramp(win32.RAMP_SIZE)) else recovered
            display.leftover = True
            if recovered is not None:
                log.warning("%s still had the tint Winlight left when it last stopped unexpectedly; restoring the ramp it had before", display.name)
            else:
                self._warn_once(
                    display.name,
                    "tinted",
                    "%s already had a tinted gamma ramp (left over from a crash, or another app such as f.lux); starting from a neutral one",
                    display.name,
                )
        else:
            self._journal.forget(key)  # whatever we left there is gone: the entry is stale
            if ramps_match(current, identity_ramp(win32.RAMP_SIZE)):
                display.base = None
            else:
                display.base = current
                log.info("Keeping the existing gamma ramp of %s (its calibration) as the base for night light", display.name)

    def _foreign_change(self, display: _GammaDisplay) -> None:
        """Our ramp was replaced: once is Windows (a reset) or a tool starting;
        again and again is another program fighting over it (see CONTEST_CHANGES)."""
        now = time.monotonic()
        display.foreign = [t for t in display.foreign if now - t < CONTEST_WINDOW_S] + [now]
        if display.contested:
            display.contested_until = now + display.backoff  # still at it: keep away
            log.debug("The gamma ramp of %s was changed by another program again", display.name)
            return
        if len(display.foreign) < CONTEST_CHANGES:
            log.info("The gamma ramp of %s was changed by something else; using it as the new base", display.name)
            return
        display.backoff = min(CONTEST_BACKOFF_MAX_S, display.backoff * 2) if display.backoff else CONTEST_BACKOFF_S
        display.contested, display.contested_until = True, now + display.backoff
        display.foreign.clear()
        log.warning(
            "Another program keeps changing the colors of %s (f.lux, a GPU color tool or a game?). To stop the screen flickering "
            "between the two, Winlight leaves that display alone for %.0f min; close the other program or turn off its color changes",
            display.name, display.backoff / 60,
        )

    def _warn_once(self, display: str, kind: str, message: str, *args) -> None:
        if (display, kind) not in self._warned:
            self._warned.add((display, kind))
            log.warning(message, *args)
