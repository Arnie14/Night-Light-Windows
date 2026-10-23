# SPDX-License-Identifier: GPL-3.0-only
import itertools
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from nightlight.config import Mode, NightLightConfig
from nightlight.core.schedule import Override, OverrideKind, compute_state, next_transition, schedule_around

UTC = UTC
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


# --- Never pausing late at night ----------------------------------------------


def held(config: NightLightConfig, when: datetime, **changes) -> bool:
    from dataclasses import replace

    from nightlight.core.schedule import pausing_held

    return pausing_held(replace(config, never_pause_at_night=True, **changes), when)


def test_never_pausing_is_off_unless_chosen():
    from nightlight.core.schedule import pausing_held

    assert not pausing_held(TIMES, at(23))


@pytest.mark.parametrize(
    ("hour", "minute", "day", "expected"),
    [
        (19, 59, 15, False),
        (20, 0, 15, True),  # the evening transition starts
        (23, 30, 15, True),
        (2, 0, 16, True),  # after midnight
        (5, 59, 16, True),
        (6, 0, 16, False),  # the morning transition starts
        (12, 0, 16, False),
    ],
)
def test_from_the_evening_transition_until_the_morning_one(hour, minute, day, expected):
    assert held(TIMES, at(hour, minute, day), never_pause_from="evening") is expected


def test_from_night_waits_for_the_evening_transition_to_end():
    assert not held(TIMES, at(20, 15), never_pause_from="night")  # still warming
    assert held(TIMES, at(20, 30), never_pause_from="night")
    assert not held(TIMES, at(6, 10, 16), never_pause_from="night")


@pytest.mark.parametrize(
    ("time", "hour", "minute", "day", "expected"),
    [
        ("22:00", 21, 59, 15, False),
        ("22:00", 22, 0, 15, True),
        ("22:00", 3, 0, 16, True),
        ("22:00", 6, 0, 16, False),
        ("01:00", 0, 30, 16, False),  # a bedtime after midnight
        ("01:00", 1, 30, 16, True),
        ("01:00", 7, 0, 16, False),
    ],
)
def test_from_a_set_time_until_the_morning_transition(time, hour, minute, day, expected):
    assert held(TIMES, at(hour, minute, day), never_pause_from="time", never_pause_time=time) is expected


def test_with_sunset_and_sunrise():
    kyiv = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=50.45, longitude=30.52)
    assert held(kyiv, at(23, 30, tz=KYIV_SUMMER), never_pause_from="evening")
    assert not held(kyiv, at(12, 0, tz=KYIV_SUMMER), never_pause_from="evening")


def test_with_night_light_always_on_the_custom_times_stand_for_night():
    always = NightLightConfig(enabled=True, mode=Mode.ALWAYS, night_start="21:00", day_start="07:00")
    assert held(always, at(22), never_pause_from="night")
    assert not held(always, at(15), never_pause_from="night")


# --- High latitudes -----------------------------------------------------------

STOCKHOLM = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=59.33, longitude=18.07)
REYKJAVIK = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=64.15, longitude=-21.94)


def zone(name):
    return ZoneInfo(name)


def evening_and_morning(config, day, tz):
    """The evening that starts on `day` (local) and the morning after it."""
    transitions = schedule_around(config, datetime(2026, day[0], day[1], 15, tzinfo=tz), tz)
    evening = next(t for t in transitions if t.to_night and t.start > datetime(2026, day[0], day[1], 12, tzinfo=tz))
    morning = next(t for t in transitions if not t.to_night and t.start >= evening.start)
    return evening, morning


@pytest.mark.parametrize(("config", "tz", "day", "sunset"), [
    (STOCKHOLM, "Europe/Stockholm", (6, 15), "22:05"),  # dusk past midnight in other years: this day fell back to 18:00
    (NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=60.17, longitude=24.94), "Europe/Helsinki", (6, 2), "22:31"),
])
def test_summer_days_whose_dusk_nears_midnight_keep_their_real_times(config, tz, day, sunset):
    evening, morning = evening_and_morning(config, day, zone(tz))
    assert f"{evening.start:%H:%M}" == sunset
    assert evening.end < morning.start  # a short night between


def test_a_white_night_turns_at_solar_midnight():
    """Reykjavik in June: the sun sets after midnight and never gets 6
    degrees down, so there is no civil dusk. It used to fall back to
    18:00 and 06:00; now the darkest moment ends the evening and starts
    the morning (as LightBulb takes the closest point)."""
    evening, morning = evening_and_morning(REYKJAVIK, (6, 20), zone("Atlantic/Reykjavik"))
    assert f"{evening.start:%m-%d %H:%M}" == "06-21 00:03"  # sunset, just after midnight
    assert evening.end == morning.start and f"{evening.end:%H:%M}" == "01:29"  # solar midnight
    assert f"{morning.end:%H:%M}" == "02:55"  # sunrise
    darkest = compute_state(REYKJAVIK, evening.end, zone=zone("Atlantic/Reykjavik"))
    assert darkest.night_factor > 0.99


def test_a_long_chosen_transition_stops_at_solar_midnight():
    night = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=64.15, longitude=-21.94, solar_transition_minutes=120)
    evening, morning = evening_and_morning(night, (6, 20), zone("Atlantic/Reykjavik"))
    assert evening.end == morning.start  # 2 h each way would overlap: they meet at the darkest moment


@pytest.mark.parametrize(("config", "tz"), [(STOCKHOLM, "Europe/Stockholm"), (REYKJAVIK, "Atlantic/Reykjavik")])
def test_every_day_of_the_year_has_its_own_sun_times_without_overlaps_or_jumps(config, tz):
    tz = zone(tz)
    for offset in range(0, 365, 3):
        day = datetime(2026, 1, 1, 12, tzinfo=tz) + timedelta(days=offset)
        transitions = schedule_around(config, day, tz)
        assert {f"{t.start:%H:%M}" for t in transitions} != {"06:00", "18:00"}, f"fell back on {day:%m-%d}"
        for earlier, later in itertools.pairwise(transitions):
            assert earlier.to_night != later.to_night and earlier.end <= later.start, f"{day:%m-%d}"
    previous = None
    for minutes in range(0, 3 * 24 * 60, 10):  # midsummer, every 10 minutes: no jumps
        factor = compute_state(config, datetime(2026, 6, 20, tzinfo=tz) + timedelta(minutes=minutes), zone=tz).night_factor
        assert previous is None or abs(factor - previous) < 0.25
        previous = factor


def test_polar_night_falls_back_like_polar_day():
    tromso = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=69.65, longitude=18.96)
    tz = zone("Europe/Oslo")
    transitions = schedule_around(tromso, datetime(2026, 12, 21, 12, tzinfo=tz), tz)
    assert {f"{t.start:%H:%M}" for t in transitions} == {"06:00", "18:00"}
