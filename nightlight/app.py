# SPDX-License-Identifier: GPL-3.0-only
"""Wires the services (monitors, night light) to the UI (tray, flyout, settings)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from nightlight import config
from nightlight.config import NightLightConfig
from nightlight.controller import NightLightController
from nightlight.hardware.brightness import BrightnessBackend
from nightlight.hardware.gamma import GammaBackend
from nightlight.monitors import MonitorManager
from nightlight.ui.flyout import Flyout
from nightlight.ui.settings_window import SettingsWindow
from nightlight.ui.tray import Tray
from nightlight.watcher import DisplayWatcher

log = logging.getLogger(__name__)

# When to detect monitors again after a change, in seconds. Monitors can take
# a while to answer DDC/CI after being plugged in or woken, so look more than
# once (Monitorian: 1-20 s after display changes, 5-60 s after resume).
RESCAN_AFTER_CHANGE_S = (2, 8)
RESCAN_AFTER_RESUME_S = (5, 15, 30)


class NightLightApp:
    def __init__(self, qapp: QApplication, brightness: BrightnessBackend, gamma: GammaBackend, config_path: Path) -> None:
        self._qapp = qapp
        self._config_path = config_path
        self._config = config.load(config_path)
        log.info("Settings file: %s", config_path)

        self._gamma = gamma
        self.monitors = MonitorManager(brightness)
        self.controller = NightLightController(gamma, self._config.night_light)
        self._settings: SettingsWindow | None = None

        has_tray = QSystemTrayIcon.isSystemTrayAvailable()
        # Wayland doesn't let apps position their own popups, so there the
        # flyout is an ordinary window. Without a tray it's the main window.
        as_window = not has_tray or QGuiApplication.platformName() == "wayland"
        self.flyout = Flyout(self.monitors, self.controller, as_window=as_window)
        self.flyout.configureRequested.connect(self.show_settings)

        self.tray: Tray | None = None
        if has_tray:
            self.tray = Tray(self.controller)
            self.tray.flyoutRequested.connect(self.flyout.toggle)
            self.tray.configureRequested.connect(self.show_settings)
            self.tray.quitRequested.connect(qapp.quit)
            self.tray.show()
            qapp.setQuitOnLastWindowClosed(False)
        else:
            log.warning("No system tray available; showing the panel as a window")
            self.flyout.show_near()

        self._watcher = DisplayWatcher(qapp)
        self._watcher.displaysChanged.connect(lambda: self._on_displays_changed(RESCAN_AFTER_CHANGE_S))
        self._watcher.resumed.connect(lambda: self._on_displays_changed(RESCAN_AFTER_RESUME_S))

        qapp.aboutToQuit.connect(self._shutdown)
        self.monitors.refresh()

    def show_panel(self) -> None:
        self.flyout.show_near(self.tray.geometry() if self.tray else None)

    def show_settings(self) -> None:
        if self._settings is None:
            self._settings = SettingsWindow(self.controller)
            self._settings.applied.connect(self._apply_night_light)
        if self.tray:
            self.flyout.hide()
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def _apply_night_light(self, night_light: NightLightConfig) -> None:
        self._config = config.AppConfig(night_light=night_light)
        try:
            config.save(self._config, self._config_path)
        except OSError:
            log.exception("Could not save settings to %s", self._config_path)
        self.controller.set_config(night_light)

    def _on_displays_changed(self, rescan_after_s: tuple[float, ...]) -> None:
        # Display changes and sleep can reset gamma ramps: re-apply now.
        if displays_changed := getattr(self._gamma, "displays_changed", None):
            displays_changed()
        self.controller.update()
        self.monitors.refresh_later(rescan_after_s)

    def _shutdown(self) -> None:
        self.controller.shutdown()
        self.monitors.shutdown()
