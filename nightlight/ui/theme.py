# SPDX-License-Identifier: GPL-3.0-only
"""The light or dark theme of the app's windows: Windows' own, or one the
user picks (Settings > General > Appearance). Qt 6.8+ lets an app override
the color scheme; the Windows 11 style and every custom-painted part follow
the palette it brings."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

log = logging.getLogger(__name__)

SCHEMES = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}


def apply(theme: str) -> None:
    """"system" follows Windows; "light" and "dark" override it."""
    hints = QGuiApplication.styleHints()
    if not hasattr(hints, "setColorScheme"):  # Qt before 6.8
        return
    scheme = SCHEMES.get(theme, Qt.ColorScheme.Unknown)  # Unknown: back to the system's
    if hints.colorScheme() != scheme or scheme is Qt.ColorScheme.Unknown:
        hints.setColorScheme(scheme)
        log.debug("Theme: %s", theme)
