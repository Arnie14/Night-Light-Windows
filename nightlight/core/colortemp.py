# SPDX-License-Identifier: GPL-3.0-only
"""Colour temperature maths: Kelvin -> RGB whitepoint -> gamma ramp.

Night light works by scaling each colour channel's gamma ramp. 6500 K is
neutral (no change); lower temperatures dim blue and green to warm the image.
"""

from __future__ import annotations

import itertools
import math

MIN_KELVIN = 1000
MAX_KELVIN = 6500
NEUTRAL_KELVIN = 6500

Whitepoint = tuple[float, float, float]
NEUTRAL_WHITEPOINT: Whitepoint = (1.0, 1.0, 1.0)

# Whitepoints at 100 K intervals, interpolated in between. Table by Ingo Thies
# (2013), as used by Redshift (GPL-3.0) and KWin's night light
# (src/3rdparty/colortemperature.h, GPL-2.0-or-later); see
# https://github.com/jonls/redshift/blob/master/README-colorramp
# Values are gamma-encoded, i.e. meant to scale a gamma ramp directly.
_BLACKBODY: tuple[Whitepoint, ...] = (
    (1.00000000, 0.18172716, 0.00000000),  # 1000 K
    (1.00000000, 0.25503671, 0.00000000),  # 1100 K
    (1.00000000, 0.30942099, 0.00000000),  # 1200 K
    (1.00000000, 0.35357379, 0.00000000),  # 1300 K
    (1.00000000, 0.39091524, 0.00000000),  # 1400 K
    (1.00000000, 0.42322816, 0.00000000),  # 1500 K
    (1.00000000, 0.45159884, 0.00000000),  # 1600 K
    (1.00000000, 0.47675916, 0.00000000),  # 1700 K
    (1.00000000, 0.49923747, 0.00000000),  # 1800 K
    (1.00000000, 0.51943421, 0.00000000),  # 1900 K
    (1.00000000, 0.54360078, 0.08679949),  # 2000 K
    (1.00000000, 0.56618736, 0.14065513),  # 2100 K
    (1.00000000, 0.58734976, 0.18362641),  # 2200 K
    (1.00000000, 0.60724493, 0.22137978),  # 2300 K
    (1.00000000, 0.62600248, 0.25591950),  # 2400 K
    (1.00000000, 0.64373109, 0.28819679),  # 2500 K
    (1.00000000, 0.66052319, 0.31873863),  # 2600 K
    (1.00000000, 0.67645822, 0.34786758),  # 2700 K
    (1.00000000, 0.69160518, 0.37579588),  # 2800 K
    (1.00000000, 0.70602449, 0.40267128),  # 2900 K
    (1.00000000, 0.71976951, 0.42860152),  # 3000 K
    (1.00000000, 0.73288760, 0.45366838),  # 3100 K
    (1.00000000, 0.74542112, 0.47793608),  # 3200 K
    (1.00000000, 0.75740814, 0.50145662),  # 3300 K
    (1.00000000, 0.76888303, 0.52427322),  # 3400 K
    (1.00000000, 0.77987699, 0.54642268),  # 3500 K
    (1.00000000, 0.79041843, 0.56793692),  # 3600 K
    (1.00000000, 0.80053332, 0.58884417),  # 3700 K
    (1.00000000, 0.81024551, 0.60916971),  # 3800 K
    (1.00000000, 0.81957693, 0.62893653),  # 3900 K
    (1.00000000, 0.82854786, 0.64816570),  # 4000 K
    (1.00000000, 0.83717703, 0.66687674),  # 4100 K
    (1.00000000, 0.84548188, 0.68508786),  # 4200 K
    (1.00000000, 0.85347859, 0.70281616),  # 4300 K
    (1.00000000, 0.86118227, 0.72007777),  # 4400 K
    (1.00000000, 0.86860704, 0.73688797),  # 4500 K
    (1.00000000, 0.87576611, 0.75326132),  # 4600 K
    (1.00000000, 0.88267187, 0.76921169),  # 4700 K
    (1.00000000, 0.88933596, 0.78475236),  # 4800 K
    (1.00000000, 0.89576933, 0.79989606),  # 4900 K
    (1.00000000, 0.90198230, 0.81465502),  # 5000 K
    (1.00000000, 0.90963069, 0.82838210),  # 5100 K
    (1.00000000, 0.91710889, 0.84190889),  # 5200 K
    (1.00000000, 0.92441842, 0.85523742),  # 5300 K
    (1.00000000, 0.93156127, 0.86836903),  # 5400 K
    (1.00000000, 0.93853986, 0.88130458),  # 5500 K
    (1.00000000, 0.94535695, 0.89404470),  # 5600 K
    (1.00000000, 0.95201559, 0.90658983),  # 5700 K
    (1.00000000, 0.95851906, 0.91894041),  # 5800 K
    (1.00000000, 0.96487079, 0.93109690),  # 5900 K
    (1.00000000, 0.97107439, 0.94305985),  # 6000 K
    (1.00000000, 0.97713351, 0.95482993),  # 6100 K
    (1.00000000, 0.98305189, 0.96640795),  # 6200 K
    (1.00000000, 0.98883326, 0.97779486),  # 6300 K
    (1.00000000, 0.99448139, 0.98899179),  # 6400 K
    (1.00000000, 1.00000000, 1.00000000),  # 6500 K
)


def whitepoint(kelvin: float) -> Whitepoint:
    """Per-channel multipliers in [0, 1]; exactly (1, 1, 1) at 6500 K."""
    kelvin = max(MIN_KELVIN, min(MAX_KELVIN, kelvin))
    if kelvin >= MAX_KELVIN:
        return NEUTRAL_WHITEPOINT
    index, remainder = divmod(kelvin - MIN_KELVIN, 100)
    index = int(index)
    blend = remainder / 100
    low, high = _BLACKBODY[index], _BLACKBODY[index + 1]
    return tuple(a * (1 - blend) + b * blend for a, b in zip(low, high))


def gamma_ramp(wp: Whitepoint, size: int = 256, base: list[list[int]] | None = None) -> list[list[int]]:
    """Three 16-bit ramps (R, G, B) of `size` entries, scaled by the whitepoint.

    `base` is the ramp to tint, such as a monitor's calibration; the default
    is the identity ramp.
    """
    if base is None:
        base = identity_ramp(size)
    return [[min(65535, round(value * factor)) for value in channel] for channel, factor in zip(base, wp)]


# Software dimming through the gamma ramp, for displays without hardware
# brightness control. Windows rejects ramps far from identity, so, as Twinkle
# Tray does, the ramp's peak never drops below DRIVER_SAFE_PEAK; darker levels
# bend the curve so the mid-tones reach the requested level instead. Its
# range 20-100% maps to a 60-100% scale, which Twinkle Tray found reliable.
SOFTWARE_DIM_MIN = 20
_DIM_SCALE_AT_MIN = 0.6
DRIVER_SAFE_PEAK = 0.7


def dimming_curve(percent: float) -> tuple[float, float]:
    """(peak, exponent) of the dimming curve output = peak * input ** exponent."""
    percent = max(SOFTWARE_DIM_MIN, min(100, percent))
    scale = _DIM_SCALE_AT_MIN + (percent - SOFTWARE_DIM_MIN) * (1 - _DIM_SCALE_AT_MIN) / (100 - SOFTWARE_DIM_MIN)
    if scale >= DRIVER_SAFE_PEAK:
        return scale, 1.0
    # Keep the peak driver-safe, and pick the exponent that puts half input at `scale` of half output.
    return DRIVER_SAFE_PEAK, math.log(scale * 0.5 / DRIVER_SAFE_PEAK) / math.log(0.5)


def dimmed_ramp(percent: float, size: int = 256, base: list[list[int]] | None = None) -> list[list[int]]:
    """`base` (the identity by default) dimmed to `percent`; 100 returns it unchanged.
    Scaling the base, rather than replacing it, keeps a monitor's calibration."""
    if base is None:
        base = identity_ramp(size)
    if percent >= 100:
        return [list(channel) for channel in base]
    peak, exponent = dimming_curve(percent)
    factors = [0.0] + [peak * (i / (size - 1)) ** (exponent - 1) for i in range(1, size)]
    return [[min(65535, round(value * factor)) for value, factor in zip(channel, factors)] for channel in base]


def identity_ramp(size: int = 256) -> list[list[int]]:
    return [[round(i * 65535 / (size - 1)) for i in range(size)] for _ in range(3)]


# A ramp whose channels all still reach this share of full output is taken
# to be a calibration (or the identity), not a leftover tint from a night
# light: warming always pulls the blue channel's top well below full.
PLAUSIBLE_BASE_PEAK = 0.9
# Below that peak, the shape decides. A night light tint scales each channel
# along a straight line (gamma_ramp: exactly straight); a calibration bends
# its channels to correct the display's response. A calibration to a warm
# white point (D50, say) also pulls blue below 90%, so a low peak alone would
# throw such a calibration away: one that bends by more than this share of
# full output is kept. Measured: Winlight's tints deviate 0%, a D50-style
# calibration about 3%.
TINT_STRAIGHTNESS = 0.01


def achievable_kelvin(requested: int, warmest: int | None) -> int:
    """The temperature a display shows when `requested` is asked for, given
    the warmest Windows has accepted there (None or 0: no limit known).

    Windows refuses gamma ramps far from neutral unless GdiIcmGammaRange is
    raised; the limit depends on the display and on any dimming, and is only
    learnt when a ramp is refused. A request warmer than the limit gets the
    limit: the warmest the display accepts."""
    return max(requested, warmest) if warmest else requested


def limited_displays(requested: int, limits: dict[str, int]) -> dict[str, int]:
    """The displays where `requested` is warmer than Windows allows, with the
    temperature each one shows instead; empty when no limit is in the way."""
    return {name: limit for name, limit in limits.items() if achievable_kelvin(requested, limit) != requested}


def plausible_base(ramp: list[list[int]]) -> bool:
    """Whether a ramp found on a display is worth keeping as the base to tint.

    Gamma ramps outlive the app, so the ramp found at startup may be our own
    tint from a crashed session, or another tool's (f.lux, Windows Night
    Light); tinting that again would compound it. But it may also be a
    calibration, and discarding one loses it until Windows loads it again
    (usually at the next sign-in). So only a ramp shaped like a tint (every
    channel straight, at least one well below full) is rejected; anything
    curved is kept. A ramp that isn't three rising channels is never a base.
    """
    if len(ramp) != 3:
        return False
    for channel in ramp:
        if not channel or any(b < a for a, b in itertools.pairwise(channel)):
            return False
    if all(channel[-1] >= PLAUSIBLE_BASE_PEAK * 65535 for channel in ramp):
        return True
    return not looks_like_tint(ramp)


def looks_like_tint(ramp: list[list[int]]) -> bool:
    """Whether every channel runs in a straight line from 0 to its peak, as a
    night light tint does (a calibration curves)."""
    for channel in ramp:
        steps = len(channel) - 1
        if steps <= 0:
            return False
        peak = channel[-1]
        if max(abs(value - peak * i / steps) for i, value in enumerate(channel)) > TINT_STRAIGHTNESS * 65535:
            return False
    return True


# Drivers may keep fewer than 16 bits per entry, so a ramp read back can
# differ slightly from the one set (same tolerance as Twinkle Tray).
RAMP_TOLERANCE = 256


def ramps_match(a: list[list[int]], b: list[list[int]], tolerance: int = RAMP_TOLERANCE) -> bool:
    if len(a) != len(b):
        return False
    return all(len(x) == len(y) and all(abs(p - q) <= tolerance for p, q in zip(x, y)) for x, y in zip(a, b))
