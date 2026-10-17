# SPDX-License-Identifier: GPL-3.0-only
"""The quick-access panel opened from the tray, like Plasma's "Brightness and Color" applet."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
)

from nightlight import APP_NAME
from nightlight.controller import NightLightController
from nightlight.hardware.brightness import MonitorInfo
from nightlight.monitors import MonitorManager
from nightlight.ui import icons
from nightlight.ui.night_light_text import build_override_menu, is_night_light_on, night_light_status
from nightlight.ui.placement import flyout_position
from nightlight.ui.widgets import GlyphButton, MonitorBrightness, SecondaryLabel, Separator, set_font

WIDTH = 340


class Flyout(QFrame):
    configureRequested = Signal()

    def __init__(self, monitors: MonitorManager, controller: NightLightController, as_window: bool) -> None:
        super().__init__()
        self._monitors = monitors
        self._controller = controller
        self._rows: dict[int, MonitorBrightness] = {}
        self._linked = False
        self._hidden_at = 0.0
        self._as_window = as_window

        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.WindowType.Window if as_window else Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 14, 16)
        layout.setSpacing(14)

        header = QHBoxLayout()
        title = QLabel("Brightness and Color")
        set_font(title, 1.15, QFont.Weight.DemiBold)
        refresh = GlyphButton(icons.refresh_glyph, "Detect monitors again")
        refresh.clicked.connect(self._refresh)
        header.addWidget(title, 1)
        header.addWidget(refresh)
        layout.addLayout(header)

        self._monitor_status = SecondaryLabel("Detecting monitors…")
        self._monitor_status.setWordWrap(True)
        layout.addWidget(self._monitor_status)
        self._monitor_layout = QVBoxLayout()
        self._monitor_layout.setContentsMargins(0, 0, 4, 0)  # line up with the header's right edge
        self._monitor_layout.setSpacing(14)
        layout.addLayout(self._monitor_layout)

        layout.addSpacing(2)
        layout.addWidget(Separator())
        layout.addSpacing(2)

        night = QHBoxLayout()
        night.setSpacing(10)
        self._night_icon = QLabel()
        night_labels = QVBoxLayout()
        night_labels.setSpacing(0)
        night_title = QLabel("Night Light")
        set_font(night_title, weight=QFont.Weight.DemiBold)
        night_labels.addWidget(night_title)
        self._night_text = SecondaryLabel(scale=0.92)
        self._night_text.setWordWrap(True)
        night_labels.addWidget(self._night_text)
        self._override_menu = QMenu(self)
        self._toggle = QPushButton()
        self._toggle.clicked.connect(self._on_toggle_clicked)
        configure = GlyphButton(icons.gear_glyph, "Settings")
        configure.clicked.connect(self.configureRequested)
        night.addWidget(self._night_icon)
        night.addLayout(night_labels, 1)
        night.addWidget(self._toggle)
        night.addWidget(configure)
        layout.addLayout(night)

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
        return flyout_position(self.sizeHint(), screen.geometry(), screen.availableGeometry(), anchor)

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
            row.brightnessRequested.connect(self._on_brightness_requested)
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

    def set_linked(self, linked: bool) -> None:
        """Whether moving one monitor's slider moves them all, like Monitorian's
        "unison" and Twinkle Tray's "linked levels"."""
        self._linked = linked

    def _on_brightness_requested(self, index: int, value: int) -> None:
        self._monitors.set_brightness(index, value)
        if self._linked:
            for other in self._rows.values():
                if other.info.index != index and other.info.supported:
                    self._monitors.set_brightness(other.info.index, other.clamp(value))

    def _on_brightness(self, index: int, value: int | None) -> None:
        if row := self._rows.get(index):
            row.set_brightness(value)

    def _update_night_light(self) -> None:
        controller = self._controller
        self._night_icon.setPixmap((icons.night_icon() if is_night_light_on(controller) else icons.day_icon()).pixmap(26, 26))
        self._night_text.setText(night_light_status(controller))
        if self._override_menu.isVisible():
            return  # don't rebuild the menu under the user's cursor
        self._toggle.setText(build_override_menu(controller, self._override_menu))
        self._toggle.setMenu(None if self._override_menu.isEmpty() else self._override_menu)

    def _on_toggle_clicked(self) -> None:
        # Only reached without a menu, i.e. while an override is active.
        self._controller.clear_override()
