"""The quick-access panel opened from the tray, like Plasma's "Brightness and Color" applet."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nightlight.controller import NightLightController
from nightlight.hardware.brightness import MonitorInfo
from nightlight.monitors import MonitorManager
from nightlight.ui import icons
from nightlight.ui.widgets import MonitorBrightness, night_light_status


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
        self._night_text = QLabel()
        self._night_text.setWordWrap(True)
        self._toggle = QPushButton()
        self._toggle.clicked.connect(lambda: controller.set_inhibited(not controller.inhibited))
        configure = QToolButton(text="⚙", toolTip="Configure Night Light…")
        configure.clicked.connect(self.configureRequested)
        night.addWidget(self._night_icon)
        night.addWidget(self._night_text, 1)
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
        area = screen.availableGeometry()
        size = self.sizeHint()
        x = point.x() - size.width() // 2
        # Open away from the screen edge the tray sits on.
        y = point.y() - size.height() - 8 if point.y() > area.center().y() else point.y() + 8
        x = max(area.left(), min(x, area.right() - size.width()))
        y = max(area.top(), min(y, area.bottom() - size.height()))
        return QPoint(x, y)

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
        night = controller.config.enabled and not controller.inhibited and controller.state.night_factor > 0
        self._night_icon.setPixmap((icons.night_icon() if night else icons.day_icon()).pixmap(22, 22))
        self._night_text.setText(night_light_status(controller))
        self._toggle.setVisible(controller.config.enabled)
        self._toggle.setText("Resume" if controller.inhibited else "Suspend")
