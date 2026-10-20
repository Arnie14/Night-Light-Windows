# SPDX-License-Identifier: GPL-3.0-only
"""Dimming every display while the PC is left alone (Twinkle Tray's "Idle
Detection"), and bringing them back on the first input.

Idle means no keyboard or mouse input for the chosen minutes. It never
counts while an app keeps the display on (video players, browsers playing
video, video calls, presentations: Windows' display-required state, the
signal they set for exactly this), while a game runs exclusive fullscreen or
Windows' presentation mode is on, or while an app the user listed is in
front (NightLightApp._idle_held). A window that merely fills the screen (a
browser or an editor in fullscreen) doesn't hold it: in beta 5 it did, so
dimming never happened with such a window in front. Music alone doesn't
hold it either: the screen isn't needed for that.

How each display is dimmed, the safe way for each:
- External displays (DDC/CI monitors, TVs, displays without control) are
  dimmed in software, a separate "idle" layer of the gamma ramp, composed
  with night light and with a TV's own software dimming. No DDC/CI writes:
  idle cycles happen several times a day, and some monitors save every
  change to their memory, or misbehave when written to unprompted.
- The laptop panel is dimmed for real through WMI, Windows' own control,
  which also saves power (Windows dims it the same way when idle).
- HDR displays ignore gamma ramps, so they're dimmed through Windows' SDR
  content brightness where that is their control (also Windows' own,
  nothing sent to the display), and otherwise left alone.

Safety rules:
- Dimming only ever lowers a display, never brightens one already below
  the idle level, and leaves hidden displays alone.
- Laptop panels and SDR levels go through MonitorManager.restore_brightness:
  not the user's choice (not remembered), skipped if asleep.
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
from nightlight.monitors import IDLE, RESTORE, MonitorManager

log = logging.getLogger(__name__)

CHECK_MS = 5000  # while waiting to dim
WATCH_MS = 300  # while dimmed: notice input quickly
# The software layer fades in these steps (the hardware ones fade by themselves).
FADE_STEPS = 8
FADE_STEP_MS = 60


class IdleDimmer(QObject):
    def __init__(
        self,
        monitors: MonitorManager,
        memory: BrightnessMemory,
        idle_seconds: Callable[[], float],
        keeps_display_on: Callable[[], bool],
        hidden: Callable[[], frozenset[str]] = frozenset,
        gamma=None,  # a gamma backend with set_idle_dim, for software dimming
        parent: QObject | None = None,
        scheduler=None,  # the brightness schedule, held back while dimmed like restores
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
        self._gamma = gamma if hasattr(gamma, "set_idle_dim") else None
        self._scheduler = scheduler
        self._before: dict[str, int] | None = None  # display key -> level before dimming, while dimmed
        self._software: list[str] = []  # displays dimmed in software, while dimmed
        self._fade: list[dict[str, int]] = []  # software levels still to apply, one per step
        self._fade_timer = QTimer(self, interval=FADE_STEP_MS)
        self._fade_timer.timeout.connect(self._fade_step)
        self._timer = QTimer(self, interval=CHECK_MS)
        self._timer.timeout.connect(self.check)

    @property
    def dimmed(self) -> bool:
        return self._before is not None

    def plan(self) -> tuple[dict[str, int], list[str]]:
        """Which displays to dim, and how: (display key -> hardware/SDR level for the
        laptop panel and SDR-controlled HDR displays, never brighter than now),
        [GDI names to dim in software]."""
        hidden = self._hidden()
        levels, software = {}, []
        for monitor in self._monitors.monitors:
            if monitor.key in hidden:
                continue
            current = self._monitors.value(monitor.key)
            if monitor.supported and (monitor.internal and monitor.control == "hardware" or monitor.control == "sdr"):
                target = max(monitor.minimum, self._level)
                if current is not None and target < current:
                    levels[monitor.key] = target
            elif not monitor.hdr and not monitor.internal and monitor.display and self._gamma is not None:
                if monitor.display not in software:  # cloned displays share one ramp
                    software.append(monitor.display)
        return levels, software

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
        elif self._enabled and idle >= self._after_s and not self._keeps_display_on():
            self._dim(idle)

    def wake(self) -> None:
        """Input again (or the feature turned off, or quitting): bring every display back."""
        if self._before is None:
            return
        before, self._before = self._before, None
        self._memory.set_held(False)
        log.info("Input again; bringing displays back from idle dimming")
        for monitor in self._monitors.monitors:
            if monitor.key in before:
                level = self._memory.target(monitor)
                self._monitors.restore_brightness(monitor.key, before[monitor.key] if level is None else level, fade=True, source=RESTORE)
        if self._scheduler is not None:
            # After the restores: a point that began while away replaces them in the queue.
            self._scheduler.set_held(False)
        self._fade_software(self._level, 100, self._software)
        self._software = []
        if self._enabled:
            self._timer.start(CHECK_MS)

    def release(self) -> None:
        """Quitting: undo at once, without fading, and stop watching (wake()
        starts the idle check again, which could dim after signing out began)."""
        self._fade_timer.stop()
        self._fade = []
        if self.dimmed:
            self.wake()
        self._enabled = False
        self._timer.stop()
        self._fade_timer.stop()
        if self._gamma is not None:
            self._gamma.set_idle_dim({})

    def _dim(self, idle: float) -> None:
        levels, software = self.plan()
        if not levels and not software:
            return
        log.info(
            "No input for %.0f min; dimming to %d%%: %d display(s) in software, %d through Windows",
            idle / 60, self._level, len(software), len(levels),
        )
        self._before = {key: self._monitors.value(key) for key in levels}
        for key, level in levels.items():
            self._monitors.restore_brightness(key, level, fade=True, source=IDLE)
        self._software = software
        self._fade_software(100, self._level, software)
        self._memory.set_held(True)
        if self._scheduler is not None:
            self._scheduler.set_held(True)
        self._timer.start(WATCH_MS)

    def _fade_software(self, start: int, end: int, displays: list[str]) -> None:
        if self._gamma is None or not displays:
            return
        steps = [round(start + (end - start) * i / FADE_STEPS) for i in range(1, FADE_STEPS + 1)]
        self._fade = [{d: level for d in displays} if level < 100 else {} for level in steps]
        self._fade_timer.start()
        self._fade_step()

    def _fade_step(self) -> None:
        if not self._fade:
            self._fade_timer.stop()
            return
        self._gamma.set_idle_dim(self._fade.pop(0))
