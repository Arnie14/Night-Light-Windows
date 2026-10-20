# SPDX-License-Identifier: GPL-3.0-only
"""Wall-clock times that stay right across daylight saving changes.

Schedules say "20:00 every day", which is a different UTC offset on either
side of a daylight saving change. Two traps:

- `datetime.now().astimezone()` has a fixed offset (today's), so applying it
  to tomorrow's 20:00 is an hour off when the clocks change overnight.
- With a real time zone (zoneinfo), Python compares and subtracts datetimes
  that share the zone object by their wall-clock fields, ignoring the
  offset, which is wrong across a change.

So schedules build each day's times in the real zone (`wall_time`), then
turn them into fixed-offset datetimes at once, which compare and subtract
as absolute instants. A time skipped by the clocks springing forward
happens at the first moment after the gap; a time repeated when they fall
back happens once, at its first occurrence.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo


def fixed(moment: datetime) -> datetime:
    """The same instant with a fixed UTC offset instead of a zone."""
    offset = moment.utcoffset()
    if offset is None:
        raise ValueError("need a timezone-aware datetime")
    return moment.astimezone(timezone(offset))


def wall_time(day: date, at: time, zone: tzinfo) -> datetime:
    """`at` on `day` in `zone`, as a fixed-offset datetime (see the module docstring)."""
    naive = datetime.combine(day, at.replace(tzinfo=None))
    local = naive.replace(tzinfo=zone, fold=0)
    # A time in a spring-forward gap doesn't exist: fold=0 reads it with the
    # offset from before the change, which lands just after the gap.
    return fixed(local.astimezone(UTC).astimezone(zone))


def local(moment: datetime, zone: tzinfo | None) -> datetime:
    """An instant shown in `zone` (if given), with a fixed offset."""
    return fixed(moment.astimezone(zone)) if zone is not None else moment


def local_date(moment: datetime, zone: tzinfo | None) -> date:
    return moment.astimezone(zone).date() if zone is not None else moment.date()


def days_around(moment: datetime, zone: tzinfo | None, before: int = 1, after: int = 2) -> list[date]:
    today = local_date(moment, zone)
    return [today + timedelta(days=offset) for offset in range(-before, after + 1)]


def zone_named(name: str) -> tzinfo | None:
    """The IANA zone `name` ("Europe/Kyiv"), or None if unknown or unavailable.
    Windows has no zone database of its own; the tzdata package provides it."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return None
