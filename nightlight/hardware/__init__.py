# SPDX-License-Identifier: GPL-3.0-only
"""Picks the hardware backends for the current platform."""

from __future__ import annotations

import logging
import sys

from nightlight.hardware.brightness import BrightnessBackend, DdcBackend, FakeBrightnessBackend
from nightlight.hardware.gamma import FakeGammaBackend, GammaBackend

log = logging.getLogger(__name__)

BACKENDS = ("auto", "real", "kde", "fake")


def create_backends(kind: str = "auto") -> tuple[BrightnessBackend, GammaBackend]:
    """`kind` is one of BACKENDS. "auto" picks real on Windows, KDE on a Plasma
    desktop, and fake otherwise. Must be called after QApplication exists."""
    if kind == "auto":
        if sys.platform == "win32":
            kind = "real"
        else:
            from nightlight.hardware import kde

            kind = "kde" if kde.is_available() else "fake"

    if kind == "fake":
        log.info("Using fake monitors and fake gamma")
        return FakeBrightnessBackend(), FakeGammaBackend()
    if kind == "kde":
        from nightlight.hardware.kde import KdeBrightnessBackend, KWinNightLightBackend

        log.info("Using KDE Plasma (PowerDevil brightness, KWin night light)")
        return KdeBrightnessBackend(), KWinNightLightBackend()

    if sys.platform == "win32":
        from nightlight.hardware import win32
        from nightlight.hardware.windows import WindowsBrightnessBackend, WindowsGammaBackend

        log.info("Using Windows (DDC/CI, WMI and SDR brightness, gamma ramp night light and dimming)")
        # ctypes layouts are checked against the SDK's before any call; a
        # mismatch turns off the affected feature instead of corrupting memory.
        win32.self_check()
        log.info("Windows capabilities: %s", ", ".join(f"{k}: {v}" for k, v in win32.capabilities().items() if k != "windows"))
        gamma = WindowsGammaBackend()
        return WindowsBrightnessBackend(gamma), gamma
    # DDC/CI works on Linux via i2c-dev, but gamma is Windows-only.
    log.warning("Gamma control is only implemented on Windows; night light will be simulated")
    return DdcBackend(), FakeGammaBackend()
