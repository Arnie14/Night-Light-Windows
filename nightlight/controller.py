"""Keeps the displays' colour temperature in line with the schedule."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from nightlight.config import NightLightConfig
from nightlight.core.colortemp import NEUTRAL_KELVIN, whitepoint
from nightlight.core.schedule import NightLightState, compute_state
from nightlight.hardware.gamma import GammaBackend

log = logging.getLogger(__name__)

# Frequent enough for smooth transitions, and it re-applies the ramp after
# sleep, resolution changes or fullscreen apps have reset it.
TICK_MS = 10_000


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
        self._inhibited = False  # "Suspend Night Light" from the tray
        self._preview: int | None = None  # live preview while dragging a slider
        self._last_applied: int | None = None
        self.state: NightLightState = compute_state(config, clock())

        self._timer = QTimer(self, interval=TICK_MS)
        self._timer.timeout.connect(self.update)
        self._timer.start()
        self.update()

    @property
    def config(self) -> NightLightConfig:
        return self._config

    @property
    def inhibited(self) -> bool:
        return self._inhibited

    @property
    def effective_temperature(self) -> int:
        if self._preview is not None:
            return self._preview
        if self._inhibited:
            return NEUTRAL_KELVIN
        return self.state.temperature

    def set_config(self, config: NightLightConfig) -> None:
        self._config = config
        self.update()

    def set_inhibited(self, inhibited: bool) -> None:
        self._inhibited = inhibited
        self.update()

    def preview(self, kelvin: int | None) -> None:
        """Show `kelvin` immediately, or pass None to return to the schedule."""
        self._preview = kelvin
        self._apply()

    def update(self) -> None:
        self.state = compute_state(self._config, self._clock())
        self._apply()
        self.changed.emit()

    def shutdown(self) -> None:
        self._timer.stop()
        self._gamma.reset()

    def _apply(self) -> None:
        kelvin = self.effective_temperature
        # Don't keep stomping on neutral, so other tools (or Windows' own
        # Night Light) aren't fought while ours is off.
        if kelvin == NEUTRAL_KELVIN and self._last_applied == NEUTRAL_KELVIN:
            return
        try:
            self._gamma.apply(whitepoint(kelvin))
            self._last_applied = kelvin
        except Exception:
            log.warning("Applying %d K failed", kelvin, exc_info=True)
