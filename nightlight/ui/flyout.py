# SPDX-License-Identifier: GPL-3.0-only
"""The quick-access panel opened from the tray, like Plasma's "Brightness and Color" applet."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nightlight.controller import NightLightController
from nightlight.hardware.brightness import MonitorInfo
from nightlight.monitors import MonitorManager
from nightlight.ui import icons
from nightlight.ui.night_light_text import build_override_menu, is_night_light_on, night_light_status
from nightlight.ui.placement import flyout_position
from nightlight.ui.theme import theme
from nightlight.ui.widgets import MonitorBrightness

# Room around the card for its shadow. It also is the gap to the taskbar, since
# the flyout is placed flush against it.
SHADOW = 12
GLYPH = QSize(18, 18)


class Flyout(QWidget):
    configureRequested = Signal()

    def __init__(self, monitors: MonitorManager, controller: NightLightController, as_window: bool) -> None:
        super().__init__()
        self._monitors = monitors
        self._controller = controller
        self._rows: dict[int, MonitorBrightness] = {}
        self._hidden_at = 0.0
        self._as_window = as_window

        self.setWindowTitle("Brightness and Color")
        if as_window:
            self.setWindowFlags(Qt.WindowType.Window)
        else:
            # A rounded card with a soft shadow, drawn on a see-through window.
            self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setProperty("windowed", as_window)

        outer = QVBoxLayout(self)
        margin = 0 if as_window else SHADOW
        outer.setContentsMargins(margin, margin, margin, margin)
        card = QFrame(objectName="card")
        card.setProperty("windowed", as_window)
        card.setMinimumWidth(360)
        if not as_window:
            shadow = QGraphicsDropShadowEffect(card, blurRadius=2 * SHADOW, xOffset=0, yOffset=3)
            shadow.setColor(QColor(0, 0, 0, 110))
            card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Brightness and Color")
        title.setProperty("title", True)
        self._refresh_button = QToolButton(toolTip="Detect monitors again", iconSize=GLYPH)
        self._refresh_button.clicked.connect(self._refresh)
        header.addWidget(title, 1)
        header.addWidget(self._refresh_button)
        layout.addLayout(header)

        self._monitor_status = QLabel("Detecting monitors…")
        self._monitor_status.setWordWrap(True)
        self._monitor_status.setProperty("dim", True)
        layout.addWidget(self._monitor_status)
        self._monitor_layout = QVBoxLayout()
        self._monitor_layout.setSpacing(16)
        layout.addLayout(self._monitor_layout)

        section = QFrame(objectName="section")
        night = QHBoxLayout(section)
        night.setContentsMargins(14, 12, 12, 12)
        night.setSpacing(12)
        self._night_icon = QLabel()
        night_labels = QVBoxLayout()
        night_labels.setSpacing(1)
        night_title = QLabel("Night Light")
        night_title.setProperty("heading", True)
        night_labels.addWidget(night_title)
        self._night_text = QLabel()
        self._night_text.setWordWrap(True)
        self._night_text.setProperty("dim", True)
        night_labels.addWidget(self._night_text)
        self._override_menu = QMenu(self)
        self._toggle = QPushButton()
        self._toggle.clicked.connect(self._on_toggle_clicked)
        self._configure_button = QToolButton(toolTip="Configure Night Light…", iconSize=GLYPH)
        self._configure_button.clicked.connect(self.configureRequested)
        night.addWidget(self._night_icon)
        night.addLayout(night_labels, 1)
        night.addWidget(self._toggle)
        night.addWidget(self._configure_button)
        layout.addWidget(section)

        theme.changed.connect(self._update_glyphs)
        self._update_glyphs()

        monitors.monitorsChanged.connect(self._rebuild)
        monitors.brightnessChanged.connect(self._on_brightness)
        monitors.error.connect(self._monitor_status.setText)
        controller.changed.connect(self._update_night_light)
        self._rebuild(monitors.monitors)
        self._update_night_light()

    def toggle(self, anchor: QRect | None = None) -> None:
        # Clicking the tray icon while the popup is open first closes it (an
        # outside click), then delivers the click; don't reopen immediately.
        if self.isVisible() or time.monotonic() - self._hidden_at < 0.3:
            self.hide()
            return
        self.show_near(anchor)

    def show_near(self, anchor: QRect | None = None) -> None:
        self._monitors.reread_brightness()
        self.adjustSize()
        if not self._as_window:
            self.move(self._popup_position(anchor))
        self.show()
        self.raise_()
        self.activateWindow()

    def hideEvent(self, event) -> None:
        self._hidden_at = time.monotonic()
        super().hideEvent(event)

    def _popup_position(self, anchor: QRect | None) -> QPoint:
        point = anchor.center() if anchor and anchor.isValid() else QCursor.pos()
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        # No extra margin: the window's own shadow border is the gap.
        return flyout_position(self.sizeHint(), screen.geometry(), screen.availableGeometry(), anchor, margin=0)

    def _update_glyphs(self) -> None:
        color = theme.tokens.text_dim
        self._refresh_button.setIcon(icons.refresh_glyph(color))
        self._configure_button.setIcon(icons.gear_glyph(color))

    def _refresh(self) -> None:
        self._monitor_status.setText("Detecting monitors…")
        self._monitor_status.show()
        self._monitors.refresh()

    def _rebuild(self, monitors: list[MonitorInfo]) -> None:
        for row in self._rows.values():
            row.deleteLater()
        self._rows.clear()
        for info in monitors:
            row = MonitorBrightness(info)
            row.brightnessRequested.connect(self._monitors.set_brightness)
            if info.index in self._monitors.brightness:
                row.set_brightness(self._monitors.brightness[info.index])
            self._monitor_layout.addWidget(row)
            self._rows[info.index] = row
        if monitors:
            self._monitor_status.hide()
        else:
            self._monitor_status.setText("No monitors with DDC/CI brightness control found.")
            self._monitor_status.show()
        self.adjustSize()

    def _on_brightness(self, index: int, value: int | None) -> None:
        if row := self._rows.get(index):
            row.set_brightness(value)

    def _update_night_light(self) -> None:
        controller = self._controller
        self._night_icon.setPixmap((icons.night_icon() if is_night_light_on(controller) else icons.day_icon()).pixmap(30, 30))
        self._night_text.setText(night_light_status(controller))
        if self._override_menu.isVisible():
            return  # don't rebuild the menu under the user's cursor
        self._toggle.setText(build_override_menu(controller, self._override_menu))
        has_menu = not self._override_menu.isEmpty()
        self._toggle.setMenu(self._override_menu if has_menu else None)
        self._toggle.setProperty("menu", has_menu)
        self._toggle.style().polish(self._toggle)  # the stylesheet depends on the property

    def _on_toggle_clicked(self) -> None:
        # Only reached without a menu, i.e. while an override is active.
        self._controller.clear_override()
