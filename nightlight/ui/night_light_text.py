"""Night Light status text and override menus.

Wording follows KDE Plasma's Brightness and Color applet and Night Light page.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtWidgets import QMenu

from nightlight.config import Mode
from nightlight.controller import NightLightController
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


def schedule_description(controller: NightLightController) -> str:
    """When the next changes happen, worded like KDE's Night Light page."""
    config, now = controller.config, controller.now()
    if not config.enabled or config.mode is Mode.ALWAYS:
        return ""
    lines = []
    evening = next_transition(config, now, to_night=True)
    morning = next_transition(config, now, to_night=False)
    if evening:
        lines.append(f"Color temperature begins changing to night time at {_hhmm(evening.start)} and is fully changed by {_hhmm(evening.end)}")
    if morning:
        lines.append(f"Color temperature begins changing to day time at {_hhmm(morning.start)} and is fully changed by {_hhmm(morning.end)}")
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
    if transition := next_transition(controller.config, now, to_night):
        day = "this" if transition.start.date() == now.date() else "tomorrow"
        menu.addAction(until_label.format(day=day), lambda: controller.set_override(kind, transition.start))
    menu.addAction(manual_label, lambda: controller.set_override(kind, None))
    return label


def _hhmm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def _format_until(moment: datetime, now: datetime) -> str:
    return _hhmm(moment) if moment.date() == now.date() else f"{moment:%a} {_hhmm(moment)}"
