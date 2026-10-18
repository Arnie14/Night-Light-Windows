# SPDX-License-Identifier: GPL-3.0-only
"""`main.py --probe`: a report of what each Windows API says about the displays.

Modelled on Monitorian's "Probe into monitors": when a monitor misbehaves,
this shows which API sees it, what it answers, and what the app makes of it.
Nothing is changed, except that each display is briefly tinted to find the
warmest gamma ramp Windows accepts, and then restored.
"""

from __future__ import annotations

import logging
import platform
import sys
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import PySide6
from PySide6.QtCore import qVersion
from PySide6.QtGui import QGuiApplication

from nightlight import __version__

log = logging.getLogger(__name__)


def run(backend: str, out_dir: Path | None) -> int:
    lines: list[str] = []
    section = _sections(lines)
    section("System", _system)
    section("Qt screens", _qt_screens)
    if sys.platform == "win32":
        section("Windows capabilities and ctypes layouts", _capabilities)
        section("Displays (QueryDisplayConfig)", _display_config)
        section("DDC/CI monitors (dxva2, read-only)", _ddc)
        section("Built-in panels (WMI)", _wmi)
        section("Gamma ramps (night light)", _gamma)
    section(f"What the app sees (--backend {backend})", lambda out: _backend(out, backend))

    report = "\n".join(lines) + "\n"
    print(report)
    if out_dir is not None:
        path = out_dir / "probe.txt"
        try:
            path.write_text(report, encoding="utf-8")
            print(f"Saved to {path}")
        except OSError as exc:
            print(f"Could not save the report to {path}: {exc}")
    return 0


def _sections(lines: list[str]) -> Callable[[str, Callable[[Callable[[str], None]], None]], None]:
    def section(title: str, body: Callable[[Callable[[str], None]], None]) -> None:
        lines.extend(["", f"== {title} =="])
        try:
            body(lambda text="": lines.append(f"  {text}" if text else ""))
        except Exception:
            lines.append("  FAILED:")
            lines.extend(f"    {line}" for line in traceback.format_exc().splitlines())

    return section


def _system(out) -> None:
    out(f"Night Light {__version__}, {datetime.now().astimezone():%Y-%m-%d %H:%M %Z}")
    out(f"OS: {platform.platform()}")
    if sys.platform == "win32":
        version = sys.getwindowsversion()
        out(f"Windows build: {version.major}.{version.minor}.{version.build}")
    out(f"Python {platform.python_version()} ({platform.architecture()[0]}), PySide6 {PySide6.__version__}, Qt {qVersion()}")
    out(f"Qt platform: {QGuiApplication.platformName()}")


def _qt_screens(out) -> None:
    primary = QGuiApplication.primaryScreen()
    for screen in QGuiApplication.screens():
        g, a = screen.geometry(), screen.availableGeometry()
        out(f"{screen.name()}{' (primary)' if screen is primary else ''}: {screen.manufacturer()} {screen.model()}".rstrip())
        out(f"  geometry {g.x()},{g.y()} {g.width()}x{g.height()}, available {a.x()},{a.y()} {a.width()}x{a.height()}, scale {screen.devicePixelRatio():g}")


def _capabilities(out) -> None:
    from nightlight.hardware import win32

    win32.display_targets()  # tries the optional HDR query once, so its support is known
    for name, value in win32.capabilities().items():
        if name != "windows":
            out(f"{name}: {', '.join(value) if isinstance(value, list) else value}")


def _display_config(out) -> None:
    from nightlight.hardware import win32

    targets = win32.display_targets()
    if not targets:
        out("No displays (the query failed; see the log). The app falls back to EnumDisplayDevices:")
        for name in win32.attached_display_names():
            out(f"  {name}")
        return
    for target in targets:
        out(f"{target.gdi_name}: {target.friendly_name or '(no EDID name)'}")
        out(f"  connector {target.connector}{', built-in' if target.internal else ''}, HDR {'on' if target.hdr else 'off'}")
        if target.hdr:
            nits = win32.get_sdr_white_level(target)
            out(f"  SDR content brightness: {f'{nits:.0f} nits' if nits is not None else 'unavailable'}")
        out(f"  instance {target.instance_id or '?'}")
        if edid := target.edid:
            size = f', {edid.diagonal_inches:g}"' if edid.diagonal_inches else ""
            out(f"  EDID: {edid.manufacturer} {edid.product_code:04X} {edid.name or '(no name)'}, made {edid.year}{size}, "
                f"serial {'present' if edid.serial else 'none'}; identity {target.key}")
        else:
            out(f"  EDID: not in the registry; identity {target.key or '?'}")


def _ddc(out) -> None:
    from nightlight.hardware import win32

    if not win32.ddc_available():
        out("dxva2.dll is not available")
        return
    monitors = win32.physical_monitors()
    if not monitors:
        out("No physical monitors")
    for monitor in monitors:
        out(f"{monitor.gdi_name}: {monitor.description} (handle {monitor.handle})")
        try:
            # Brightness, then what helps explain a monitor that misbehaves:
            # whether it reports its power state (checked before restoring
            # brightness), and other brightness-like codes some monitors
            # answer instead of 0x10. Each read is timed: slow answers
            # (hundreds of ms) point at a dock, adapter or busy monitor.
            for code, label in (
                (win32.VCP_LUMINANCE, "brightness"),
                (win32.VCP_POWER_MODE, "power mode (1 = on)"),
                (win32.VCP_CONTRAST, "contrast"),
                (win32.VCP_BACKLIGHT_CONTROL, "backlight control"),
                (win32.VCP_BACKLIGHT_WHITE, "backlight level (white)"),
            ):
                started = time.perf_counter()
                try:
                    current, maximum = win32.get_vcp(monitor.handle, code)
                    result = f"{current} of {maximum}"
                except win32.DdcError as exc:
                    result = str(exc)
                out(f"  VCP 0x{code:02X} {label}: {result} ({(time.perf_counter() - started) * 1000:.0f} ms)")
            started = time.perf_counter()
            try:
                minimum, current, maximum = win32.get_monitor_brightness(monitor.handle)
                result = f"{current} in {minimum}-{maximum}"
            except win32.DdcError as exc:
                result = str(exc)
            out(f"  GetMonitorBrightness: {result} ({(time.perf_counter() - started) * 1000:.0f} ms)")
        finally:
            win32.destroy_physical_monitor(monitor.handle)


def _wmi(out) -> None:
    try:
        import pythoncom
        import wmi
    except ImportError:
        out("The 'wmi' package is not installed (pip install wmi)")
        return
    pythoncom.CoInitialize()
    try:
        items = wmi.WMI(namespace="wmi").WmiMonitorBrightness()
    except Exception as exc:  # expected on desktops: the class has no instances
        out(f"WmiMonitorBrightness: {exc}")
        return
    if not items:
        out("No WMI brightness (normal on desktops; some laptops control the panel another way)")
    for item in items:
        levels = sorted(set(item.Level or ()))
        out(f"{item.InstanceName}: {item.CurrentBrightness}%, {len(levels)} levels{f' ({levels[0]}-{levels[-1]})' if levels else ''}")


def _gamma(out) -> None:
    import winreg

    from nightlight.core.colortemp import MIN_KELVIN, NEUTRAL_KELVIN, gamma_ramp, plausible_base, whitepoint
    from nightlight.hardware import win32

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM") as key:
            value, _type = winreg.QueryValueEx(key, "GdiIcmGammaRange")
        out(f"GdiIcmGammaRange registry value: {value}")
    except OSError:
        out("GdiIcmGammaRange registry value: not set (Windows limits how warm a ramp can be)")

    hdr = {t.gdi_name: t.hdr for t in win32.display_targets()}
    for name in win32.attached_display_names():
        original = win32.get_gamma_ramp(name)
        if original is None:
            out(f"{name}: could not read the gamma ramp")
            continue
        peaks = ", ".join(f"{c} {channel[-1] * 100 / 65535:.0f}%" for c, channel in zip("RGB", original))
        out(f"{name}: current ramp peaks {peaks}; {'kept as the base' if plausible_base(original) else 'looks tinted, not kept'}")
        if hdr.get(name):
            out("  HDR is on: Windows ignores gamma ramps, night light skips this display")
            continue
        warmest = None
        try:
            for kelvin in range(MIN_KELVIN, NEUTRAL_KELVIN + 1, 100):
                if win32.set_gamma_ramp(name, gamma_ramp(whitepoint(kelvin), win32.RAMP_SIZE, original)):
                    warmest = kelvin
                    break
        finally:
            restored = win32.set_gamma_ramp(name, original)
        out(f"  warmest night light Windows accepts: {f'{warmest} K' if warmest else 'none'}; original ramp restored: {restored}")


def _backend(out, kind: str) -> None:
    from nightlight.hardware import create_backends

    brightness, _gamma = create_backends(kind)
    try:
        monitors = brightness.list_monitors()
        if not monitors:
            out("No monitors")
        for info in monitors:
            kind = "built-in" if info.internal else "external"
            out(f"{info.index}: {info.name} [{info.key or 'no id'}, {kind}, control: {info.control if info.supported else 'none'}]")
            if info.supported:
                power = getattr(brightness, "is_powered_on", lambda index: None)(info.index)
                awake = {True: "on", False: "asleep", None: "unknown"}[power]
                out(f"  brightness {brightness.get_brightness(info.index)}%, power state {awake}")
            else:
                out(f"  not supported{f': {info.reason}' if info.reason else ''}")
    finally:
        if close := getattr(brightness, "close", None):
            close()
