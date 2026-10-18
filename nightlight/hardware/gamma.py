# SPDX-License-Identifier: GPL-3.0-only
"""Gamma backends: apply a night light colour temperature to the displays.

Night light is global, as in KDE: the same ramp goes to every display.
The Windows backend is in nightlight.hardware.windows.
"""

from __future__ import annotations

import logging
from typing import Protocol

from nightlight.core.colortemp import NEUTRAL_KELVIN, Whitepoint, whitepoint

log = logging.getLogger(__name__)


class GammaBackend(Protocol):
    """Backends may also set `animates = True` if they animate changes
    themselves, and define `displays_changed()` to hear about display changes."""

    def apply(self, kelvin: int) -> None: ...
    def reset(self) -> None: ...


class FakeGammaBackend:
    """Remembers and logs the temperature instead of touching any display."""

    def __init__(self) -> None:
        self.kelvin = NEUTRAL_KELVIN
        self.dims: dict[str, int] = {}
        self.overrides: dict[str, int] = {}

    def set_override(self, name: str, kelvin: int | None) -> None:
        if kelvin is None:
            self.overrides.pop(name, None)
        else:
            self.overrides[name] = kelvin

    def verify(self) -> bool:
        return False

    def set_idle_dim(self, levels: dict[str, int]) -> None:
        if levels != getattr(self, "idle_dims", {}):
            log.info("Fake idle dimming: %s", levels or "off")
        self.idle_dims = dict(levels)

    @property
    def dimmed(self) -> bool:
        return any(dim < 100 for dim in self.dims.values())

    def can_dim(self, name: str) -> bool:
        return True

    def dimming(self, name: str) -> int:
        return self.dims.get(name, 100)

    def set_dimming(self, name: str, percent: int) -> int:
        log.info("Fake software dimming of %s -> %d%%", name, percent)
        self.dims[name] = percent
        return percent

    def release(self) -> None:
        self.dims.clear()
        self.reset()

    @property
    def whitepoint(self) -> Whitepoint:
        return whitepoint(self.kelvin)

    def apply(self, kelvin: int) -> None:
        if kelvin != self.kelvin:
            log.info("Fake gamma -> %d K (r=%.3f g=%.3f b=%.3f)", kelvin, *whitepoint(kelvin))
        self.kelvin = kelvin

    def reset(self) -> None:
        self.apply(NEUTRAL_KELVIN)

