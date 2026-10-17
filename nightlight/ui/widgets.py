# SPDX-License-Identifier: GPL-3.0-only
"""Small reusable widgets shared by the flyout and the settings window."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QSlider, QStyle, QStyleOptionSlider, QVBoxLayout, QWidget

from nightlight.core.colortemp import MAX_KELVIN, MIN_KELVIN
from nightlight.hardware.brightness import MonitorInfo
from nightlight.ui import icons
from nightlight.ui.theme import theme

# While the slider moves, send at most one brightness change per this long,
# so the monitor follows the drag (as in Monitorian and Twinkle Tray) without
# flooding a slow DDC/CI link. The final position is always sent.
BRIGHTNESS_THROTTLE_MS = 100
NOT_SUPPORTED = "Brightness control not supported (DDC/CI unavailable or disabled in the monitor's menu)"


class Slider(QSlider):
    """A horizontal slider with a thin rounded track and a round white handle.

    Painted here rather than with a stylesheet, whose slider rules also draw
    boxes where none are wanted. Mouse and keyboard handling are QSlider's.
    With `gradient`, the track is a cool-to-warm colour ramp instead of a
    filled bar, for choosing a colour temperature.
    """

    TRACK = 6
    HANDLE = 18

    def __init__(self, gradient: bool = False) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self._gradient = gradient
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)  # for the hover highlight
        self._hovered = False

    def sizeHint(self) -> QSize:
        return QSize(120, 24)

    def minimumSizeHint(self) -> QSize:
        return QSize(40, 24)

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        t = theme.tokens
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderHandle, self)
        center = QPointF(handle.center())
        center.setY(self.height() / 2)
        inset = handle.width() / 2
        track = QRectF(inset, (self.height() - self.TRACK) / 2, self.width() - 2 * inset, self.TRACK)
        radius = self.TRACK / 2

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        enabled = self.isEnabled()
        if self._gradient:
            ramp = QLinearGradient(track.left(), 0, track.right(), 0)
            ramp.setColorAt(0, QColor("#c9e0ff"))
            ramp.setColorAt(0.45, QColor("#fff4e6"))
            ramp.setColorAt(1, QColor("#ff9a3d"))
            painter.setOpacity(1.0 if enabled else 0.4)
            painter.setBrush(ramp)
            painter.drawRoundedRect(track, radius, radius)
            painter.setOpacity(1.0)
        else:
            painter.setBrush(QColor(t.groove))
            painter.drawRoundedRect(track, radius, radius)
            if enabled:
                filled = QRectF(track)
                if self.invertedAppearance():
                    filled.setLeft(center.x())
                else:
                    filled.setRight(center.x())
                painter.setBrush(QColor(t.accent))
                painter.drawRoundedRect(filled, radius, radius)

        # A soft shadow ring, then the handle.
        diameter = self.HANDLE
        painter.setBrush(QColor(0, 0, 0, 28 if not t.dark else 80))
        painter.drawEllipse(center + QPointF(0, 1), diameter / 2 + 0.5, diameter / 2 + 0.5)
        pressed = self.isSliderDown()
        face = QColor("#ffffff" if not t.dark else "#f2f2f5")
        if not enabled:
            face = QColor(t.control_hover)
        elif pressed:
            face = face.darker(106)
        painter.setBrush(face)
        border = QColor(t.accent) if enabled and (self._hovered or pressed) else QColor(0, 0, 0, 40)
        painter.setPen(QPen(border, 1))
        painter.drawEllipse(center, diameter / 2 - 0.5, diameter / 2 - 0.5)


class MonitorBrightness(QWidget):
    """Monitor name above a brightness slider, like Plasma's brightness applet."""

    brightnessRequested = Signal(int, int)  # index, value

    def __init__(self, info: MonitorInfo) -> None:
        super().__init__()
        self.info = info
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        name = QLabel(info.name)
        name.setProperty("heading", True)
        layout.addWidget(name)

        if not info.supported:
            label = QLabel(info.reason or NOT_SUPPORTED)
            label.setWordWrap(True)
            label.setProperty("dim", True)
            layout.addWidget(label)
            return

        row = QHBoxLayout()
        row.setSpacing(10)
        self._glyph = QLabel()
        self._update_glyph()
        theme.changed.connect(self._update_glyph)
        row.addWidget(self._glyph)
        self.slider = Slider()
        self.slider.setRange(0, 100)
        self.slider.setEnabled(False)  # until the current value has been read
        self.value_label = QLabel("…")
        self.value_label.setMinimumWidth(40)
        self.value_label.setProperty("dim", True)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_label)
        layout.addLayout(row)

        self._unsent = False
        self._throttle = QTimer(self, singleShot=True, interval=BRIGHTNESS_THROTTLE_MS)
        self._throttle.timeout.connect(self._on_throttle)
        self.slider.valueChanged.connect(self._on_slider_changed)

    def _update_glyph(self) -> None:
        self._glyph.setPixmap(icons.brightness_glyph(theme.tokens.text_dim).pixmap(18, 18))

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
    """Kelvin slider like KDE's: "Cool (no filter)" on the left, "Warm" on the right."""

    valueChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.slider = Slider(gradient=True)
        self.slider.setRange(MIN_KELVIN // 100, MAX_KELVIN // 100)  # steps of 100 K
        self.slider.setPageStep(5)
        self.slider.setInvertedAppearance(True)  # high (cool) values on the left
        self.slider.setInvertedControls(True)
        self._label = QLabel()
        self._label.setMinimumWidth(60)
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self.slider, 1)
        top.addWidget(self._label)
        ends = QHBoxLayout()
        ends.setContentsMargins(0, 0, self._label.minimumWidth() + 12, 0)
        cool, warm = QLabel("Cool (no filter)"), QLabel("Warm")
        for label in (cool, warm):
            label.setProperty("dim", True)
            label.setStyleSheet("font-size: 8pt;")
        ends.addWidget(cool)
        ends.addStretch(1)
        ends.addWidget(warm)
        layout.addLayout(top)
        layout.addLayout(ends)
        self.slider.valueChanged.connect(self._on_changed)
        self._on_changed(self.slider.value())

    def value(self) -> int:
        return self.slider.value() * 100

    def setValue(self, kelvin: int) -> None:
        self.slider.setValue(round(kelvin / 100))

    def _on_changed(self, steps: int) -> None:
        self._label.setText(f"{steps * 100} K")
        self.valueChanged.emit(steps * 100)
