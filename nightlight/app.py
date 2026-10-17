# SPDX-License-Identifier: GPL-3.0-only
"""Wires the services (monitors, night light) to the UI (tray, flyout, settings)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from nightlight import autostart, config
from nightlight.brightness_memory import BrightnessMemory
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
        general = self._config.general
        self.brightness_memory = BrightnessMemory(self.monitors, config_path.with_name("brightness.json"), general.remember_brightness)
        self.controller = NightLightController(gamma, self._config.night_light)
        self._settings: SettingsWindow | None = None
        self._autostart = autostart.create()
        # Re-write (or remove) the sign-in entry, in case the app has moved.
        autostart.sync(self._autostart, self._config.general.start_with_windows)

        has_tray = QSystemTrayIcon.isSystemTrayAvailable()
        # Wayland doesn't let apps position their own popups, so there the
        # flyout is an ordinary window. Without a tray it's the main window.
        as_window = not has_tray or QGuiApplication.platformName() == "wayland"
        self.flyout = Flyout(self.monitors, self.controller, as_window=as_window)
        self.flyout.configureRequested.connect(self.show_settings)
        self.flyout.set_linked(general.link_brightness)

        self.tray: Tray | None = None
        if has_tray:
            self.tray = Tray(self.controller)
            self.tray.flyoutRequested.connect(self.flyout.toggle)
            self.tray.configureRequested.connect(self.show_settings)
            self.tray.quitRequested.connect(qapp.quit)
            self.tray.show()
            qapp.setQuitOnLastWindowClosed(False)
            if not self._config.general.start_minimized:
                self.show_settings()
        else:
            log.warning("No system tray available; showing the panel as a window")
            self.flyout.show_near()

        self._watcher = DisplayWatcher(qapp)
        self._watcher.displaysChanged.connect(lambda: self._on_displays_changed(RESCAN_AFTER_CHANGE_S))
        self._watcher.resumed.connect(self._on_resumed)

        qapp.aboutToQuit.connect(self._shutdown)
        self.monitors.refresh()

    def show_panel(self) -> None:
        self.flyout.show_near(self.tray.geometry() if self.tray else None)

    def show_settings(self) -> None:
        if self._settings is None:
            self._settings = SettingsWindow(self.controller, self._config, can_autostart=self._autostart is not None)
            self._settings.applied.connect(self._apply_settings)
        if self.tray:
            self.flyout.hide()
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def _apply_settings(self, new: config.AppConfig) -> None:
        self._config = new
        try:
            config.save(new, self._config_path)
        except OSError:
            log.exception("Could not save settings to %s", self._config_path)
        self.controller.set_config(new.night_light)
        autostart.sync(self._autostart, new.general.start_with_windows)
        self.brightness_memory.set_enabled(new.general.remember_brightness)
        self.flyout.set_linked(new.general.link_brightness)

    def _on_resumed(self) -> None:
        # Monitors and laptop panels often wake at another brightness.
        self.brightness_memory.restore_all_soon()
        self._on_displays_changed(RESCAN_AFTER_RESUME_S)

    def _on_displays_changed(self, rescan_after_s: tuple[float, ...]) -> None:
        # Display changes and sleep can reset gamma ramps: re-apply now.
        if displays_changed := getattr(self._gamma, "displays_changed", None):
            displays_changed()
        self.controller.update()
        self.monitors.refresh_later(rescan_after_s)

    def _shutdown(self) -> None:
        self.controller.shutdown()
        self.brightness_memory.shutdown()
        self.monitors.shutdown()
