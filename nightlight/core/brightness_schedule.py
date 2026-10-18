# SPDX-License-Identifier: GPL-3.0-only
"""Which brightness the schedule asks for at a given moment.

Twinkle Tray's "time of day adjustments": a list of steps, each a clock time
or sunrise/sunset plus an offset, and a level. The level of the most recent
step applies until the next one. Pure logic with no Qt or hardware.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from astral import Observer
from astral.sun import sun

from nightlight.config import SUNRISE, SUNSET, BrightnessScheduleConfig, BrightnessStep, parse_hhmm

log = logging.getLogger(__name__)

# When the sun doesn't rise or set (polar day or night), or no location is
# set, sunrise and sunset steps fall back to these, as KDE's night light does.
FALLBACK = {SUNRISE: time(6, 0), SUNSET: time(18, 0)}


@dataclass(frozen=True)
class ScheduledLevel:
    level: int
    since: datetime  # when this step started; a manual change after it wins until the next
    until: datetime | None  # when the next step starts


def step_times(step: BrightnessStep, day: date, now: datetime, location: tuple[float, float] | None) -> datetime:
    tz = now.tzinfo
    if step.at in (SUNRISE, SUNSET):
        moment = None
        if location is not None:
            try:
                moment = sun(Observer(*location), date=day, tzinfo=tz)[step.at]
            except ValueError:
                pass  # no sunrise or sunset that day
        if moment is None:
            moment = datetime.combine(day, FALLBACK[step.at], tz)
    else:
        moment = datetime.combine(day, parse_hhmm(step.at), tz)
    return moment + timedelta(minutes=step.offset_minutes)


def scheduled_level(config: BrightnessScheduleConfig, now: datetime, location: tuple[float, float] | None = None) -> ScheduledLevel | None:
    """The level the schedule asks for at `now` (timezone-aware), or None
    when the schedule is off or empty."""
    if not config.enabled or not config.steps:
        return None
    moments = sorted(
        (step_times(step, now.date() + timedelta(days=offset), now, location), step.level)
        for offset in (-1, 0, 1)
        for step in config.steps
    )
    started = [m for m in moments if m[0] <= now]
    upcoming = [m for m in moments if m[0] > now]
    if not started:  # can't happen with yesterday's steps included, but be safe
        return None
    since, level = started[-1]
    return ScheduledLevel(level, since, upcoming[0][0] if upcoming else None)
