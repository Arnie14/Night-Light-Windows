# SPDX-License-Identifier: GPL-3.0-only
"""User settings, stored as JSON in the platform's config directory.

Loading is deliberately forgiving: a missing, corrupt or hand-edited file
never stops the app from starting; bad values fall back to defaults.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, time
from enum import StrEnum
from pathlib import Path

import platformdirs

from nightlight.core.colortemp import MAX_KELVIN, MIN_KELVIN

log = logging.getLogger(__name__)

CONFIG_VERSION = 1
MAX_TRANSITION_MINUTES = 600


class Mode(StrEnum):
    """When night light switches on, mirroring KDE's "Switching times"."""

    LOCATION = "location"  # sunset and sunrise at a manual location
    TIMES = "times"  # custom times
    ALWAYS = "always"  # always on night light


@dataclass(frozen=True)
class NightLightConfig:
    enabled: bool = False
    mode: Mode = Mode.TIMES
    day_temperature: int = 6500
    night_temperature: int = 4500
    transition_minutes: int = 30
    night_start: str = "18:00"  # HH:MM, when the evening transition begins
    day_start: str = "06:00"  # HH:MM, when the morning transition begins
    latitude: float = 0.0
    longitude: float = 0.0
    timezone: str = ""  # IANA zone the location was picked from; "" for a custom location

    def normalized(self) -> NightLightConfig:
        defaults = NightLightConfig()
        return replace(
            self,
            day_temperature=_clamp(self.day_temperature, MIN_KELVIN, MAX_KELVIN),
            night_temperature=_clamp(self.night_temperature, MIN_KELVIN, MAX_KELVIN),
            transition_minutes=_clamp(self.transition_minutes, 0, MAX_TRANSITION_MINUTES),
            night_start=self.night_start if _is_hhmm(self.night_start) else defaults.night_start,
            day_start=self.day_start if _is_hhmm(self.day_start) else defaults.day_start,
            latitude=_clamp(self.latitude, -90.0, 90.0),
            longitude=_clamp(self.longitude, -180.0, 180.0),
        )


@dataclass(frozen=True)
class GeneralConfig:
    start_with_windows: bool = False  # start when the user signs in
    start_minimized: bool = True  # start in the tray only, without opening the settings
    remember_brightness: bool = True  # restore each monitor's level after start, sleep and reconnecting
    link_brightness: bool = False  # one slider moves every monitor's brightness
    software_dimming: bool = True  # dim displays without brightness control (TVs) through the gamma ramp


class Anchor(StrEnum):
    """What a brightness schedule point's time is relative to."""

    CLOCK = "clock"  # a time of day
    SUNRISE = "sunrise"
    SUNSET = "sunset"


@dataclass(frozen=True)
class SchedulePoint:
    """From this moment on, displays are set to `level` (until the next point)."""

    level: int = 100
    at: str = "07:00"  # HH:MM, for Anchor.CLOCK
    anchor: Anchor = Anchor.CLOCK
    offset_minutes: int = 0  # before (-) or after (+) sunrise or sunset

    def normalized(self) -> SchedulePoint:
        return replace(
            self,
            level=_clamp(self.level, 0, 100),
            at=self.at if _is_hhmm(self.at) else "07:00",
            offset_minutes=_clamp(self.offset_minutes, -MAX_SUN_OFFSET_MINUTES, MAX_SUN_OFFSET_MINUTES),
        )


MAX_SUN_OFFSET_MINUTES = 240
MAX_SCHEDULE_POINTS = 12
DEFAULT_SCHEDULE_POINTS = (SchedulePoint(100, "07:00"), SchedulePoint(50, "20:00"))


@dataclass(frozen=True)
class BrightnessScheduleConfig:
    """Brightness by time of day, like Twinkle Tray's time adjustments."""

    enabled: bool = False
    preset: str = "day_night"  # the preset the points came from, or "custom"
    points: tuple[SchedulePoint, ...] = DEFAULT_SCHEDULE_POINTS
    transition_minutes: int = 0  # 0: change at once; else gradually after each point

    def normalized(self) -> BrightnessScheduleConfig:
        points = tuple(p.normalized() for p in self.points[:MAX_SCHEDULE_POINTS]) or DEFAULT_SCHEDULE_POINTS
        return replace(self, points=points, transition_minutes=_clamp(self.transition_minutes, 0, MAX_TRANSITION_MINUTES))


@dataclass(frozen=True)
class AppConfig:
    night_light: NightLightConfig = field(default_factory=NightLightConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)
    brightness_schedule: BrightnessScheduleConfig = field(default_factory=BrightnessScheduleConfig)


def parse_hhmm(text: str) -> time:
    return datetime.strptime(text, "%H:%M").time()


def default_path() -> Path:
    return Path(platformdirs.user_config_dir("night-light", appauthor=False)) / "settings.json"


def load(path: Path) -> AppConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppConfig()
    except (OSError, ValueError):
        log.warning("Could not read %s, using default settings", path, exc_info=True)
        return AppConfig()
    if not isinstance(raw, dict):
        log.warning("Ignoring malformed settings file %s", path)
        return AppConfig()
    return AppConfig(
        night_light=_from_dict(NightLightConfig, "night_light", raw.get("night_light")).normalized(),
        general=_from_dict(GeneralConfig, "general", raw.get("general")),
        brightness_schedule=_schedule_from_dict(raw.get("brightness_schedule")),
    )


def _schedule_from_dict(data: object) -> BrightnessScheduleConfig:
    schedule = _from_dict(BrightnessScheduleConfig, "brightness_schedule", {k: v for k, v in data.items() if k != "points"} if isinstance(data, dict) else None)
    raw_points = data.get("points") if isinstance(data, dict) else None
    if isinstance(raw_points, list):
        points = tuple(_from_dict(SchedulePoint, "brightness_schedule.points", p) for p in raw_points if isinstance(p, dict))
        schedule = replace(schedule, points=points)
    return schedule.normalized()


def save(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": CONFIG_VERSION, **asdict(config)}
    # Write then rename, so a crash mid-write can't leave a truncated file.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _from_dict[T](cls: type[T], section: str, data: object) -> T:
    """`cls` from a JSON object, keeping the default for each missing or invalid value."""
    defaults = cls()
    if not isinstance(data, dict):
        return defaults
    values = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        try:
            values[f.name] = _coerce(f.name, data[f.name], getattr(defaults, f.name))
        except (TypeError, ValueError):
            log.warning("Ignoring invalid setting %s.%s=%r", section, f.name, data[f.name])
    return replace(defaults, **values)


def _coerce(name: str, value: object, default: object) -> object:
    if name == "mode":
        return Mode(value)
    if name == "anchor":
        return Anchor(value)
    if isinstance(default, tuple):
        raise TypeError(f"{name} is read separately")
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
    elif isinstance(default, (int, float)):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return type(default)(value)
    elif isinstance(default, str) and isinstance(value, str):
        return value
    raise TypeError(f"unexpected type for {name}")


def _clamp(value, low, high):
    return max(low, min(high, value))


def _is_hhmm(text: str) -> bool:
    try:
        parse_hhmm(text)
    except ValueError:
        return False
    return True
