# SPDX-License-Identifier: GPL-3.0-only
import json

from nightlight import config
from nightlight.config import Anchor, AppConfig, BrightnessScheduleConfig, GeneralConfig, Mode, NightLightConfig, SchedulePoint


def test_missing_file_gives_defaults(tmp_path):
    assert config.load(tmp_path / "nope.json") == AppConfig()


def test_round_trip(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    original = AppConfig(
        NightLightConfig(enabled=True, mode=Mode.LOCATION, night_temperature=3200, latitude=40.7, longitude=-74.0, timezone="America/New_York"),
        GeneralConfig(start_with_windows=True, start_minimized=False),
    )
    config.save(original, path)
    assert config.load(path) == original


def test_corrupt_file_gives_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert config.load(path) == AppConfig()


def test_bad_values_are_ignored_or_clamped(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "night_light": {
                    "enabled": "yes",  # wrong type -> default
                    "mode": "bogus",  # unknown -> default
                    "night_temperature": 200,  # too low -> clamped
                    "night_start": "25:99",  # invalid -> default
                    "latitude": 51.5,
                    "unknown_key": 1,
                }
            }
        )
    )
    loaded = config.load(path).night_light
    defaults = NightLightConfig()
    assert loaded.enabled == defaults.enabled
    assert loaded.mode == defaults.mode
    assert loaded.night_temperature == 1000
    assert loaded.night_start == defaults.night_start
    assert loaded.latitude == 51.5


def test_files_from_before_general_settings_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"version": 1, "night_light": {"enabled": True, "latitude": 10.0}}))
    loaded = config.load(path)
    assert loaded.general == GeneralConfig()
    assert loaded.night_light.enabled and loaded.night_light.timezone == ""


def test_bad_general_values_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"general": {"start_with_windows": "yes", "start_minimized": False}}))
    assert config.load(path).general == GeneralConfig(start_with_windows=False, start_minimized=False)


def test_brightness_schedule_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    schedule = BrightnessScheduleConfig(
        enabled=True,
        preset="custom",
        points=(SchedulePoint(90, "07:30"), SchedulePoint(40, anchor=Anchor.SUNSET, offset_minutes=45)),
        transition_minutes=20,
    )
    config.save(AppConfig(brightness_schedule=schedule), path)
    assert config.load(path).brightness_schedule == schedule


def test_a_bad_brightness_schedule_is_repaired(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"brightness_schedule": {
        "enabled": True,
        "points": [
            {"level": 150, "at": "7:5x"},  # clamped, invalid time -> default
            {"level": 30, "anchor": "moonrise"},  # unknown anchor -> clock
            "garbage",
            {"level": 50, "anchor": "sunset", "offset_minutes": 9999},
        ],
        "transition_minutes": -5,
    }}))
    schedule = config.load(path).brightness_schedule
    assert schedule.enabled and schedule.transition_minutes == 0
    assert [p.level for p in schedule.points] == [100, 30, 50]
    assert schedule.points[0].at == "07:00" and schedule.points[1].anchor is Anchor.CLOCK
    assert schedule.points[2].offset_minutes == config.MAX_SUN_OFFSET_MINUTES


def test_an_empty_schedule_gets_the_default_points(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"brightness_schedule": {"enabled": True, "points": []}}))
    assert config.load(path).brightness_schedule.points == config.DEFAULT_SCHEDULE_POINTS


def test_settings_from_a_newer_version_still_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"general": {"idle_dim": True}, "displays": {}, "brightness_schedule": {"steps": []}}))
    assert config.load(path) == AppConfig()
