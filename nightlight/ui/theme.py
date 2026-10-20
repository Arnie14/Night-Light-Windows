# SPDX-License-Identifier: GPL-3.0-only
"""The look of the app's windows: light or dark (Windows' own, or one the
user picks), and an accent colour (Windows' own, or a preset), both in
Settings > General > Appearance.

Qt 6.8+ lets an app override the color scheme; the Windows 11 style and every
custom-painted part follow the palette it brings. An accent preset sets only
the palette's Accent role, which Qt keeps across light and dark switches, so
nothing else about the system palette changes. Like Windows, each preset has
a deeper shade for light mode and a lighter one for dark mode, so switches,
sliders and the Apply button keep their contrast; text on accent-filled
buttons picks black or white by the fill (settings_parts.ActionButton).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette

log = logging.getLogger(__name__)

SCHEMES = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}
# Accent presets: (light mode, dark mode). The keys are config.ACCENTS.
ACCENT_COLORS = {
    "amber": ("#a35200", "#ffb547"),
    "blue": ("#005fb8", "#60cdff"),
    "teal": ("#00707a", "#4fd8d0"),
    "green": ("#0f7b0f", "#6ccb5f"),
    "purple": ("#7a3dbf", "#c8a2ff"),
    "rose": ("#b3305c", "#ff8fb0"),
}
ACCENT_NAMES = {
    "windows": "Windows accent color",
    "amber": "Amber",
    "blue": "Blue",
    "teal": "Teal",
    "green": "Green",
    "purple": "Purple",
    "rose": "Rose",
}
_ACCENT_ROLE = getattr(QPalette.ColorRole, "Accent", QPalette.ColorRole.Highlight)

_accent = "windows"
_theme = "system"
_following = False  # connected to the color scheme's changes
_overridden = False  # the app palette carries a preset accent


def apply(theme: str, accent: str = "windows") -> None:
    """"system" follows Windows; "light" and "dark" override it. `accent` is
    "windows" or a key of ACCENT_COLORS."""
    global _accent, _following, _theme
    _theme = theme
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+
        scheme = SCHEMES.get(theme, Qt.ColorScheme.Unknown)  # Unknown: back to the system's
        if hints.colorScheme() != scheme or scheme is Qt.ColorScheme.Unknown:
            hints.setColorScheme(scheme)
            log.debug("Theme: %s", theme)
    _accent = accent if accent in ACCENT_COLORS else "windows"
    if not _following:
        hints.colorSchemeChanged.connect(lambda _scheme: _apply_accent())
        _following = True
    _apply_accent()


def accent_color(accent: str, dark: bool) -> QColor | None:
    """A preset's colour for light or dark mode; None for Windows' own."""
    shades = ACCENT_COLORS.get(accent)
    return QColor(shades[1] if dark else shades[0]) if shades else None


def _apply_accent() -> None:
    global _overridden
    app = QGuiApplication.instance()
    if app is None:
        return
    if _theme in SCHEMES:
        dark = _theme == "dark"
    else:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme != Qt.ColorScheme.Unknown:
            dark = scheme == Qt.ColorScheme.Dark
        else:
            dark = app.palette().color(QPalette.ColorRole.Window).lightnessF() < 0.5
    color = accent_color(_accent, dark)
    current = app.palette()
    if color is None:
        if _overridden:
            app.setPalette(QPalette())  # back to the system palette, Windows' accent included
            _overridden = False
        return
    if not _overridden or current.color(_ACCENT_ROLE) != color:
        palette = QPalette()
        palette.setColor(_ACCENT_ROLE, color)
        app.setPalette(palette)
        _overridden = True
