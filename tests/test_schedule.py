from datetime import datetime, timedelta, timezone

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


def test_next_transition_for_override_menus():
    # Still uses the configured times even when night light is always on.
    config = NightLightConfig(enabled=True, mode=Mode.ALWAYS, night_start="20:00", day_start="06:00")
    assert next_transition(config, at(12), to_night=True).start == at(20)
    assert next_transition(config, at(12), to_night=False).start == at(6, day=16)
