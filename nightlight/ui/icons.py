"""Icons drawn in code, so the app looks the same without an icon theme (Windows has none)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

_SIZE = 64


def night_icon() -> QIcon:
    """A crescent moon."""
    pixmap = _blank()
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#f5c26b"))
    painter.drawEllipse(QRectF(6, 6, 52, 52))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.drawEllipse(QRectF(22, -2, 48, 48))
    painter.end()
    return QIcon(pixmap)


def day_icon() -> QIcon:
    """A sun."""
    pixmap = _blank()
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
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
    painter.end()
    return QIcon(pixmap)


def _blank() -> QPixmap:
    pixmap = QPixmap(_SIZE, _SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    return pixmap
