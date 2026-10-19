# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from nightlight.config import Mode, NightLightConfig
from nightlight.core.schedule import Override, OverrideKind, compute_state, next_transition, schedule_around

UTC = timezone.utc
KYIV_SUMMER = timezone(timedelta(hours=3))

TIMES = NightLightConfig(
    enabled=True,
    mode=Mode.TIMES,
    day_temperature=6500,
    night_temperature=4500,
    transition_minutes=30,
    night_start="20:00",
    day_start="06:00",
)


def at(hour: int, minute: int = 0, day: int = 15, tz=UTC) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=tz)


def test_disabled_is_neutral():
    state = compute_state(NightLightConfig(enabled=False), at(23))
    assert state.temperature == 6500
    assert state.next_transition is None


def test_always_on_uses_night_temperature():
    config = NightLightConfig(enabled=True, mode=Mode.ALWAYS, night_temperature=3000)
    assert compute_state(config, at(12)).temperature == 3000


@pytest.mark.parametrize(
    ("hour", "minute", "temperature", "transitioning"),
    [
        (12, 0, 6500, False),  # day
        (20, 0, 6500, True),  # evening transition just started
        (20, 15, 5500, True),  # halfway through the evening transition
        (20, 30, 4500, False),  # night
        (2, 0, 4500, False),  # night, after midnight
        (6, 15, 5500, True),  # halfway through the morning transition
        (6, 30, 6500, False),  # day again
    ],
)
def test_custom_times(hour, minute, temperature, transitioning):
    state = compute_state(TIMES, at(hour, minute))
    assert state.temperature == temperature
    assert state.transitioning == transitioning


def test_next_transition_points_at_upcoming_change():
    assert compute_state(TIMES, at(12)).next_change == at(20)
    assert compute_state(TIMES, at(23)).next_change == at(6, day=16)


def test_night_window_inside_one_day():
    """A "night" that doesn't cross midnight, e.g. for a night-shift worker."""
    config = NightLightConfig(enabled=True, mode=Mode.TIMES, transition_minutes=0, night_start="09:00", day_start="17:00")
    assert compute_state(config, at(12)).temperature == config.night_temperature
    assert compute_state(config, at(20)).temperature == config.day_temperature


def test_solar_transitions_match_kde_reference():
    """knighttime's autotest values for Kyiv, 2025-05-25 (UTC+3): the morning
    runs civil dawn -> sunrise and the evening sunset -> civil dusk."""
    config = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=50.45, longitude=30.52)
    now = datetime(2025, 5, 25, 12, 0, tzinfo=KYIV_SUMMER)
    transitions = [t for t in schedule_around(config, now) if t.start.date() == now.date()]
    morning, evening = transitions
    expected = [
        (morning.start, "04:15:17"),
        (morning.end, "04:57:31"),
        (evening.start, "20:52:16"),
        (evening.end, "21:34:30"),
    ]
    for actual, reference in expected:
        reference_dt = datetime.combine(now.date(), datetime.strptime(reference, "%H:%M:%S").time(), KYIV_SUMMER)
        assert abs(actual - reference_dt) < timedelta(minutes=2), (actual, reference)
    assert not morning.to_night and evening.to_night


def test_location_day_and_night():
    config = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=51.5, longitude=-0.13)  # London
    assert compute_state(config, at(12)).temperature == config.day_temperature
    assert compute_state(config, at(23)).temperature == config.night_temperature


def test_polar_day_falls_back_to_six_to_six():
    """Like KDE: no sunset (Svalbard in June) -> 06:00/18:00 schedule."""
    config = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=78.2, longitude=15.6)
    assert compute_state(config, at(12)).temperature == config.day_temperature
    assert compute_state(config, at(23)).temperature == config.night_temperature
    assert compute_state(config, at(12)).next_change == at(18)


def test_suspend_override_uses_day_temperature():
    config = NightLightConfig(enabled=True, mode=Mode.ALWAYS, day_temperature=6000, night_temperature=3000)
    state = compute_state(config, at(23), Override(OverrideKind.SUSPEND, None))
    assert state.temperature == 6000
    assert state.override is not None


def test_activate_override_works_when_disabled():
    config = NightLightConfig(enabled=False, night_temperature=3000)
    assert compute_state(config, at(12), Override(OverrideKind.ACTIVATE, at(13))).temperature == 3000


def test_override_expires():
    override = Override(OverrideKind.ACTIVATE, at(13))
    state = compute_state(NightLightConfig(enabled=False), at(14), override)
    assert state.temperature == 6500
    assert state.override is None


NEW_YORK = ZoneInfo("America/New_York")  # clocks change on 8 March and 1 November 2026


def test_tomorrows_times_use_tomorrows_offset_after_spring_forward():
    # Saturday 22:00 EST. Sunday morning's 06:00 is EDT (10:00 UTC); applying
    # Saturday's offset would put it at 11:00 UTC, an hour late.
    now = datetime(2026, 3, 8, 3, 0, tzinfo=UTC).astimezone(NEW_YORK)
    morning = next_transition(TIMES, now, to_night=False, zone=NEW_YORK)
    assert morning.start.astimezone(UTC) == datetime(2026, 3, 8, 10, 0, tzinfo=UTC)
    assert morning.start.astimezone(NEW_YORK).hour == 6


def test_tomorrows_times_use_tomorrows_offset_after_falling_back():
    now = datetime(2026, 11, 1, 3, 0, tzinfo=UTC).astimezone(NEW_YORK)  # Saturday 23:00 EDT
    morning = next_transition(TIMES, now, to_night=False, zone=NEW_YORK)
    assert morning.start.astimezone(UTC) == datetime(2026, 11, 1, 11, 0, tzinfo=UTC)  # 06:00 EST


def test_a_transition_through_the_repeated_hour_takes_its_real_length():
    config = NightLightConfig(enabled=True, mode=Mode.TIMES, night_start="01:00", day_start="09:00", transition_minutes=60)
    start = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)  # 01:00 EDT
    halfway = compute_state(config, start + timedelta(minutes=30), zone=NEW_YORK)
    assert halfway.transitioning and halfway.temperature == 5500
    assert not compute_state(config, start + timedelta(minutes=60), zone=NEW_YORK).transitioning


def test_a_start_time_in_the_skipped_hour_happens_right_after_it():
    config = NightLightConfig(enabled=True, mode=Mode.TIMES, night_start="02:30", day_start="09:00", transition_minutes=0)
    evening = next_transition(config, datetime(2026, 3, 8, 6, 0, tzinfo=UTC), to_night=True, zone=NEW_YORK)
    assert evening.start.astimezone(UTC) == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)  # 03:30 EDT


def test_solar_times_across_the_change_stay_near_sunset():
    config = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=40.71, longitude=-74.0)
    for day in (7, 8, 9):  # either side of spring forward
        now = datetime(2026, 3, day, 16, 0, tzinfo=UTC)
        evening = next_transition(config, now, to_night=True, zone=NEW_YORK)
        local = evening.start.astimezone(NEW_YORK)
        assert 17 <= local.hour <= 19, local  # around 18:00 local either side


def test_next_transition_for_override_menus():
    # Still uses the configured times even when night light is always on.
    config = NightLightConfig(enabled=True, mode=Mode.ALWAYS, night_start="20:00", day_start="06:00")
    assert next_transition(config, at(12), to_night=True).start == at(20)
    assert next_transition(config, at(12), to_night=False).start == at(6, day=16)


def solar_day(minutes):
    config = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=50.45, longitude=30.52, solar_transition_minutes=minutes)
    now = datetime(2025, 5, 25, 12, 0, tzinfo=KYIV_SUMMER)
    return [t for t in schedule_around(config, now) if t.start.date() == now.date()]


@pytest.mark.parametrize("minutes", [5, 30, 60, 120])
def test_a_chosen_solar_transition_length(minutes):
    """Evening from sunset, morning ending at sunrise (Kyiv, 2025-05-25:
    sunrise 04:57:31, sunset 20:52:16, knighttime's values)."""
    morning, evening = solar_day(minutes)
    day = datetime(2025, 5, 25, tzinfo=KYIV_SUMMER)
    sunrise, sunset = day.replace(hour=4, minute=57, second=31), day.replace(hour=20, minute=52, second=16)
    assert abs(morning.end - sunrise) < timedelta(minutes=2) and abs(evening.start - sunset) < timedelta(minutes=2)
    assert morning.end - morning.start == timedelta(minutes=minutes)
    assert evening.end - evening.start == timedelta(minutes=minutes)


def test_an_instant_solar_switch():
    morning, evening = solar_day(0)
    assert morning.start == morning.end and evening.start == evening.end
    config = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=50.45, longitude=30.52, solar_transition_minutes=0)
    just_after = evening.start + timedelta(seconds=30)
    assert compute_state(config, just_after).temperature == config.night_temperature


def test_twilight_is_the_default():
    assert NightLightConfig().solar_transition_minutes == -1
    assert solar_day(-1) == [t for t in schedule_around(
        NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=50.45, longitude=30.52),
        datetime(2025, 5, 25, 12, 0, tzinfo=KYIV_SUMMER)) if t.start.date() == datetime(2025, 5, 25).date()]


def test_solar_transition_lengths_are_clamped():
    assert NightLightConfig(solar_transition_minutes=-7).normalized().solar_transition_minutes == -1
    assert NightLightConfig(solar_transition_minutes=999).normalized().solar_transition_minutes == 120


@pytest.mark.parametrize("zone", ["America/New_York", "America/Los_Angeles", "Pacific/Auckland", "Asia/Kolkata"])
def test_twilight_never_ends_before_it_begins_far_from_the_place(zone):
    """Kyiv's schedule on a PC in another zone: dawn and sunrise can fall on
    either side of midnight there. Beta 5 then paired dawn with the next
    day's sunrise, and the morning change "ended" 23 hours before it began."""
    config = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=50.45, longitude=30.52)
    for day in range(1, 29, 3):
        now = datetime(2026, 10, day, 12, 0, tzinfo=ZoneInfo(zone))
        for transition in schedule_around(config, now):
            assert transition.start <= transition.end, (zone, transition)
            assert transition.end - transition.start < timedelta(hours=2), (zone, transition)
