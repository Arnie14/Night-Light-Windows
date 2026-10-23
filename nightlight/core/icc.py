# SPDX-License-Identifier: GPL-3.0-only
"""A display's calibration, read from its ICC color profile.

Calibrating a display produces curves for the graphics card's gamma ramp,
stored in the display's profile; Windows loads them at sign-in. Winlight
reads them for "Reload calibrations", as calibration loaders do:

- the `vcgt` tag (Apple's, used by DisplayCAL and ArgyllCMS, most
  calibrator software and display makers' profiles): a table or a formula
  per channel, as in ArgyllCMS's icclib;
- the curves of Windows' own Display Color Calibration (DCCW), in the WCS
  profile it embeds (the `MS00` tag): gamma curves, read only when their
  gain is 1 and offset 0, the only case whose result doesn't depend on how
  Windows combines the three numbers (not documented).

A profile with neither has no calibration: the ramp is the identity.
Pure logic, no Windows calls.
"""

from __future__ import annotations

import re
import struct

HEADER_SIZE = 128
VCGT = b"vcgt"
WCS = b"MS00"


class UnreadableCalibration(ValueError):
    """The profile has a calibration in a form this module doesn't read."""


def calibration_ramp(profile: bytes, size: int = 256) -> list[list[int]] | None:
    """The gamma ramp (3 channels of `size` 16-bit values) a profile calibrates
    the display to, or None if it holds no calibration. Raises
    UnreadableCalibration for a calibration in a form it can't read, and
    ValueError for a file that isn't an ICC profile."""
    tags = _tags(profile)
    if VCGT in tags:
        return _vcgt(tags[VCGT], size)
    if WCS in tags:
        return _wcs(tags[WCS], size)
    return None


def _tags(profile: bytes) -> dict[bytes, bytes]:
    if len(profile) < HEADER_SIZE + 4 or profile[36:40] != b"acsp":
        raise ValueError("not an ICC profile")
    (count,) = struct.unpack_from(">I", profile, HEADER_SIZE)
    tags = {}
    for i in range(min(count, 1000)):
        position = HEADER_SIZE + 4 + 12 * i
        if position + 12 > len(profile):
            raise ValueError("truncated ICC tag table")
        signature, offset, length = struct.unpack_from(">4sII", profile, position)
        if offset + length > len(profile):
            raise ValueError(f"tag {signature!r} runs past the end of the profile")
        tags[signature] = profile[offset:offset + length]
    return tags


def _vcgt(data: bytes, size: int) -> list[list[int]]:
    if len(data) < 12 or data[:4] != VCGT:
        raise UnreadableCalibration("malformed vcgt tag")
    (kind,) = struct.unpack_from(">I", data, 8)
    if kind == 0:  # a table
        if len(data) < 18:
            raise UnreadableCalibration("truncated vcgt table")
        channels, entries, entry_size = struct.unpack_from(">HHH", data, 12)
        if channels not in (1, 3) or entries < 2 or entry_size not in (1, 2):
            raise UnreadableCalibration(f"vcgt table of {channels} channels, {entries} entries of {entry_size} bytes")
        if len(data) < 18 + channels * entries * entry_size:
            raise UnreadableCalibration("truncated vcgt table")
        code, full = (">" + "H" * entries, 65535) if entry_size == 2 else ("B" * entries, 255)
        curves = [struct.unpack_from(code, data, 18 + c * entries * entry_size) for c in range(channels)]
        curves = curves * 3 if channels == 1 else curves
        return [_resample([v / full for v in curve], size) for curve in curves]
    if kind == 1:  # a formula per channel: min + (max - min) * x ** gamma
        if len(data) < 12 + 36:
            raise UnreadableCalibration("truncated vcgt formula")
        values = [v / 65536 for v in struct.unpack_from(">9i", data, 12)]
        ramps = []
        for gamma, low, high in (values[0:3], values[3:6], values[6:9]):
            if gamma <= 0:
                raise UnreadableCalibration(f"vcgt formula with gamma {gamma}")
            ramps.append(_ramp(lambda x, g=gamma, lo=low, hi=high: lo + (hi - lo) * x ** g, size))
        return ramps
    raise UnreadableCalibration(f"vcgt of type {kind}")


_CURVE = re.compile(r"<\w+:(Red|Green|Blue)TRC\b([^>]*)/?>")
_ATTRIBUTE = re.compile(r'(\w+)="([^"]*)"')


def _wcs(data: bytes, size: int) -> list[list[int]] | None:
    """The DCCW curves in a WCS profile's XML (UTF-16), or None if it has none."""
    text = data.decode("utf-16-le", errors="ignore")
    start, end = text.find("<cdm:Calibration>"), text.find("</cdm:Calibration>")
    if start < 0 or end < 0:
        return None
    calibration = text[start:end]
    if "AdapterGammaConfiguration" not in calibration:
        return None
    if "ParameterizedCurves" not in calibration:
        raise UnreadableCalibration("a Windows calibration that isn't a set of gamma curves")
    curves = {}
    for channel, attributes in _CURVE.findall(calibration):
        values = dict(_ATTRIBUTE.findall(attributes))
        try:
            gamma = float(values["Gamma"])
            gain = float(values.get("Gain", "1"))
            offset = float(values.get("Offset1", "0"))
        except (KeyError, ValueError):
            raise UnreadableCalibration(f"a Windows calibration curve without a gamma: {attributes.strip()}") from None
        if gamma <= 0 or abs(gain - 1) > 1e-6 or abs(offset) > 1e-6:
            raise UnreadableCalibration(f"a Windows calibration curve with gain {gain} and offset {offset}")
        curves[channel] = _ramp(lambda x, g=gamma: x ** g, size)
    if set(curves) != {"Red", "Green", "Blue"}:
        raise UnreadableCalibration("a Windows calibration without three curves")
    return [curves["Red"], curves["Green"], curves["Blue"]]


def _ramp(curve, size: int) -> list[int]:
    return [_word(curve(i / (size - 1))) for i in range(size)]


def _resample(values: list[float], size: int) -> list[int]:
    """A curve of any length as `size` entries, interpolated linearly."""
    last = len(values) - 1
    out = []
    for i in range(size):
        position = i * last / (size - 1)
        low = int(position)
        high = min(low + 1, last)
        out.append(_word(values[low] + (values[high] - values[low]) * (position - low)))
    return out


def _word(value: float) -> int:
    return max(0, min(65535, round(value * 65535)))
