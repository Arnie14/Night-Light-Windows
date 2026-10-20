# SPDX-License-Identifier: GPL-3.0-only
"""Wires the services (monitors, night light, brightness schedule) to the UI (tray, flyout, settings)."""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QTimer, QTimeZone, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from nightlight import APP_NAME, autostart, compat, config, diagnostics
from nightlight.brightness_keys import BrightnessKeys
from nightlight.brightness_memory import BrightnessMemory
from nightlight.brightness_scheduler import BrightnessScheduler
from nightlight.commands import CommandHandler
from nightlight.controller import NightLightController
from nightlight.core.clock import zone_named
from nightlight.exemptions import ForegroundWatcher
from nightlight.hardware import winsystem
from nightlight.hardware.brightness import BrightnessBackend
from nightlight.hardware.gamma import GammaBackend
from nightlight.idle_dim import IdleDimmer
from nightlight.monitors import MonitorManager
from nightlight.safe_mode import SafeMode
from nightlight.ui import reveal, theme
from nightlight.ui.flyout import Flyout
from nightlight.ui.settings_window import SettingsWindow
from nightlight.ui.tray import Tray
from nightlight.wake_report import WakeReport
from nightlight.watcher import DisplayWatcher

log = logging.getLogger(__name__)

# When to detect monitors again after a change, in seconds. Monitors can take
# a while to answer DDC/CI after being plugged in or woken, so look more than
# once: Monitorian re-scans over about two minutes after resume (its issue
# #50 was monitors scanned before Windows was ready), Twinkle Tray waits 5 s
# after hardware changes and 8 s after waking. After waking from sleep, see
# wake_report.py (a longer schedule, with timings logged).
RESCAN_AFTER_CHANGE_S = (2, 8)
RESCAN_AFTER_DISPLAYS_ON_S = (3, 8, 20)
# How often to look whether Windows' own Night Light is on (a registry read).
WINDOWS_NIGHT_LIGHT_CHECK_MS = 10_000
# How often to look whether the time zone changed (travel, or changed by
# hand), besides when Windows reports a clock change.
ZONE_CHECK_MS = 60_000
# Deleting old diagnostics zips: a while after start (out of start-up's
# way), then once a day for an app left running.
CLEANUP_DELAY_MS = 60_000
CLEANUP_INTERVAL_MS = 24 * 3600_000


def system_zone():
    """The local IANA time zone (Qt maps Windows' zone names), or None.
    Not logged: a time zone tells where the user is (privacy.py)."""
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
        log.info("Time zone: %s", "known" if zone is not None else "unknown; using the current UTC offset")

        self._gamma = gamma
        if set_journal := getattr(gamma, "set_journal", None):
            set_journal(config_path.with_name("gamma.json"))  # before the first ramp is touched
        general = self._config.general
        # Set up before the monitor thread starts using the backend.
        self._safe_mode = SafeMode(config_path.parent)
        if hasattr(brightness, "safe_mode"):
            brightness.safe_mode = self._safe_mode
        if set_dimming := getattr(brightness, "set_software_dimming", None):
            set_dimming(general.software_dimming)
        if keep := getattr(gamma, "set_keep_calibration", None):
            keep(general.keep_calibration)  # before night light first sets the ramps
        self.monitors = MonitorManager(brightness)
        # Memory before the scheduler: both act on new detections, and the
        # schedule's level, set second, is the one that stands.
        self.brightness_memory = BrightnessMemory(self.monitors, config_path.with_name("brightness.json"), general.remember_brightness)
        self.scheduler = BrightnessScheduler(
            self.monitors, self._config.brightness_schedule, config_path.with_name("schedule.json"), location=self._location, zone=zone
        )
        self.wake_report = WakeReport(self.monitors)
        self.idle_dimmer = IdleDimmer(
            self.monitors,
            self.brightness_memory,
            winsystem.idle_seconds,
            self._idle_held,
            self._hidden_keys,
            gamma,
            scheduler=self.scheduler,
        )
        self.brightness_keys = BrightnessKeys(self.monitors, self._hidden_keys)
        self.controller = NightLightController(gamma, self._config.night_light, zone=zone)
        self.commands = CommandHandler(self.monitors, self.controller, lambda: self._config)
        self._settings: SettingsWindow | None = None
        self._windows_night_light: bool | None = None
        self._autostart = autostart.create()
        self._read_autostart()  # only read: writing it at every start set off Defender

        has_tray = QSystemTrayIcon.isSystemTrayAvailable()
        # Wayland doesn't let apps position their own popups, so there the
        # flyout is an ordinary window. Without a tray it's the main window.
        as_window = not has_tray or QGuiApplication.platformName() == "wayland"
        self.flyout = Flyout(self.monitors, self.controller, as_window=as_window)
        self.flyout.configureRequested.connect(self.show_settings)
        self.flyout.set_scheduler(self.scheduler)

        self._exemptions = ForegroundWatcher(winsystem.foreground_app, self._config.night_light,
                                             media=lambda: winsystem.display_required() or winsystem.exclusive_fullscreen())
        self._exemptions.pausedChanged.connect(self.controller.set_paused)
        # Its first check ran before this connection: an exempt or fullscreen
        # app already in front at start pauses night light too.
        self.controller.set_paused(self._exemptions.paused)
        self._apply_preferences(self._config)

        self.tray: Tray | None = None
        if has_tray:
            self.tray = Tray(self.controller)
            self.tray.display_name = self.flyout.display_name_of
            self.tray.set_scheduler(self.scheduler)
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

        # Display changes Qt sees, and (on Windows) sleep, waking, displays
        # turning off and on, unlocking and clock changes, straight from
        # Windows: gamma ramps are reset then, night light should be back
        # before anyone notices, and nothing goes to sleeping displays.
        self.system_events = winsystem.SystemEvents(qapp)
        self.system_events.endingSession.connect(self._shutdown)  # signing out: there may be no later chance
        self.system_events.unlocked.connect(self.controller.reapply_now)
        self.system_events.displayChanged.connect(self.controller.reapply_now)  # at once; detection follows once settled
        self.system_events.panelBrightness.connect(self.brightness_keys.on_windows_event)
        self._watcher = DisplayWatcher(qapp, events=self.system_events,
                                       idle_seconds=winsystem.idle_seconds if sys.platform == "win32" else None)
        self._watcher.displaysChanged.connect(lambda: self._on_displays_changed(RESCAN_AFTER_CHANGE_S))
        self._watcher.suspending.connect(self._on_suspending)
        self._watcher.resumed.connect(self._on_resumed)
        self._watcher.displaysOff.connect(self._on_suspending)
        self._watcher.displaysOn.connect(self._on_displays_on)
        self._watcher.clockChanged.connect(self._on_clock_changed)

        self._night_light_check = QTimer(interval=WINDOWS_NIGHT_LIGHT_CHECK_MS)
        self._night_light_check.timeout.connect(self._check_windows_night_light)
        self._night_light_check.start()
        self._check_windows_night_light()
        self._zone_check = QTimer(interval=ZONE_CHECK_MS)
        self._zone_check.timeout.connect(self._check_zone)
        self._zone_check.start()
        # Old Report a Problem zips go from the Desktop: at start, then daily.
        self._cleanup = QTimer(interval=CLEANUP_INTERVAL_MS)
        self._cleanup.timeout.connect(diagnostics.remove_old_bundles)
        self._cleanup.start()
        QTimer.singleShot(CLEANUP_DELAY_MS, diagnostics.remove_old_bundles)

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

    def _read_autostart(self) -> None:
        """Take "start when I sign in" from Windows: the installer, Task
        Manager or Windows' Settings may have changed it."""
        enabled = autostart.read(self._autostart)
        if enabled is not None and enabled != self._config.general.start_with_windows:
            self._config = replace(self._config, general=replace(self._config.general, start_with_windows=enabled))

    def show_settings(self) -> None:
        if self._settings is None:
            self._read_autostart()
            self._settings = SettingsWindow(
                self.controller,
                self._config,
                can_autostart=self._autostart is not None,
                monitors=self.monitors,
                scheduler=self.scheduler,
                compatibility=self.compatibility,
                report=self.compatibility_report,
                running_apps=winsystem.running_apps,
                report_problem=self.report_problem,
            )
            self._settings.applied.connect(self._apply_settings)
        self._settings.set_windows_night_light(self._windows_night_light)
        if self.tray:
            self.flyout.hide()
        reveal.show_window(self._settings)

    def compatibility(self) -> list[compat.CompatibilityEntry]:
        warmest = getattr(self._gamma, "warmest_accepted", dict)()
        return compat.entries(self.monitors.monitors, self.monitors.states, warmest)

    def compatibility_report(self) -> str:
        return compat.render(self.compatibility(), compat.system_summary())

    def report_problem(self) -> None:
        """Save a diagnostics zip to the Desktop, copy a summary, and say where it is."""
        try:
            path, text = diagnostics.write_bundle(self._diagnostics_sources())
        except OSError as exc:
            QMessageBox.warning(None, APP_NAME, f"Could not save the diagnostics: {exc}")
            return
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(text)
        # Cleared later if it's still ours: nothing lingers on the clipboard.
        QTimer.singleShot(diagnostics.CLIPBOARD_MINUTES * 60_000, lambda: clipboard.text() == text and clipboard.clear())
        box = QMessageBox(QMessageBox.Icon.Information, f"{APP_NAME}: Report a Problem", "")
        box.setText(f"<b>Diagnostics saved</b><br>{path.name}, in your {path.parent.name} folder. A summary is on the clipboard.")
        box.setInformativeText(
            "To report the problem, open an issue on GitHub: describe what happened, paste the summary and attach the file.\n\n"
            "Nothing personal is included (no names or location). The file is deleted after "
            f"{diagnostics.KEEP_DAYS} days and the clipboard is cleared after {diagnostics.CLIPBOARD_MINUTES} minutes."
        )
        show = box.addButton("Show File", QMessageBox.ButtonRole.ActionRole)
        issue = box.addButton("Open GitHub Issues", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)
        reveal.fade_in(box)
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
        try:
            answer = self.commands.handle(request)
        except Exception:  # a bug must still get an answer, not leave the command line waiting
            log.exception("Handling a command-line request failed")
            answer = {"ok": False, "output": "Winlight couldn't do that; see its log for why."}
        reply(answer)
        if request.get("panel") and answer.get("ok"):
            self.show_panel()  # --panel: show the result, unless a game or video is fullscreen

    def _diagnostics_sources(self) -> diagnostics.Sources:
        return diagnostics.Sources(
            monitors=self.monitors,
            controller=self.controller,
            config=lambda: self._config,
            config_path=self._config_path,
            log_dir=config.log_dir(),
            skipped=lambda: set(self._safe_mode.skipped),
            last_wake=lambda: list(self.wake_report.lines),
            compatibility=self.compatibility_report,
        )

    # --- Settings -------------------------------------------------------------

    def _location(self) -> tuple[float, float] | None:
        """The Night Light location, which sunrise and sunset schedule points use too."""
        night_light = self._config.night_light
        if night_light.timezone or (night_light.latitude, night_light.longitude) != (0.0, 0.0):
            return night_light.latitude, night_light.longitude
        return None

    def _idle_held(self) -> bool:
        """Whether not to dim for idle now. Not just because a window is
        fullscreen (a browser or an editor can be): an app keeping the screen
        on (videos, calls, presentations; Windows' display-required state),
        an exclusive-fullscreen game, or an app the user listed in front."""
        if winsystem.display_required() or winsystem.exclusive_fullscreen():
            return True
        listed = self._config.general.idle_exempt_apps
        if listed:
            app = winsystem.foreground_app()
            return bool(app and app.exe in listed)
        return False

    def _hidden_keys(self) -> frozenset[str]:
        """The connected displays the user hid (found under older keys too)."""
        return frozenset(m.key for m in self.monitors.monitors if self._config.hidden(m.key))

    def _apply_settings(self, new: config.AppConfig) -> None:
        old = self._config
        self._config = new
        try:
            config.save(new, self._config_path)
        except OSError:
            log.exception("Could not save settings to %s", self._config_path)
        self.controller.set_config(new.night_light)
        if new.general.start_with_windows != old.general.start_with_windows:
            autostart.change(self._autostart, new.general.start_with_windows)
        self.brightness_memory.set_enabled(new.general.remember_brightness)
        if keep := getattr(self._gamma, "set_keep_calibration", None):
            keep(new.general.keep_calibration)
        if new.general.software_dimming != old.general.software_dimming:
            self.monitors.set_software_dimming(new.general.software_dimming)
        self._apply_preferences(new)
        self.scheduler.set_config(new.brightness_schedule)
        if new.night_light != old.night_light:
            self.scheduler.evaluate("location")  # sunrise and sunset points follow the location

    def _apply_preferences(self, settings: config.AppConfig) -> None:
        """What every part needs from the settings besides night light and the schedule."""
        general = settings.general
        self.flyout.set_linked(general.link_brightness)
        self.brightness_keys.set_enabled(general.follow_laptop_brightness)
        self.idle_dimmer.configure(general.idle_dim, general.idle_minutes, general.idle_level)
        self.flyout.set_display_prefs(settings.display)
        self.brightness_memory.set_hidden(settings.hidden)
        self.scheduler.set_hidden(settings.hidden)
        self._exemptions.set_config(settings.night_light)
        theme.apply(general.theme, general.accent)

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

    def _on_suspending(self) -> None:
        # Nothing goes to the displays while the PC sleeps or the screens are
        # off: DDC/CI can wake a sleeping monitor or hang its firmware.
        self.monitors.suspend()

    def _on_resumed(self) -> None:
        # Windows' event and the clock check can both report one wake; the
        # wake report follows it once (and schedules detection, logging when
        # each display answers again).
        new_wake = self.wake_report.woke()
        if self.monitors.paused:
            self.monitors.resume(() if new_wake else RESCAN_AFTER_CHANGE_S)
        if not new_wake:
            return
        # Monitors and laptop panels often wake at another brightness, and a
        # schedule point may have passed while asleep (only the latest applies).
        self.controller.reapply_now()
        self.brightness_memory.restore_all_soon()
        self._reapply_colors()
        self.scheduler.evaluate("wake")

    def _on_displays_on(self) -> None:
        # The screen timeout ended without the PC sleeping: no display change
        # is reported, but ramps and levels may have been reset (Monitorian
        # re-checks then too), and DisplayPort monitors often drop out while
        # off; detection brings them back.
        if self.monitors.paused:
            self.monitors.resume(RESCAN_AFTER_DISPLAYS_ON_S)
        else:
            self.monitors.refresh_later(RESCAN_AFTER_DISPLAYS_ON_S)
        self.controller.reapply_now()
        self.brightness_memory.restore_all_soon()
        self._reapply_colors()

    def _on_clock_changed(self) -> None:
        if not self._check_zone():
            self.controller.update()
            self.scheduler.evaluate("clock")

    def _check_zone(self) -> bool:
        """Follow a change of time zone: both schedules are laid out in it.
        True if it changed (and the schedules were brought up to date)."""
        zone = system_zone()
        if zone is None or zone == self.controller.zone:
            return False
        log.info("The time zone changed; both schedules follow it")  # which one isn't logged (privacy.py)
        self.controller.zone = self.scheduler.zone = zone
        self.controller.update()
        self.scheduler.evaluate("time zone")
        return True

    def _on_displays_changed(self, rescan_after_s: tuple[float, ...]) -> None:
        self._reapply_colors()
        self.controller.reapply_now()
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
        self._night_light_check.stop()
        self._zone_check.stop()
        self._cleanup.stop()
        self._watcher.stop()  # signing out keeps the event loop running: no late display events
        self.brightness_keys.set_enabled(False)
        self._exemptions.stop()
        self.idle_dimmer.release()  # quitting while dimmed: levels back first (queued before the monitor thread stops)
        self.scheduler.shutdown()
        self.controller.shutdown()
        self.brightness_memory.shutdown()
        self.monitors.shutdown()
        self.system_events.close()


def _reveal(path: Path) -> None:
    """Show a file in Explorer (selected), or its folder elsewhere."""
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
