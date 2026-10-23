# SPDX-License-Identifier: GPL-3.0-only
"""The quick-access panel opened from the tray, like Plasma's "Brightness and Color" applet."""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nightlight import APP_NAME
from nightlight.brightness.monitors import MonitorManager
from nightlight.color.controller import NightLightController
from nightlight.config import DisplayPrefs
from nightlight.hardware.brightness import MonitorInfo
from nightlight.ui import icons
from nightlight.ui.night_light_text import (
    brightness_status,
    build_override_menu,
    can_suspend,
    display_label,
    is_night_light_on,
    night_light_status,
    paused_text,
    toggle_suspended,
)
from nightlight.ui.placement import flyout_position
from nightlight.ui.widgets import HEADING_WEIGHT, GlyphButton, MonitorBrightness, SecondaryLabel, Separator, WindowsNightLightNotice, set_font

WIDTH = 340


class Flyout(QFrame):
    configureRequested = Signal()

    def __init__(self, monitors: MonitorManager, controller: NightLightController, as_window: bool) -> None:
        super().__init__()
        self._monitors = monitors
        self._controller = controller
        self._rows: dict[str, MonitorBrightness] = {}  # by display key
        self._linked = False
        self._hidden_at = 0.0
        self._anchor: QRect | None = None  # the tray icon it opened from
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
        set_font(title, 1.15, HEADING_WEIGHT)
        refresh = GlyphButton(icons.refresh_glyph, "Detect displays again")
        refresh.clicked.connect(self._refresh)
        header.addWidget(title, 1)
        header.addWidget(refresh)
        layout.addLayout(header)

        self.windows_night_light = WindowsNightLightNotice(compact=True)
        layout.addWidget(self.windows_night_light)

        self._monitor_status = SecondaryLabel("Detecting monitors…")
        self._monitor_status.setWordWrap(True)
        layout.addWidget(self._monitor_status)
        self._monitor_layout = QVBoxLayout()
        self._monitor_layout.setContentsMargins(0, 0, 4, 0)  # line up with the header's right edge
        self._monitor_layout.setSpacing(14)
        layout.addLayout(self._monitor_layout)
        self._scheduler = None
        self.schedule_text = SecondaryLabel(scale=0.92)  # the brightness schedule, while it is on
        self.schedule_text.setWordWrap(True)
        self.schedule_text.setVisible(False)
        layout.addWidget(self.schedule_text)

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
        # KDE's Suspend button: a click suspends until resumed; its arrow has
        # the timed choices. "Resume" or "Turn Off" while an override lasts.
        self._toggle = QToolButton()
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._toggle.clicked.connect(self._on_toggle_clicked)
        configure = GlyphButton(icons.gear_glyph, "Settings")
        configure.clicked.connect(self.configureRequested)
        night.addWidget(self._night_icon)
        night.addLayout(night_labels, 1)
        night.addWidget(self._toggle)
        night.addWidget(configure)
        layout.addLayout(night)

        monitors.monitorsChanged.connect(self._rebuild)
        monitors.displayChanged.connect(self._on_display_changed)
        monitors.error.connect(self._show_error)
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
        self._anchor = anchor
        # Size hints depend on fonts and styles that only apply once each
        # widget is polished, normally on first show; polish first, so the
        # first opening is placed with its real size.
        for widget in (self, *self.findChildren(QWidget)):
            widget.ensurePolished()
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self.activateWindow()
        QTimer.singleShot(0, self._place)  # in case the size still settled while showing

    def resizeEvent(self, event) -> None:
        # The panel can change size while open (monitors detected after it
        # opened, a status line wrapping): stay against the taskbar corner
        # rather than growing off the screen from the top-left.
        super().resizeEvent(event)
        if self.isVisible():
            self._place()

    def _place(self) -> None:
        if not self._as_window:
            self.move(self._popup_position(self._anchor))

    def hideEvent(self, event) -> None:
        self._hidden_at = time.monotonic()
        super().hideEvent(event)

    def _popup_position(self, anchor: QRect | None) -> QPoint:
        point = anchor.center() if anchor and anchor.isValid() else QCursor.pos()
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        size = self.size() if self.isVisible() else self.sizeHint()
        return flyout_position(size, screen.geometry(), screen.availableGeometry(), anchor)

    def _refresh(self) -> None:
        self._monitor_status.setText("Detecting monitors…")
        self._monitor_status.show()
        self._monitors.retry()  # the user asked: check every display, those skipped after a crash too

    def _show_error(self, message: str) -> None:
        self._monitor_status.setText(message)
        self._monitor_status.show()

    def _rebuild(self, monitors: list[MonitorInfo]) -> None:
        for row in self._rows.values():
            row.deleteLater()
        self._rows.clear()
        for info in monitors:
            prefs = self._display_prefs(info.key)
            if prefs.hidden:
                continue
            row = MonitorBrightness(info, self._monitors.state(info.key), prefs.name)
            row.brightnessRequested.connect(self._on_brightness_requested)
            self._monitor_layout.addWidget(row)
            self._rows[info.key] = row
        if self._rows:
            self._monitor_status.hide()
        elif monitors:
            self._monitor_status.setText("Every display is hidden; show them in Settings > Displays.")
            self._monitor_status.show()
        else:
            self._monitor_status.setText("No displays found.")
            self._monitor_status.show()
        self.adjustSize()

    def set_windows_night_light(self, on: bool | None) -> None:
        """Show the notice while Windows' own Night Light is on too (it stacks with ours)."""
        if self.windows_night_light.isVisible() != bool(on):
            self.windows_night_light.setVisible(bool(on))
            self.adjustSize()

    def set_display_prefs(self, prefs: tuple[DisplayPrefs, ...] | Callable[[str], DisplayPrefs]) -> None:
        """Custom names, and displays to leave out of the panel (and linked changes).
        A lookup (AppConfig.display) also finds names saved under older keys."""
        if callable(prefs):
            self._display_prefs = prefs
        else:
            by_key = {p.key: p for p in prefs}
            self._display_prefs = lambda key: by_key.get(key, DisplayPrefs(key))
        self._rebuild(self._monitors.monitors)

    def _display_prefs(self, key: str) -> DisplayPrefs:
        return DisplayPrefs(key)

    def set_linked(self, linked: bool) -> None:
        """Whether moving one monitor's slider moves them all, like Monitorian's
        "unison" and Twinkle Tray's "linked levels"."""
        self._linked = linked

    def _on_brightness_requested(self, key: str, value: int) -> None:
        self._monitors.set_brightness(key, value)
        if self._linked:
            for other in self._rows.values():
                if other.key != key and other.controllable:
                    self._monitors.set_brightness(other.key, other.clamp(value))

    def _on_display_changed(self, key: str) -> None:
        row, state = self._rows.get(key), self._monitors.state(key)
        if row is not None and state is not None:
            row.update_state(state)

    def _update_night_light(self) -> None:
        controller = self._controller
        self._night_icon.setPixmap((icons.night_icon() if is_night_light_on(controller) else icons.day_icon()).pixmap(26, 26))
        paused = paused_text(controller, self.display_name_of)
        self._night_text.setText(night_light_status(controller) + (f"\n{paused}" if paused else ""))
        if self._override_menu.isVisible():
            return  # don't rebuild the menu under the user's cursor
        label = build_override_menu(controller, self._override_menu)
        Mode = QToolButton.ToolButtonPopupMode
        if controller.state.override is not None:  # "Resume" or "Turn Off": a click ends it
            self._toggle.setText(label)
            self._toggle.setToolTip("")
            self._toggle.setMenu(None)
        elif controller.config.enabled:
            self._toggle.setText("Suspend")
            self._toggle.setToolTip("Night light stays off until you resume it. The arrow has timed choices")
            self._toggle.setMenu(self._override_menu)
            self._toggle.setPopupMode(Mode.MenuButtonPopup)
        else:  # night light is off: only "Activate ▸"
            self._toggle.setText(label)
            self._toggle.setToolTip("")
            self._toggle.setMenu(self._override_menu)
            self._toggle.setPopupMode(Mode.InstantPopup)

    def set_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler
        scheduler.changed.connect(self._update_schedule)
        self._update_schedule()

    def _update_schedule(self) -> None:
        text = brightness_status(self._scheduler)
        self.schedule_text.setText(f"Brightness schedule: {text}")
        self.schedule_text.setVisible(bool(text))

    def display_name_of(self, gdi_name: str) -> str:
        """A display's name (its custom one if given), from its GDI name."""
        for info in self._monitors.monitors:
            if info.display == gdi_name:
                return self._display_prefs(info.key).name or info.name
        return display_label(gdi_name)

    def _on_toggle_clicked(self) -> None:
        controller = self._controller
        if controller.state.override is not None:
            controller.clear_override()  # "Resume" or "Turn Off"
        elif can_suspend(controller):
            toggle_suspended(controller)  # "Suspend": until resumed
