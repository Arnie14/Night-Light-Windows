# SPDX-License-Identifier: GPL-3.0-only
"""Reading a monitor's EDID: who made it, which model, its serial number.

The EDID is the block of identity data every monitor sends its GPU. Windows
keeps a copy per monitor in the registry, so it can be read without talking
to the monitor at all (unlike DDC/CI). Its manufacturer and product code
don't change with the port or cable, and its serial number tells identical
monitors apart, which together make a stable identity for remembering each
display's settings. Pure parsing, no Windows calls, so it is testable anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass

HEADER = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
BLOCK = 128
_DESCRIPTOR_SERIAL = 0xFF
_DESCRIPTOR_NAME = 0xFC


@dataclass(frozen=True)
class Edid:
    manufacturer: str  # three-letter PnP id, e.g. "DEL"
    product_code: int
    serial_number: int  # 0 when the monitor doesn't fill it in
    serial_text: str  # the serial printed on the label, if the EDID has it
    name: str  # model name, e.g. "DELL U2720Q"; "" if absent
    week: int
    year: int  # of manufacture (or the model year)
    width_cm: int  # 0 when unknown (projectors, some TVs)
    height_cm: int
    digital: bool

    @property
    def hardware_id(self) -> str:
        """Manufacturer and product code as Windows writes them: "DEL41A2"."""
        return f"{self.manufacturer}{self.product_code:04X}"

    @property
    def serial(self) -> str:
        """The best serial available: the label text, else the number; "" if neither."""
        if self.serial_text:
            return self.serial_text
        # Many monitors leave the number at 0, or at 0x01010101 as a placeholder.
        return str(self.serial_number) if self.serial_number not in (0, 0x01010101, 0xFFFFFFFF) else ""

    @property
    def diagonal_inches(self) -> float:
        return round((self.width_cm**2 + self.height_cm**2) ** 0.5 / 2.54, 1) if self.width_cm and self.height_cm else 0.0


def parse(data: bytes | bytearray | None) -> Edid | None:
    """The EDID's identity fields, or None if `data` isn't a valid EDID base block."""
    if not data or len(data) < BLOCK or bytes(data[:8]) != HEADER:
        return None
    block = bytes(data[:BLOCK])
    if sum(block) % 256 != 0:
        return None  # corrupted copy
    code = int.from_bytes(block[8:10], "big")
    manufacturer = "".join(chr(ord("A") - 1 + ((code >> shift) & 0x1F)) for shift in (10, 5, 0))
    if not manufacturer.isalpha() or not manufacturer.isupper():
        return None
    name = serial_text = ""
    for offset in range(54, 126, 18):
        descriptor = block[offset : offset + 18]
        if descriptor[0:3] != b"\x00\x00\x00":
            continue  # a timing descriptor
        if descriptor[3] == _DESCRIPTOR_NAME:
            name = _text(descriptor)
        elif descriptor[3] == _DESCRIPTOR_SERIAL:
            serial_text = _text(descriptor)
    return Edid(
        manufacturer=manufacturer,
        product_code=int.from_bytes(block[10:12], "little"),
        serial_number=int.from_bytes(block[12:16], "little"),
        serial_text=serial_text,
        name=name,
        week=block[16],
        year=1990 + block[17],
        width_cm=block[21],
        height_cm=block[22],
        digital=bool(block[20] & 0x80),
    )


def _text(descriptor: bytes) -> str:
    raw = descriptor[5:18].split(b"\x0a", 1)[0]
    return raw.decode("cp437", "replace").strip()


def build(manufacturer: str = "DEL", product_code: int = 0x41A2, serial_number: int = 0, name: str = "", serial_text: str = "",
          year: int = 2021, size_cm: tuple[int, int] = (60, 34)) -> bytes:
    """A valid EDID base block with the given identity, for tests."""
    block = bytearray(BLOCK)
    block[:8] = HEADER
    code = 0
    for letter in manufacturer:
        code = (code << 5) | (ord(letter) - ord("A") + 1)
    block[8:10] = code.to_bytes(2, "big")
    block[10:12] = product_code.to_bytes(2, "little")
    block[12:16] = serial_number.to_bytes(4, "little")
    block[16], block[17] = 10, year - 1990
    block[18], block[19] = 1, 4
    block[20] = 0x80
    block[21], block[22] = size_cm
    offset = 54
    for kind, text in ((_DESCRIPTOR_NAME, name), (_DESCRIPTOR_SERIAL, serial_text)):
        if text:
            payload = text.encode("ascii")[:13]
            payload = payload + b"\x0a" + b" " * (12 - len(payload)) if len(payload) < 13 else payload
            block[offset : offset + 18] = b"\x00\x00\x00" + bytes([kind, 0]) + payload[:13]
            offset += 18
    block[127] = (-sum(block[:127])) % 256
    return bytes(block)
