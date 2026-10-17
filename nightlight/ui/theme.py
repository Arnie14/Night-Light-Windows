# SPDX-License-Identifier: GPL-3.0-only
"""The look of the app: soft rounded cards, thin sliders and quiet colours that
follow the system light/dark setting and accent colour, in the spirit of macOS
and KDE's Breeze. One stylesheet for every window, rebuilt when the system
switches between light and dark."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QStandardPaths, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication

FONT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans", "Cantarell", "Ubuntu", "sans-serif"]
DEFAULT_ACCENT = "#3daee9"  # Breeze blue, when the system gives none


@dataclass(frozen=True)
class Tokens:
    dark: bool
    accent: str
    window: str  # window background
    card: str  # raised surface
    card_border: str
    control: str  # buttons, inputs
    control_hover: str
    control_border: str
    text: str
    text_dim: str
    groove: str  # empty part of a slider
    separator: str


LIGHT = dict(
    dark=False,
    window="#f2f2f5",
    card="#ffffff",
    card_border="#dcdce1",
    control="#ffffff",
    control_hover="#ececf1",
    control_border="#d2d2d8",
    text="#1c1c1e",
    text_dim="#6c6c70",
    groove="#d6d6dc",
    separator="#e4e4e8",
)
DARK = dict(
    dark=True,
    window="#1c1c1e",
    card="#2b2b2e",
    card_border="#3c3c40",
    control="#38383c",
    control_hover="#45454a",
    control_border="#4a4a4f",
    text="#f2f2f5",
    text_dim="#a0a0a6",
    groove="#4a4a50",
    separator="#3a3a3e",
)


class Theme(QObject):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.tokens = _tokens(False, DEFAULT_ACCENT)  # until install() knows the system
        self._accent = DEFAULT_ACCENT

    def install(self, app: QApplication) -> None:
        """Style the whole application, and keep following the system."""
        accent = app.palette().color(QPalette.ColorRole.Highlight)
        if accent.isValid() and accent.saturation() > 40:
            self._accent = accent.name()
        # Fusion looks the same on every Windows version and on Linux, which
        # the stylesheet below builds on.
        app.setStyle("Fusion")
        font = QFont()
        font.setFamilies(FONT_FAMILIES)
        font.setPointSizeF(9.5)
        app.setFont(font)
        self._apply(app)
        app.styleHints().colorSchemeChanged.connect(lambda _: self._apply(app))

    def _apply(self, app: QApplication) -> None:
        self.tokens = _tokens(_is_dark(), self._accent)
        app.setPalette(_palette(self.tokens))
        app.setStyleSheet(stylesheet(self.tokens, _arrow_images(self.tokens.text_dim)))
        self.changed.emit()



def _is_dark() -> bool:
    return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark


def _tokens(dark: bool, accent: str) -> Tokens:
    return Tokens(accent=accent, **(DARK if dark else LIGHT))


def _palette(t: Tokens) -> QPalette:
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: t.window,
        QPalette.ColorRole.WindowText: t.text,
        QPalette.ColorRole.Base: t.control,
        QPalette.ColorRole.AlternateBase: t.card,
        QPalette.ColorRole.Text: t.text,
        QPalette.ColorRole.Button: t.control,
        QPalette.ColorRole.ButtonText: t.text,
        QPalette.ColorRole.ToolTipBase: t.card,
        QPalette.ColorRole.ToolTipText: t.text,
        QPalette.ColorRole.Highlight: t.accent,
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: t.text_dim,
        QPalette.ColorRole.Link: t.accent,
    }.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(t.text_dim))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(t.text_dim))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(t.text_dim))
    return palette


def with_alpha(color: str, alpha: float) -> str:
    c = QColor(color)
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(alpha * 255)})"


def _arrow_images(color: str) -> dict[str, str]:
    """Stylesheets can't draw the little arrows of combo and spin boxes, only
    show images, so write them out. Returns the url() for "up" and "down"."""
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "arrows"
    urls = {}
    for name, direction in (("up", -1), ("down", 1)):  # which way the chevron points, as a sign of y
        path = folder / f"{name}-{color.lstrip('#')}.png"
        if not path.exists():
            pixmap = QPixmap(24, 24)  # drawn at 2x for sharp edges on HiDPI
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(color), 2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline([QPointF(6.5, 12 - direction * 2.5), QPointF(12, 12 + direction * 2.5), QPointF(17.5, 12 - direction * 2.5)])
            painter.end()
            folder.mkdir(parents=True, exist_ok=True)
            pixmap.save(str(path))
        urls[name] = "url(" + path.as_posix() + ")" if path.exists() else "none"
    return urls


def stylesheet(t: Tokens, arrows: dict[str, str] | None = None) -> str:
    arrows = arrows or {"up": "none", "down": "none"}
    return f"""
QWidget {{ color: {t.text}; }}
QLabel {{ background: transparent; }}
QLabel[dim="true"], QLabel:disabled {{ color: {t.text_dim}; }}
QLabel[title="true"] {{ font-size: 15pt; font-weight: 600; }}
QLabel[heading="true"] {{ font-size: 11pt; font-weight: 600; }}

SettingsWindow, Flyout[windowed="true"] {{ background: {t.window}; }}

QFrame#card {{ background: {t.card}; border: 1px solid {t.card_border}; border-radius: 14px; }}
QFrame#card[windowed="true"] {{ border: none; border-radius: 0; }}
QFrame#section {{ background: {t.window if t.dark else "#f7f7fa"}; border: 1px solid {t.separator}; border-radius: 12px; }}
QFrame#banner {{ background: {with_alpha(t.accent, 0.14)}; border: 1px solid {with_alpha(t.accent, 0.35)}; border-radius: 12px; }}
QFrame#separator {{ background: {t.separator}; border: none; max-height: 1px; min-height: 1px; }}

QPushButton {{
    background: {t.control}; border: 1px solid {t.control_border}; border-radius: 8px;
    padding: 6px 16px; min-height: 18px;
}}
QPushButton:hover {{ background: {t.control_hover}; }}
QPushButton:pressed {{ background: {t.groove}; }}
QPushButton:disabled {{ color: {t.text_dim}; background: transparent; border-color: {t.separator}; }}
QPushButton[primary="true"] {{ background: {t.accent}; border-color: {t.accent}; color: #ffffff; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {t.accent}; border-color: {t.text_dim}; }}
QPushButton[primary="true"]:disabled {{ background: transparent; color: {t.text_dim}; border-color: {t.separator}; }}
QPushButton::menu-indicator {{ subcontrol-position: right center; subcontrol-origin: padding; right: 8px; width: 8px; }}
QPushButton[menu="true"] {{ padding-right: 24px; }}

QToolButton {{ background: transparent; border: none; border-radius: 8px; padding: 5px; }}
QToolButton:hover {{ background: {t.control_hover}; }}
QToolButton:pressed {{ background: {t.groove}; }}


QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit {{
    background: {t.control}; border: 1px solid {t.control_border}; border-radius: 8px;
    padding: 5px 10px; min-height: 18px; selection-background-color: {t.accent}; selection-color: #ffffff;
}}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QTimeEdit:hover {{ border-color: {t.text_dim}; }}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus {{ border-color: {t.accent}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: {arrows["down"]}; width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{
    background: {t.card}; border: 1px solid {t.card_border}; border-radius: 8px; padding: 4px; outline: none;
    selection-background-color: {t.accent}; selection-color: #ffffff;
}}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button,
QTimeEdit::up-button, QTimeEdit::down-button {{ border: none; background: transparent; width: 22px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QTimeEdit::up-arrow {{ image: {arrows["up"]}; width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QTimeEdit::down-arrow {{ image: {arrows["down"]}; width: 10px; height: 10px; }}

QMenu {{ background: {t.card}; border: 1px solid {t.card_border}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 6px 28px 6px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: {t.accent}; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: {t.separator}; margin: 5px 8px; }}

QToolTip {{ background: {t.card}; color: {t.text}; border: 1px solid {t.card_border}; padding: 4px 6px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.groove}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


theme = Theme()
