# SPDX-License-Identifier: GPL-3.0-only
"""Night Light status text and override menus.

Wording follows KDE Plasma's Brightness and Color applet and Night Light page.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from PySide6.QtWidgets import QMenu

from nightlight.color.controller import NightLightController
from nightlight.config import Mode
from nightlight.core.schedule import OverrideKind, next_transition


def is_night_light_on(controller: NightLightController) -> bool:
    """Whether the screen is (or is becoming) warmer than day light."""
    return controller.state.night_factor > 0


def night_light_status(controller: NightLightController) -> str:
    config, state = controller.config, controller.state
    if state.override is not None:
        until = state.override.until
        if state.override.kind is OverrideKind.ACTIVATE:
            return "Activated until manually disabled" if until is None else f"Activated until: {_format_until(until, controller.now())}"
        return "Suspended until manually resumed" if until is None else f"Suspended until: {_format_until(until, controller.now())}"
    if not config.enabled:
        return "Not enabled"
    if config.mode is Mode.ALWAYS:
        phase = "On"
    elif state.transitioning:
        # The transition in progress is the opposite of the upcoming one.
        evening = state.next_transition is not None and not state.next_transition.to_night
        phase = "Evening Transition" if evening else "Morning Transition"
    else:
        phase = "Night" if state.night_factor >= 1.0 else "Day"
    return f"{phase} · {state.temperature}K"


def paused_text(controller: NightLightController, display_name: Callable[[str], str] | None = None) -> str:
    """"Paused on DELL U2720Q for photoshop.exe" while night light is paused
    on a display for an app. `display_name` gives a GDI name's display name."""
    if not controller.paused or not is_night_light_on(controller):
        return ""
    name = display_name or display_label
    parts = []
    for display, reason in sorted(controller.paused.items()):
        app, _, kind = reason.partition(", ")
        known = app not in ("", "an app")
        if kind == "fullscreen":
            what = f"a fullscreen {app}" if known else "a fullscreen app"
        elif kind == "automatic":
            what = f"{app} (automatic)" if known else "an app (automatic)"
        else:
            what = app
        parts.append(f"{name(display)} for {what}")
    return "Paused on " + "; ".join(parts)


def brightness_status(scheduler) -> str:
    """The brightness schedule now, like night light's status: "70% → 40% at
    20:00" (the next change), "55% → 40%, changing until 20:30" during one;
    "" while it is off. `scheduler` is a BrightnessScheduler, or None."""
    state = getattr(scheduler, "state", None)
    if state is None or not scheduler.config.enabled:
        return ""
    if state.transitioning and state.level != state.since.level:
        end = f", changing until {_hhmm(state.transition_end)}" if state.transition_end is not None else ", changing"
        return f"{state.level}% → {state.since.level}%{end}"
    if state.next is not None and state.next.level != state.level:
        return f"{state.level}% → {state.next.level}% at {_hhmm(state.next.at)}"
    return f"{state.level}%"


def display_label(gdi_name: str) -> str:
    """A GDI name such as DISPLAY2 -> "display 2"."""
    number = gdi_name.rsplit("DISPLAY", 1)[-1]
    return f"display {number}" if number.isdigit() else gdi_name


def schedule_description(controller: NightLightController) -> str:
    """When the next changes happen, as KDE's Night Light page says it."""
    config, now = controller.config, controller.now()
    if not config.enabled or config.mode is Mode.ALWAYS:
        return ""
    lines = []
    evening = next_transition(config, now, to_night=True, zone=controller.zone)
    morning = next_transition(config, now, to_night=False, zone=controller.zone)
    if evening:
        lines.append(f"Night light turns on at {_hhmm(evening.start)}." if evening.start == evening.end else
                     f"Night light starts warming at {_hhmm(evening.start)} and is fully on by {_hhmm(evening.end)}.")
    if morning:
        lines.append(f"Night light turns off at {_hhmm(morning.start)}." if morning.start == morning.end else
                     f"Day light starts returning at {_hhmm(morning.start)} and is fully back by {_hhmm(morning.end)}.")
    return "\n".join(lines)


def build_override_menu(controller: NightLightController, menu: QMenu) -> str:
    """Fill `menu` with KDE's override choices; returns the button label.

    While an override is active the label is "Resume"/"Turn Off" and the menu
    is left empty: the button ends the override directly.
    """
    menu.clear()
    state, now = controller.state, controller.now()
    if state.override is not None:
        return "Resume" if state.override.kind is OverrideKind.SUSPEND else "Turn Off"

    if is_night_light_on(controller):
        kind, to_night, label = OverrideKind.SUSPEND, True, "Suspend"
        until_label = "Until {day} evening"
        manual_label = "Until manually resumed"
    else:
        kind, to_night, label = OverrideKind.ACTIVATE, False, "Activate"
        until_label = "Until {day} morning"
        manual_label = "Until manually disabled"

    menu.addAction("For 1 hour", lambda: controller.set_override(kind, controller.now() + timedelta(hours=1)))
    if transition := next_transition(controller.config, now, to_night, zone=controller.zone):
        day = "this" if transition.start.date() == now.date() else "tomorrow"
        menu.addAction(until_label.format(day=day), lambda: controller.set_override(kind, transition.start))
    menu.addAction(manual_label, lambda: controller.set_override(kind, None))
    return label


def _hhmm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def _format_until(moment: datetime, now: datetime) -> str:
    return _hhmm(moment) if moment.date() == now.date() else f"{moment:%a} {_hhmm(moment)}"
