# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timedelta, timezone

from nightlight.config import BrightnessScheduleConfig, BrightnessStep
from nightlight.core.brightness_schedule import scheduled_level

UTC = timezone.utc
STEPS = BrightnessScheduleConfig(True, (BrightnessStep("07:00", 80), BrightnessStep("20:00", 30)))


def at(hour, minute=0, day=15):
    return datetime(2026, 6, day, hour, minute, tzinfo=UTC)


def test_off_or_empty_means_no_level():
    assert scheduled_level(BrightnessScheduleConfig(False, STEPS.steps), at(12)) is None
    assert scheduled_level(BrightnessScheduleConfig(True, ()), at(12)) is None


def test_the_most_recent_step_applies_until_the_next():
    day = scheduled_level(STEPS, at(12))
    assert (day.level, day.since, day.until) == (80, at(7), at(20))
    night = scheduled_level(STEPS, at(23))
    assert (night.level, night.since, night.until) == (30, at(20), at(7, day=16))


def test_after_midnight_yesterdays_evening_step_applies():
    early = scheduled_level(STEPS, at(3))
    assert (early.level, early.since) == (30, at(20, day=14))


def test_sun_steps_follow_the_location_with_offsets():
    london = (51.5, -0.13)
    config = BrightnessScheduleConfig(True, (BrightnessStep("sunrise", 70, 30), BrightnessStep("sunset", 40, -15)))
    evening = scheduled_level(config, at(21), london)  # a June sunset in London is around 20:20 UTC
    assert evening.level == 40
    assert at(19, 30) < evening.since < at(20, 30)


def test_sun_steps_without_a_location_fall_back_to_fixed_times():
    config = BrightnessScheduleConfig(True, (BrightnessStep("sunrise", 70), BrightnessStep("sunset", 40)))
    assert scheduled_level(config, at(12)).since == at(6)
    assert scheduled_level(config, at(19)).since == at(18)


def test_polar_summer_falls_back_too():
    tromso = (69.65, 18.96)
    config = BrightnessScheduleConfig(True, (BrightnessStep("sunrise", 70), BrightnessStep("sunset", 40)))
    level = scheduled_level(config, at(12), tromso)
    assert level.since - at(6) < timedelta(hours=24)  # something sensible, not a crash
