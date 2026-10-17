# SPDX-License-Identifier: GPL-3.0-only
"""Decides what colour temperature night light should use at a given moment.

Pure logic with no Qt or hardware, so it is easy to test. The model follows
KDE's (KWin night light + knighttime): a schedule is a list of evening
(day -> night) and morning (night -> day) transitions for yesterday, today
and tomorrow, and the current state follows from the most recent transition
that has started, which handles schedules that cross midnight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from enum import StrEnum

from astral import Observer
from astral.sun import sun

from nightlight.config import Mode, NightLightConfig, parse_hhmm
from nightlight.core.colortemp import NEUTRAL_KELVIN

log = logging.getLogger(__name__)

# KDE's fallback when the sun doesn't rise or set (polar day or night).
FALLBACK_MORNING = time(6, 0)
FALLBACK_EVENING = time(18, 0)
FALLBACK_TRANSITION = timedelta(minutes=30)


@dataclass(frozen=True)
class Transition:
    start: datetime
    end: datetime
    to_night: bool


class OverrideKind(StrEnum):
    SUSPEND = "suspend"  # force day light
    ACTIVATE = "activate"  # force night light, even if night light is off


@dataclass(frozen=True)
class Override:
    """A temporary manual override, like KDE's "Suspend/Activate ... for 1 hour"."""

    kind: OverrideKind
    until: datetime | None  # None: until manually ended

    def active_at(self, now: datetime) -> bool:
        return self.until is None or now < self.until


@dataclass(frozen=True)
class NightLightState:
    temperature: int
    night_factor: float  # 0.0 = full day, 1.0 = full night
    transitioning: bool
    next_transition: Transition | None
    override: Override | None = None  # the override in effect, if any

    @property
    def next_change(self) -> datetime | None:
        return self.next_transition.start if self.next_transition else None


def compute_state(config: NightLightConfig, now: datetime, override: Override | None = None) -> NightLightState:
    """`now` must be timezone-aware."""
    if override is not None and override.active_at(now):
        if override.kind is OverrideKind.ACTIVATE:
            return NightLightState(config.night_temperature, 1.0, False, None, override)
        # Like KWin, suspending returns to the *day* temperature, not neutral.
        return NightLightState(config.day_temperature, 0.0, False, None, override)
    if not config.enabled:
        return NightLightState(NEUTRAL_KELVIN, 0.0, False, None)
    if config.mode is Mode.ALWAYS:
        return NightLightState(config.night_temperature, 1.0, False, None)

    transitions = schedule_around(config, now)
    factor, transitioning = 0.0, False
    started = [t for t in transitions if t.start <= now]
    if started:
        current = started[-1]
        if now < current.end:
            progress = (now - current.start) / (current.end - current.start)
            factor = progress if current.to_night else 1.0 - progress
            transitioning = True
        else:
            factor = 1.0 if current.to_night else 0.0
    upcoming = next((t for t in transitions if t.start > now), None)

    temperature = round(config.day_temperature + (config.night_temperature - config.day_temperature) * factor)
    return NightLightState(temperature, factor, transitioning, upcoming)


def next_transition(config: NightLightConfig, now: datetime, to_night: bool) -> Transition | None:
    """The next evening (to_night) or morning transition of the configured
    day-night cycle, even when night light itself is off or always on. Used
    for "until this evening" / "until tomorrow morning" overrides."""
    return next((t for t in schedule_around(config, now) if t.start > now and t.to_night == to_night), None)


def schedule_around(config: NightLightConfig, now: datetime) -> list[Transition]:
    days = [now.date() + timedelta(days=offset) for offset in (-1, 0, 1, 2)]
    if config.mode is Mode.LOCATION:
        transitions = _solar_schedule(config.latitude, config.longitude, days, now.tzinfo)
        if transitions is None:
            log.debug("No sunrise/sunset at (%s, %s); using 06:00/18:00", config.latitude, config.longitude)
            transitions = _timed_schedule(FALLBACK_MORNING, FALLBACK_EVENING, FALLBACK_TRANSITION, days, now.tzinfo)
    else:
        duration = timedelta(minutes=config.transition_minutes)
        transitions = _timed_schedule(parse_hhmm(config.day_start), parse_hhmm(config.night_start), duration, days, now.tzinfo)
    return sorted(transitions, key=lambda t: t.start)


def _timed_schedule(morning: time, evening: time, duration: timedelta, days: list[date], tz: tzinfo | None) -> list[Transition]:
    transitions = []
    for day in days:
        start_morning = datetime.combine(day, morning, tz)
        start_evening = datetime.combine(day, evening, tz)
        transitions += [Transition(start_morning, start_morning + duration, False), Transition(start_evening, start_evening + duration, True)]
    return transitions


def _solar_schedule(latitude: float, longitude: float, days: list[date], tz: tzinfo | None) -> list[Transition] | None:
    """Morning: civil dawn -> sunrise. Evening: sunset -> civil dusk (as knighttime).

    Returns None if any day lacks these events, so the whole schedule falls
    back consistently instead of mixing solar and fixed days.
    """
    observer = Observer(latitude, longitude)
    transitions = []
    for day in days:
        try:
            # astral's dawn/dusk default to civil twilight (sun 6° below the horizon).
            events = sun(observer, date=day, tzinfo=tz)
        except ValueError:
            return None
        transitions += [Transition(events["dawn"], events["sunrise"], False), Transition(events["sunset"], events["dusk"], True)]
    return transitions
