# SPDX-License-Identifier: GPL-3.0-only
"""Notices monitors being plugged in or out, display changes, and waking from sleep.

All of these can reset gamma ramps and leave monitors briefly unreachable over
DDC/CI, so the app re-applies night light and detects monitors again (several
times, like Monitorian and Twinkle Tray).
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


class DisplayWatcher(QObject):
    displaysChanged = Signal()
    resumed = Signal()

    def __init__(self, app: QGuiApplication, clock: Callable[[], float] = time.time, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._clock = clock
        self._last_check = clock()

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

    def check_clock(self) -> None:
        now = self._clock()
        gap = now - self._last_check
        self._last_check = now
        if gap > RESUME_GAP_S:
            log.info("Woke up after %.0f s (sleep, hibernation or a clock change)", gap)
            self.resumed.emit()

    def _on_screen_added(self, screen: QScreen) -> None:
        self._watch(screen)
        self._changed(f"screen added: {screen.name()}")

    def _watch(self, screen: QScreen) -> None:
        screen.geometryChanged.connect(lambda _rect: self._changed(f"{screen.name()} changed resolution or position"))

    def _changed(self, why: str) -> None:
        log.info("Display change (%s)", why)
        self._settle.start()

    def _emit_changed(self) -> None:
        self.displaysChanged.emit()
