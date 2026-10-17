"""Small widgets and helpers shared between the flyout and the settings window."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from nightlight.config import Mode
from nightlight.controller import NightLightController
from nightlight.core.colortemp import MAX_KELVIN, MIN_KELVIN
from nightlight.hardware.brightness import MonitorInfo

# Wait this long after the slider stops moving before sending a DDC/CI write.
BRIGHTNESS_DEBOUNCE_MS = 150


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
            label = QLabel("Brightness control not supported (DDC/CI unavailable or disabled in the monitor's menu)")
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

        self._debounce = QTimer(self, singleShot=True, interval=BRIGHTNESS_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._send)
        self.slider.valueChanged.connect(self._on_slider_changed)

    def set_brightness(self, value: int | None) -> None:
        if not self.info.supported:
            return
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
        self._debounce.start()

    def _send(self) -> None:
        self.brightnessRequested.emit(self.info.index, self.slider.value())


class TemperatureSlider(QWidget):
    """Kelvin slider, warm on the left, with a "4500 K" readout."""

    valueChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(MIN_KELVIN // 100, MAX_KELVIN // 100)  # steps of 100 K
        self.slider.setPageStep(5)
        self._label = QLabel()
        self._label.setMinimumWidth(60)
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(QLabel("Warm"))
        layout.addWidget(self.slider, 1)
        layout.addWidget(QLabel("Cool"))
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


def night_light_status(controller: NightLightController) -> str:
    config, state = controller.config, controller.state
    if not config.enabled:
        return "Night Light is off"
    if controller.inhibited:
        return "Night Light is suspended"
    if config.mode is Mode.ALWAYS:
        return f"Night Light is on · {state.temperature} K"
    next_at = state.next_change.strftime("%H:%M") if state.next_change else None
    if state.transitioning:
        return f"Transitioning · {state.temperature} K"
    if state.night_factor >= 1.0:
        return f"Night Light is on · {state.temperature} K" + (f" · Day light at {next_at}" if next_at else "")
    return "Day light" + (f" · Night Light at {next_at}" if next_at else "")
