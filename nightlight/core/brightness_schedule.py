# SPDX-License-Identifier: GPL-3.0-only
"""Brightness by time of day: which level the schedule asks for at a moment.

Pure logic, like core/schedule.py. A schedule is a list of points ("from
20:00, 50%"; "from 30 minutes after sunset, 40%"). The level at any moment
comes from the most recent point that has passed, found by laying the points
out over yesterday, today and tomorrow, the way Twinkle Tray finds "the
latest adjustment at or before now" and KDE's night light finds its
transition. That makes the answer a function of the time alone, never of
what happened before, which is what lets the app apply the schedule
idempotently: asking again gives the same point, and only a new point
(identified by the instant it began) is a change to apply.

Times are built in the local zone and compared as absolute instants (see
core/clock.py), so daylight saving changes neither skip nor repeat a point.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo

from astral import Observer
from astral.sun import sun

from nightlight.config import (
    DEFAULT_SCHEDULE_POINTS,
    DEFAULT_SCHEDULE_TRANSITION,
    Anchor,
    BrightnessScheduleConfig,
    SchedulePoint,
    parse_hhmm,
)
from nightlight.core.clock import days_around, fixed, local, wall_time

log = logging.getLogger(__name__)

# When the sun doesn't rise or set (polar day and night), or no location is
# set, sunrise and sunset points use these times instead, as night light does
# (KDE's fallback).
FALLBACK_SUNRISE = time(6, 0)
FALLBACK_SUNSET = time(18, 0)


@dataclass(frozen=True)
class Preset:
    name: str
    description: str
    points: tuple[SchedulePoint, ...]
    transition_minutes: int = 0


CUSTOM = "custom"
# Each preset changes gradually, over its transition_minutes from each time
# (never running into the next time), so none of them jumps.
PRESETS: dict[str, Preset] = {
    "day_night": Preset(
        "Day and night",
        "100% from 07:00 and 50% from 20:00, each change taking 30 minutes",
        DEFAULT_SCHEDULE_POINTS,
        transition_minutes=DEFAULT_SCHEDULE_TRANSITION,
    ),
    "follow_sun": Preset(
        "Follow the sun",
        "Rises to 100% in the hour after sunrise, lowers to 70% after sunset and to 40% later in the evening. "
        "Uses the location set for Night Light",
        (SchedulePoint(100, anchor=Anchor.SUNRISE), SchedulePoint(70, anchor=Anchor.SUNSET), SchedulePoint(40, anchor=Anchor.SUNSET, offset_minutes=90)),
        transition_minutes=60,
    ),
    "office": Preset(
        "Office hours",
        "100% for the working day from 08:00, 70% from 17:00 and 40% from 21:00, each change taking 45 minutes",
        (SchedulePoint(100, "08:00"), SchedulePoint(70, "17:00"), SchedulePoint(40, "21:00")),
        transition_minutes=45,
    ),
    "wind_down": Preset(
        "Evening wind-down",
        "100% by day, then lower in steps from 18:00 to 22:00, each spread over 90 minutes for a slow, steady fade",
        (SchedulePoint(100, "07:00"), SchedulePoint(80, "18:00"), SchedulePoint(60, "20:00"), SchedulePoint(35, "22:00")),
        transition_minutes=90,
    ),
    "night_owl": Preset(
        "Night owl",
        "For late hours: 90% from 10:00, then lower from 20:00, past midnight and after 02:00, each change taking an hour",
        (SchedulePoint(90, "10:00"), SchedulePoint(75, "20:00"), SchedulePoint(50, "00:00"), SchedulePoint(30, "02:00")),
        transition_minutes=60,
    ),
    "dark_room": Preset(
        "Dark room",
        "Low levels all day for a dim room or an OLED display: 60% from 08:00, 35% from 19:00 and 15% from 23:00",
        (SchedulePoint(60, "08:00"), SchedulePoint(35, "19:00"), SchedulePoint(15, "23:00")),
        transition_minutes=60,
    ),
}


@dataclass(frozen=True)
class Boundary:
    """One point of the schedule on one day."""

    at: datetime  # fixed-offset, see core/clock.py
    level: int
    point: int  # index into the schedule's points

    @property
    def id(self) -> str:
        """Identifies this occurrence across restarts: the instant, in UTC."""
        return self.at.astimezone(_UTC).isoformat()


@dataclass(frozen=True)
class ScheduledLevel:
    level: int  # what the schedule asks for now
    since: Boundary  # the point in effect
    next: Boundary | None
    transitioning: bool  # still moving from the previous point's level
    approximate_sun: bool  # sunrise/sunset points used the fallback times
    transition_end: datetime | None = None  # when it reaches since.level, while transitioning


_UTC = timezone.utc


def sun_times(day: date, zone: tzinfo, location: tuple[float, float] | None) -> tuple[datetime, datetime, bool]:
    """(sunrise, sunset, approximate) on `day`; the fallback times when the
    location is unknown or the sun doesn't rise or set there that day."""
    if location is not None:
        try:
            events = sun(Observer(*location), date=day, tzinfo=zone)
            return fixed(events["sunrise"]), fixed(events["sunset"]), False
        except ValueError:
            pass  # polar day or night
    return wall_time(day, FALLBACK_SUNRISE, zone), wall_time(day, FALLBACK_SUNSET, zone), True


def boundaries(config: BrightnessScheduleConfig, now: datetime, zone: tzinfo | None = None,
               location: tuple[float, float] | None = None) -> tuple[list[Boundary], bool]:
    """Every point on the days around `now`, in time order, and whether any
    sun point was approximate. Points falling on the same instant are merged,
    the later one in the list winning, so two points never fire together."""
    tz = zone or now.tzinfo
    found: dict[datetime, Boundary] = {}
    approximate = False
    for day in days_around(now, zone, before=2, after=1):
        sun_cache: tuple[datetime, datetime, bool] | None = None
        for index, point in enumerate(config.points):
            if point.anchor is Anchor.CLOCK:
                at = wall_time(day, parse_hhmm(point.at), tz)
            else:
                sun_cache = sun_cache or sun_times(day, tz, location)
                sunrise, sunset, approx = sun_cache
                approximate |= approx
                at = local((sunrise if point.anchor is Anchor.SUNRISE else sunset) + timedelta(minutes=point.offset_minutes), tz)
            key = at.astimezone(_UTC)
            found[key] = Boundary(at, point.level, index)  # same instant: the later point wins
    return sorted(found.values(), key=lambda b: b.at), approximate


def scheduled_level(config: BrightnessScheduleConfig, now: datetime, zone: tzinfo | None = None,
                    location: tuple[float, float] | None = None) -> ScheduledLevel | None:
    """The level the schedule asks for at `now` (timezone-aware); None if it is off."""
    if not config.enabled or not config.points:
        return None
    laid_out, approximate = boundaries(config, now, zone, location)
    passed = [b for b in laid_out if b.at <= now]
    if not passed:
        return None
    since = passed[-1]
    upcoming = next((b for b in laid_out if b.at > now), None)
    level, transitioning, end = since.level, False, None
    if config.transition_minutes > 0 and len(passed) >= 2:
        duration = timedelta(minutes=config.transition_minutes)
        if upcoming is not None:
            duration = min(duration, upcoming.at - since.at)  # never runs into the next point
        elapsed = now - since.at
        if elapsed < duration:
            previous = passed[-2].level
            level = round(previous + (since.level - previous) * (elapsed / duration))
            transitioning, end = True, since.at + duration
    return ScheduledLevel(level, since, upcoming, transitioning, approximate, end)


def describe(config: BrightnessScheduleConfig) -> str:
    """The points in words: "100% at 07:00, 50% at 20:00"."""
    parts = []
    for point in config.points:
        if point.anchor is Anchor.CLOCK:
            when = point.at
        elif point.offset_minutes:
            side = "after" if point.offset_minutes > 0 else "before"
            when = f"{abs(point.offset_minutes)} min {side} {point.anchor.value}"
        else:
            when = point.anchor.value
        parts.append(f"{point.level}% at {when}")
    return ", ".join(parts)
