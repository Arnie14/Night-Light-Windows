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

from nightlight.ui.widgets import SecondaryLabel, blend, set_font, set_heading_font

RADIUS = 8.0
SLIDER_HINT = "Let go of the slider to go back"


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
        set_heading_font(self.value, 2.0)
        self.hint = SecondaryLabel(SLIDER_HINT, scale=0.92)
        for label in (title, self.value, self.hint):
            label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(label)

    def show_kelvin(self, kelvin: int, near: QWidget | None = None, hint: str = "") -> None:
        """Show (or update) the note, in the middle of the screen `near` is on;
        `hint` replaces the line about the slider (the 24-hour preview's time)."""
        self.value.setText(f"{kelvin} K")
        self.hint.setText(hint or SLIDER_HINT)
        if self.isVisible() and self.sizeHint() == self.size():
            return
        self.adjustSize()  # and centered again: the hint's length can change
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
