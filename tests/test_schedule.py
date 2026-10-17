from datetime import datetime, timezone

import pytest

from nightlight.config import Mode, NightLightConfig
from nightlight.core.schedule import compute_state

UTC = timezone.utc

TIMES = NightLightConfig(
    enabled=True,
    mode=Mode.TIMES,
    day_temperature=6500,
    night_temperature=4500,
    transition_minutes=30,
    night_start="20:00",
    day_start="06:00",
)


def at(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 6, day, hour, minute, tzinfo=UTC)


def test_disabled_is_neutral():
    state = compute_state(NightLightConfig(enabled=False), at(23))
    assert state.temperature == 6500
    assert state.next_change is None


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


def test_next_change_points_at_upcoming_transition():
    assert compute_state(TIMES, at(12)).next_change == at(20)
    assert compute_state(TIMES, at(23)).next_change == at(6, day=16)


def test_night_window_inside_one_day():
    """A "night" that doesn't cross midnight, e.g. for a night-shift worker."""
    config = NightLightConfig(enabled=True, mode=Mode.TIMES, transition_minutes=0, night_start="09:00", day_start="17:00")
    assert compute_state(config, at(12)).temperature == config.night_temperature
    assert compute_state(config, at(20)).temperature == config.day_temperature


def test_location_london_in_summer():
    # Sunset in London mid-June is ~20:20 UTC, sunrise ~03:45 UTC.
    config = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=51.5, longitude=-0.13, transition_minutes=30)
    assert compute_state(config, at(12)).temperature == config.day_temperature
    assert compute_state(config, at(23)).temperature == config.night_temperature
    next_change = compute_state(config, at(12)).next_change
    assert next_change is not None and 20 <= next_change.hour <= 21


def test_location_polar_day_stays_day():
    config = NightLightConfig(enabled=True, mode=Mode.LOCATION, latitude=78.2, longitude=15.6)  # Svalbard
    assert compute_state(config, at(23)).temperature == config.day_temperature
