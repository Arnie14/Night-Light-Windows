from nightlight.core.colortemp import NEUTRAL_KELVIN, gamma_ramp, whitepoint


def test_neutral_is_identity():
    assert whitepoint(NEUTRAL_KELVIN) == (1.0, 1.0, 1.0)


def test_warm_temperatures_reduce_blue_more_than_green():
    r, g, b = whitepoint(3000)
    assert r == 1.0
    assert 0 < b < g < 1.0


def test_lower_is_warmer():
    assert whitepoint(2000)[2] < whitepoint(4000)[2] < whitepoint(6000)[2]


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
