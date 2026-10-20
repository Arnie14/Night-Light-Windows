# SPDX-License-Identifier: GPL-3.0-only
"""One-click diagnostics for bug reports ("Report a problem…" in the tray menu).

Monitorian's FAQ asks for its probe and operation logs; collecting them by
hand is where reports stall. This gathers what a report needs into one zip
in the Reports folder of Winlight's own data folder, and a short summary for
the clipboard to paste into an issue:
the app and Windows versions, each display and how it's controlled, night
light's state, and the logs and settings.

It's quick and safe to run any time: it reads what the app already knows and
cheap display queries, but doesn't scan DDC/CI or tint displays (that's
`--probe`, whose report is included if one was made).

Reports get posted publicly, so nothing personal goes in (see privacy.py):
no location, city or time zone, no custom display names (only that a
display was renamed), no names of apps listed for pausing night light or idle
dimming (only how many), and every file, logs included, is scrubbed of the user name,
computer name, home folder and email addresses.

Nothing is left behind for long: zips older than KEEP_DAYS are deleted from
where they are saved (only files named exactly as this module names them),
and the app clears the summary from the clipboard after CLIPBOARD_MINUTES
if it is still there.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import sys
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import PySide6
from PySide6.QtCore import QStandardPaths, qVersion
from PySide6.QtGui import QGuiApplication

from nightlight import APP_NAME, __version__, config
from nightlight.color.exemptions import describe as describe_pause
from nightlight.support.privacy import scrub

log = logging.getLogger(__name__)

ISSUES_URL = "https://github.com/Arnie14/WinLight/issues/new"
KEEP_DAYS = 7
CLIPBOARD_MINUTES = 10
# The names write_bundle gives, and only those, are cleaned up.
BUNDLE_NAME = re.compile(rf"{re.escape(APP_NAME)}-diagnostics-\d{{8}}-\d{{6}}\.zip")


@dataclass
class Sources:
    """What the report is made from; filled in by the app."""

    monitors: object  # MonitorManager
    controller: object  # NightLightController
    config: Callable[[], config.AppConfig]
    config_path: Path
    log_dir: Path | None
    skipped: Callable[[], set[str]] = lambda: set()
    last_wake: Callable[[], list[str]] = list
    compatibility: Callable[[], str] | None = None  # the compatibility report (compat.py)


def summary(sources: Sources) -> str:
    from nightlight.hardware import winsystem
    from nightlight.ui.night_light_text import night_light_status

    lines = [
        f"{APP_NAME} {__version__} ({'installed build' if getattr(sys, 'frozen', False) else 'from source'})",
        f"Windows: {platform.platform()}" + (f" (build {sys.getwindowsversion().build})" if sys.platform == "win32" else ""),
        f"Python {platform.python_version()}, PySide6 {PySide6.__version__}, Qt {qVersion()}, {QGuiApplication.platformName()}",
        f"Report made {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        "Displays (as the app sees them):",
    ]
    monitors = sources.monitors
    prefs = sources.config()
    for number, info in enumerate(monitors.monitors, 1):
        custom = prefs.display(info.key)
        name = info.name  # the display's own (model) name; a custom one may be personal
        live = monitors.state(info.key)
        if info.supported:
            state = f"{monitors.value(info.key)}% via {info.channel_label if info.channel else info.control}"
            if live is not None and not live.controllable:
                state += f" ({live.status.value}: {live.message})" if live.message else f" ({live.status.value})"
        else:
            state = f"not supported: {info.reason or 'no DDC/CI'}"
        flags = ", ".join(f for f in ("built-in" if info.internal else "", "renamed" if custom.name else "", "hidden" if custom.hidden else "") if f)
        lines.append(f"  {number}. {name} [{info.key}{', ' + flags if flags else ''}]: {state}")
    if not monitors.monitors:
        lines.append("  none detected")
    lines += _windows_displays()

    controller = sources.controller
    lines += ["", f"Night light: {night_light_status(controller)}, showing {controller.current_temperature} K"]
    if controller.paused:
        kinds = (describe_pause(why) for why in controller.paused.values())
        lines.append("  paused on: " + ", ".join(f"{d} (for {kind})" for d, kind in zip(controller.paused, kinds)))
    windows_night_light = winsystem.windows_night_light_on()
    lines.append(f"Windows Night Light: {'on' if windows_night_light else 'off' if windows_night_light is False else 'unknown'}")
    if skipped := sources.skipped():
        lines.append("Skipped after crashing or hanging: " + ", ".join(sorted(skipped)))
    if wake := sources.last_wake():
        lines += ["", "Last wake from sleep (seconds after waking: displays answering):", *(f"  {line}" for line in wake)]
    return scrub("\n".join(lines) + "\n")


def _windows_displays() -> list[str]:
    if sys.platform != "win32":
        return []
    from nightlight.hardware import win32

    lines = ["", "Displays (as Windows describes them):"]
    try:
        for target in win32.display_targets():
            hdr = ""
            if target.hdr:
                nits = win32.get_sdr_white_level(target)
                hdr = f", HDR on (SDR {nits:.0f} nits)" if nits else ", HDR on"
            ramp = win32.get_gamma_ramp(target.gdi_name)
            peaks = " ".join(f"{c}{channel[-1] * 100 // 65535}%" for c, channel in zip("RGB", ramp)) if ramp else "unreadable"
            lines.append(
                f"  {target.gdi_name}: {target.friendly_name or '(no name)'}, {target.connector}"
                f"{', built-in' if target.internal else ''}{hdr}, id {target.hardware_id or '?'}, gamma peaks {peaks}"
            )
    except Exception as exc:  # a report must never fail for this
        lines.append(f"  could not query: {exc}")
    return lines


def redacted_settings(app_config: config.AppConfig) -> dict:
    """The settings, minus anything personal: where, which apps, which names."""
    data = config._to_json(app_config)
    night_light = data["night_light"]
    located = night_light.pop("timezone") or (night_light["latitude"], night_light["longitude"]) != (0.0, 0.0)
    del night_light["latitude"], night_light["longitude"]
    night_light["location"] = ("a city" if app_config.night_light.timezone else "coordinates") if located else "not set"
    night_light["exempt_apps"] = f"{len(app_config.night_light.exempt_apps)} app(s)"
    data["general"]["idle_exempt_apps"] = f"{len(app_config.general.idle_exempt_apps)} app(s)"
    data["displays"] = {key: {"renamed": bool(value["name"]), "hidden": value["hidden"]} for key, value in data["displays"].items()}
    return data


def default_folder() -> Path:
    r"""Where reports are saved: Reports in Winlight's data folder
    (%LOCALAPPDATA%\WinLight\Reports). Not the Desktop, which OneDrive's
    folder backup often syncs to the cloud; nothing syncs local app data.
    "Report a Problem…" shows the file in Explorer, to attach from there."""
    return config.data_dir() / "Reports"


def legacy_folders() -> list[Path]:
    """Where versions before 0.3.0 saved reports, still cleaned up."""
    folders = []
    for location in (QStandardPaths.StandardLocation.DesktopLocation, QStandardPaths.StandardLocation.DownloadLocation):
        if folder := QStandardPaths.writableLocation(location):
            folders.append(Path(folder))
    return folders


def remove_old_bundles(folder: Path | None = None, now: float | None = None, days: int = KEEP_DAYS) -> list[Path]:
    """Delete diagnostics zips older than `days` from `folder`, or by default
    from the Reports folder and where older versions saved them (the
    Desktop, Downloads); returns what was deleted. Never raises."""
    if folder is None:
        removed = []
        for each in [default_folder(), *legacy_folders()]:
            removed += remove_old_bundles(each, now, days)
        return removed
    cutoff = (time.time() if now is None else now) - days * 86400
    removed = []
    try:
        candidates = [path for path in folder.iterdir() if BUNDLE_NAME.fullmatch(path.name)]
    except OSError:
        return removed
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
        except OSError:
            log.debug("Could not delete %s", path.name, exc_info=True)
    if removed:
        log.info("Deleted %d diagnostics file(s) older than %d days", len(removed), days)
    return removed


def write_bundle(sources: Sources, folder: Path | None = None) -> tuple[Path, str]:
    """Write the zip; returns its path and the summary (for the clipboard)."""
    text = summary(sources)
    folder = folder or default_folder()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{APP_NAME}-diagnostics-{datetime.now():%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("summary.txt", text)
        bundle.writestr("settings.json", scrub(json.dumps(redacted_settings(sources.config()), indent=2)))
        if sources.compatibility is not None:
            try:
                bundle.writestr("compatibility.txt", scrub(sources.compatibility()))
            except Exception:  # a report must never fail for this
                log.warning("Could not add the compatibility report", exc_info=True)
        if sources.log_dir is not None and sources.log_dir.is_dir():
            for file in sorted(sources.log_dir.iterdir()):
                if file.is_file() and (file.name.startswith("night-light.log") or file.name == "probe.txt"):
                    # Scrubbed again: lines from older versions weren't.
                    bundle.writestr(f"logs/{file.name}", scrub(file.read_text(encoding="utf-8", errors="replace")))
    log.info("Diagnostics saved to %s", path)
    return path, text
