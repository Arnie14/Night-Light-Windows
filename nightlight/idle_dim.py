# SPDX-License-Identifier: GPL-3.0-only
"""Dimming every display while the PC is left alone (Twinkle Tray's "Idle
Detection"), and bringing them back on the first input.

Idle means no keyboard or mouse input for the chosen minutes. It never
counts while an app keeps the display on (video players, browsers playing
video, presentations: Windows' display-required state, the signal they set
for exactly this), nor while a game or video is fullscreen or Windows says
not to interrupt; Twinkle Tray has the same two exceptions ("fullscreen apps
block idle detection", "media blocks idle detection").

Safety rules:
- Dimming only ever lowers a display, never brightens one already below
  the idle level, and leaves hidden displays alone.
- It goes through MonitorManager.restore_brightness: it isn't the user's
  choice (not remembered), and monitors that say they're asleep are skipped.
- Coming back, each display gets the level it should have now, by the same
  rule as restores and the brightness schedule (BrightnessMemory.target), so
  a schedule step that passed while away isn't undone; else its level
  before dimming. While dimmed, the schedule and restores hold off, so
  nothing brightens an idle screen.
- Quitting while dimmed puts the levels back first.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from nightlight.brightness_memory import BrightnessMemory
from nightlight.monitors import MonitorManager

log = logging.getLogger(__name__)

CHECK_MS = 5000  # while waiting to dim
WATCH_MS = 300  # while dimmed: notice input quickly


class IdleDimmer(QObject):
    def __init__(
        self,
        monitors: MonitorManager,
        memory: BrightnessMemory,
        idle_seconds: Callable[[], float],
        keeps_display_on: Callable[[], bool],
        hidden: Callable[[], frozenset[str]] = frozenset,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._monitors = monitors
        self._memory = memory
        self._idle_seconds = idle_seconds
        self._keeps_display_on = keeps_display_on
        self._hidden = hidden
        self._enabled = False
        self._after_s = 300.0
        self._level = 20
        self._before: dict[int, int] | None = None  # index -> level before dimming, while dimmed
        self._timer = QTimer(self, interval=CHECK_MS)
        self._timer.timeout.connect(self.check)

    @property
    def dimmed(self) -> bool:
        return self._before is not None

    def configure(self, enabled: bool, minutes: int, level: int) -> None:
        self._enabled = enabled
        self._after_s = max(1, min(240, minutes)) * 60
        self._level = max(0, min(100, level))
        if enabled:
            self._timer.start(WATCH_MS if self.dimmed else CHECK_MS)
        else:
            self._timer.stop()
            self.wake()

    def check(self) -> None:
        idle = self._idle_seconds()
        if self.dimmed:
            if idle < self._after_s:
                self.wake()
        elif idle >= self._after_s and not self._keeps_display_on():
            self._dim(idle)

    def wake(self) -> None:
        """Input again (or the feature turned off, or quitting): bring every display back."""
        if self._before is None:
            return
        before, self._before = self._before, None
        self._memory.set_held(False)
        log.info("Input again; bringing displays back from idle dimming")
        for monitor in self._monitors.monitors:
            if monitor.index in before:
                level = self._memory.target(monitor)
                self._monitors.restore_brightness(monitor.index, before[monitor.index] if level is None else level, fade=True)
        if self._enabled:
            self._timer.start(CHECK_MS)

    def _dim(self, idle: float) -> None:
        hidden = self._hidden()
        before = {}
        for monitor in self._monitors.monitors:
            current = self._monitors.brightness.get(monitor.index)
            if not monitor.supported or monitor.key in hidden or current is None:
                continue
            target = max(monitor.minimum, self._level)
            if target < current:  # never brighten
                before[monitor.index] = current
                self._monitors.restore_brightness(monitor.index, target, fade=True)
        if not before:
            return
        log.info("No input for %.0f min; dimming %d display(s) to %d%%", idle / 60, len(before), self._level)
        self._before = before
        self._memory.set_held(True)
        self._timer.start(WATCH_MS)
