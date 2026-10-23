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
from functools import lru_cache

from astral import Observer
from astral.sun import elevation
from astral.sun import noon as solar_noon

from nightlight.config import Mode, NightLightConfig, parse_hhmm
from nightlight.core.clock import days_around, local, wall_time
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


def compute_state(config: NightLightConfig, now: datetime, override: Override | None = None, zone: tzinfo | None = None) -> NightLightState:
    """`now` must be timezone-aware. `zone` is the local time zone, for days
    on the other side of a daylight saving change (see core/clock.py);
    without it, now's offset is used for every day."""
    if override is not None and override.active_at(now):
        if override.kind is OverrideKind.ACTIVATE:
            return NightLightState(config.night_temperature, 1.0, False, None, override)
        # Like KWin, suspending returns to the *day* temperature, not neutral.
        return NightLightState(config.day_temperature, 0.0, False, None, override)
    if not config.enabled:
        return NightLightState(NEUTRAL_KELVIN, 0.0, False, None)
    if config.mode is Mode.ALWAYS:
        return NightLightState(config.night_temperature, 1.0, False, None)

    transitions = schedule_around(config, now, zone)
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


def next_transition(config: NightLightConfig, now: datetime, to_night: bool, zone: tzinfo | None = None) -> Transition | None:
    """The next evening (to_night) or morning transition of the configured
    day-night cycle, even when night light itself is off or always on. Used
    for "until this evening" / "until tomorrow morning" overrides."""
    return next((t for t in schedule_around(config, now, zone) if t.start > now and t.to_night == to_night), None)


def pausing_held(config: NightLightConfig, now: datetime, zone: tzinfo | None = None) -> bool:
    """Whether "never pause late at night" holds at `now`: from the evening
    transition's start ("evening"), its end ("night") or a set time ("time")
    until the next morning transition starts. With night light always on,
    the custom times stand for evening and morning (as for overrides)."""
    if not config.never_pause_at_night:
        return False
    transitions = schedule_around(config, now, zone)
    if config.never_pause_from == "time":
        at = parse_hhmm(config.never_pause_time)
        starts = [wall_time(day, at, zone or now.tzinfo) for day in days_around(now, zone)]
    else:
        starts = [t.start if config.never_pause_from == "evening" else t.end for t in transitions if t.to_night]
    since = max((start for start in starts if start <= now), default=None)
    if since is None:
        return False
    morning = next((t.start for t in transitions if not t.to_night and t.start > since), None)
    return morning is None or now < morning


def schedule_around(config: NightLightConfig, now: datetime, zone: tzinfo | None = None) -> list[Transition]:
    days = days_around(now, zone)
    tz = zone or now.tzinfo
    if config.mode is Mode.LOCATION:
        minutes = config.solar_transition_minutes
        transitions = _solar_schedule(config.latitude, config.longitude, days, tz, minutes)
        if transitions is None:
            log.debug("No sunrise/sunset at the location today; using 06:00/18:00")  # no coordinates: logs are shared
            duration = FALLBACK_TRANSITION if minutes < 0 else timedelta(minutes=minutes)
            transitions = _timed_schedule(FALLBACK_MORNING, FALLBACK_EVENING, duration, days, tz)
    else:
        duration = timedelta(minutes=config.transition_minutes)
        transitions = _timed_schedule(parse_hhmm(config.day_start), parse_hhmm(config.night_start), duration, days, tz)
    return sorted(transitions, key=lambda t: t.start)


def _timed_schedule(morning: time, evening: time, duration: timedelta, days: list[date], tz: tzinfo) -> list[Transition]:
    transitions = []
    for day in days:
        for at, to_night in ((morning, False), (evening, True)):
            start = wall_time(day, at, tz)
            transitions.append(Transition(start, local(start + duration, tz), to_night))
    return transitions


def _solar_schedule(latitude: float, longitude: float, days: list[date], tz: tzinfo | None,
                    minutes: int = -1) -> list[Transition] | None:
    """Morning: civil dawn -> sunrise. Evening: sunset -> civil dusk (as knighttime).
    With `minutes` >= 0, a fixed length instead: the evening change starts at
    sunset, the morning one ends at sunrise (0: at once).

    In a white night (Reykjavik from May to July: the sun sets but never
    gets 6 degrees below the horizon) there is no civil dusk or dawn; the
    darkest moment, solar midnight, stands for both, as LightBulb takes the
    closest point when the sun doesn't reach an angle. A fixed length ends
    there at the latest, so evening and morning never overlap.

    Returns None if the sun doesn't set or rise on any of the days (polar
    day or night), so the whole schedule falls back consistently instead of
    mixing solar and fixed days.
    """
    transitions = []
    for day in days:
        events = solar_events(latitude, longitude, day)
        if events is None:
            return None
        midnight_before, dawn, sunrise, sunset, dusk, midnight_after = (local(e, tz) for e in events)
        if minutes >= 0:
            length = timedelta(minutes=minutes)
            dawn, dusk = max(sunrise - length, midnight_before), min(sunset + length, midnight_after)
        transitions += [Transition(dawn, sunrise, False), Transition(sunset, dusk, True)]
    return transitions


# The sun's centre this far below the horizon: sunrise and sunset (with
# refraction and the sun's radius, as astral and knighttime use), and civil
# twilight's end.
SUNRISE_ANGLE = -0.833
CIVIL_ANGLE = -6.0
_BISECTIONS = 22  # 12 h / 2^22: well under a second


def solar_events(latitude: float, longitude: float, day: date) -> tuple[datetime, ...] | None:
    """See _solar_day; for the brightness schedule's sunrise and sunset too."""
    return _solar_day(round(latitude, 4), round(longitude, 4), day)


@lru_cache(maxsize=64)
def _solar_day(latitude: float, longitude: float, day: date) -> tuple[datetime, ...] | None:
    """For `day`'s solar noon (UTC): the solar midnight before, civil dawn,
    sunrise, sunset, civil dusk and the solar midnight after; None if the sun
    doesn't rise or set that day. Found as the moments the sun's elevation
    passes each angle between solar midnight and noon, so an event just
    past midnight in some time zone is no different from any other (astral's
    per-date functions refuse those, and refuse a dusk that never comes)."""
    observer = Observer(latitude, longitude)
    noon = solar_noon(observer, day)
    # Solar midnight: halfway between noons, so one day's evening ends exactly
    # where the next morning starts.
    before = noon - (noon - solar_noon(observer, day - timedelta(days=1))) / 2
    after = noon + (solar_noon(observer, day + timedelta(days=1)) - noon) / 2
    sunrise, sunset = _crossing(observer, before, noon, SUNRISE_ANGLE), _crossing(observer, noon, after, SUNRISE_ANGLE)
    if sunrise is None or sunset is None:
        return None
    dawn = _crossing(observer, before, sunrise, CIVIL_ANGLE) or before
    dusk = _crossing(observer, sunset, after, CIVIL_ANGLE) or after
    return before, dawn, sunrise, sunset, dusk, after


def _crossing(observer: Observer, start: datetime, end: datetime, angle: float) -> datetime | None:
    """When the sun's elevation passes `angle` between `start` and `end`, over
    which it only rises or only sets; None if it stays on one side."""
    first = elevation(observer, start, with_refraction=False) - angle
    if first * (elevation(observer, end, with_refraction=False) - angle) > 0:
        return None
    low, high = start, end
    for _ in range(_BISECTIONS):
        middle = low + (high - low) / 2
        if (elevation(observer, middle, with_refraction=False) - angle) * first > 0:
            low = middle
        else:
            high = middle
    return low + (high - low) / 2
