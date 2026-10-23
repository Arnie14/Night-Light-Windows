# SPDX-License-Identifier: GPL-3.0-only
"""User settings, stored as JSON in the platform's config directory.

Loading is deliberately forgiving: a missing, corrupt or hand-edited file
never stops the app from starting; bad values fall back to defaults.
Settings written by older versions load too (the brightness schedule's
"steps" from 0.1.0 beta 3 and 4 become "points").
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, time
from enum import StrEnum
from pathlib import Path

import platformdirs

from nightlight.core.colortemp import MAX_KELVIN, MIN_KELVIN
from nightlight.system.files import write_atomic

log = logging.getLogger(__name__)

CONFIG_VERSION = 1
MAX_TRANSITION_MINUTES = 600
MAX_IDLE_MINUTES = 240
MAX_SOLAR_TRANSITION_MINUTES = 120
THEMES = ("system", "light", "dark")
# When "never pause late at night" begins: as the evening transition starts,
# once night light is fully on, or at a set time (bedtime, say).
NEVER_PAUSE_FROM = ("evening", "night", "time")
ACCENTS = ("windows", "amber", "blue", "teal", "green", "purple", "rose")  # colors in ui/theme.py


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
    exempt_fullscreen: bool = False  # pause it on a display while any app is fullscreen there (games, video)
    pause_for_apps: bool = True  # pause it for exempt_apps (a switch of its own; the list is kept while off)
    # Pause it, as Winlight judges from what Windows reports, for video,
    # calls and games (an app keeping the screen on, exclusive fullscreen)
    # and for known color-critical apps (exemptions.COLOR_APPS).
    pause_automatically: bool = False
    # Sunrise and sunset: how long the change takes. -1 follows twilight
    # (sunset to civil dusk, dawn to sunrise, about 30 min); 0 is instant.
    solar_transition_minutes: int = -1
    # Late at night none of the above pause night light, for sleep's sake:
    # from never_pause_from (NEVER_PAUSE_FROM; "time": never_pause_time)
    # until the morning transition starts.
    never_pause_at_night: bool = False
    never_pause_from: str = "night"
    never_pause_time: str = "22:00"  # HH:MM

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
            solar_transition_minutes=-1 if self.solar_transition_minutes < 0 else _clamp(self.solar_transition_minutes, 0, MAX_SOLAR_TRANSITION_MINUTES),
            never_pause_from=self.never_pause_from if self.never_pause_from in NEVER_PAUSE_FROM else defaults.never_pause_from,
            never_pause_time=self.never_pause_time if _is_hhmm(self.never_pause_time) else defaults.never_pause_time,
        )


@dataclass(frozen=True)
class GeneralConfig:
    start_with_windows: bool = True  # start when the user signs in
    start_minimized: bool = True  # start in the tray only, without opening the settings
    remember_brightness: bool = True  # restore each monitor's level after start, sleep and reconnecting
    link_brightness: bool = False  # one slider moves every monitor's brightness
    software_dimming: bool = True  # dim displays without brightness control (TVs) through the gamma ramp
    # Don't open windows unasked (a second launch's panel, settings at sign-in)
    # while a game, video or presentation is fullscreen: that can minimize it.
    respect_fullscreen: bool = True
    # External displays follow the laptop panel's level (its brightness keys).
    follow_laptop_brightness: bool = False
    # Dim every display after this many minutes without input (Twinkle Tray's
    # idle detection), not while a video, game or presentation keeps it on.
    idle_dim: bool = False
    idle_minutes: int = 5
    idle_level: int = 20
    # Never dim for idle while one of these apps is in front (a call, a video
    # in a player that doesn't keep the screen on): lower-case exe names.
    idle_exempt_apps: tuple[str, ...] = ()
    # Put the built-in display's level back when Windows changes it for being
    # plugged in or unplugged (brightness/power_source.py).
    keep_brightness_on_power_change: bool = False
    # Put each display's color calibration back when Windows drops it (e.g.
    # after the screen turns off and on), as calibration loaders do.
    keep_calibration: bool = False
    theme: str = "system"  # "system" (follow Windows), "light" or "dark"
    accent: str = "windows"  # Windows' accent colour, or one of ACCENTS

    def normalized(self) -> GeneralConfig:
        # The same ranges as the settings window's fields.
        return replace(
            self,
            idle_minutes=_clamp(self.idle_minutes, 1, MAX_IDLE_MINUTES),
            idle_level=_clamp(self.idle_level, 0, 100),
            idle_exempt_apps=tuple(sorted({app.strip().lower() for app in self.idle_exempt_apps if app.strip()})),
            theme=self.theme if self.theme in THEMES else "system",
            accent=self.accent if self.accent in ACCENTS else "windows",
        )


class Anchor(StrEnum):
    """What a brightness schedule point's time is relative to."""

    CLOCK = "clock"  # a time of day
    SUNRISE = "sunrise"
    SUNSET = "sunset"


# Kept for code and settings from 0.1.0 beta 3 and 4, which wrote "at": "sunrise".
SUNRISE, SUNSET = Anchor.SUNRISE.value, Anchor.SUNSET.value
MAX_SUN_OFFSET_MINUTES = 240
MAX_SCHEDULE_POINTS = 12


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


DEFAULT_SCHEDULE_POINTS = (SchedulePoint(100, "07:00"), SchedulePoint(50, "20:00"))
DEFAULT_SCHEDULE_TRANSITION = 30  # the "Day and night" preset's


@dataclass(frozen=True)
class BrightnessScheduleConfig:
    """Brightness by time of day, like Twinkle Tray's time adjustments."""

    enabled: bool = False
    preset: str = "day_night"  # the preset the points came from, or "custom"
    points: tuple[SchedulePoint, ...] = DEFAULT_SCHEDULE_POINTS
    transition_minutes: int = DEFAULT_SCHEDULE_TRANSITION  # 0: change at each point (with a short fade); else gradually

    def normalized(self) -> BrightnessScheduleConfig:
        points = tuple(p.normalized() for p in self.points[:MAX_SCHEDULE_POINTS]) or DEFAULT_SCHEDULE_POINTS
        return replace(self, points=points, transition_minutes=_clamp(self.transition_minutes, 0, MAX_TRANSITION_MINUTES))


@dataclass(frozen=True)
class DisplayPrefs:
    """A display's custom name and visibility, keyed by MonitorInfo.key (as Twinkle Tray keys them)."""

    key: str
    name: str = ""  # "" keeps the display's own name
    hidden: bool = False  # left out of the panel, linked changes and the brightness schedule
    # While HDR is on, only the user's own slider changes it over DDC/CI: no
    # restoring, schedule or following the laptop's keys. Some monitors show a
    # tint after DDC/CI brightness changes in HDR mode (Twinkle Tray #1310).
    hdr_manual: bool = False


@dataclass(frozen=True)
class AppConfig:
    night_light: NightLightConfig = field(default_factory=NightLightConfig)
    general: GeneralConfig = field(default_factory=GeneralConfig)
    brightness_schedule: BrightnessScheduleConfig = field(default_factory=BrightnessScheduleConfig)
    displays: tuple[DisplayPrefs, ...] = ()

    def display(self, key: str) -> DisplayPrefs:
        """A display's preferences, also when saved under its key from an
        older version (the hardware id without the serial)."""
        exact = next((d for d in self.displays if d.key == key), None)
        if exact is not None:
            return exact
        legacy = key.split(":", 1)[0]
        return next((replace(d, key=key) for d in self.displays if d.key == legacy and legacy != key), DisplayPrefs(key))

    def hidden(self, key: str) -> bool:
        """Whether a display is hidden; always ask this way (it knows the older keys)."""
        return self.display(key).hidden


def parse_hhmm(text: str) -> time:
    return datetime.strptime(text, "%H:%M").time()


# The folder for settings, state and logs (%LOCALAPPDATA%\WinLight on
# Windows). Versions before 0.3.0 used "night-light"; the first start of a
# newer version renames that folder, so nothing inside it changes.
DATA_FOLDER = "WinLight"
OLD_DATA_FOLDER = "night-light"
# The log file in Logs/, with its rotated copies (WinLight.log.1, .2).
# Before 0.3.0 it was night-light.log; the first start renames those too.
LOG_FILE = "WinLight.log"
OLD_LOG_FILE = "night-light.log"
_data_dir: Path | None = None


def data_dir() -> Path:
    """The settings folder, moved from its old name the first time."""
    global _data_dir
    if _data_dir is None:
        _data_dir = _migrated(Path(platformdirs.user_config_dir(DATA_FOLDER, appauthor=False)),
                              Path(platformdirs.user_config_dir(OLD_DATA_FOLDER, appauthor=False)))
    return _data_dir


def _migrated(new: Path, old: Path) -> Path:
    """Rename `old` to `new` if only `old` exists. If it can't be renamed
    (an older copy still has files open in it), `old` stays in use this
    time and the rename is tried again at the next start."""
    if new.exists() or not old.is_dir():
        return new
    try:
        old.rename(new)
    except OSError as exc:
        log.warning("Could not rename the settings folder %s to %s (%s); using it as it is for now", old.name, new.name, exc)
        return old
    log.info("Renamed the settings folder %s to %s", old.name, new.name)
    return new


def migrate_log_files(folder: Path) -> None:
    """Rename night-light.log and its rotated copies to WinLight.log, .1, .2,
    so the log keeps its history under the new name. Skipped if the new log
    already exists; a file that can't be renamed (an older copy has it
    open) stays, and reports still include it."""
    if (folder / LOG_FILE).exists():
        return
    for old in sorted(folder.glob(OLD_LOG_FILE + "*")):
        new = folder / (LOG_FILE + old.name[len(OLD_LOG_FILE):])
        try:
            old.rename(new)
        except OSError:
            log.debug("Could not rename %s", old.name, exc_info=True)


def is_log_file(name: str) -> bool:
    """A log file of this app, by its current or its old name."""
    return name.startswith((LOG_FILE, OLD_LOG_FILE))


def default_path() -> Path:
    return data_dir() / "settings.json"


def log_dir() -> Path:
    """Where WinLight.log, probe.txt and compatibility.txt go: Logs in
    the settings folder on Windows (so they move with it), the usual log
    folder elsewhere."""
    if sys.platform == "win32":
        return data_dir() / "Logs"
    return Path(platformdirs.user_log_dir(DATA_FOLDER, appauthor=False))


def load(path: Path) -> AppConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AppConfig()
    except (OSError, ValueError):
        log.warning("Could not read %s, using default settings", path, exc_info=True)
        _keep_damaged(path)
        return AppConfig()
    if not isinstance(raw, dict):
        log.warning("Ignoring malformed settings file %s", path)
        _keep_damaged(path)
        return AppConfig()
    return AppConfig(
        night_light=_from_dict(NightLightConfig, "night_light", raw.get("night_light")).normalized(),
        general=_from_dict(GeneralConfig, "general", raw.get("general")).normalized(),
        brightness_schedule=_schedule_from(raw.get("brightness_schedule")),
        displays=_displays_from(raw.get("displays")),
    )


def _keep_damaged(path: Path) -> None:
    """Copy an unreadable settings file aside before defaults are saved over
    it, so the user's settings can still be recovered by hand."""
    backup = path.with_name(path.name + ".damaged")
    try:
        if path.is_file():
            backup.write_bytes(path.read_bytes())
            log.warning("Kept a copy of the unreadable settings as %s", backup.name)
    except OSError:
        log.warning("Could not keep a copy of the unreadable settings", exc_info=True)


def save(config: AppConfig, path: Path) -> None:
    data = {"version": CONFIG_VERSION, **_to_json(config)}
    write_atomic(path, json.dumps(data, indent=2))  # a crash mid-write can't leave a truncated file


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
    """The brightness schedule, from "points" (this version) or "steps" (0.1.0 beta 3 and 4)."""
    if not isinstance(data, dict):
        return BrightnessScheduleConfig()
    scalars = {k: v for k, v in data.items() if k not in ("points", "steps")}
    schedule = _from_dict(BrightnessScheduleConfig, "brightness_schedule", scalars)
    if isinstance(data.get("points"), list):
        points = tuple(_from_dict(SchedulePoint, "brightness_schedule.points", p) for p in data["points"] if isinstance(p, dict))
        schedule = replace(schedule, points=points)
    elif isinstance(data.get("steps"), list):
        points = tuple(point for item in data["steps"] if (point := _point_from_step(item)) is not None)
        # Old schedules had no presets or gradual changes; an empty one gets the default points (and their preset).
        if points and "preset" not in data:
            schedule = replace(schedule, preset="custom")
        schedule = replace(schedule, points=points)
    return schedule.normalized()


def _point_from_step(item: object) -> SchedulePoint | None:
    """A 0.1.0 beta 3/4 step: {"at": "HH:MM" | "sunrise" | "sunset", "level", "offset_minutes"}."""
    if not isinstance(item, dict):
        log.warning("Ignoring invalid brightness step %r", item)
        return None
    at, level, offset = item.get("at", "07:00"), item.get("level", 80), item.get("offset_minutes", 0)
    if not isinstance(level, int) or isinstance(level, bool) or not isinstance(offset, int) or isinstance(offset, bool):
        log.warning("Ignoring invalid brightness step %r", item)
        return None
    if at in (SUNRISE, SUNSET):
        return SchedulePoint(level, anchor=Anchor(at), offset_minutes=offset)
    if isinstance(at, str) and _is_hhmm(at):
        return SchedulePoint(level, at)
    log.warning("Ignoring invalid brightness step %r", item)
    return None


def _displays_from(data: object) -> tuple[DisplayPrefs, ...]:
    if not isinstance(data, dict):
        return ()
    prefs = []
    for key, value in data.items():
        if isinstance(value, dict):
            name = value.get("name") if isinstance(value.get("name"), str) else ""
            hidden = value.get("hidden") if isinstance(value.get("hidden"), bool) else False
            hdr_manual = value.get("hdr_manual") if isinstance(value.get("hdr_manual"), bool) else False
            if name.strip() or hidden or hdr_manual:
                prefs.append(DisplayPrefs(str(key), name.strip(), hidden, hdr_manual))
    return tuple(prefs)


def _to_json(config: AppConfig) -> dict:
    data = asdict(config)
    data["displays"] = {d.key: {"name": d.name, "hidden": d.hidden, "hdr_manual": d.hdr_manual} for d in config.displays}
    data["night_light"]["exempt_apps"] = list(config.night_light.exempt_apps)
    data["general"]["idle_exempt_apps"] = list(config.general.idle_exempt_apps)
    data["brightness_schedule"]["points"] = [asdict(p) for p in config.brightness_schedule.points]
    return data


def _coerce(name: str, value: object, default: object) -> object:
    if name == "mode":
        return Mode(value)
    if name == "anchor":
        return Anchor(value)
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
    except (TypeError, ValueError):
        return False
    return True
