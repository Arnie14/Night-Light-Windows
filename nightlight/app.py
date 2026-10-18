# SPDX-License-Identifier: GPL-3.0-only
"""Wires the services (monitors, night light, brightness schedule) to the UI (tray, flyout, settings)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QTimeZone
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from nightlight import autostart, compat, config
from nightlight.brightness_memory import BrightnessMemory
from nightlight.brightness_scheduler import BrightnessScheduler
from nightlight.controller import NightLightController
from nightlight.core.clock import zone_named
from nightlight.hardware.brightness import BrightnessBackend
from nightlight.hardware.gamma import GammaBackend
from nightlight.hardware.winevents import SystemEvents
from nightlight.monitors import MonitorManager
from nightlight.ui.flyout import Flyout
from nightlight.ui.settings_window import SettingsWindow
from nightlight.ui.tray import Tray
from nightlight.watcher import DisplayWatcher

log = logging.getLogger(__name__)

# When to detect monitors again after a change, in seconds. Monitors can take
# a while to answer DDC/CI after being plugged in or woken, so look more than
# once: Monitorian re-scans over about two minutes after resume (its issue
# #50 was monitors scanned before Windows was ready), Twinkle Tray waits 5 s
# after hardware changes and 8 s after waking.
RESCAN_AFTER_CHANGE_S = (2, 8)
RESCAN_AFTER_RESUME_S = (3, 8, 15, 30, 60, 120)
RESCAN_AFTER_DISPLAYS_ON_S = (3, 8, 20)


def system_zone():
    """The local IANA time zone (Qt maps Windows' zone names), or None."""
    try:
        return zone_named(QTimeZone.systemTimeZoneId().data().decode("ascii", "replace"))
    except Exception:
        log.debug("Could not read the system time zone", exc_info=True)
        return None


class NightLightApp:
    def __init__(self, qapp: QApplication, brightness: BrightnessBackend, gamma: GammaBackend, config_path: Path) -> None:
        self._qapp = qapp
        self._config_path = config_path
        self._config = config.load(config_path)
        self._shut_down = False
        log.info("Settings file: %s", config_path)
        zone = system_zone()
        log.info("Time zone: %s", zone or "unknown; using the current UTC offset")

        self._gamma = gamma
        if set_journal := getattr(gamma, "set_journal", None):
            set_journal(config_path.with_name("gamma.json"))  # before the first ramp is touched
        general = self._config.general
        if set_dimming := getattr(brightness, "set_software_dimming", None):
            set_dimming(general.software_dimming)  # before the worker thread starts using it
        self.monitors = MonitorManager(brightness)
        # Memory before the scheduler: both act on new detections, and the
        # schedule's level, set second, is the one that stands.
        self.brightness_memory = BrightnessMemory(self.monitors, config_path.with_name("brightness.json"), general.remember_brightness)
        self.scheduler = BrightnessScheduler(
            self.monitors, self._config.brightness_schedule, config_path.with_name("schedule.json"), location=self._location, zone=zone
        )
        self.controller = NightLightController(gamma, self._config.night_light, zone=zone)
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

        self.system_events = SystemEvents()
        self.system_events.endingSession.connect(self._shutdown)  # signing out: there may be no later chance
        self._watcher = DisplayWatcher(qapp, events=self.system_events)
        self._watcher.displaysChanged.connect(lambda: self._on_displays_changed(RESCAN_AFTER_CHANGE_S))
        self._watcher.suspending.connect(self._on_suspending)
        self._watcher.resumed.connect(self._on_resumed)
        self._watcher.displaysOff.connect(self._on_suspending)
        self._watcher.displaysOn.connect(self._on_displays_on)
        self._watcher.clockChanged.connect(self._on_clock_changed)

        qapp.aboutToQuit.connect(self._shutdown)
        self.monitors.refresh()

    def show_panel(self) -> None:
        self.flyout.show_near(self.tray.geometry() if self.tray else None)

    def show_settings(self) -> None:
        if self._settings is None:
            self._settings = SettingsWindow(
                self.controller,
                self._config,
                can_autostart=self._autostart is not None,
                monitors=self.monitors,
                scheduler=self.scheduler,
                compatibility=self.compatibility,
                report=self.compatibility_report,
            )
            self._settings.applied.connect(self._apply_settings)
        if self.tray:
            self.flyout.hide()
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def compatibility(self) -> list[compat.CompatibilityEntry]:
        warmest = getattr(self._gamma, "warmest_accepted", dict)()
        return compat.entries(self.monitors.monitors, self.monitors.states, warmest)

    def compatibility_report(self) -> str:
        return compat.render(self.compatibility(), compat.system_summary())

    def _location(self) -> tuple[float, float] | None:
        """The Night Light location, which sunrise and sunset schedule points use too."""
        night_light = self._config.night_light
        if night_light.timezone or (night_light.latitude, night_light.longitude) != (0.0, 0.0):
            return night_light.latitude, night_light.longitude
        return None

    def _apply_settings(self, new: config.AppConfig) -> None:
        old = self._config
        self._config = new
        try:
            config.save(new, self._config_path)
        except OSError:
            log.exception("Could not save settings to %s", self._config_path)
        self.controller.set_config(new.night_light)
        autostart.sync(self._autostart, new.general.start_with_windows)
        self.brightness_memory.set_enabled(new.general.remember_brightness)
        if new.general.software_dimming != old.general.software_dimming:
            self.monitors.set_software_dimming(new.general.software_dimming)
        self.flyout.set_linked(new.general.link_brightness)
        self.scheduler.set_config(new.brightness_schedule)
        if new.night_light != old.night_light:
            self.scheduler.evaluate("location")  # sunrise and sunset points follow the location

    def _on_suspending(self) -> None:
        # Nothing goes to the displays while the PC sleeps or the screens are
        # off: DDC/CI can wake a sleeping monitor or hang its firmware.
        self.monitors.suspend()

    def _on_resumed(self) -> None:
        # Monitors and laptop panels often wake at another brightness, and a
        # schedule point may have passed while asleep (only the latest applies).
        self.brightness_memory.restore_all_soon()
        self.monitors.resume(RESCAN_AFTER_RESUME_S)
        self._reapply_colors()
        self.scheduler.evaluate("wake")

    def _on_displays_on(self) -> None:
        # The screen timeout ended, or the session was unlocked. DisplayPort
        # monitors often drop out while off; detection brings them back, and
        # BrightnessMemory restores the ones that reappear.
        self.monitors.resume(RESCAN_AFTER_DISPLAYS_ON_S)
        self._reapply_colors()

    def _on_clock_changed(self) -> None:
        self.controller.update()
        self.scheduler.evaluate("clock")

    def _on_displays_changed(self, rescan_after_s: tuple[float, ...]) -> None:
        self._reapply_colors()
        self.monitors.refresh_later(rescan_after_s)

    def _reapply_colors(self) -> None:
        # Display changes and sleep can reset gamma ramps: re-apply now.
        if displays_changed := getattr(self._gamma, "displays_changed", None):
            displays_changed()
        self.controller.update()

    def _shutdown(self) -> None:
        if self._shut_down:
            return  # quitting after signing out started it already
        self._shut_down = True
        self.scheduler.shutdown()
        self.controller.shutdown()
        self.brightness_memory.shutdown()
        self.monitors.shutdown()
        self.system_events.close()
