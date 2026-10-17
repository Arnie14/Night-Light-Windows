# SPDX-License-Identifier: GPL-3.0-only
"""Icons drawn in code, so the app looks the same without an icon theme (Windows has none)."""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QTransform

_SIZE = 64  # drawing coordinates
_PIXMAP_SIZES = (16, 20, 24, 32, 40, 48, 64)

SUN_TOP, SUN_BOTTOM = "#ffd45e", "#ff9a2e"
MOON_TOP, MOON_BOTTOM = "#ffd98a", "#ff9f4a"


def night_icon() -> QIcon:
    """A warm crescent moon with a star."""

    def draw(painter: QPainter) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_gradient(MOON_TOP, MOON_BOTTOM))
        moon = QPainterPath()
        moon.addEllipse(QRectF(5, 7, 50, 50))
        bite = QPainterPath()
        bite.addEllipse(QRectF(21, -1, 46, 46))
        painter.drawPath(moon.subtracted(bite))
        _star(painter, QPointF(47, 44), 7.5)

    return _icon(draw)


def day_icon() -> QIcon:
    """A sun with soft rays."""

    def draw(painter: QPainter) -> None:
        _sun(painter, QPointF(_SIZE / 2, _SIZE / 2), 15, 22, 30, 5.5, _gradient(SUN_TOP, SUN_BOTTOM))

    return _icon(draw)


def brightness_glyph(color: str, size: int = 18) -> QIcon:
    """A small outlined sun, the label for brightness sliders."""

    def draw(painter: QPainter) -> None:
        _sun(painter, QPointF(_SIZE / 2, _SIZE / 2), 12, 22, 30, 5, QColor(color))

    return _icon(draw, (size, size * 2))


def refresh_glyph(color: str, size: int = 18) -> QIcon:
    def draw(painter: QPainter) -> None:
        pen = QPen(QColor(color), 6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # An almost closed circle, open at the top right, with an arrowhead there.
        painter.drawArc(QRectF(12, 12, 40, 40), 70 * 16, -300 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        tip = QPointF(32 + 20 * math.cos(math.radians(70)), 32 - 20 * math.sin(math.radians(70)))
        painter.drawPolygon([tip + QPointF(-3, -13), tip + QPointF(13, 4), tip + QPointF(-12, 7)])

    return _icon(draw, (size, size * 2))


def gear_glyph(color: str, size: int = 18) -> QIcon:
    def draw(painter: QPainter) -> None:
        center = QPointF(_SIZE / 2, _SIZE / 2)
        gear = QPainterPath()
        gear.addEllipse(center, 17, 17)
        for i in range(8):
            tooth = QPainterPath()
            tooth.addRoundedRect(QRectF(-5, -28, 10, 14), 2.5, 2.5)
            gear.addPath(_rotated(tooth, center, i * 45))
        hole = QPainterPath()
        hole.addEllipse(center, 8, 8)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPath(gear.simplified().subtracted(hole))

    return _icon(draw, (size, size * 2))


def _rotated(path: QPainterPath, center: QPointF, degrees: float) -> QPainterPath:
    return QTransform().translate(center.x(), center.y()).rotate(degrees).map(path)


def _gradient(top: str, bottom: str) -> QBrush:
    gradient = QLinearGradient(0, 0, 0, _SIZE)
    gradient.setColorAt(0, QColor(top))
    gradient.setColorAt(1, QColor(bottom))
    return QBrush(gradient)


def _sun(painter: QPainter, center: QPointF, radius: float, ray_from: float, ray_to: float, ray_width: float, brush) -> None:
    pen = QPen(QBrush(brush), ray_width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    for i in range(8):
        angle = i * math.pi / 4
        direction = QPointF(math.cos(angle), math.sin(angle))
        painter.drawLine(center + direction * ray_from, center + direction * ray_to)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(brush)
    painter.drawEllipse(center, radius, radius)


def _star(painter: QPainter, center: QPointF, radius: float) -> None:
    """A four-pointed sparkle."""
    star = QPainterPath()
    for i in range(8):
        angle = i * math.pi / 4 - math.pi / 2
        r = radius if i % 2 == 0 else radius * 0.3
        point = center + QPointF(math.cos(angle), math.sin(angle)) * r
        star.moveTo(point) if i == 0 else star.lineTo(point)
    star.closeSubpath()
    painter.setBrush(QColor("#fff1c9"))
    painter.drawPath(star)


def _icon(draw: Callable[[QPainter], None], sizes: tuple[int, ...] = _PIXMAP_SIZES) -> QIcon:
    """Draw at each size the tray may ask for (16-32 px depending on DPI)
    rather than letting Windows shrink one large image into a blur."""
    icon = QIcon()
    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(size / _SIZE, size / _SIZE)
        draw(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
