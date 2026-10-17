from __future__ import annotations

from PySide6.QtCore import QRect, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from nightlight.controller import NightLightController
from nightlight.ui import icons
from nightlight.ui.widgets import night_light_status


class Tray(QSystemTrayIcon):
    flyoutRequested = Signal(QRect)  # the icon's on-screen geometry, may be empty
    configureRequested = Signal()
    quitRequested = Signal()

    def __init__(self, controller: NightLightController) -> None:
        super().__init__()
        self._controller = controller

        self._menu = QMenu()
        self._suspend = QAction("Suspend Night Light", self._menu, checkable=True)
        self._suspend.toggled.connect(controller.set_inhibited)
        self._menu.addAction(self._suspend)
        self._menu.addAction("Configure Night Light…", self.configureRequested)
        self._menu.addSeparator()
        self._menu.addAction("Quit", self.quitRequested)
        self.setContextMenu(self._menu)

        self.activated.connect(self._on_activated)
        controller.changed.connect(self._update)
        self._update()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.flyoutRequested.emit(self.geometry())

    def _update(self) -> None:
        controller = self._controller
        night = controller.config.enabled and not controller.inhibited and controller.state.night_factor > 0
        self.setIcon(icons.night_icon() if night else icons.day_icon())
        self.setToolTip(f"Brightness and Color\n{night_light_status(controller)}")
        self._suspend.setEnabled(controller.config.enabled)
        self._suspend.blockSignals(True)
        self._suspend.setChecked(controller.inhibited)
        self._suspend.blockSignals(False)
