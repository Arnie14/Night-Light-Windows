# SPDX-License-Identifier: GPL-3.0-only
"""Writes Winlight's .ico from the icon the app draws in code (ui/icons.py),
so the executable, installer and shortcuts match the tray and windows.

    python packaging/make_icon.py build/winlight.ico
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QGuiApplication

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)  # what Explorer, the taskbar and Alt+Tab ask for


def png_bytes(icon, size: int) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    icon.pixmap(size, size).toImage().save(buffer, "PNG")
    return bytes(data.data())


def write_ico(path: Path) -> None:
    """An ICO holding one PNG per size (supported since Windows Vista)."""
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841, pixmaps need one
    from nightlight.ui import icons

    icon = icons.night_icon()
    images = [(size, png_bytes(icon, size)) for size in SIZES]
    offset = 6 + 16 * len(images)
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 means 256 in an ICO directory
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(blobs))
        blobs += png
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)


if __name__ == "__main__":
    write_ico(Path(sys.argv[1] if len(sys.argv) > 1 else "build/winlight.ico"))
