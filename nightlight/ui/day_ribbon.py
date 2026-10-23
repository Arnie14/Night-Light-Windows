# SPDX-License-Identifier: GPL-3.0-only
"""A day of night light at a glance: a strip from midnight to midnight, each
moment painted in the white the screen shows then (the same blackbody table
as the gamma ramps), with a needle at the time shown.

In Settings it follows the settings on the page, applied or not, so changing
the times shows the day changing; the 24-hour preview moves its needle. The
panel shows a slim one under Night Light.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta, tzinfo

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from nightlight.core.clock import wall_time
from nightlight.ui.widgets import blend, kelvin_color

SAMPLES = 144  # one color per 10 minutes of a 24-hour day: transitions read as gradients
BAR = 22  # px, the full ribbon
SLIM_BAR = 5
HOURS = (0, 6, 12, 18, 24)
REDRAW_MS = 60_000  # the needle moves a pixel or so a minute
NEUTRAL = 6500


def day_bounds(moment: datetime, zone: tzinfo | None) -> tuple[datetime, datetime]:
    """The midnight `moment`'s day starts at and the next one, in `zone`:
    23 or 25 hours apart on a daylight saving change (core/clock.py)."""
    if zone is None:
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    day = moment.astimezone(zone).date()
    return wall_time(day, time(0), zone), wall_time(day + timedelta(days=1), time(0), zone)


class DayRibbon(QWidget):
    def __init__(self, temperature_at: Callable[[datetime], int], now: Callable[[], datetime], slim: bool = False,
                 zone: Callable[[], tzinfo | None] = lambda: None) -> None:
        super().__init__()
        self._temperature_at = temperature_at
        self._now = now
        self._zone = zone
        self._slim = slim
        self._marker: datetime | None = None  # a moment to show instead of now (the 24-hour preview)
        self._kelvins: list[int] | None = None  # one per sample of _day
        self._day: tuple[date, datetime] | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(SLIM_BAR + 2 if slim else BAR + self.fontMetrics().height() + 6)
        self.setToolTip("Night light today, from midnight to midnight; the line is now")
        self.setAccessibleName("Night light today")
        self._timer = QTimer(self, interval=REDRAW_MS)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    def refresh(self) -> None:
        """The settings changed: work the day out again."""
        self._kelvins = None
        self.update()

    def set_marker(self, moment: datetime | None) -> None:
        self._marker = moment
        self.update()

    def kelvins(self, start: datetime, end: datetime) -> list[int]:
        """The temperature at each sample from `start` to `end`, worked out
        once per day and settings."""
        if self._kelvins is None or self._day != (start.date(), start):
            self._day = (start.date(), start)
            step = (end - start) / SAMPLES
            try:
                self._kelvins = [self._temperature_at(start + step * i) for i in range(SAMPLES + 1)]
            except Exception:  # a setting half typed in mustn't stop the window painting
                self._kelvins = [NEUTRAL] * (SAMPLES + 1)
        return self._kelvins

    def paintEvent(self, _event) -> None:
        if self.width() < 4:
            return
        moment = self._marker or self._now()
        start, end = day_bounds(moment, self._zone())
        span = end - start
        palette = self.palette()
        window, text = palette.color(QPalette.ColorRole.Window), palette.color(QPalette.ColorRole.WindowText)
        dark = window.lightnessF() < 0.5
        bar_height = SLIM_BAR if self._slim else BAR
        bar = QRectF(0.5, 0.5, self.width() - 1, bar_height)
        gradient = QLinearGradient(QPointF(bar.left(), 0), QPointF(bar.right(), 0))
        for i, kelvin in enumerate(self.kelvins(start, end)):
            # Softened toward the window in dark mode, so day isn't a glaring white bar.
            color = kelvin_color(kelvin)
            gradient.setColorAt(i / SAMPLES, blend(window, color, 0.78) if dark else color)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(blend(window, text, 0.18), 1))
        painter.setBrush(gradient)
        radius = bar_height / 2 if self._slim else 6
        painter.drawRoundedRect(bar, radius, radius)

        x = bar.left() + bar.width() * max(0.0, min(1.0, (moment - start) / span))
        # The needle, in the text color with a halo of the window's, so it shows on amber and on white.
        for color, width in ((window, 4), (text, 2)):
            pen = QPen(color, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, bar.top() + 1.5), QPointF(x, bar.bottom() - 1.5))
        if self._slim:
            return
        painter.setPen(blend(window, text, 0.55))
        font = painter.font()
        font.setPointSizeF(font.pointSizeF() * 0.85)
        painter.setFont(font)
        label_top = bar.bottom() + 3
        zone = self._zone()
        for hour in HOURS:
            label = f"{hour:02d}:00"
            # Where the clock shows that hour: off the even spacing on a daylight saving change.
            at = end if hour == 24 else (start + timedelta(hours=hour) if zone is None else wall_time(start.astimezone(zone).date(),
                                                                                                         time(hour), zone))
            hx = bar.left() + bar.width() * (at - start) / span
            width = painter.fontMetrics().horizontalAdvance(label)
            left = min(max(hx - width / 2, bar.left()), bar.right() - width)  # the ends stay inside
            painter.drawText(QRectF(left, label_top, width + 2, self.height() - label_top), Qt.AlignmentFlag.AlignLeft, label)
