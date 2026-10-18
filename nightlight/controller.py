# SPDX-License-Identifier: GPL-3.0-only
"""Keeps the displays' colour temperature in line with the schedule."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, tzinfo

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
        zone: tzinfo | None = None,
    ) -> None:
        super().__init__(parent)
        self._gamma = gamma
        self._config = config
        self._clock = clock
        # The local time zone, so schedules stay right across daylight saving changes.
        self.zone = zone
        self._override: Override | None = None
        self._preview: int | None = None  # live preview while dragging a slider
        self._current: int | None = None  # what the displays show now
        self._animation_target: int | None = None
        self.state: NightLightState = compute_state(config, clock(), zone=zone)

        self._animation = QTimer(self)
        self._animation.timeout.connect(self._animation_step)
        self._timer = QTimer(self, interval=TICK_MS)
        self._timer.timeout.connect(self.update)
        self._timer.start()
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
        self.state = compute_state(self._config, now, self._override, self.zone)
        self._retarget(reapply=True)
        self.changed.emit()

    def shutdown(self) -> None:
        self._timer.stop()
        self._animation.stop()
        # Undo everything, software dimming included, where the backend can.
        getattr(self._gamma, "release", self._gamma.reset)()

    def _retarget(self, reapply: bool = False) -> None:
        target = self.target_temperature
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
