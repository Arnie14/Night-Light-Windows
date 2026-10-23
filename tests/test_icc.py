# SPDX-License-Identifier: GPL-3.0-only
import struct

import pytest

from nightlight.core import icc
from nightlight.core.colortemp import identity_ramp


def profile(tags: dict[bytes, bytes]) -> bytes:
    """A minimal ICC profile: the header's signature, the tag table, the tags."""
    header = bytearray(128)
    header[36:40] = b"acsp"
    table = struct.pack(">I", len(tags))
    data = b""
    start = 128 + 4 + 12 * len(tags)
    for signature, body in tags.items():
        table += struct.pack(">4sII", signature, start + len(data), len(body))
        data += body
    return bytes(header) + table + data


def vcgt_table(curves: list[list[int]], entry_size: int = 2) -> bytes:
    code = ">" + ("H" if entry_size == 2 else "B") * len(curves[0])
    body = b"".join(struct.pack(code, *curve) for curve in curves)
    return b"vcgt" + bytes(4) + struct.pack(">IHHH", 0, len(curves), len(curves[0]), entry_size) + body


def vcgt_formula(*channels: tuple[float, float, float]) -> bytes:
    values = [round(v * 65536) for channel in channels for v in channel]
    return b"vcgt" + bytes(4) + struct.pack(">I9i", 1, *values)


def wcs(curves: str) -> bytes:
    """An MS00 tag as Windows' Display Color Calibration writes it (trimmed)."""
    xml = ('<?xml version="1.0" encoding="utf-16"?><cdm:ColorDeviceModel><cdm:Calibration><cal:AdapterGammaConfiguration>'
           f"<cal:ParameterizedCurves>{curves}</cal:ParameterizedCurves></cal:AdapterGammaConfiguration></cdm:Calibration>"
           "</cdm:ColorDeviceModel>")
    return b"MS10" + bytes(4) + xml.encode("utf-16-le")


def dccw_curves(gamma: float, gain: float = 1.0, offset: float = 0.0) -> str:
    return "".join(f'<wcs:{c}TRC Gamma="{gamma}" Gain="{gain:f}" Offset1="{offset}"/>' for c in ("Red", "Green", "Blue"))


def test_a_profile_without_a_calibration_has_none():
    assert icc.calibration_ramp(profile({b"rTRC": b"curv" + bytes(8)})) is None


def test_a_16_bit_vcgt_table_per_channel():
    red = [round(i * 65535 / 255) for i in range(256)]
    blue = [round(i * 60000 / 255) for i in range(256)]
    ramp = icc.calibration_ramp(profile({b"vcgt": vcgt_table([red, red, blue])}))
    assert ramp[0] == red and ramp[1] == red and ramp[2] == blue


def test_a_shorter_8_bit_table_for_every_channel_is_stretched_to_the_ramp():
    ramp = icc.calibration_ramp(profile({b"vcgt": vcgt_table([[0, 128, 255]], entry_size=1)}))
    assert ramp[0] == ramp[1] == ramp[2]
    assert ramp[0][0] == 0 and ramp[0][-1] == 65535 and abs(ramp[0][128] - 128 * 257) < 300


def test_a_vcgt_formula():
    ramp = icc.calibration_ramp(profile({b"vcgt": vcgt_formula((1.0, 0.0, 1.0), (1.0, 0.0, 1.0), (2.0, 0.0, 0.9))}))
    assert ramp[0] == identity_ramp()[0]
    assert abs(ramp[2][-1] - 0.9 * 65535) <= 2  # 0.9 in 16.16 fixed point
    assert ramp[2][128] < ramp[0][128] / 2 + 100


def test_windows_calibration_curves():
    ramp = icc.calibration_ramp(profile({b"MS00": wcs(dccw_curves(0.790909))}))
    assert ramp[0] == ramp[1] == ramp[2]
    assert ramp[0][0] == 0 and ramp[0][-1] == 65535 and ramp[0][128] == round((128 / 255) ** 0.790909 * 65535)


def test_windows_curves_with_a_gain_are_not_guessed_at():
    # How Windows combines gain, offset and gamma isn't documented.
    with pytest.raises(icc.UnreadableCalibration):
        icc.calibration_ramp(profile({b"MS00": wcs(dccw_curves(1.0, gain=0.9))}))


def test_a_wcs_profile_without_calibration_has_none():
    xml = '<cdm:ColorDeviceModel><cdm:Description/></cdm:ColorDeviceModel>'.encode("utf-16-le")
    assert icc.calibration_ramp(profile({b"MS00": b"MS10" + bytes(4) + xml})) is None


@pytest.mark.parametrize("data", [b"", b"not a profile" * 20, profile({b"vcgt": b"vcgt" + bytes(4) + struct.pack(">I", 7)})[:-2]])
def test_broken_files_are_errors_not_crashes(data):
    with pytest.raises(ValueError):
        icc.calibration_ramp(data)


def test_an_unknown_vcgt_type_is_unreadable():
    with pytest.raises(icc.UnreadableCalibration):
        icc.calibration_ramp(profile({b"vcgt": b"vcgt" + bytes(4) + struct.pack(">I", 7)}))
