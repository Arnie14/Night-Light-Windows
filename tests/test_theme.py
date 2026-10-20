# SPDX-License-Identifier: GPL-3.0-only
import pytest
from PySide6.QtGui import QColor, QGuiApplication, QPalette

from nightlight.config import ACCENTS, GeneralConfig
from nightlight.ui import theme
from nightlight.ui.settings_parts import contrast, text_on
from nightlight.ui.widgets import SecondaryLabel

ACCENT = getattr(QPalette.ColorRole, "Accent", QPalette.ColorRole.Highlight)


@pytest.fixture(autouse=True)
def back_to_windows():
    yield
    theme.apply("system", "windows")


def accent_now():
    return QGuiApplication.instance().palette().color(ACCENT)


def test_every_accent_in_the_settings_has_colors():
    assert set(ACCENTS) == {"windows", *theme.ACCENT_COLORS} == set(theme.ACCENT_NAMES)
    assert GeneralConfig(accent="mauve").normalized().accent == "windows"


def test_an_accent_follows_light_and_dark():
    theme.apply("light", "teal")
    assert accent_now() == QColor(theme.ACCENT_COLORS["teal"][0])
    theme.apply("dark", "teal")
    assert accent_now() == QColor(theme.ACCENT_COLORS["teal"][1])


def test_windows_accent_comes_back():
    windows = accent_now()
    theme.apply("system", "rose")
    assert accent_now() != windows
    theme.apply("system", "windows")
    assert accent_now() == windows


@pytest.mark.parametrize("key", sorted(theme.ACCENT_COLORS))
def test_preset_shades_keep_contrast(key):
    """Deep in light mode (white knob and text on it), light in dark mode
    (black knob and text), as Windows' own accent shades are: at least
    WCAG's 4.5:1 for text, and the Apply button picks that text colour."""
    light, dark = (QColor(c) for c in theme.ACCENT_COLORS[key])
    assert contrast(light, QColor("#ffffff")) >= 4.5 and contrast(dark, QColor("#000000")) >= 4.5
    assert text_on(light) == QColor("#ffffff") and text_on(dark) == QColor("#000000")


def test_quieter_text_is_darker_in_light_mode_than_before():
    """Windows 11's light palette, set directly: the offscreen test platform
    can't switch the system to light mode."""
    app = QGuiApplication.instance()
    light = QPalette()
    light.setColor(QPalette.ColorRole.Window, QColor("#f3f3f3"))
    light.setColor(QPalette.ColorRole.WindowText, QColor("#1a1a1a"))
    app.setPalette(light)
    try:
        quiet = SecondaryLabel("70%").palette().color(QPalette.ColorRole.WindowText)
        # 0.1.0 Beta 5 faded it to about #6f6f6f (lightness 0.44); now near Windows' #5f5f5f.
        assert 0.2 < quiet.lightnessF() < 0.4
    finally:
        app.setPalette(QPalette())
