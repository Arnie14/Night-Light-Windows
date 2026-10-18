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
    # Pause night light on the display showing these apps (f.lux's "disable
    # for" list): lower-case executable names such as "photoshop.exe".
    exempt_apps: tuple[str, ...] = ()
    exempt_fullscreen: bool = False  # pause it on a display while an app is fullscreen there (games, video)

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
            exempt_apps=tuple(sorted({app.strip().lower() for app in self.exempt_apps if app.strip()})),
        )


@dataclass(frozen=True)
class GeneralConfig:
    start_with_windows: bool = False  # start when the user signs in
    start_minimized: bool = True  # start in the tray only, without opening the settings
    remember_brightness: bool = True  # restore each monitor's level after start, sleep and reconnecting
    link_brightness: bool = False  # one slider moves every monitor's brightness
    software_dimming: bool = True  # dim displays without brightness control (TVs) through the gamma ramp


SUNRISE, SUNSET = "sunrise", "sunset"


@dataclass(frozen=True)
class BrightnessStep:
    """At `at` ("HH:MM", "sunrise" or "sunset", plus `offset_minutes`), set displays to `level`."""

    at: str = "07:00"
    level: int = 80
    offset_minutes: int = 0


@dataclass(frozen=True)
class BrightnessScheduleConfig:
    """Twinkle Tray's "time of day adjustments": brightness levels at times of day."""

    enabled: bool = False
    steps: tuple[BrightnessStep, ...] = (BrightnessStep("07:00", 80), BrightnessStep("20:00", 40))


@dataclass(frozen=True)
class DisplayPrefs:
    """A display's custom name and visibility, keyed by MonitorInfo.key (as Twinkle Tray keys them)."""

    key: str
    name: str = ""  # "" keeps the display's own name
    hidden: bool = False  # left out of the panel, linked changes and the brightness schedule


@dataclass(frozen=True)
class AppConfig:
    night_light: NightLightConfig = field(default_factory=NightLightConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)
    brightness_schedule: BrightnessScheduleConfig = field(default_factory=BrightnessScheduleConfig)
    displays: tuple[DisplayPrefs, ...] = ()

    def display(self, key: str) -> DisplayPrefs:
        return next((d for d in self.displays if d.key == key), DisplayPrefs(key))


def parse_hhmm(text: str) -> time:
    return datetime.strptime(text, "%H:%M").time()


def default_path() -> Path:
    return Path(platformdirs.user_config_dir("night-light", appauthor=False)) / "settings.json"


def log_dir() -> Path:
    """Where night-light.log and probe.txt go."""
    return Path(platformdirs.user_log_dir("night-light", appauthor=False))


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
        brightness_schedule=_schedule_from(raw.get("brightness_schedule")),
        displays=_displays_from(raw.get("displays")),
    )


def save(config: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": CONFIG_VERSION, **_to_json(config)}
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


def _schedule_from(data: object) -> BrightnessScheduleConfig:
    defaults = BrightnessScheduleConfig()
    if not isinstance(data, dict):
        return defaults
    enabled = data.get("enabled") if isinstance(data.get("enabled"), bool) else defaults.enabled
    steps = defaults.steps
    if isinstance(data.get("steps"), list):
        steps = tuple(step for item in data["steps"] if (step := _step_from(item)) is not None)
    return BrightnessScheduleConfig(enabled, steps)


def _step_from(item: object) -> BrightnessStep | None:
    step = _from_dict(BrightnessStep, "brightness_schedule.steps", item)
    if not isinstance(item, dict) or not (step.at in (SUNRISE, SUNSET) or _is_hhmm(step.at)):
        log.warning("Ignoring invalid brightness step %r", item)
        return None
    return replace(step, level=_clamp(step.level, 0, 100), offset_minutes=_clamp(step.offset_minutes, -720, 720))


def _displays_from(data: object) -> tuple[DisplayPrefs, ...]:
    if not isinstance(data, dict):
        return ()
    prefs = []
    for key, value in data.items():
        if isinstance(value, dict):
            name = value.get("name") if isinstance(value.get("name"), str) else ""
            hidden = value.get("hidden") if isinstance(value.get("hidden"), bool) else False
            if name.strip() or hidden:
                prefs.append(DisplayPrefs(str(key), name.strip(), hidden))
    return tuple(prefs)


def _to_json(config: AppConfig) -> dict:
    data = asdict(config)
    data["displays"] = {d.key: {"name": d.name, "hidden": d.hidden} for d in config.displays}
    data["night_light"]["exempt_apps"] = list(config.night_light.exempt_apps)
    return data


def _coerce(name: str, value: object, default: object) -> object:
    if name == "mode":
        return Mode(value)
    if isinstance(default, tuple):  # a list of strings, such as exempt_apps
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            return tuple(value)
        raise TypeError(f"unexpected type for {name}")
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
