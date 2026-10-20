# SPDX-License-Identifier: GPL-3.0-only
import itertools

import pytest

from nightlight.core.colortemp import (
    DRIVER_SAFE_PEAK,
    NEUTRAL_KELVIN,
    achievable_kelvin,
    dimmed_ramp,
    gamma_ramp,
    identity_ramp,
    limited_displays,
    looks_like_tint,
    plausible_base,
    ramps_match,
    whitepoint,
)


def test_neutral_is_identity():
    assert whitepoint(NEUTRAL_KELVIN) == (1.0, 1.0, 1.0)


def test_warm_temperatures_reduce_blue_more_than_green():
    r, g, b = whitepoint(3000)
    assert r == 1.0
    assert 0 < b < g < 1.0


def test_lower_is_warmer():
    assert whitepoint(2000)[2] < whitepoint(4000)[2] < whitepoint(6000)[2]


def test_matches_kde_redshift_table():
    assert whitepoint(1000) == (1.0, 0.18172716, 0.0)
    assert whitepoint(4500) == (1.0, 0.86860704, 0.73688797)


def test_interpolates_between_table_entries():
    low, high, mid = whitepoint(3400), whitepoint(3500), whitepoint(3450)
    for a, b, m in zip(low, high, mid):
        assert abs(m - (a + b) / 2) < 1e-9


def test_out_of_range_is_clamped():
    assert whitepoint(10_000) == whitepoint(6500)
    assert whitepoint(100) == whitepoint(1000)


def test_gamma_ramp_shape_and_endpoints():
    ramp = gamma_ramp((1.0, 0.5, 0.0), size=256)
    assert [len(channel) for channel in ramp] == [256, 256, 256]
    assert [channel[0] for channel in ramp] == [0, 0, 0]
    assert ramp[0][-1] == 65535
    assert ramp[1][-1] == 32768
    assert ramp[2][-1] == 0
    assert ramp[0] == sorted(ramp[0])


def test_gamma_ramp_tints_a_base_ramp():
    base = [[round(v * 0.95) for v in channel] for channel in identity_ramp()]
    ramp = gamma_ramp((1.0, 0.5, 0.0), base=base)
    assert ramp[0] == base[0]
    assert ramp[1][-1] == round(base[1][-1] * 0.5)
    assert ramp[2] == [0] * 256


def test_plausible_base_accepts_calibration_but_not_a_tint():
    assert plausible_base(identity_ramp())
    assert plausible_base([[round(v * 0.95) for v in channel] for channel in identity_ramp()])
    assert not plausible_base(gamma_ramp(whitepoint(4500)))
    assert not plausible_base([list(reversed(channel)) for channel in identity_ramp()])


def test_ramps_match_allows_driver_rounding():
    ramp = identity_ramp()
    assert ramps_match(ramp, [[min(65535, v + 200) for v in channel] for channel in ramp])
    assert not ramps_match(ramp, gamma_ramp(whitepoint(5000)))


def test_full_brightness_leaves_the_ramp_alone():
    base = identity_ramp()
    base[0][-1] = 60000  # a calibration
    assert dimmed_ramp(100, base=base) == base


@pytest.mark.parametrize("percent", [20, 30, 45, 60, 80, 95])
def test_dimming_stays_driver_safe_and_monotonic(percent):
    ramp = dimmed_ramp(percent)
    for channel in ramp:
        assert channel[-1] >= DRIVER_SAFE_PEAK * 65535 - 1  # Windows rejects darker peaks
        assert all(b >= a for a, b in itertools.pairwise(channel))


def test_dimming_darkens_mid_tones_in_order():
    mid = [dimmed_ramp(p)[0][128] for p in (20, 40, 60, 80, 100)]
    assert mid == sorted(mid) and mid[0] < mid[-1] * 0.65


def test_dimming_below_the_minimum_is_clamped():
    assert dimmed_ramp(0) == dimmed_ramp(20)


def _calibration(peaks, curve=1.1):
    """A calibration-like ramp: a gamma correction per channel, each to its own peak."""
    return [[min(65535, round(65535 * (i / 255) ** (1 / curve) * peak)) for i in range(256)] for peak in peaks]


@pytest.mark.parametrize("kelvin", [6000, 5000, 4500, 3400, 2700, 1900])
def test_every_night_light_tint_is_recognised_as_one(kelvin):
    ramp = gamma_ramp(whitepoint(kelvin))
    assert looks_like_tint(ramp)
    if kelvin <= 5500:  # warm enough to pull blue below the plausible peak
        assert not plausible_base(ramp)


def test_a_calibration_to_a_warm_white_point_is_kept():
    """Before 0.3.0 any ramp with a channel below 90% was taken for a tint and
    reset to neutral, discarding a D50-style calibration (blue at about 80%)
    until Windows loaded it again at the next sign-in."""
    calibration = _calibration((1.0, 0.97, 0.82))
    assert not looks_like_tint(calibration)
    assert plausible_base(calibration)


def test_a_tint_over_a_straight_ramp_is_still_rejected():
    tinted = gamma_ramp(whitepoint(3400), base=identity_ramp())
    assert not plausible_base(tinted)


def test_ramps_that_cant_be_a_base():
    assert not plausible_base([])
    assert not plausible_base([[0] * 256, [0] * 256])
    falling = identity_ramp()
    falling[1] = list(reversed(falling[1]))
    assert not plausible_base(falling)


# --- Windows' limit on warm ramps --------------------------------------------------


@pytest.mark.parametrize(("requested", "warmest", "shown"), [
    (2700, None, 2700),  # no limit known
    (2700, 0, 2700),
    (2700, 3300, 3300),  # warmer than allowed: the warmest it accepts
    (3300, 3300, 3300),
    (4500, 3300, 4500),  # within the limit: as asked
])
def test_achievable_kelvin(requested, warmest, shown):
    assert achievable_kelvin(requested, warmest) == shown


def test_only_displays_whose_limit_is_in_the_way_are_listed():
    limits = {r"\.\DISPLAY1": 3300, r"\.\DISPLAY2": 2400}
    assert limited_displays(2700, limits) == {r"\.\DISPLAY1": 3300}
    assert limited_displays(4000, limits) == {}
    assert limited_displays(2700, {}) == {}
