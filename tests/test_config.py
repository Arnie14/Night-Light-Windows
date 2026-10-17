# SPDX-License-Identifier: GPL-3.0-only
import json

from nightlight import config
from nightlight.config import AppConfig, Mode, NightLightConfig


def test_missing_file_gives_defaults(tmp_path):
    assert config.load(tmp_path / "nope.json") == AppConfig()


def test_round_trip(tmp_path):
    path = tmp_path / "sub" / "settings.json"
    original = AppConfig(NightLightConfig(enabled=True, mode=Mode.LOCATION, night_temperature=3200, latitude=40.7, longitude=-74.0))
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
