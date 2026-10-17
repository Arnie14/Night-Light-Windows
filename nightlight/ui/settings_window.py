# SPDX-License-Identifier: GPL-3.0-only
"""Night Light settings, modelled on KDE System Settings > Display & Monitor > Night Light."""

from __future__ import annotations

from PySide6.QtCore import QTime, QTimeZone, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from nightlight.config import MAX_TRANSITION_MINUTES, Mode, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.core import locations
from nightlight.core.schedule import OverrideKind
from nightlight.ui import icons
from nightlight.ui.night_light_text import is_night_light_on, night_light_status, schedule_description
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
CUSTOM_COORDINATES = ""  # the location choice for typed-in latitude and longitude
FIELD_WIDTH = 150


class SettingsWindow(QWidget):
    applied = Signal(object)  # NightLightConfig

    def __init__(self, controller: NightLightController) -> None:
        super().__init__()
        self._controller = controller
        self._saved = controller.config
        self.setWindowTitle("Night Light — Settings")
        self.setWindowIcon(icons.night_icon())
        self.setMinimumWidth(580)

        header = QHBoxLayout()
        header.setSpacing(14)
        self._header_icon = QLabel()
        header_text = QVBoxLayout()
        header_text.setSpacing(1)
        title = QLabel("Night Light")
        title.setProperty("title", True)
        description = QLabel("The blue light filter makes the colors on the screen warmer.")
        description.setWordWrap(True)
        description.setProperty("dim", True)
        header_text.addWidget(title)
        header_text.addWidget(description)
        header.addWidget(self._header_icon)
        header.addLayout(header_text, 1)

        # Shown while a temporary override from the tray/flyout is in effect.
        self.banner = QFrame(objectName="banner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(14, 10, 10, 10)
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
        self.location = _location_combo()
        self.use_system_zone = QPushButton("Use system", toolTip="Use the location of this computer's time zone")
        self.use_system_zone.clicked.connect(self._pick_system_zone)
        self.latitude = QDoubleSpinBox(minimum=-90, maximum=90, decimals=4, suffix="°")
        self.longitude = QDoubleSpinBox(minimum=-180, maximum=180, decimals=4, suffix="°")
        self.night_start = QTimeEdit(displayFormat="HH:mm")
        self.day_start = QTimeEdit(displayFormat="HH:mm")
        self.transition = QSpinBox(minimum=1, maximum=MAX_TRANSITION_MINUTES, suffix=" min")
        for field in (self.latitude, self.longitude, self.night_start, self.day_start, self.transition):
            field.setFixedWidth(FIELD_WIDTH)
        self.schedule_info = QLabel()
        self.schedule_info.setWordWrap(True)
        self.schedule_info.setProperty("dim", True)

        location_row = QWidget()
        location_layout = QHBoxLayout(location_row)
        location_layout.setContentsMargins(0, 0, 0, 0)
        location_layout.addWidget(self.location, 1)
        location_layout.addWidget(self.use_system_zone)
        self._location_row = location_row

        self.form = QFormLayout()
        self.form.setVerticalSpacing(12)
        self.form.setHorizontalSpacing(18)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.form.addRow("Switching times:", self.mode)
        self.form.addRow("Location:", location_row)
        self.form.addRow("Latitude:", self.latitude)
        self.form.addRow("Longitude:", self.longitude)
        self.form.addRow("Begin night light at:", self.night_start)
        self.form.addRow("Begin day light at:", self.day_start)
        self.form.addRow("Transition duration:", self.transition)
        self.form.addRow("Day light temperature:", self.day_temperature)
        self.form.addRow("Night light temperature:", self.night_temperature)

        card = QFrame(objectName="card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 20, 22, 20)
        card_layout.setSpacing(14)
        card_layout.addLayout(self.form)
        card_layout.addWidget(self.schedule_info)

        Button = QDialogButtonBox.StandardButton
        self.buttons = QDialogButtonBox(Button.RestoreDefaults | Button.Reset | Button.Apply)
        self.buttons.button(Button.RestoreDefaults).clicked.connect(lambda: self._load(NightLightConfig()))
        self.buttons.button(Button.Reset).clicked.connect(lambda: self._load(self._saved))
        self.buttons.button(Button.Apply).clicked.connect(self._apply)
        self.buttons.button(Button.Apply).setProperty("primary", True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        layout.addLayout(header)
        layout.addWidget(self.banner)
        layout.addWidget(card)
        layout.addStretch(1)
        layout.addWidget(self.buttons)

        self._update_header_icon()
        controller.changed.connect(self._update_header_icon)

        for signal in (
            self.mode.currentIndexChanged,
            self.day_temperature.valueChanged,
            self.night_temperature.valueChanged,
            self.location.currentIndexChanged,
            self.latitude.valueChanged,
            self.longitude.valueChanged,
            self.night_start.timeChanged,
            self.day_start.timeChanged,
            self.transition.valueChanged,
        ):
            signal.connect(self._on_changed)
        self.location.currentIndexChanged.connect(self._on_location_picked)
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
            timezone=self.location.currentData(),
        )

    def closeEvent(self, event) -> None:
        self._controller.preview(None)
        super().closeEvent(event)

    def _load(self, config: NightLightConfig) -> None:
        self.mode.setCurrentIndex(self.mode.findData(config.mode if config.enabled else ALWAYS_OFF))
        self.day_temperature.setValue(config.day_temperature)
        self.night_temperature.setValue(config.night_temperature)
        self.location.setCurrentIndex(max(0, self.location.findData(config.timezone)))
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
        custom = self.location.currentData() == CUSTOM_COORDINATES
        visible = {
            self._location_row: mode is Mode.LOCATION,
            self.latitude: mode is Mode.LOCATION and custom,
            self.longitude: mode is Mode.LOCATION and custom,
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

    def _on_location_picked(self) -> None:
        """Picking a place sets the coordinates; they stay editable under "Custom coordinates"."""
        if place := locations.find(self.location.currentData()):
            self.latitude.setValue(place.latitude)
            self.longitude.setValue(place.longitude)

    def _pick_system_zone(self) -> None:
        zone = bytes(QTimeZone.systemTimeZoneId()).decode(errors="replace")
        if place := locations.find(zone):
            self.location.setCurrentIndex(self.location.findData(place.zone))
        else:
            self.use_system_zone.setToolTip(f"Couldn't find the system time zone ({zone or 'unknown'}) in the list.")
            self.location.setFocus()

    def _update_header_icon(self) -> None:
        icon = icons.night_icon() if is_night_light_on(self._controller) else icons.day_icon()
        self._header_icon.setPixmap(icon.pixmap(40, 40))

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


def _location_combo() -> QComboBox:
    """Type to search the list of places; "Custom coordinates" lets you type latitude and longitude."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    # Otherwise the widest of the 400+ names sets the minimum width of the window.
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(16)
    combo.addItem("Custom coordinates", CUSTOM_COORDINATES)
    for place in locations.all_locations():
        combo.addItem(place.label, place.zone)
    completer = combo.completer()
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setCompletionMode(completer.CompletionMode.PopupCompletion)
    combo.setMaxVisibleItems(12)
    # Text that isn't a place goes back to the selected one instead of lingering.
    combo.lineEdit().editingFinished.connect(lambda: combo.setEditText(combo.itemText(combo.currentIndex())))
    return combo
