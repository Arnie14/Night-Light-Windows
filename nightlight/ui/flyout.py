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
    QToolButton,
    QVBoxLayout,
)

from nightlight.controller import NightLightController
from nightlight.hardware.brightness import MonitorInfo
from nightlight.monitors import MonitorManager
from nightlight.ui import icons
from nightlight.ui.night_light_text import build_override_menu, is_night_light_on, night_light_status
from nightlight.ui.placement import flyout_position
from nightlight.ui.widgets import MonitorBrightness


class Flyout(QFrame):
    configureRequested = Signal()

    def __init__(self, monitors: MonitorManager, controller: NightLightController, as_window: bool) -> None:
        super().__init__()
        self._monitors = monitors
        self._controller = controller
        self._rows: dict[int, MonitorBrightness] = {}
        self._hidden_at = 0.0
        self._as_window = as_window

        self.setWindowTitle("Brightness and Color")
        self.setWindowFlags(Qt.WindowType.Window if as_window else Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        title = QLabel("Brightness and Color")
        font = QFont(title.font())
        font.setPointSizeF(font.pointSizeF() * 1.2)
        font.setBold(True)
        title.setFont(font)
        refresh = QToolButton(text="⟳", toolTip="Detect monitors again")
        refresh.clicked.connect(self._refresh)
        header.addWidget(title, 1)
        header.addWidget(refresh)
        layout.addLayout(header)

        self._monitor_status = QLabel("Detecting monitors…")
        self._monitor_status.setWordWrap(True)
        self._monitor_status.setEnabled(False)
        layout.addWidget(self._monitor_status)
        self._monitor_layout = QVBoxLayout()
        self._monitor_layout.setSpacing(12)
        layout.addLayout(self._monitor_layout)

        line = QFrame(frameShape=QFrame.Shape.HLine, frameShadow=QFrame.Shadow.Sunken)
        layout.addWidget(line)

        night = QHBoxLayout()
        self._night_icon = QLabel()
        night_labels = QVBoxLayout()
        night_labels.setSpacing(0)
        night_labels.addWidget(QLabel("Night Light"))
        self._night_text = QLabel()
        self._night_text.setWordWrap(True)
        self._night_text.setEnabled(False)
        night_labels.addWidget(self._night_text)
        self._override_menu = QMenu(self)
        self._toggle = QPushButton()
        self._toggle.clicked.connect(self._on_toggle_clicked)
        configure = QToolButton(text="⚙", toolTip="Configure Night Light…")
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
        self._night_icon.setPixmap((icons.night_icon() if is_night_light_on(controller) else icons.day_icon()).pixmap(22, 22))
        self._night_text.setText(night_light_status(controller))
        if self._override_menu.isVisible():
            return  # don't rebuild the menu under the user's cursor
        self._toggle.setText(build_override_menu(controller, self._override_menu))
        self._toggle.setMenu(None if self._override_menu.isEmpty() else self._override_menu)

    def _on_toggle_clicked(self) -> None:
        # Only reached without a menu, i.e. while an override is active.
        self._controller.clear_override()
