# SPDX-License-Identifier: GPL-3.0-only
"""Small reusable widgets shared by the flyout and the settings window."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from nightlight.core.colortemp import MAX_KELVIN, MIN_KELVIN
from nightlight.hardware.brightness import MonitorInfo

# While the slider moves, send at most one brightness change per this long,
# so the monitor follows the drag (as in Monitorian and Twinkle Tray) without
# flooding a slow DDC/CI link. The final position is always sent.
BRIGHTNESS_THROTTLE_MS = 100
NOT_SUPPORTED = "Brightness control not supported (DDC/CI unavailable or disabled in the monitor's menu)"


class MonitorBrightness(QWidget):
    """Monitor name above a brightness slider, like Plasma's brightness applet."""

    brightnessRequested = Signal(int, int)  # index, value

    def __init__(self, info: MonitorInfo) -> None:
        super().__init__()
        self.info = info
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(info.name))

        if not info.supported:
            label = QLabel(info.reason or NOT_SUPPORTED)
            label.setWordWrap(True)
            label.setEnabled(False)
            layout.addWidget(label)
            return

        row = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setEnabled(False)  # until the current value has been read
        self.value_label = QLabel("…")
        self.value_label.setMinimumWidth(40)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_label)
        layout.addLayout(row)

        self._unsent = False
        self._throttle = QTimer(self, singleShot=True, interval=BRIGHTNESS_THROTTLE_MS)
        self._throttle.timeout.connect(self._on_throttle)
        self.slider.valueChanged.connect(self._on_slider_changed)

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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(MIN_KELVIN // 100, MAX_KELVIN // 100)  # steps of 100 K
        self.slider.setPageStep(5)
        self.slider.setInvertedAppearance(True)  # high (cool) values on the left
        self.slider.setInvertedControls(True)
        self._label = QLabel()
        self._label.setMinimumWidth(60)
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(QLabel("Cool (no filter)"))
        layout.addWidget(self.slider, 1)
        layout.addWidget(QLabel("Warm"))
        layout.addWidget(self._label)
        self.slider.valueChanged.connect(self._on_changed)
        self._on_changed(self.slider.value())

    def value(self) -> int:
        return self.slider.value() * 100

    def setValue(self, kelvin: int) -> None:
        self.slider.setValue(round(kelvin / 100))

    def _on_changed(self, steps: int) -> None:
        self._label.setText(f"{steps * 100} K")
        self.valueChanged.emit(steps * 100)
