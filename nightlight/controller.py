# SPDX-License-Identifier: GPL-3.0-only
"""Keeps the displays' colour temperature in line with the schedule."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from nightlight.config import NightLightConfig
from nightlight.core.colortemp import NEUTRAL_KELVIN
from nightlight.core.schedule import NightLightState, Override, OverrideKind, compute_state
from nightlight.hardware.gamma import GammaBackend

log = logging.getLogger(__name__)

# Frequent enough for smooth transitions, and it re-applies the ramp after
# sleep, resolution changes or fullscreen apps have reset it.
TICK_MS = 10_000

# Sudden changes (turning on, suspending, changing settings) are animated
# instead of snapping, in steps of TEMPERATURE_STEP over QUICK_ADJUST_MS;
# previews run PREVIEW_SPEEDUP times faster. Same values as KWin.
QUICK_ADJUST_MS = 2000
TEMPERATURE_STEP = 50
PREVIEW_SPEEDUP = 8

# How often to check that Windows hasn't reset our gamma ramps (it does on the
# lock screen and after sleep, often before any event says so). Each check is
# one GetDeviceGammaRamp per display.
VERIFY_MS = 1500
# After unlocking or waking, check again at these moments (ms): Windows may
# reset the ramp once more after the event, while the desktop reappears.
REAPPLY_BURST_MS = (0, 100, 300, 700, 1500, 3000)

# Pausing night light on one display (an exempt app or fullscreen game) and
# resuming it fade in about a quarter of a second, not a jump.
PAUSE_STEP = 250
PAUSE_TICK_MS = 20

# Quitting fades back to neutral this quickly, blocking: the app is exiting.
QUIT_FADE_STEPS = 8
QUIT_FADE_STEP_S = 0.03


def step_towards(current: int, target: int, step: int = TEMPERATURE_STEP) -> int:
    return min(current + step, target) if current < target else max(current - step, target)


class NightLightController(QObject):
    changed = Signal()

    def __init__(
        self,
        gamma: GammaBackend,
        config: NightLightConfig,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._gamma = gamma
        self._config = config
        self._clock = clock
        self._override: Override | None = None
        self._preview: int | None = None  # live preview while dragging a slider
        self._current: int | None = None  # what the displays show now
        self._animation_target: int | None = None
        self._paused: dict[str, str] = {}  # display -> why night light is paused there
        self._overrides: dict[str, int] = {}  # display -> its own temperature while pausing/resuming
        self.state: NightLightState = compute_state(config, clock())

        self._animation = QTimer(self)
        self._animation.timeout.connect(self._animation_step)
        self._timer = QTimer(self, interval=TICK_MS)
        self._timer.timeout.connect(self.update)
        self._timer.start()
        self._pause_timer = QTimer(self, interval=PAUSE_TICK_MS)
        self._pause_timer.timeout.connect(self._pause_step)
        self._verify_timer = QTimer(self, interval=VERIFY_MS)
        self._verify_timer.timeout.connect(self.verify)
        if hasattr(gamma, "verify"):
            self._verify_timer.start()
        self.update()

    @property
    def config(self) -> NightLightConfig:
        return self._config

    @property
    def override(self) -> Override | None:
        return self.state.override

    @property
    def current_temperature(self) -> int:
        return self._current if self._current is not None else NEUTRAL_KELVIN

    @property
    def target_temperature(self) -> int:
        return self._preview if self._preview is not None else self.state.temperature

    def now(self) -> datetime:
        return self._clock()

    @property
    def paused(self) -> Mapping[str, str]:
        """Displays where night light is paused, and why ("game.exe", "fullscreen")."""
        return self._paused

    def set_paused(self, paused: Mapping[str, str]) -> None:
        """Pause night light on these displays (fading), resume it on the others."""
        if dict(paused) == self._paused or not hasattr(self._gamma, "set_override"):
            return
        if paused:
            log.info("Night light paused on %s", ", ".join(f"{d} ({why})" for d, why in paused.items()))
        else:
            log.info("Night light resumed on every display")
        self._paused = dict(paused)
        self._pause_timer.start()
        self._pause_step()
        self.changed.emit()

    def verify(self) -> None:
        """Re-apply our ramps where Windows reset them."""
        if verify := getattr(self._gamma, "verify", None):
            try:
                verify()
            except Exception:
                log.warning("Checking the gamma ramps failed", exc_info=True)

    def reapply_now(self) -> None:
        """After unlocking, waking or a display turning on: check right away and
        a few more times as the desktop comes back."""
        for delay in REAPPLY_BURST_MS:
            QTimer.singleShot(delay, self.verify)

    def set_config(self, config: NightLightConfig) -> None:
        self._config = config
        self.update()

    def set_override(self, kind: OverrideKind, until: datetime | None = None) -> None:
        self._override = Override(kind, until)
        self.update()

    def clear_override(self) -> None:
        self._override = None
        self.update()

    def preview(self, kelvin: int | None) -> None:
        """Show `kelvin` immediately, or pass None to return to the schedule."""
        self._preview = kelvin
        self._retarget()

    def update(self) -> None:
        now = self._clock()
        if self._override is not None and not self._override.active_at(now):
            log.info("Night light override expired")
            self._override = None
        self.state = compute_state(self._config, now, self._override)
        self._retarget(reapply=True)
        self.changed.emit()

    def shutdown(self) -> None:
        self._timer.stop()
        self._animation.stop()
        self._pause_timer.stop()
        self._verify_timer.stop()
        self._fade_out()
        # Undo everything, software dimming included, where the backend can.
        getattr(self._gamma, "release", self._gamma.reset)()

    def _fade_out(self) -> None:
        """A short fade to neutral before quitting, rather than a jump."""
        current = self._current
        if current is None or current == NEUTRAL_KELVIN or getattr(self._gamma, "animates", False):
            return
        for i in range(1, QUIT_FADE_STEPS):
            kelvin = round(current + (NEUTRAL_KELVIN - current) * i / QUIT_FADE_STEPS)
            try:
                self._gamma.apply(kelvin)
            except Exception:
                return
            time.sleep(QUIT_FADE_STEP_S)

    def _pause_step(self) -> None:
        """Move each paused display towards neutral, and each resumed one back
        to the night light temperature, one step per tick."""
        settled = True
        for display in set(self._overrides) | set(self._paused):
            shown = self.current_temperature
            current = self._overrides.get(display, shown)
            target = NEUTRAL_KELVIN if display in self._paused else shown
            value = step_towards(current, target, PAUSE_STEP)
            if value == shown:
                # Same as every other display (resumed, or paused while night
                # light is off): no override, so the ramp isn't touched for nothing.
                if self._overrides.pop(display, None) is not None:
                    self._gamma.set_override(display, None)
                continue
            self._overrides[display] = value
            self._gamma.set_override(display, value)
            settled = settled and value == target
        if settled:
            self._pause_timer.stop()

    def _retarget(self, reapply: bool = False) -> None:
        target = self.target_temperature
        if self._current is None and target != NEUTRAL_KELVIN and not getattr(self._gamma, "animates", False):
            # Starting at night: fade in from the neutral screen there is, as when
            # turning night light on, instead of snapping to the night temperature.
            self._current = NEUTRAL_KELVIN
        # Backends that animate on their own (KWin) get the target directly.
        if self._current is None or getattr(self._gamma, "animates", False) or abs(target - self._current) <= TEMPERATURE_STEP:
            self._animation.stop()
            self._set(target, reapply)
            return
        if self._animation.isActive() and self._animation_target == target:
            return
        self._animation_target = target
        steps = abs(target - self._current) / TEMPERATURE_STEP
        duration = QUICK_ADJUST_MS / (PREVIEW_SPEEDUP if self._preview is not None else 1)
        self._animation.start(max(1, int(duration / steps)))

    def _animation_step(self) -> None:
        target = self._animation_target
        if target is None or self._current is None:
            self._animation.stop()
            return
        if not self._set(step_towards(self._current, target)) or self._current == target:
            self._animation.stop()

    def _set(self, kelvin: int, reapply: bool = False) -> bool:
        if kelvin == self._current and not reapply:
            return True
        if self._paused or self._overrides:
            self._pause_timer.start()  # paused displays keep their own temperature as this one changes
        try:
            if kelvin == NEUTRAL_KELVIN:
                # Reset rather than "apply 6500 K" and don't repeat it, so other
                # tools (Windows Night Light, KWin's own) aren't fought while ours is idle.
                # Displays dimmed in software keep their (dimmed) ramp,
                # which Windows may reset meanwhile; re-apply that one.
                if self._current != NEUTRAL_KELVIN or (reapply and getattr(self._gamma, "dimmed", False)):
                    self._gamma.reset()
            else:
                self._gamma.apply(kelvin)
            self._current = kelvin
            return True
        except Exception:
            log.warning("Applying %d K failed", kelvin, exc_info=True)
            return False
