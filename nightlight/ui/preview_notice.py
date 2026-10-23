# SPDX-License-Identifier: GPL-3.0-only
"""The note in the middle of the screen while a night light slider is held.

KDE shows one too: the whole screen turns warm (or cool) while the slider is
dragged, and without it that looks like something went wrong. It says what
is happening, and goes as soon as the slider is let go.

It never takes focus or clicks (Windows' foreground app stays the same, so
pausing for apps isn't affected) and has no taskbar button.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFont, QGuiApplication, QPainter, QPalette, QPen
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from nightlight.ui.widgets import HEADING_WEIGHT, SecondaryLabel, blend, set_font

RADIUS = 8.0


class PreviewNotice(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput)
        for attribute in (Qt.WidgetAttribute.WA_ShowWithoutActivating, Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                          Qt.WidgetAttribute.WA_TranslucentBackground):
            self.setAttribute(attribute)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 18, 28, 18)
        layout.setSpacing(2)
        title = QLabel("Night light preview")
        set_font(title, 1.05, QFont.Weight.DemiBold)
        self.value = QLabel()
        set_font(self.value, 2.0, HEADING_WEIGHT)
        hint = SecondaryLabel("Let go of the slider to go back", scale=0.92)
        for label in (title, self.value, hint):
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(label)

    def show_kelvin(self, kelvin: int, near: QWidget | None = None) -> None:
        """Show (or update) the note, in the middle of the screen `near` is on."""
        self.value.setText(f"{kelvin} K")
        if self.isVisible():
            return
        self.adjustSize()
        screen = (near.screen() if near is not None else None) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.geometry()
            self.move(area.center().x() - self.width() // 2, area.center().y() - self.height() // 2)
        self.show()

    def paintEvent(self, event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(blend(window, palette.color(QPalette.ColorRole.WindowText), 0.2), 1))
        painter.setBrush(window)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
