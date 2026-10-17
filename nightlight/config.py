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
class AppConfig:
    night_light: NightLightConfig = field(default_factory=NightLightConfig)


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
    return AppConfig(night_light=_night_light_from_dict(raw.get("night_light")))


def save(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": CONFIG_VERSION, **asdict(config)}
    # Write then rename, so a crash mid-write can't leave a truncated file.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _night_light_from_dict(data: object) -> NightLightConfig:
    defaults = NightLightConfig()
    if not isinstance(data, dict):
        return defaults
    values = {}
    for f in fields(NightLightConfig):
        if f.name not in data:
            continue
        try:
            values[f.name] = _coerce(f.name, data[f.name], getattr(defaults, f.name))
        except (TypeError, ValueError):
            log.warning("Ignoring invalid setting night_light.%s=%r", f.name, data[f.name])
    return replace(defaults, **values).normalized()


def _coerce(name: str, value: object, default: object) -> object:
    if name == "mode":
        return Mode(value)
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
