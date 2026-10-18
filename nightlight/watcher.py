# SPDX-License-Identifier: GPL-3.0-only
"""Notices monitors being plugged in or out, display changes, sleep and waking.

All of these can reset gamma ramps and leave monitors briefly unreachable over
DDC/CI, so the app re-applies night light and detects monitors again (several
times, like Monitorian and Twinkle Tray).

Sources, merged so that one event gives one signal:

- Qt's screen signals: screens added, removed or resized.
- Windows' notifications (hardware/winevents.py): suspend and resume,
  displays off and on, unlock, WM_DISPLAYCHANGE (which also covers HDR
  being switched), clock changes.
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


class DisplayWatcher(QObject):
    displaysChanged = Signal()
    suspending = Signal()
    resumed = Signal()
    displaysOff = Signal()
    displaysOn = Signal()  # displays back on after Windows' screen timeout, or unlocked after a while
    clockChanged = Signal()

    def __init__(self, app: QGuiApplication, clock: Callable[[], float] = time.time, parent: QObject | None = None, events=None) -> None:
        super().__init__(parent)
        self._clock = clock
        self._last_check = clock()
        self._native_resume_at: float | None = None
        self._suspended = False

        self._settle = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._settle.timeout.connect(self._emit_changed)
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(lambda screen: self._changed(f"screen removed: {screen.name()}"))
        app.primaryScreenChanged.connect(lambda screen: self._changed(f"primary screen is now {screen.name()}"))
        for screen in app.screens():
            self._watch(screen)

        self._clock_timer = QTimer(self, interval=CLOCK_CHECK_MS)
        self._clock_timer.timeout.connect(self.check_clock)
        self._clock_timer.start()

        if events is not None:
            events.suspending.connect(self._on_suspending)
            events.resumed.connect(self._on_native_resume)
            events.displaysOff.connect(self.displaysOff)
            events.displaysOn.connect(self.displaysOn)
            events.unlocked.connect(self.displaysOn)
            events.displaysChanged.connect(lambda: self._changed("Windows reported a display change"))
            events.clockChanged.connect(self.clockChanged)

    def check_clock(self) -> None:
        now = self._clock()
        gap = now - self._last_check
        self._last_check = now
        if gap > RESUME_GAP_S:
            if self._native_resume_at is not None and now - self._native_resume_at < NATIVE_RESUME_WINDOW_S:
                return  # Windows already said so
            log.info("Woke up after %.0f s (sleep, hibernation or a clock change)", gap)
            self._resume()

    def _on_suspending(self) -> None:
        self._suspended = True
        self._settle.stop()  # nothing to re-apply while asleep
        self.suspending.emit()

    def _on_native_resume(self) -> None:
        self._native_resume_at = self._clock()
        self._last_check = self._native_resume_at
        self._resume()

    def _resume(self) -> None:
        self._suspended = False
        self.resumed.emit()

    def _on_screen_added(self, screen: QScreen) -> None:
        self._watch(screen)
        self._changed(f"screen added: {screen.name()}")

    def _watch(self, screen: QScreen) -> None:
        screen.geometryChanged.connect(lambda _rect: self._changed(f"{screen.name()} changed resolution or position"))

    def _changed(self, why: str) -> None:
        if self._suspended:
            log.debug("Ignoring a display change while suspending (%s)", why)
            return
        log.info("Display change (%s)", why)
        self._settle.start()

    def _emit_changed(self) -> None:
        self.displaysChanged.emit()
