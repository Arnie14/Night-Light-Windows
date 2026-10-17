# SPDX-License-Identifier: GPL-3.0-only
from nightlight.core.colortemp import NEUTRAL_KELVIN, gamma_ramp, identity_ramp, plausible_base, ramps_match, whitepoint


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
