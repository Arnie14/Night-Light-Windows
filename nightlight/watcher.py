# SPDX-License-Identifier: GPL-3.0-only
"""Notices monitors being plugged in or out, display changes, sleep and waking.

All of these can reset gamma ramps and leave monitors briefly unreachable over
DDC/CI, so the app re-applies night light and detects monitors again (several
times, like Monitorian and Twinkle Tray).

Sources, merged so that one event gives one signal:

- Qt's screen signals: screens added, removed or resized.
- Windows' notifications (hardware/winsystem.py): suspend and resume,
  displays off and on, WM_DISPLAYCHANGE (which also covers HDR being
  switched), clock changes. Unlocking goes to the app directly: it only
  needs night light put back at once, not displays detected again.
- A jump in the wall clock between checks, which notices waking even
  without Windows' notifications (other platforms, or if they failed).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QScreen

log = logging.getLogger(__name__)

# Windows sends bursts of changes while a monitor connects or the layout
# changes; act once they've settled.
SETTLE_MS = 1000

# Resume is noticed as a jump in wall-clock time between checks, which needs
# no OS-specific power notifications.
CLOCK_CHECK_MS = 5000
RESUME_GAP_S = 30.0
# A clock jump this soon after Windows reported the resume is the same wake.
NATIVE_RESUME_WINDOW_S = 120.0
# Windows said it was going to sleep, yet the clock checks keep running
# normally this long after: it didn't (a sleep that failed or was cancelled
# sends no resume). Without this, displays stayed "asleep" for the session.
STALE_SUSPEND_S = 90.0
# Input after the displays were reported off means they're on: Windows'
# "display on" notification was missed. Only input this long after the "off"
# counts; the key or click that turned them off comes just before it.
INPUT_AFTER_OFF_S = 2.0
# A display that keeps changing (a loose cable, a monitor cycling its inputs)
# would postpone the settled signal for ever; it fires at least this often.
SETTLE_MAX_WAIT_S = 5.0


class DisplayWatcher(QObject):
    displaysChanged = Signal()
    suspending = Signal()
    resumed = Signal()
    displaysOff = Signal()
    displaysOn = Signal()  # displays back on after Windows' screen timeout
    clockChanged = Signal()

    def __init__(self, app: QGuiApplication, clock: Callable[[], float] = time.time, parent: QObject | None = None, events=None,
                 idle_seconds: Callable[[], float | None] | None = None) -> None:
        super().__init__(parent)
        self._clock = clock
        self._stopped = False
        self._idle_seconds = idle_seconds  # seconds since the last keyboard or mouse input (winsystem.idle_seconds)
        self._last_check = clock()
        self._native_resume_at: float | None = None
        self._suspended = False
        self._suspended_at = 0.0
        self._displays_off = False
        self._displays_off_at = 0.0
        self._pending_since: float | None = None  # first change not yet signalled

        self._settle = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._settle.timeout.connect(self._emit_changed)
        # Methods, not lambdas: Qt disconnects them when the watcher goes, so
        # the application's signals never call into a deleted object.
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screen_removed)
        app.primaryScreenChanged.connect(self._on_primary_changed)
        for screen in app.screens():
            self._watch(screen)

        self._clock_timer = QTimer(self, interval=CLOCK_CHECK_MS)
        self._clock_timer.timeout.connect(self.check_clock)
        self._clock_timer.start()

        if events is not None:
            events.suspending.connect(self._on_suspending)
            events.resumed.connect(self._on_native_resume)
            events.displayOff.connect(self._on_displays_off)
            events.displayOn.connect(self._on_displays_on)
            events.displayChanged.connect(self._on_windows_display_change)
            events.clockChanged.connect(self._on_clock_changed)

    def stop(self) -> None:
        """Quitting: no more signals (signing out keeps the event loop running a while)."""
        self._clock_timer.stop()
        self._settle.stop()
        self._stopped = True

    def check_clock(self) -> None:
        if self._stopped:
            return
        now = self._clock()
        gap = now - self._last_check
        self._last_check = now
        if gap > RESUME_GAP_S:
            if self._native_resume_at is not None and now - self._native_resume_at < NATIVE_RESUME_WINDOW_S:
                return  # Windows already said so
            log.info("Woke up after %.0f s (sleep or hibernation, noticed from the clock)", gap)
            self._resume()
            return
        if self._suspended and now - self._suspended_at > STALE_SUSPEND_S:
            log.warning("Windows said the PC was going to sleep %.0f s ago, but it is still running; carrying on as awake",
                        now - self._suspended_at)
            self._resume()
        if self._displays_off and self._idle_seconds is not None:
            idle = self._idle_seconds()
            if idle is not None and now - idle > self._displays_off_at + INPUT_AFTER_OFF_S:
                log.info("Input while the displays were reported off: they're on (Windows didn't say so)")
                self._on_displays_on()

    def _on_clock_changed(self) -> None:
        # The clock was changed (by hand, or synced): its jump isn't a wake.
        self._last_check = self._clock()
        self.clockChanged.emit()

    def _on_displays_off(self) -> None:
        self._displays_off = True
        self._displays_off_at = self._clock()
        self.displaysOff.emit()

    def _on_displays_on(self) -> None:
        self._displays_off = False
        self.displaysOn.emit()

    def _on_suspending(self) -> None:
        self._suspended = True
        self._suspended_at = self._clock()
        self._settle.stop()  # nothing to re-apply while asleep
        self.suspending.emit()

    def _on_native_resume(self) -> None:
        self._native_resume_at = self._clock()
        self._last_check = self._native_resume_at
        self._resume()

    def _resume(self) -> None:
        self._suspended = False
        self._displays_off = False  # waking turns them on
        self.resumed.emit()

    def _on_screen_added(self, screen: QScreen) -> None:
        self._watch(screen)
        self._changed(f"screen added: {screen.name()}")

    def _on_screen_removed(self, screen: QScreen) -> None:
        self._changed(f"screen removed: {screen.name()}")

    def _on_primary_changed(self, screen: QScreen) -> None:
        self._changed(f"primary screen is now {screen.name() if screen is not None else 'none'}")

    def _on_windows_display_change(self) -> None:
        self._changed("Windows reported a display change")

    def _on_geometry_changed(self, _rect) -> None:
        screen = self.sender()
        self._changed(f"{screen.name() if isinstance(screen, QScreen) else 'a screen'} changed resolution or position")

    def _watch(self, screen: QScreen) -> None:
        screen.geometryChanged.connect(self._on_geometry_changed)

    def _changed(self, why: str) -> None:
        if self._stopped:
            return
        if self._suspended:
            log.debug("Ignoring a display change while suspending (%s)", why)
            return
        log.info("Display change (%s)", why)
        now = self._clock()
        if self._pending_since is None:
            self._pending_since = now
        if now - self._pending_since >= SETTLE_MAX_WAIT_S:
            log.info("Displays still changing after %.0f s; acting on it now", now - self._pending_since)
            self._settle.stop()
            self._emit_changed()
            return
        self._settle.start()

    def _emit_changed(self) -> None:
        self._pending_since = None
        if not self._stopped:
            self.displaysChanged.emit()
