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
    assert config.load(path).general == GeneralConfig(start_minimized=False)  # the default, on


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
    path.write_text(json.dumps({"version": 99, "general": {"future_option": 1}, "something_new": {}, "displays": {},
                                "brightness_schedule": {"steps": [], "future": True}}))
    assert config.load(path) == AppConfig()  # unknown settings ignored; an empty schedule gets the default points


# --- From 0.1.0 beta 3 and 4 -------------------------------------------------


def test_new_sections_round_trip(tmp_path):
    from nightlight.config import DisplayPrefs

    path = tmp_path / "settings.json"
    original = AppConfig(
        NightLightConfig(exempt_apps=("game.exe", "photoshop.exe"), exempt_fullscreen=True),
        general=GeneralConfig(respect_fullscreen=False, follow_laptop_brightness=True, idle_dim=True, idle_minutes=12, idle_level=30),
        brightness_schedule=BrightnessScheduleConfig(
            True, "custom", (SchedulePoint(70, anchor=Anchor.SUNRISE, offset_minutes=30), SchedulePoint(25, "21:00"))
        ),
        displays=(DisplayPrefs("DEL41A2", "Left", False), DisplayPrefs("SAM7136", "", True)),
    )
    config.save(original, path)
    assert config.load(path) == original
    saved = json.loads(path.read_text())
    assert saved["displays"] == {"DEL41A2": {"name": "Left", "hidden": False}, "SAM7136": {"name": "", "hidden": True}}


def test_bad_new_sections_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "night_light": {"exempt_apps": ["Game.EXE ", "", 5]},
        "brightness_schedule": {"enabled": "yes", "steps": [{"at": "25:00", "level": 10}, {"at": "sunset", "level": 150}, "x"]},
        "displays": {"A": {"name": 3, "hidden": True}, "B": "nope", "C": {"name": "  "}},
    }))
    loaded = config.load(path)
    assert loaded.night_light.exempt_apps == ()  # not all strings: ignored
    assert loaded.brightness_schedule.enabled is False
    assert [(p.anchor, p.level) for p in loaded.brightness_schedule.points] == [(Anchor.SUNSET, 100)]
    assert [(d.key, d.name, d.hidden) for d in loaded.displays] == [("A", "", True)]
    assert BrightnessScheduleConfig().points  # defaults exist for when it's turned on


def test_exempt_apps_are_normalized():
    assert NightLightConfig(exempt_apps=(" Game.EXE", "game.exe", "")).normalized().exempt_apps == ("game.exe",)


def test_a_beta_4_schedule_carries_over_as_points(tmp_path):
    # Beta 3 and 4 saved "steps": {"at": "HH:MM" | "sunrise" | "sunset", "level", "offset_minutes"}.
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"brightness_schedule": {"enabled": True, "steps": [
        {"at": "sunrise", "level": 80, "offset_minutes": 30}, {"at": "21:00", "level": 40, "offset_minutes": 0}]}}))
    schedule = config.load(path).brightness_schedule
    assert schedule == BrightnessScheduleConfig(
        True, "custom", (SchedulePoint(80, anchor=Anchor.SUNRISE, offset_minutes=30), SchedulePoint(40, "21:00"))
    )
    config.save(config.load(path), path)  # saved again in this version's form
    saved = json.loads(path.read_text())["brightness_schedule"]
    assert "steps" not in saved and len(saved["points"]) == 2
    assert config.load(path).brightness_schedule == schedule


def test_display_prefs_saved_under_an_older_key_still_apply():
    # Beta 4 keyed displays by hardware id; now the EDID serial is part of the key.
    from nightlight.config import DisplayPrefs

    prefs = AppConfig(displays=(DisplayPrefs("DEL41A2", "Left", True),))
    assert prefs.display("DEL41A2:7XKJ9F3") == DisplayPrefs("DEL41A2:7XKJ9F3", "Left", True)
    assert prefs.hidden("DEL41A2:7XKJ9F3") and prefs.hidden("DEL41A2")
    assert not prefs.hidden("GSM5B7F:1234567")


def test_beta_4_general_settings_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"general": {"respect_fullscreen": False, "follow_laptop_brightness": True,
                                            "idle_dim": True, "idle_minutes": 0, "idle_level": 300}}))
    general = config.load(path).general
    assert (general.respect_fullscreen, general.follow_laptop_brightness, general.idle_dim) == (False, True, True)
    assert general.idle_minutes >= 1 and 0 <= general.idle_level <= 100  # clamped into range


def test_an_unreadable_settings_file_is_kept_before_defaults_replace_it(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"general": {"idle_dim": tru')  # cut short
    assert config.load(path) == AppConfig()
    assert (tmp_path / "settings.json.damaged").read_text() == '{"general": {"idle_dim": tru'
    path.write_text("[1, 2]")
    config.load(path)
    assert (tmp_path / "settings.json.damaged").read_text() == "[1, 2]"


# --- The data folder's new name (0.3.0) -------------------------------------------


def test_the_old_data_folder_is_renamed_with_everything_in_it(tmp_path):
    old, new = tmp_path / "night-light", tmp_path / "WinLight"
    (old / "Logs").mkdir(parents=True)
    (old / "settings.json").write_text('{"general": {"idle_dim": true}}')
    (old / "gamma.json").write_text("{}")
    (old / "Logs" / "night-light.log").write_text("a line\n")
    assert config._migrated(new, old) == new
    assert not old.exists()
    assert (new / "settings.json").read_text() == '{"general": {"idle_dim": true}}'
    assert (new / "gamma.json").exists() and (new / "Logs" / "night-light.log").read_text() == "a line\n"
    assert config.load(new / "settings.json").general.idle_dim


def test_an_existing_new_folder_is_never_replaced(tmp_path):
    old, new = tmp_path / "night-light", tmp_path / "WinLight"
    old.mkdir()
    new.mkdir()
    (new / "settings.json").write_text("{}")
    assert config._migrated(new, old) == new
    assert old.exists() and (new / "settings.json").read_text() == "{}"


def test_no_old_folder_means_the_new_one(tmp_path):
    assert config._migrated(tmp_path / "WinLight", tmp_path / "night-light") == tmp_path / "WinLight"


def test_a_folder_that_cant_be_renamed_stays_in_use(tmp_path, monkeypatch):
    """An older copy still running keeps files open in it: Windows refuses the rename."""
    old, new = tmp_path / "night-light", tmp_path / "WinLight"
    old.mkdir()

    def refuse(self, target):
        raise PermissionError("in use")

    monkeypatch.setattr(type(old), "rename", refuse)
    assert config._migrated(new, old) == old
    assert old.exists() and not new.exists()


def test_settings_and_logs_live_in_the_winlight_folder(monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(config, "_data_dir", None)
    monkeypatch.setattr(config.platformdirs, "user_config_dir", lambda name, appauthor=False: str(tmp_path / name))
    assert config.default_path() == tmp_path / "WinLight" / "settings.json"
    if sys.platform == "win32":
        assert config.log_dir() == tmp_path / "WinLight" / "Logs"
    monkeypatch.setattr(config, "_data_dir", None)


def test_the_log_files_take_the_new_name_with_their_history(tmp_path):
    for name, text in (("night-light.log", "today\n"), ("night-light.log.1", "yesterday\n"), ("night-light.log.2", "before\n")):
        (tmp_path / name).write_text(text)
    config.migrate_log_files(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["WinLight.log", "WinLight.log.1", "WinLight.log.2"]
    assert (tmp_path / "WinLight.log").read_text() == "today\n" and (tmp_path / "WinLight.log.2").read_text() == "before\n"


def test_an_existing_winlight_log_is_never_replaced(tmp_path):
    (tmp_path / "WinLight.log").write_text("new\n")
    (tmp_path / "night-light.log").write_text("old\n")
    config.migrate_log_files(tmp_path)
    assert (tmp_path / "WinLight.log").read_text() == "new\n" and (tmp_path / "night-light.log").exists()


def test_log_files_by_either_name():
    assert config.is_log_file("WinLight.log") and config.is_log_file("WinLight.log.2")
    assert config.is_log_file("night-light.log.1")  # one an older copy kept open
    assert not config.is_log_file("probe.txt") and not config.is_log_file("settings.json")


def test_never_pausing_late_at_night_round_trips_and_bad_values_fall_back(tmp_path):
    path = tmp_path / "settings.json"
    night_light = NightLightConfig(never_pause_at_night=True, never_pause_from="time", never_pause_time="23:15")
    config.save(AppConfig(night_light=night_light), path)
    assert config.load(path).night_light == night_light
    path.write_text('{"night_light": {"never_pause_at_night": true, "never_pause_from": "dusk", "never_pause_time": "25:00"}}')
    loaded = config.load(path).night_light
    assert (loaded.never_pause_at_night, loaded.never_pause_from, loaded.never_pause_time) == (True, "night", "22:00")
