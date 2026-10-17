# SPDX-License-Identifier: GPL-3.0-only
"""Icons drawn in code, so the app looks the same without an icon theme (Windows has none)."""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

_SIZE = 64  # drawing coordinates
_PIXMAP_SIZES = (16, 20, 24, 32, 40, 48, 64)


def night_icon() -> QIcon:
    """A crescent moon."""

    def draw(painter: QPainter) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f5c26b"))
        painter.drawEllipse(QRectF(6, 6, 52, 52))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.drawEllipse(QRectF(22, -2, 48, 48))

    return _icon(draw)


def day_icon() -> QIcon:
    """A sun."""

    def draw(painter: QPainter) -> None:
        color = QColor("#f2a93b")
        pen = QPen(color, 5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        center = QPointF(_SIZE / 2, _SIZE / 2)
        for i in range(8):
            angle = i * math.pi / 4
            direction = QPointF(math.cos(angle), math.sin(angle))
            painter.drawLine(center + direction * 22, center + direction * 29)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center, 14, 14)

    return _icon(draw)


def _icon(draw: Callable[[QPainter], None]) -> QIcon:
    """Draw at each size the tray may ask for (16-32 px depending on DPI)
    rather than letting Windows shrink one large image into a blur."""
    icon = QIcon()
    for size in _PIXMAP_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / _SIZE, size / _SIZE)
        draw(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
