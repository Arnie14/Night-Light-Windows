# SPDX-License-Identifier: GPL-3.0-only
"""Reading monitor identity from the EDID."""

from nightlight.core import edid
from nightlight.hardware.win32 import stable_key


def test_identity_fields():
    data = edid.build("DEL", 0x41A2, 123456, "DELL U2720Q", "7XKJ9F3", year=2021, size_cm=(60, 34))
    parsed = edid.parse(data)
    assert (parsed.manufacturer, parsed.product_code, parsed.hardware_id) == ("DEL", 0x41A2, "DEL41A2")
    assert (parsed.name, parsed.serial_text, parsed.serial) == ("DELL U2720Q", "7XKJ9F3", "7XKJ9F3")
    assert (parsed.year, parsed.width_cm, parsed.height_cm, parsed.diagonal_inches) == (2021, 60, 34, 27.2)
    assert parsed.digital


def test_the_serial_number_is_used_when_there_is_no_serial_text():
    assert edid.parse(edid.build(serial_number=987654)).serial == "987654"


def test_placeholder_serials_are_no_serial():
    for number in (0, 0x01010101):
        assert edid.parse(edid.build(serial_number=number)).serial == ""


def test_invalid_data_is_rejected():
    good = edid.build()
    assert edid.parse(None) is None
    assert edid.parse(b"") is None
    assert edid.parse(good[:100]) is None  # truncated
    assert edid.parse(b"\x01" + good[1:]) is None  # bad header
    corrupted = bytearray(good)
    corrupted[20] ^= 0xFF
    assert edid.parse(bytes(corrupted)) is None  # checksum


def test_extension_blocks_are_ignored():
    assert edid.parse(edid.build() + bytes(128)).hardware_id == "DEL41A2"


def test_real_laptop_panel():
    # A 15.6" AUO panel's EDID as Windows stores it: no name, no serial.
    parsed = edid.parse(edid.build("AUO", 0x24ED, 0, "", "", year=2016, size_cm=(34, 19)))
    assert parsed.hardware_id == "AUO24ED" and parsed.serial == "" and parsed.diagonal_inches == 15.3


def test_stable_keys():
    assert stable_key("DEL41A2", "7XKJ9F3") == "DEL41A2:7XKJ9F3"
    assert stable_key("DEL41A2", "") == "DEL41A2"  # what older versions used
    assert stable_key("DEL41A2", "AB 12/x") == "DEL41A2:AB12x"  # safe characters only
    assert stable_key("", "123") == ""
