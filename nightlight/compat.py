# SPDX-License-Identifier: GPL-3.0-only
"""Which displays work with Winlight, and how: the list in Settings > Displays,
and the hardware compatibility report users can copy or save.

Built only from what the app already knows (detection, the EDID in the
registry, each display's live state), so producing it sends nothing to the
displays: DDC/CI stays conservative. `--probe` is the deeper diagnostic that
does ask each display and API directly.
"""

from __future__ import annotations

import platform
import textwrap
import sys
from dataclasses import dataclass
from datetime import datetime, timezone

from nightlight import APP_NAME, __version__
from nightlight.hardware.brightness import CHANNEL_LABELS, MonitorInfo
from nightlight.privacy import scrub

# The ways in on Windows, for the report's last line (the others are the
# Linux development backends').
WINDOWS_CHANNELS = ("ddc", "ddc-high-level", "wmi", "sdr", "software")
# What to try for each way a display can fall short.
ADVICE = {
    "unsupported": "Turn DDC/CI on in the display's own menu, and connect it directly (no adapter, hub or dock); "
    "or turn on Settings > Brightness > Dim displays without brightness control.",
    "not responding": "A call to it hung. Try another cable or port; if it keeps happening, turn DDC/CI off in its menu.",
    "retrying": "Usually it answers a few seconds after waking or being plugged in.",
}


@dataclass(frozen=True)
class CompatibilityEntry:
    name: str
    key: str
    manufacturer: str
    model: str
    serial: str  # partly hidden
    connector: str
    built_in: bool
    hdr: bool
    year: int
    diagonal_inches: float
    brightness: str  # how brightness is controlled, e.g. "DDC/CI"; "None" if it isn't
    works: bool
    status: str  # "Works", or a short reason it doesn't now
    detail: str
    night_light: str
    why: str = ""  # how its brightness is controlled, and why

    @property
    def summary(self) -> str:
        return f"{self.brightness}" if self.works else f"Not controllable: {self.status}"


def advice_for(status: str) -> str:
    status = status.lower()
    if "not responding" in status or "stuck" in status:
        return ADVICE["not responding"]
    if "retrying" in status or "not answering yet" in status or "waking" in status:
        return ADVICE["retrying"]
    return ADVICE["unsupported"]


def mask_serial(serial: str) -> str:
    """Enough of a serial to tell identical displays apart, not the whole thing."""
    return "" if not serial else serial if len(serial) <= 4 else f"…{serial[-4:]}"


def entries(monitors: list[MonitorInfo], states: dict | None = None, warmest: dict[str, int] | None = None) -> list[CompatibilityEntry]:
    """One entry per display. `states` are MonitorManager.states (for live
    status); `warmest` maps a GDI name to the warmest night light Windows
    accepted there, where it refused warmer."""
    states, warmest = states or {}, warmest or {}
    result = []
    for info in monitors:
        details = info.details
        state = states.get(info.key)
        works = info.supported and (state is None or state.controllable or state.status.value in ("reading", "detecting", "paused"))
        if works:
            status, detail = "Works", ""
        elif state is not None and info.supported:
            status, detail = state.message or state.status.value, state.detail
        else:
            status, detail = info.reason or "No brightness control", info.detail
        hdr = bool(details and details.hdr)
        source = details.source if details else ""
        if hdr:
            night_light = "No: HDR is on, and Windows ignores gamma ramps then"
        elif source in warmest:
            night_light = f"Yes, down to {warmest[source]} K (Windows' limit; GdiIcmGammaRange lifts it)"
        else:
            night_light = "Yes"
        result.append(
            CompatibilityEntry(
                name=info.name,
                key=info.key,
                manufacturer=details.manufacturer if details else "",
                model=details.model if details else "",
                serial=mask_serial(details.serial if details else ""),
                connector=details.connector if details else "",
                built_in=info.internal,
                hdr=hdr,
                year=details.year if details else 0,
                diagonal_inches=details.diagonal_inches if details else 0.0,
                brightness=CHANNEL_LABELS.get(info.channel if info.supported else "", info.channel),
                works=works,
                status=status,
                detail=detail,
                night_light=night_light,
                why=info.why,
            )
        )
    return result


def system_summary() -> dict[str, str]:
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion

    summary = {
        APP_NAME: __version__,
        "System": platform.platform(),
        "Running": "installed build" if getattr(sys, "frozen", False) else "from source",
        "Python": f"{platform.python_version()} ({platform.architecture()[0]})",
        "Qt": f"{qVersion()} (PySide6 {pyside_version})",
    }
    if sys.platform == "win32":
        from nightlight.hardware import win32

        for name, value in win32.capabilities().items():
            if name != "windows":
                summary[name] = ", ".join(value) if isinstance(value, list) else str(value)
    return summary


def render(items: list[CompatibilityEntry], system: dict[str, str] | None = None) -> str:
    """The report as plain text, readable in an issue or an email."""
    # In UTC: a local time zone's name tells where the user is (privacy.py).
    lines = [f"{APP_NAME} hardware compatibility report", f"Created {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", ""]
    if system:
        width = max(len(k) for k in system)
        lines += [f"  {k.ljust(width)}  {v}" for k, v in system.items()] + [""]
    working = sum(1 for e in items if e.works)
    lines.append(f"Displays: {len(items)}, {working} with brightness control")
    for number, e in enumerate(items, 1):
        lines += ["", f"{number}. {e.name}" + (" (built-in)" if e.built_in else "")]
        facts = [
            ("Brightness", e.brightness if e.works else f"not controllable: {e.status}"),
            ("How", e.why),
            ("Night light", e.night_light),
            ("Connection", e.connector),
            ("Manufacturer", e.manufacturer),
            ("Model", e.model),
            ("Serial", e.serial),
            ("Made", str(e.year) if e.year else ""),
            ("Size", f'{e.diagonal_inches:g}"' if e.diagonal_inches else ""),
            ("HDR", "on" if e.hdr else "off"),
            ("Identity", e.key),
        ]
        lines += [textwrap.fill(f"   {label + ':':<14}{value}", width=100, subsequent_indent=" " * 17) for label, value in facts if value]
        if not e.works:
            if e.detail:
                lines.append(f"   {'Why:':<14}{e.detail}")
            lines.append(f"   {'Try:':<14}{advice_for(e.status)}")
    lines += ["", f"Brightness is reached through: {', '.join(CHANNEL_LABELS[k] for k in WINDOWS_CHANNELS)}."]
    # Shared in bug reports: no user, computer or home names, serials shortened (privacy.py).
    return scrub("\n".join(lines) + "\n")
