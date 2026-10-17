"""Decides what colour temperature night light should use at a given moment.

Pure logic with no Qt or hardware, so it is easy to test. The schedule is a
list of transitions (evening: day -> night, morning: night -> day) for
yesterday, today and tomorrow; the current state follows from the most recent
transition that has started, which handles schedules that cross midnight.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

from astral import Observer
from astral.sun import sun

from nightlight.config import Mode, NightLightConfig, parse_hhmm
from nightlight.core.colortemp import NEUTRAL_KELVIN

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Transition:
    start: datetime
    end: datetime
    to_night: bool


@dataclass(frozen=True)
class NightLightState:
    temperature: int
    night_factor: float  # 0.0 = full day, 1.0 = full night
    transitioning: bool
    next_change: datetime | None  # when the next transition starts


def compute_state(config: NightLightConfig, now: datetime) -> NightLightState:
    """`now` must be timezone-aware."""
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
    next_change = next((t.start for t in transitions if t.start > now), None)

    temperature = round(config.day_temperature + (config.night_temperature - config.day_temperature) * factor)
    return NightLightState(temperature, factor, transitioning, next_change)


def schedule_around(config: NightLightConfig, now: datetime) -> list[Transition]:
    duration = timedelta(minutes=config.transition_minutes)
    transitions = []
    for offset in (-1, 0, 1):
        day = now.date() + timedelta(days=offset)
        if config.mode is Mode.TIMES:
            evening = datetime.combine(day, parse_hhmm(config.night_start), now.tzinfo)
            morning = datetime.combine(day, parse_hhmm(config.day_start), now.tzinfo)
            transitions += [Transition(evening, evening + duration, True), Transition(morning, morning + duration, False)]
        elif config.mode is Mode.LOCATION:
            times = _sun_times(config.latitude, config.longitude, day, now.tzinfo)
            if times is None:
                continue
            sunrise, sunset = times
            # Warm up starting at sunset; be back to day light by sunrise.
            transitions += [Transition(sunset, sunset + duration, True), Transition(sunrise - duration, sunrise, False)]
    return sorted(transitions, key=lambda t: t.start)


def _sun_times(latitude: float, longitude: float, day: date, tz: tzinfo | None) -> tuple[datetime, datetime] | None:
    try:
        events = sun(Observer(latitude, longitude), date=day, tzinfo=tz)
    except ValueError:
        # Polar day or night: the sun doesn't rise or set. Treated as day.
        log.debug("No sunrise/sunset at (%s, %s) on %s", latitude, longitude, day)
        return None
    return events["sunrise"], events["sunset"]
