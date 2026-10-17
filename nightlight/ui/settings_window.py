"""Night Light settings, modelled on KDE System Settings > Display & Monitor > Night Light."""

from __future__ import annotations

from PySide6.QtCore import QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from nightlight.config import MAX_TRANSITION_MINUTES, Mode, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.ui import icons
from nightlight.ui.widgets import TemperatureSlider, night_light_status

MODE_LABELS = {
    Mode.LOCATION: "Sunset and sunrise at manual location",
    Mode.TIMES: "Custom times",
    Mode.ALWAYS: "Always on night light",
}


class SettingsWindow(QWidget):
    applied = Signal(object)  # NightLightConfig

    def __init__(self, controller: NightLightController) -> None:
        super().__init__()
        self._controller = controller
        self._saved = controller.config
        self.setWindowTitle("Night Light — Settings")
        self.setWindowIcon(icons.night_icon())
        self.setMinimumWidth(520)

        self.enabled = QCheckBox("Activate Night Light")
        self.mode = QComboBox()
        for mode, label in MODE_LABELS.items():
            self.mode.addItem(label, mode)
        self.day_temperature = TemperatureSlider()
        self.night_temperature = TemperatureSlider()
        self.latitude = QDoubleSpinBox(minimum=-90, maximum=90, decimals=4, suffix="°")
        self.longitude = QDoubleSpinBox(minimum=-180, maximum=180, decimals=4, suffix="°")
        self.night_start = QTimeEdit(displayFormat="HH:mm")
        self.day_start = QTimeEdit(displayFormat="HH:mm")
        self.transition = QSpinBox(minimum=0, maximum=MAX_TRANSITION_MINUTES, suffix=" min")
        self.status = QLabel()
        self.status.setEnabled(False)

        self.form = QFormLayout()
        self.form.addRow(self.enabled)
        self.form.addRow("Switching times:", self.mode)
        self.form.addRow("Day light temperature:", self.day_temperature)
        self.form.addRow("Night light temperature:", self.night_temperature)
        self.form.addRow("Latitude:", self.latitude)
        self.form.addRow("Longitude:", self.longitude)
        self.form.addRow("Begin night light at:", self.night_start)
        self.form.addRow("Begin day light at:", self.day_start)
        self.form.addRow("Transition duration:", self.transition)
        self.form.addRow(self.status)

        Button = QDialogButtonBox.StandardButton
        self.buttons = QDialogButtonBox(Button.RestoreDefaults | Button.Reset | Button.Apply)
        self.buttons.button(Button.RestoreDefaults).clicked.connect(lambda: self._load(NightLightConfig()))
        self.buttons.button(Button.Reset).clicked.connect(lambda: self._load(self._saved))
        self.buttons.button(Button.Apply).clicked.connect(self._apply)

        layout = QVBoxLayout(self)
        layout.addLayout(self.form)
        layout.addStretch(1)
        layout.addWidget(self.buttons)

        for signal in (
            self.enabled.toggled,
            self.mode.currentIndexChanged,
            self.day_temperature.valueChanged,
            self.night_temperature.valueChanged,
            self.latitude.valueChanged,
            self.longitude.valueChanged,
            self.night_start.timeChanged,
            self.day_start.timeChanged,
            self.transition.valueChanged,
        ):
            signal.connect(self._on_changed)
        for slider in (self.day_temperature, self.night_temperature):
            self._connect_preview(slider)
        controller.changed.connect(self._update_status)

        self._load(self._saved)

    def current_config(self) -> NightLightConfig:
        return NightLightConfig(
            enabled=self.enabled.isChecked(),
            mode=Mode(self.mode.currentData()),  # Qt hands enum data back as a plain str
            day_temperature=self.day_temperature.value(),
            night_temperature=self.night_temperature.value(),
            transition_minutes=self.transition.value(),
            night_start=self.night_start.time().toString("HH:mm"),
            day_start=self.day_start.time().toString("HH:mm"),
            latitude=self.latitude.value(),
            longitude=self.longitude.value(),
        )

    def closeEvent(self, event) -> None:
        self._controller.preview(None)
        super().closeEvent(event)

    def _load(self, config: NightLightConfig) -> None:
        self.enabled.setChecked(config.enabled)
        self.mode.setCurrentIndex(self.mode.findData(config.mode))
        self.day_temperature.setValue(config.day_temperature)
        self.night_temperature.setValue(config.night_temperature)
        self.latitude.setValue(config.latitude)
        self.longitude.setValue(config.longitude)
        self.night_start.setTime(QTime.fromString(config.night_start, "HH:mm"))
        self.day_start.setTime(QTime.fromString(config.day_start, "HH:mm"))
        self.transition.setValue(config.transition_minutes)
        self._on_changed()

    def _apply(self) -> None:
        self._saved = self.current_config()
        self.applied.emit(self._saved)
        self._on_changed()

    def _on_changed(self) -> None:
        config = self.current_config()
        Button = QDialogButtonBox.StandardButton
        self.buttons.button(Button.Apply).setEnabled(config != self._saved)
        self.buttons.button(Button.Reset).setEnabled(config != self._saved)
        self.buttons.button(Button.RestoreDefaults).setEnabled(config != NightLightConfig())

        # Like KDE: only show the fields that matter for the chosen mode.
        mode = config.mode
        visible = {
            self.day_temperature: mode is not Mode.ALWAYS,
            self.latitude: mode is Mode.LOCATION,
            self.longitude: mode is Mode.LOCATION,
            self.night_start: mode is Mode.TIMES,
            self.day_start: mode is Mode.TIMES,
            self.transition: mode is not Mode.ALWAYS,
        }
        for widget, show in visible.items():
            self.form.setRowVisible(widget, show)
        for row in range(1, self.form.rowCount()):
            for role in (QFormLayout.ItemRole.LabelRole, QFormLayout.ItemRole.FieldRole):
                if item := self.form.itemAt(row, role):
                    if item.widget():
                        item.widget().setEnabled(config.enabled)
        self.status.setEnabled(False)
        self._update_status()

    def _update_status(self) -> None:
        unsaved = self.current_config() != self._saved
        self.status.setText("Apply to see the new schedule" if unsaved else night_light_status(self._controller))

    def _connect_preview(self, slider: TemperatureSlider) -> None:
        """While a temperature slider is held, show that temperature on screen, as KDE does."""
        inner = slider.slider
        inner.sliderPressed.connect(lambda: self._controller.preview(slider.value()))
        inner.sliderMoved.connect(lambda _: self._controller.preview(slider.value()))
        inner.sliderReleased.connect(lambda: self._controller.preview(None))
