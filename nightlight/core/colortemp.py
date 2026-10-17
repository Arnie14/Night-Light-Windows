"""Colour temperature maths: Kelvin -> RGB whitepoint -> gamma ramp.

Night light works by scaling each colour channel's gamma ramp. 6500 K is
neutral (no change); lower temperatures dim blue and green to warm the image.
"""

from __future__ import annotations

import math

MIN_KELVIN = 1000
MAX_KELVIN = 6500
NEUTRAL_KELVIN = 6500

Whitepoint = tuple[float, float, float]
NEUTRAL_WHITEPOINT: Whitepoint = (1.0, 1.0, 1.0)


def _blackbody_rgb(kelvin: float) -> Whitepoint:
    """Approximate blackbody colour (Tanner Helland's fit), channels 0-255."""
    t = kelvin / 100
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255.0
    return tuple(min(255.0, max(0.0, c)) for c in (r, g, b))


_NEUTRAL_RGB = _blackbody_rgb(NEUTRAL_KELVIN)


def whitepoint(kelvin: float) -> Whitepoint:
    """Per-channel multipliers in [0, 1]; exactly (1, 1, 1) at 6500 K."""
    kelvin = max(MIN_KELVIN, min(MAX_KELVIN, kelvin))
    rgb = _blackbody_rgb(kelvin)
    return tuple(min(1.0, c / n) for c, n in zip(rgb, _NEUTRAL_RGB))


def gamma_ramp(wp: Whitepoint, size: int = 256) -> list[list[int]]:
    """Three 16-bit ramps (R, G, B) of `size` entries, scaled by the whitepoint."""
    return [[min(65535, round(i * 65535 / (size - 1) * factor)) for i in range(size)] for factor in wp]
