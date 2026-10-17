# SPDX-License-Identifier: GPL-3.0-only
"""Time zones as locations, so sunrise and sunset can be set by picking a city
instead of typing coordinates. The table (nightlight/data/timezones.csv) comes
from the IANA tz database, see tools/make_timezones.py."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class Location:
    zone: str  # IANA name, e.g. "Europe/Kyiv"
    country: str
    latitude: float
    longitude: float

    @property
    def city(self) -> str:
        return self.zone.rsplit("/", 1)[-1].replace("_", " ")

    @property
    def label(self) -> str:
        return f"{self.city}, {self.country}"


@cache
def all_locations() -> tuple[Location, ...]:
    """Every known time zone, sorted by city name."""
    with (DATA / "timezones.csv").open(encoding="utf-8", newline="") as f:
        locations = [Location(r["zone"], r["country"], float(r["latitude"]), float(r["longitude"])) for r in csv.DictReader(f)]
    return tuple(sorted(locations, key=lambda loc: (loc.city, loc.country)))


@cache
def _aliases() -> dict[str, str]:
    with (DATA / "zone_aliases.csv").open(encoding="utf-8", newline="") as f:
        return {r["alias"]: r["zone"] for r in csv.DictReader(f)}


def find(zone: str) -> Location | None:
    """The location for an IANA zone name, also under an old name ("Asia/Calcutta")."""
    zone = _aliases().get(zone, zone)
    return next((loc for loc in all_locations() if loc.zone == zone), None)
