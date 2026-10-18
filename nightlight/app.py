# SPDX-License-Identifier: GPL-3.0-only
"""Wires the services (monitors, night light) to the UI (tray, flyout, settings)."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from nightlight import APP_NAME, autostart, config, diagnostics
from nightlight.brightness_keys import BrightnessKeys
from nightlight.brightness_memory import BrightnessMemory
from nightlight.commands import CommandHandler
from nightlight.controller import NightLightController
from nightlight.exemptions import ForegroundWatcher
from nightlight.hardware import winsystem
from nightlight.hardware.brightness import BrightnessBackend
from nightlight.hardware.gamma import GammaBackend
from nightlight.monitors import MonitorManager
from nightlight.safe_mode import SafeMode
from nightlight.ui.flyout import Flyout
from nightlight.wake_report import WakeReport
from nightlight.ui.settings_window import SettingsWindow
from nightlight.ui.tray import Tray
from nightlight.watcher import DisplayWatcher

log = logging.getLogger(__name__)

# When to detect monitors again after a change, in seconds. Monitors can take
# a while to answer DDC/CI after being plugged in or woken, so look more than
# once (Monitorian: 1-20 s after display changes, 5-60 s after resume).
RESCAN_AFTER_CHANGE_S = (2, 8)
# After waking, see wake_report.py (a longer schedule, with timings logged).
# How often to look whether Windows' own Night Light is on (a registry read).
WINDOWS_NIGHT_LIGHT_CHECK_MS = 10_000


class NightLightApp:
    def __init__(self, qapp: QApplication, brightness: BrightnessBackend, gamma: GammaBackend, config_path: Path) -> None:
        self._qapp = qapp
        self._config_path = config_path
        self._config = config.load(config_path)
        log.info("Settings file: %s", config_path)

        self._gamma = gamma
        general = self._config.general
        # Set up before the monitor thread starts using the backend.
        self._safe_mode = SafeMode(config_path.parent)
        if hasattr(brightness, "safe_mode"):
            brightness.safe_mode = self._safe_mode
        if set_dimming := getattr(brightness, "set_software_dimming", None):
            set_dimming(general.software_dimming)
        self.monitors = MonitorManager(brightness)
        self.brightness_memory = BrightnessMemory(self.monitors, config_path.with_name("brightness.json"), general.remember_brightness)
        self.wake_report = WakeReport(self.monitors)
        self.brightness_keys = BrightnessKeys(self.monitors, lambda: frozenset(d.key for d in self._config.displays if d.hidden))
        self.controller = NightLightController(gamma, self._config.night_light)
        self.commands = CommandHandler(self.monitors, self.controller, lambda: self._config)
        self._settings: SettingsWindow | None = None
        self._windows_night_light: bool | None = None
        self._autostart = autostart.create()
        # Re-write (or remove) the sign-in entry, in case the app has moved.
        autostart.sync(self._autostart, self._config.general.start_with_windows)

        has_tray = QSystemTrayIcon.isSystemTrayAvailable()
        # Wayland doesn't let apps position their own popups, so there the
        # flyout is an ordinary window. Without a tray it's the main window.
        as_window = not has_tray or QGuiApplication.platformName() == "wayland"
        self.flyout = Flyout(self.monitors, self.controller, as_window=as_window)
        self.flyout.configureRequested.connect(self.show_settings)

        self._exemptions = ForegroundWatcher(winsystem.foreground_app, self._config.night_light)
        self._exemptions.pausedChanged.connect(self.controller.set_paused)
        self._apply_preferences(self._config)

        self.tray: Tray | None = None
        if has_tray:
            self.tray = Tray(self.controller)
            self.tray.flyoutRequested.connect(self._open_flyout)
            self.tray.configureRequested.connect(self.show_settings)
            self.tray.diagnosticsRequested.connect(self.report_problem)
            self.tray.quitRequested.connect(qapp.quit)
            self.tray.show()
            qapp.setQuitOnLastWindowClosed(False)
            if not self._config.general.start_minimized and not self._busy("the settings at start"):
                self.show_settings()
        else:
            log.warning("No system tray available; showing the panel as a window")
            self.flyout.show_near()

        # Display changes Qt sees, and (on Windows) unlocking, waking and
        # displays turning on, straight from Windows: gamma ramps are reset
        # then, and night light should be back before anyone notices.
        self._watcher = DisplayWatcher(qapp)
        self._watcher.displaysChanged.connect(lambda: self._on_displays_changed(RESCAN_AFTER_CHANGE_S))
        self._watcher.resumed.connect(self._on_resumed)
        self._events = winsystem.SystemEvents(qapp)
        self._events.unlocked.connect(self.controller.reapply_now)
        self._events.displayChanged.connect(self.controller.reapply_now)
        self._events.resumed.connect(self._on_resumed)
        self._events.displayOn.connect(self._on_display_on)
        self._events.panelBrightness.connect(self.brightness_keys.on_windows_event)

        self._night_light_check = QTimer(interval=WINDOWS_NIGHT_LIGHT_CHECK_MS)
        self._night_light_check.timeout.connect(self._check_windows_night_light)
        self._night_light_check.start()
        self._check_windows_night_light()

        qapp.aboutToQuit.connect(self._shutdown)
        self.monitors.refresh()

    # --- Windows and panels ---------------------------------------------------

    def show_panel(self) -> None:
        """Another launch (a shortcut, a hotkey tool) asked for the panel."""
        if self._busy("the panel"):
            return
        self._open_flyout(self.tray.geometry() if self.tray else None)

    def _busy(self, what: str) -> bool:
        """Whether to hold back opening `what` now, so a fullscreen game or
        video keeps its focus (it might otherwise be minimized)."""
        if self._config.general.respect_fullscreen and winsystem.user_busy():
            log.info("Not opening %s over a fullscreen app", what)
            return True
        return False

    def _open_flyout(self, anchor=None) -> None:
        self._check_windows_night_light()
        self.flyout.toggle(anchor)

    def show_settings(self) -> None:
        if self._settings is None:
            self._settings = SettingsWindow(
                self.controller,
                self._config,
                can_autostart=self._autostart is not None,
                monitors=self.monitors,
                running_apps=winsystem.running_apps,
            )
            self._settings.applied.connect(self._apply_settings)
        self._settings.set_windows_night_light(self._windows_night_light)
        if self.tray:
            self.flyout.hide()
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def report_problem(self) -> None:
        """Save a diagnostics zip to the Desktop, copy a summary, and say where it is."""
        try:
            path, text = diagnostics.write_bundle(self._diagnostics_sources())
        except OSError as exc:
            QMessageBox.warning(None, APP_NAME, f"Could not save the diagnostics: {exc}")
            return
        QGuiApplication.clipboard().setText(text)
        box = QMessageBox(QMessageBox.Icon.Information, f"{APP_NAME}: Report a Problem", "")
        box.setText(f"Saved <b>{path.name}</b> to {path.parent}, and copied a summary to the clipboard.")
        box.setInformativeText("Describe what happened, paste the summary, and attach the file.")
        show = box.addButton("Show the File", QMessageBox.ButtonRole.ActionRole)
        issue = box.addButton("Open GitHub Issues", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is show:
            _reveal(path)
        elif box.clickedButton() is issue:
            QDesktopServices.openUrl(QUrl(diagnostics.ISSUES_URL))

    def handle_request(self, request: dict, reply) -> None:
        """A request from the command line (winlight-cli)."""
        if request.get("command") == "diagnostics":
            try:
                path, _text = diagnostics.write_bundle(self._diagnostics_sources())
                reply({"ok": True, "output": str(path), "data": {"path": str(path)}})
            except OSError as exc:
                reply({"ok": False, "output": f"Could not save the diagnostics: {exc}"})
            return
        reply(self.commands.handle(request))

    def _diagnostics_sources(self) -> diagnostics.Sources:
        return diagnostics.Sources(
            monitors=self.monitors,
            controller=self.controller,
            config=lambda: self._config,
            config_path=self._config_path,
            log_dir=config.log_dir(),
            skipped=lambda: set(self._safe_mode.skipped),
            last_wake=lambda: list(self.wake_report.lines),
        )

    # --- Settings -------------------------------------------------------------

    def _apply_settings(self, new: config.AppConfig) -> None:
        old_general = self._config.general
        self._config = new
        try:
            config.save(new, self._config_path)
        except OSError:
            log.exception("Could not save settings to %s", self._config_path)
        self.controller.set_config(new.night_light)
        autostart.sync(self._autostart, new.general.start_with_windows)
        self.brightness_memory.set_enabled(new.general.remember_brightness)
        if new.general.software_dimming != old_general.software_dimming:
            self.monitors.set_software_dimming(new.general.software_dimming)
        self._apply_preferences(new)

    def _apply_preferences(self, settings: config.AppConfig) -> None:
        """What every part needs from the settings besides night light itself."""
        self.flyout.set_linked(settings.general.link_brightness)
        self.brightness_keys.set_enabled(settings.general.follow_laptop_brightness)
        self.flyout.set_display_prefs(settings.displays)
        self.brightness_memory.set_hidden(frozenset(d.key for d in settings.displays if d.hidden))
        self.brightness_memory.set_schedule(settings.brightness_schedule, _location(settings.night_light))
        self._exemptions.set_config(settings.night_light)

    # --- Events ---------------------------------------------------------------

    def _check_windows_night_light(self) -> None:
        on = winsystem.windows_night_light_on()
        if on != self._windows_night_light:
            if on:
                log.warning("Windows Night Light is on as well; the two tints stack")
            self._windows_night_light = on
        self.flyout.set_windows_night_light(on)
        if self._settings is not None:
            self._settings.set_windows_night_light(on)

    def _on_resumed(self) -> None:
        # Both Windows' event and the clock check report a wake; act once.
        if not self.wake_report.woke():
            return
        self.controller.reapply_now()
        # Monitors and laptop panels often wake at another brightness.
        self.brightness_memory.restore_all_soon()
        if displays_changed := getattr(self._gamma, "displays_changed", None):
            displays_changed()
        self.controller.update()

    def _on_display_on(self) -> None:
        # After the display slept (idle) without the PC sleeping: no display
        # change is reported, but ramps and levels may have been reset
        # (Monitorian re-checks then too).
        self.controller.reapply_now()
        self.brightness_memory.restore_all_soon()
        self.monitors.refresh_later((5, 10))

    def _on_displays_changed(self, rescan_after_s: tuple[float, ...]) -> None:
        # Display changes and sleep can reset gamma ramps: re-apply now.
        if displays_changed := getattr(self._gamma, "displays_changed", None):
            displays_changed()
        self.controller.update()
        self.controller.reapply_now()
        self.monitors.refresh_later(rescan_after_s)

    def _shutdown(self) -> None:
        self._night_light_check.stop()
        self._exemptions.stop()
        self.controller.shutdown()
        self.brightness_memory.shutdown()
        self.monitors.shutdown()


def _location(night_light: config.NightLightConfig) -> tuple[float, float] | None:
    """Where sunrise and sunset are, if a location has been set."""
    if night_light.timezone or (night_light.latitude, night_light.longitude) != (0.0, 0.0):
        return night_light.latitude, night_light.longitude
    return None


def _reveal(path: Path) -> None:
    """Show a file in Explorer (selected), or its folder elsewhere."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
