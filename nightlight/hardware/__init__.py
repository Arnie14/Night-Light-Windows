"""Picks the hardware backends for the current platform."""

from __future__ import annotations

import logging
import sys

from nightlight.hardware.brightness import BrightnessBackend, DdcBackend, FakeBrightnessBackend
from nightlight.hardware.gamma import FakeGammaBackend, GammaBackend

log = logging.getLogger(__name__)


def create_backends(kind: str = "auto") -> tuple[BrightnessBackend, GammaBackend]:
    """`kind` is "real", "fake", or "auto" (real on Windows, fake elsewhere)."""
    if kind == "auto":
        kind = "real" if sys.platform == "win32" else "fake"
    if kind == "fake":
        log.info("Using fake monitors and fake gamma")
        return FakeBrightnessBackend(), FakeGammaBackend()

    if sys.platform == "win32":
        from nightlight.hardware.gamma import WindowsGammaBackend

        return DdcBackend(), WindowsGammaBackend()
    # DDC/CI works on Linux via i2c-dev, but gamma is Windows-only.
    log.warning("Gamma control is only implemented on Windows; night light will be simulated")
    return DdcBackend(), FakeGammaBackend()
