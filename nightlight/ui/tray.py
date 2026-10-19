# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRect, Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from nightlight import APP_NAME
from nightlight.controller import NightLightController
from nightlight.ui import icons
from nightlight.ui.night_light_text import brightness_status, build_override_menu, is_night_light_on, night_light_status, paused_text


class Tray(QSystemTrayIcon):
    flyoutRequested = Signal(QRect)  # the icon's on-screen geometry, may be empty
    configureRequested = Signal()
    diagnosticsRequested = Signal()
    quitRequested = Signal()

    def __init__(self, controller: NightLightController) -> None:
        super().__init__()
        self._controller = controller
        self.display_name: Callable[[str], str] | None = None  # set by the app: GDI name -> display name
        self._scheduler = None

        self._menu = QMenu()
        self._override_menu = QMenu()
        self._override_action = self._menu.addAction("")
        self._override_action.triggered.connect(controller.clear_override)
        self._menu.addAction("Settings…", self.configureRequested.emit)
        self._menu.addAction("Report a Problem…", self.diagnosticsRequested.emit)
        self._menu.addSeparator()
        self._menu.addAction("Quit", self.quitRequested.emit)
        self._menu.aboutToShow.connect(self._update_menu)
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)
        controller.changed.connect(self._update)
        self._update()
        self._update_menu()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.flyoutRequested.emit(self.geometry())

    def _update(self) -> None:
        controller = self._controller
        self.setIcon(icons.night_icon() if is_night_light_on(controller) else icons.day_icon())
        paused = paused_text(controller, self.display_name)
        brightness = brightness_status(self._scheduler)
        self.setToolTip(f"{APP_NAME}\nNight Light: {night_light_status(controller)}" + (f"\nBrightness: {brightness}" if brightness else "")
                        + (f"\n{paused}" if paused else ""))

    def set_scheduler(self, scheduler) -> None:
        """Show the brightness schedule too, while it is on."""
        self._scheduler = scheduler
        scheduler.changed.connect(self._update)
        self._update()

    def _update_menu(self) -> None:
        # "Suspend ▸" / "Activate ▸" with KDE's durations, or "Resume" / "Turn Off".
        label = build_override_menu(self._controller, self._override_menu)
        self._override_action.setText(f"{label} Night Light")
        self._override_action.setMenu(None if self._override_menu.isEmpty() else self._override_menu)
