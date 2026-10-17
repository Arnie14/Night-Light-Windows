# SPDX-License-Identifier: GPL-3.0-only
"""Icons drawn in code, so the app looks the same without an icon theme (Windows has none)."""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QTransform

_SIZE = 64  # drawing coordinates
_PIXMAP_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)  # tray sizes up to taskbar and Alt+Tab


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


def refresh_glyph(color: QColor) -> QIcon:
    """A circular arrow, for "detect monitors again"."""

    def draw(painter: QPainter) -> None:
        pen = QPen(color, 5.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(QRectF(13, 13, 38, 38), 60 * 16, 290 * 16)
        # Arrowhead at the arc's start (60°), pointing along it.
        tip = QPointF(32 + 19 * math.cos(math.radians(60)), 32 - 19 * math.sin(math.radians(60)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon([tip + QPointF(-9, -9), tip + QPointF(9, 2), tip + QPointF(-6, 11)])

    return _icon(draw)


def gear_glyph(color: QColor) -> QIcon:
    """A cog, for "Configure…"."""

    def draw(painter: QPainter) -> None:
        center = QPointF(_SIZE / 2, _SIZE / 2)
        gear = QPainterPath()
        gear.addEllipse(center, 18, 18)
        for i in range(8):
            tooth = QPainterPath()
            tooth.addRoundedRect(QRectF(-5.5, -27, 11, 12), 2.5, 2.5)
            gear.addPath(QTransform().translate(center.x(), center.y()).rotate(i * 45).map(tooth))
        hole = QPainterPath()
        hole.addEllipse(center, 7.5, 7.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(gear.simplified().subtracted(hole))

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
