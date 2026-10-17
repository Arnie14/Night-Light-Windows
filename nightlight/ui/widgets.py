# SPDX-License-Identifier: GPL-3.0-only
"""Small reusable widgets shared by the flyout and the settings window.

Styling stays within what the native style draws: fonts, spacing and colours
derived from the palette, so light/dark mode and the Windows accent colour
keep working without a stylesheet.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget

from nightlight.core.colortemp import MAX_KELVIN, MIN_KELVIN
from nightlight.hardware.brightness import MonitorInfo

# While the slider moves, send at most one brightness change per this long,
# so the monitor follows the drag (as in Monitorian and Twinkle Tray) without
# flooding a slow DDC/CI link. The final position is always sent.
BRIGHTNESS_THROTTLE_MS = 100
NOT_SUPPORTED = "Brightness control not supported (DDC/CI unavailable or disabled in the monitor's menu)"


def blend(a: QColor, b: QColor, amount: float) -> QColor:
    """`amount` of the way from a to b."""
    return QColor.fromRgbF(*(x + (y - x) * amount for x, y in zip(a.getRgbF()[:3], b.getRgbF()[:3])))


def set_font(widget: QWidget, scale: float = 1.0, weight: QFont.Weight | None = None) -> None:
    font = QFont(widget.font())
    font.setPointSizeF(font.pointSizeF() * scale)
    if weight is not None:
        font.setWeight(weight)
    widget.setFont(font)


class SecondaryLabel(QLabel):
    """Quieter text for status lines and hints: the text colour faded towards
    the background, rather than the greyed-out look of a disabled label.
    Follows the system switching between light and dark mode."""

    def __init__(self, text: str = "", scale: float = 1.0) -> None:
        super().__init__(text)
        if scale != 1.0:
            set_font(self, scale)
        self._recolor()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.StyleChange):
            self._recolor()

    def _recolor(self) -> None:
        app = QApplication.palette()
        palette = self.palette()
        faded = blend(app.color(QPalette.ColorRole.WindowText), app.color(QPalette.ColorRole.Window), 0.38)
        palette.setColor(QPalette.ColorRole.WindowText, faded)
        self.setPalette(palette)


class Separator(QWidget):
    """A hairline that is visible in light and dark mode without being loud."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:
        palette = self.palette()
        painter = QPainter(self)
        painter.fillRect(self.rect(), blend(palette.color(QPalette.ColorRole.Window), palette.color(QPalette.ColorRole.WindowText), 0.2))


class GlyphButton(QToolButton):
    """A flat icon button whose glyph is drawn in the current text colour."""

    def __init__(self, draw: Callable[[QColor], QIcon], tooltip: str, size: int = 16) -> None:
        super().__init__(toolTip=tooltip, autoRaise=True)
        self._draw = draw
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._redraw()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self._redraw()

    def _redraw(self) -> None:
        palette = self.palette()
        self.setIcon(self._draw(blend(palette.color(QPalette.ColorRole.ButtonText), palette.color(QPalette.ColorRole.Button), 0.25)))


class MonitorBrightness(QWidget):
    """Monitor name and level on one line, a full-width slider below."""

    brightnessRequested = Signal(int, int)  # index, value

    def __init__(self, info: MonitorInfo) -> None:
        super().__init__()
        self.info = info
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        top = QHBoxLayout()
        name = QLabel(info.name)
        set_font(name, weight=QFont.Weight.DemiBold)
        top.addWidget(name, 1)
        layout.addLayout(top)

        if not info.supported:
            label = SecondaryLabel(info.reason or NOT_SUPPORTED, scale=0.92)
            label.setWordWrap(True)
            layout.addWidget(label)
            return

        self.value_label = SecondaryLabel("…")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setMinimumWidth(self.value_label.fontMetrics().horizontalAdvance("100%"))
        top.addWidget(self.value_label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        # A laptop panel at 0 can be fully dark on some models, which looks
        # broken; KDE keeps the built-in display above zero too.
        self.slider.setRange(1 if info.internal else 0, 100)
        self.slider.setPageStep(10)
        self.slider.setEnabled(False)  # until the current value has been read
        layout.addWidget(self.slider)

        self._unsent = False
        self._throttle = QTimer(self, singleShot=True, interval=BRIGHTNESS_THROTTLE_MS)
        self._throttle.timeout.connect(self._on_throttle)
        self.slider.valueChanged.connect(self._on_slider_changed)

    def clamp(self, value: int) -> int:
        """`value` within what this monitor's slider allows."""
        return max(self.slider.minimum(), min(self.slider.maximum(), value))

    def set_brightness(self, value: int | None) -> None:
        if not self.info.supported or self.slider.isSliderDown():
            return  # a fresh reading mustn't yank the slider while it's held
        if value is None:
            self.value_label.setText("?")
            return
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.slider.setEnabled(True)
        self.value_label.setText(f"{value}%")

    def _on_slider_changed(self, value: int) -> None:
        self.value_label.setText(f"{value}%")
        if self._throttle.isActive():
            self._unsent = True
        else:
            self._send()
            self._throttle.start()

    def _on_throttle(self) -> None:
        if self._unsent:
            self._unsent = False
            self._send()
            self._throttle.start()

    def _send(self) -> None:
        self.brightnessRequested.emit(self.info.index, self.slider.value())


class TemperatureSlider(QWidget):
    """Kelvin slider like KDE's, cool (no filter) on the left and warm on the
    right, with quiet captions at its ends and the value after it."""

    valueChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(MIN_KELVIN // 100, MAX_KELVIN // 100)  # steps of 100 K
        self.slider.setPageStep(5)
        self.slider.setInvertedAppearance(True)  # high (cool) values on the left
        self.slider.setInvertedControls(True)
        self.slider.setMinimumWidth(160)
        self.slider.setToolTip("Left: cool, no filter. Right: warm.")
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._label.setMinimumWidth(self._label.fontMetrics().horizontalAdvance("10000 K"))

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(SecondaryLabel("Cool", scale=0.88))
        row.addWidget(self.slider, 1)
        row.addWidget(SecondaryLabel("Warm", scale=0.88))
        row.addSpacing(6)
        row.addWidget(self._label)

        self.slider.valueChanged.connect(self._on_changed)
        self._on_changed(self.slider.value())

    def value(self) -> int:
        return self.slider.value() * 100

    def setValue(self, kelvin: int) -> None:
        self.slider.setValue(round(kelvin / 100))

    def _on_changed(self, steps: int) -> None:
        self._label.setText(f"{steps * 100} K")
        self.valueChanged.emit(steps * 100)
