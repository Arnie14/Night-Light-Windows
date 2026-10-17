"""Night Light settings, modelled on KDE System Settings > Display & Monitor > Night Light."""

from __future__ import annotations

from PySide6.QtCore import QTime, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from nightlight.config import MAX_TRANSITION_MINUTES, Mode, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.core.schedule import OverrideKind
from nightlight.ui import icons
from nightlight.ui.night_light_text import night_light_status, schedule_description
from nightlight.ui.widgets import TemperatureSlider

# Like KDE, "Always off" is a choice in the list even though it is stored as
# enabled=False rather than as a mode.
ALWAYS_OFF = "off"
SWITCHING_TIMES = {
    ALWAYS_OFF: "Always off",
    Mode.ALWAYS: "Always on",
    Mode.LOCATION: "Sunrise and sunset at location",
    Mode.TIMES: "Custom times",
}


class SettingsWindow(QWidget):
    applied = Signal(object)  # NightLightConfig

    def __init__(self, controller: NightLightController) -> None:
        super().__init__()
        self._controller = controller
        self._saved = controller.config
        self.setWindowTitle("Night Light — Settings")
        self.setWindowIcon(icons.night_icon())
        self.setMinimumWidth(560)

        description = QLabel("The blue light filter makes the colors on the screen warmer.")
        description.setWordWrap(True)

        # Shown while a temporary override from the tray/flyout is in effect.
        self.banner = QFrame(frameShape=QFrame.Shape.StyledPanel)
        banner_layout = QHBoxLayout(self.banner)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_button = QPushButton()
        self.banner_button.clicked.connect(controller.clear_override)
        banner_layout.addWidget(self.banner_text, 1)
        banner_layout.addWidget(self.banner_button)

        self.mode = QComboBox()
        for key, label in SWITCHING_TIMES.items():
            self.mode.addItem(label, key)
        self.day_temperature = TemperatureSlider()
        self.night_temperature = TemperatureSlider()
        self.latitude = QDoubleSpinBox(minimum=-90, maximum=90, decimals=4, suffix="°")
        self.longitude = QDoubleSpinBox(minimum=-180, maximum=180, decimals=4, suffix="°")
        self.night_start = QTimeEdit(displayFormat="HH:mm")
        self.day_start = QTimeEdit(displayFormat="HH:mm")
        self.transition = QSpinBox(minimum=1, maximum=MAX_TRANSITION_MINUTES, suffix=" min")
        self.schedule_info = QLabel()
        self.schedule_info.setWordWrap(True)
        self.schedule_info.setEnabled(False)

        self.form = QFormLayout()
        self.form.addRow("Switching times:", self.mode)
        self.form.addRow("Latitude:", self.latitude)
        self.form.addRow("Longitude:", self.longitude)
        self.form.addRow("Begin night light at:", self.night_start)
        self.form.addRow("Begin day light at:", self.day_start)
        self.form.addRow("Transition duration:", self.transition)
        self.form.addRow("Day light temperature:", self.day_temperature)
        self.form.addRow("Night light temperature:", self.night_temperature)
        self.form.addRow(self.schedule_info)

        Button = QDialogButtonBox.StandardButton
        self.buttons = QDialogButtonBox(Button.RestoreDefaults | Button.Reset | Button.Apply)
        self.buttons.button(Button.RestoreDefaults).clicked.connect(lambda: self._load(NightLightConfig()))
        self.buttons.button(Button.Reset).clicked.connect(lambda: self._load(self._saved))
        self.buttons.button(Button.Apply).clicked.connect(self._apply)

        layout = QVBoxLayout(self)
        layout.addWidget(description)
        layout.addWidget(self.banner)
        layout.addLayout(self.form)
        layout.addStretch(1)
        layout.addWidget(self.buttons)

        for signal in (
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
        choice = self.mode.currentData()
        enabled = choice != ALWAYS_OFF
        return NightLightConfig(
            enabled=enabled,
            # Qt hands enum data back as a plain str; keep the last mode while off.
            mode=Mode(choice) if enabled else self._saved.mode,
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
        self.mode.setCurrentIndex(self.mode.findData(config.mode if config.enabled else ALWAYS_OFF))
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

        # Like KDE: only show what matters for the chosen switching times.
        mode = config.mode if config.enabled else None
        visible = {
            self.latitude: mode is Mode.LOCATION,
            self.longitude: mode is Mode.LOCATION,
            self.night_start: mode is Mode.TIMES,
            self.day_start: mode is Mode.TIMES,
            # Solar transitions last from sunset to dusk (and dawn to sunrise),
            # so only custom times have a configurable duration.
            self.transition: mode is Mode.TIMES,
            self.day_temperature: mode in (Mode.LOCATION, Mode.TIMES),
        }
        for widget, show in visible.items():
            self.form.setRowVisible(widget, show)
        self.night_temperature.setEnabled(config.enabled)
        self._update_status()

    def _update_status(self) -> None:
        override = self._controller.override
        self.banner.setVisible(override is not None)
        if override is not None:
            self.banner_text.setText(f"Night Light: {night_light_status(self._controller)}")
            self.banner_button.setText("Resume" if override.kind is OverrideKind.SUSPEND else "Turn Off")

        if self.current_config() != self._saved:
            self.schedule_info.setText("Apply to see the new schedule.")
        else:
            self.schedule_info.setText(schedule_description(self._controller))

    def _connect_preview(self, slider: TemperatureSlider) -> None:
        """While a temperature slider is held, show that temperature on screen, as KDE does."""
        inner = slider.slider
        inner.sliderPressed.connect(lambda: self._controller.preview(slider.value()))
        inner.sliderMoved.connect(lambda _: self._controller.preview(slider.value()))
        inner.sliderReleased.connect(lambda: self._controller.preview(None))
