# SPDX-License-Identifier: GPL-3.0-only
"""What the running app does for the command line (`winlight-cli`).

Shaped by what users asked Monitorian and Twinkle Tray for: get and set
brightness per display or for all, relative changes (+10/-10) for scripts
and hotkey tools, refreshing displays (Twinkle Tray #1118), changes that
fade rather than jump (#1214), display numbers that match what users see
(#628: here, the order of the panel and `--list`), machine-readable output,
and night light control.

Requests and replies are dicts (JSON over the single-instance pipe); see cli.py.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from nightlight.brightness.monitors import MonitorManager
from nightlight.color.controller import NightLightController
from nightlight.config import AppConfig
from nightlight.core.schedule import OverrideKind
from nightlight.hardware.brightness import MonitorInfo
from nightlight.ui.night_light_text import is_night_light_on, night_light_status

log = logging.getLogger(__name__)

ALL = "all"


class CommandHandler:
    def __init__(self, monitors: MonitorManager, controller: NightLightController, config: Callable[[], AppConfig],
                 gamma=None) -> None:
        self._monitors = monitors
        self._controller = controller
        self._config = config
        self._gamma = gamma  # for calibrations (the Windows gamma backend)

    def handle(self, request: dict) -> dict:
        command = request.get("command")
        try:
            if command == "list":
                return self._list()
            if command == "get":
                return self._get(request.get("display", ALL))
            if command == "set":
                return self._set(str(request.get("value", "")), request.get("display", ALL))
            if command == "night-light":
                return self._night_light(request.get("action", "status"))
            if command == "calibration":
                return self._calibration(request.get("action", "status"))
            if command == "refresh":
                self._monitors.retry()
                return _ok("Detecting displays again.")
            if command == "panel":
                return _ok("")  # the app shows the panel (see NightLightApp.handle_request)
        except _Error as exc:
            return {"ok": False, "output": str(exc)}
        return {"ok": False, "output": f"Unknown command {command!r}."}

    # --- Commands ---------------------------------------------------------

    def _list(self) -> dict:
        displays = [self._describe(n, m) for n, m in enumerate(self._monitors.monitors, 1)]
        lines = [_line(d) for d in displays] or ["No displays detected yet."]
        lines.append(f"Night light: {night_light_status(self._controller)}")
        return _ok("\n".join(lines), {"displays": displays, "night_light": self._night_light_data()})

    def _get(self, selector: str) -> dict:
        displays = [self._describe(n, m) for n, m in self._select(selector, include_hidden=True)]
        output = "\n".join(f"{d['brightness']}" if len(displays) == 1 else _line(d) for d in displays)
        return _ok(output, {"displays": displays})

    def _set(self, value: str, selector: str) -> dict:
        relative = value[:1] in ("+", "-")
        try:
            amount = int(value)
        except ValueError:
            raise _Error(f"Brightness must be a number from 0 to 100, or +N/-N for a change: not {value!r}.") from None
        changed, not_now = [], []
        for number, monitor in self._select(selector, include_hidden=selector != ALL):
            if not monitor.supported:
                if selector != ALL:
                    raise _Error(f"{self._name(monitor)} has no brightness control.")
                continue
            if (why := self._why_not_now(monitor)) is not None:
                # Not responding, in standby, still being checked: a write now
                # would be dropped, so say so rather than pretend.
                if selector != ALL:
                    raise _Error(f"{self._name(monitor)} can't be changed right now: {why}.")
                not_now.append(f"{self._name(monitor)} ({why})")
                continue
            current = self._monitors.value(monitor.key)
            if relative and current is None:
                raise _Error(f"The brightness of {self._name(monitor)} isn't known yet; try again in a moment.")
            target = (current + amount) if relative else amount
            target = max(monitor.minimum, min(100, target))
            self._monitors.fade_brightness(monitor.key, target)
            changed.append(self._describe(number, monitor) | {"brightness": target})
        if not changed:
            if not_now:  # e.g. while the PC sleeps or the displays are off
                raise _Error(f"No display can be changed right now: {', '.join(not_now)}.")
            raise _Error("No display with brightness control matched.")
        return _ok("\n".join(_line(d) for d in changed), {"displays": changed})

    def _night_light(self, action: str) -> dict:
        controller = self._controller
        if action == "toggle":
            action = "off" if is_night_light_on(controller) else "on"
        if action == "suspend":
            controller.set_override(OverrideKind.SUSPEND, None)
        elif action == "resume":
            controller.clear_override()
        elif action == "on":
            controller.set_override(OverrideKind.ACTIVATE, None)
        elif action == "off":
            controller.set_override(OverrideKind.SUSPEND, None)
        elif action == "auto":
            controller.clear_override()
        elif action != "status":
            raise _Error(f"Night light action must be on, off, toggle, suspend, resume, auto or status: not {action!r}.")
        return _ok(f"Night light: {night_light_status(controller)}", {"night_light": self._night_light_data()})

    def _calibration(self, action: str) -> dict:
        """Each display's color calibration: how it stands, reloaded from its
        profile, or the remembered ones forgotten."""
        if action == "forget":
            count = self._calibration_call("forget_calibrations")
            return _ok(f"Forgot {count} remembered calibration{'s' if count != 1 else ''}.", {"forgotten": count})
        if action == "reload":
            results = self._calibration_call("reload_calibrations")
            displays = {self._gdi_name(name): outcome for name, outcome in results.items()}
            return _ok("\n".join(f"{name}: {outcome}" for name, outcome in displays.items()) or "No displays.",
                       {"calibrations": displays})
        if action != "status":
            raise _Error(f"Calibration action must be status, reload or forget: not {action!r}.")
        displays = {self._gdi_name(name): text for name, text in self._calibration_call("calibration_status").items()}
        return _ok("\n".join(f"{name}: {text}" for name, text in displays.items()) or "No displays.", {"calibrations": displays})

    def _calibration_call(self, name: str):
        method = getattr(self._gamma, name, None)
        if method is None:
            raise _Error("Calibrations are only handled on Windows.")
        return method()

    def _gdi_name(self, gdi_name: str) -> str:
        """A display's name from its GDI name, as the panel shows it."""
        monitor = next((m for m in self._monitors.monitors if m.display == gdi_name), None)
        return self._name(monitor) if monitor is not None else gdi_name

    # --- Helpers ----------------------------------------------------------

    def _select(self, selector, include_hidden: bool) -> list[tuple[int, MonitorInfo]]:
        """Displays by panel number (1-based), id, name (part of it, any case,
        custom names too) or "all" (not the hidden ones)."""
        numbered = list(enumerate(self._monitors.monitors, 1))
        if not numbered:
            raise _Error("No displays detected yet.")
        selector = str(selector).strip()
        if selector.lower() == ALL:
            return [(n, m) for n, m in numbered if include_hidden or not self._hidden(m)]
        if selector.isdigit():
            matches = [(n, m) for n, m in numbered if n == int(selector)]
        else:
            wanted = selector.lower()
            matches = [(n, m) for n, m in numbered if m.key.lower() == wanted]
            # Part of a name (custom ones too) or of an id, as Twinkle Tray's --MonitorID takes.
            matches = matches or [(n, m) for n, m in numbered if wanted in self._name(m).lower() or wanted in m.key.lower()]
        if not matches:
            raise _Error(f"No display matches {selector!r}. See `winlight-cli --list`.")
        if len(matches) > 1:
            names = ", ".join(self._name(m) for _, m in matches)
            raise _Error(f"{selector!r} matches several displays ({names}); use its number or id.")
        return matches

    def _status(self, monitor: MonitorInfo) -> str:
        """"ok", or why its brightness can't be changed now ("not responding", "unsupported", ...)."""
        state = self._monitors.state(monitor.key)
        return state.status.value if state is not None else ("ok" if monitor.supported else "unsupported")

    def _why_not_now(self, monitor: MonitorInfo) -> str | None:
        state = self._monitors.state(monitor.key)
        if state is None or state.controllable:
            return None
        return (state.message or state.status.value).rstrip(".…").lower()

    def _name(self, monitor: MonitorInfo) -> str:
        return self._config().display(monitor.key).name or monitor.name

    def _hidden(self, monitor: MonitorInfo) -> bool:
        return self._config().display(monitor.key).hidden

    def _describe(self, number: int, monitor: MonitorInfo) -> dict:
        return {
            "number": number,
            "name": self._name(monitor),
            "id": monitor.key,
            "brightness": self._monitors.value(monitor.key) if monitor.supported else None,
            "control": monitor.control if monitor.supported else None,
            "status": self._status(monitor),
            "hidden": self._hidden(monitor),
        }

    def _night_light_data(self) -> dict:
        controller = self._controller
        override = controller.override
        return {
            "on": is_night_light_on(controller),
            "temperature": controller.current_temperature,
            "override": override.kind.value if override else None,
            "paused_on": dict(controller.paused),
        }


class _Error(Exception):
    pass


def _ok(output: str, data: dict | None = None) -> dict:
    return {"ok": True, "output": output, "data": data or {}}


def _line(display: dict) -> str:
    level = "-" if display["brightness"] is None else f"{display['brightness']}%"
    extra = {"sdr": "SDR brightness", "software": "software dimming", None: "no brightness control"}.get(display["control"], "")
    hidden = ", hidden" if display["hidden"] else ""
    return f"{display['number']:>2}  {display['name']:<28} {level:>5}  {display['id']}{f'  ({extra}{hidden})' if extra or hidden else ''}"

