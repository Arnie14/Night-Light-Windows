# SPDX-License-Identifier: GPL-3.0-only
"""The settings window: Night Light, modelled on KDE System Settings >
Display & Monitor > Night Light, and general app settings (startup)."""

from __future__ import annotations

import logging
from dataclasses import replace

from PySide6.QtCore import QTime, QTimeZone, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
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
    QStyle,
    QStyleOptionButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from nightlight import APP_NAME
from nightlight.config import MAX_TRANSITION_MINUTES, AppConfig, GeneralConfig, Mode, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.core import locations
from nightlight.core.schedule import OverrideKind
from nightlight.ui import icons
from nightlight.ui.night_light_text import is_night_light_on, night_light_status, schedule_description
from nightlight.ui.widgets import HEADING_WEIGHT, SecondaryLabel, Separator, TemperatureSlider, set_font

log = logging.getLogger(__name__)

# Like KDE, "Always off" is a choice in the list even though it is stored as
# enabled=False rather than as a mode.
ALWAYS_OFF = "off"
SWITCHING_TIMES = {
    ALWAYS_OFF: "Always off",
    Mode.ALWAYS: "Always on",
    Mode.LOCATION: "Sunrise and sunset at location",
    Mode.TIMES: "Custom times",
}
# The location choice for typing in latitude and longitude.
CUSTOM_LOCATION = ""
CUSTOM_LOCATION_LABEL = "Custom location"
# Wide fields (lists, sliders) share one width, so they end on one line.
WIDE_FIELD = 340


class SettingsWindow(QWidget):
    applied = Signal(object)  # AppConfig

    def __init__(self, controller: NightLightController, config: AppConfig, can_autostart: bool = True) -> None:
        super().__init__()
        self._controller = controller
        self._saved = replace(config, night_light=controller.config)
        self._mode_while_off = self._saved.night_light.mode
        self.setWindowTitle(f"{APP_NAME} Settings")
        self.setWindowIcon(icons.night_icon())

        header = QHBoxLayout()
        header.setSpacing(14)
        self._header_icon = QLabel()
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        title = QLabel(APP_NAME)
        set_font(title, 1.45, HEADING_WEIGHT)
        header_text.addWidget(title)
        header_text.addWidget(SecondaryLabel("Brightness and night light for every display"))
        header.addWidget(self._header_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addLayout(header_text, 1)

        # Shown while a temporary override from the tray/flyout is in effect.
        self.banner = QFrame(frameShape=QFrame.Shape.StyledPanel)
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 8, 8, 8)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_button = QPushButton()
        self.banner_button.clicked.connect(controller.clear_override)
        banner_layout.addWidget(self.banner_text, 1)
        banner_layout.addWidget(self.banner_button)

        # Night Light
        self.mode = QComboBox()
        for key, label in SWITCHING_TIMES.items():
            self.mode.addItem(label, key)
        self.location = _location_combo()
        self.use_system_zone = QPushButton("Detect", toolTip="Use the place of this PC's time zone")
        self.use_system_zone.clicked.connect(self._pick_system_zone)
        self._location_row = QWidget()
        location_layout = QHBoxLayout(self._location_row)
        location_layout.setContentsMargins(0, 0, 0, 0)
        location_layout.setSpacing(8)
        location_layout.addWidget(self.location, 1)
        location_layout.addWidget(self.use_system_zone)
        self.latitude = QDoubleSpinBox(minimum=-90, maximum=90, decimals=4, suffix="°")
        self.longitude = QDoubleSpinBox(minimum=-180, maximum=180, decimals=4, suffix="°")
        self.night_start = QTimeEdit(displayFormat="HH:mm")
        self.day_start = QTimeEdit(displayFormat="HH:mm")
        self.transition = QSpinBox(minimum=1, maximum=MAX_TRANSITION_MINUTES, suffix=" min")
        self.day_temperature = TemperatureSlider()
        self.night_temperature = TemperatureSlider()
        # Fields sized to what they hold, not stretched across the window.
        for field in (self.mode, self._location_row, self.day_temperature, self.night_temperature):
            field.setFixedWidth(WIDE_FIELD)
        for field in (self.latitude, self.longitude):
            field.setFixedWidth(130)
        for field in (self.night_start, self.day_start, self.transition):
            field.setFixedWidth(100)
        for slider in (self.day_temperature, self.night_temperature):
            slider.setMinimumHeight(self.mode.sizeHint().height())  # same row rhythm as the inputs
        self.schedule_info = SecondaryLabel(scale=0.92)
        self.schedule_info.setWordWrap(True)

        # General
        self.start_with_windows = QCheckBox(f"Start {APP_NAME} when I sign in")
        self.start_minimized = QCheckBox("Start minimized in the system tray")
        self.remember_brightness = QCheckBox("Remember each display's brightness")
        self.remember_brightness.setToolTip(
            "Some displays and laptops come back at another brightness.\n"
            f"The last level set in {APP_NAME} is put back when it starts,\n"
            "after sleep and when a display is reconnected."
        )
        remember_hint = SecondaryLabel("Restored after starting, sleep and reconnecting a display", scale=0.92)
        remember_hint.setContentsMargins(self._checkbox_indent(), 0, 0, 0)
        self.link_brightness = QCheckBox("Change all displays together")
        if not can_autostart:
            self.start_with_windows.setEnabled(False)
            self.start_with_windows.setToolTip("Not available on this system")

        self.form = QFormLayout()
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.form.setHorizontalSpacing(16)
        self.form.setVerticalSpacing(10)
        self.form.addRow(_section("Night Light", "The blue light filter makes the colors on the screen warmer."))
        self.form.addRow("Switching times:", self.mode)
        self.form.addRow("Location:", self._location_row)
        self.form.addRow("Latitude:", self.latitude)
        self.form.addRow("Longitude:", self.longitude)
        self.form.addRow("Begin night light at:", self.night_start)
        self.form.addRow("Begin day light at:", self.day_start)
        self.form.addRow("Transition duration:", self.transition)
        self.form.addRow("Day light temperature:", self.day_temperature)
        self.form.addRow("Night light temperature:", self.night_temperature)
        self.form.addRow("", self.schedule_info)
        self.form.addRow(_spaced(Separator()))
        self.form.addRow(_section("General"))
        self.form.addRow("Startup:", self.start_with_windows)
        self.form.addRow("", self.start_minimized)
        self.form.addRow("Brightness:", self.remember_brightness)
        self.form.addRow("", remember_hint)
        self.form.addRow("", self.link_brightness)

        Button = QDialogButtonBox.StandardButton
        self.buttons = QDialogButtonBox(Button.RestoreDefaults | Button.Reset | Button.Apply)
        self.buttons.button(Button.RestoreDefaults).clicked.connect(lambda: self._load(AppConfig()))
        self.buttons.button(Button.Reset).clicked.connect(lambda: self._load(self._saved))
        self.buttons.button(Button.Apply).clicked.connect(self._apply)
        self.buttons.button(Button.Apply).setDefault(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 20)
        layout.setSpacing(16)
        layout.addLayout(header)
        layout.addWidget(self.banner)
        layout.addWidget(Separator())
        layout.addLayout(self.form)
        layout.addWidget(Separator())
        layout.addWidget(self.buttons)
        # The window fits its contents, growing and shrinking as rows for the
        # chosen switching times come and go.
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)
        self.schedule_info.setFixedWidth(WIDE_FIELD)

        for signal in (
            self.mode.currentIndexChanged,
            self.day_temperature.valueChanged,
            self.night_temperature.valueChanged,
            self.latitude.valueChanged,
            self.longitude.valueChanged,
            self.night_start.timeChanged,
            self.day_start.timeChanged,
            self.transition.valueChanged,
            self.start_with_windows.toggled,
            self.start_minimized.toggled,
            self.remember_brightness.toggled,
            self.link_brightness.toggled,
        ):
            signal.connect(self._on_changed)
        self.location.currentIndexChanged.connect(self._on_location_picked)
        for slider in (self.day_temperature, self.night_temperature):
            self._connect_preview(slider)
        controller.changed.connect(self._update_status)
        controller.changed.connect(self._update_header_icon)

        self._update_header_icon()
        self._load(self._saved)

    def current_config(self) -> AppConfig:
        choice = self.mode.currentData()
        enabled = choice != ALWAYS_OFF
        night_light = NightLightConfig(
            enabled=enabled,
            # Qt hands enum data back as a plain str; keep the last mode while off.
            mode=Mode(choice) if enabled else self._mode_while_off,
            day_temperature=self.day_temperature.value(),
            night_temperature=self.night_temperature.value(),
            transition_minutes=self.transition.value(),
            night_start=self.night_start.time().toString("HH:mm"),
            day_start=self.day_start.time().toString("HH:mm"),
            latitude=self.latitude.value(),
            longitude=self.longitude.value(),
            timezone=self.location.currentData() or CUSTOM_LOCATION,
        )
        general = GeneralConfig(
            start_with_windows=self.start_with_windows.isChecked(),
            start_minimized=self.start_minimized.isChecked(),
            remember_brightness=self.remember_brightness.isChecked(),
            link_brightness=self.link_brightness.isChecked(),
        )
        return AppConfig(night_light=night_light, general=general)

    def closeEvent(self, event) -> None:
        self._controller.preview(None)
        super().closeEvent(event)

    def _load(self, config: AppConfig) -> None:
        night_light, general = config.night_light, config.general
        self._mode_while_off = night_light.mode
        self.mode.setCurrentIndex(self.mode.findData(night_light.mode if night_light.enabled else ALWAYS_OFF))
        # A zone this version doesn't know (a hand-edited file) shows as a custom location.
        self.location.setCurrentIndex(max(0, self.location.findData(night_light.timezone)))
        self.latitude.setValue(night_light.latitude)
        self.longitude.setValue(night_light.longitude)
        self.day_temperature.setValue(night_light.day_temperature)
        self.night_temperature.setValue(night_light.night_temperature)
        self.night_start.setTime(QTime.fromString(night_light.night_start, "HH:mm"))
        self.day_start.setTime(QTime.fromString(night_light.day_start, "HH:mm"))
        self.transition.setValue(night_light.transition_minutes)
        self.start_with_windows.setChecked(general.start_with_windows and self.start_with_windows.isEnabled())
        self.start_minimized.setChecked(general.start_minimized)
        self.remember_brightness.setChecked(general.remember_brightness)
        self.link_brightness.setChecked(general.link_brightness)
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
        self.buttons.button(Button.RestoreDefaults).setEnabled(config != AppConfig())

        # Like KDE: only show what matters for the chosen switching times.
        night_light = config.night_light
        mode = night_light.mode if night_light.enabled else None
        custom = night_light.timezone == CUSTOM_LOCATION
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
        self.night_temperature.setEnabled(night_light.enabled)
        self._update_status()

    def _checkbox_indent(self) -> int:
        """How far a check box's text starts from its left edge, to line a hint up under it."""
        option = QStyleOptionButton()
        option.initFrom(self.remember_brightness)
        return self.style().subElementRect(QStyle.SubElement.SE_CheckBoxContents, option, self.remember_brightness).left()

    def _on_location_picked(self) -> None:
        """Picking a place fills in its coordinates, which then stay as a
        starting point if "Custom location" is chosen afterwards."""
        if place := locations.find(self.location.currentData() or ""):
            for spin, value in ((self.latitude, place.latitude), (self.longitude, place.longitude)):
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        self._on_changed()

    def _pick_system_zone(self) -> None:
        try:
            zone = QTimeZone.systemTimeZoneId().data().decode("ascii", "replace")
        except Exception:
            log.warning("Could not read the system time zone", exc_info=True)
            zone = ""
        place = locations.find(zone)
        if place is None:
            log.info("System time zone %r is not in the list of places", zone)
            self.schedule_info.setText(f"This PC's time zone ({zone or 'unknown'}) isn't in the list. Pick the nearest city instead.")
            return
        self.location.setCurrentIndex(self.location.findData(place.zone))

    def _update_header_icon(self) -> None:
        icon = icons.night_icon() if is_night_light_on(self._controller) else icons.day_icon()
        self._header_icon.setPixmap(icon.pixmap(40, 40))

    def _update_status(self) -> None:
        override = self._controller.override
        self.banner.setVisible(override is not None)
        if override is not None:
            self.banner_text.setText(f"Night Light: {night_light_status(self._controller)}")
            self.banner_button.setText("Resume" if override.kind is OverrideKind.SUSPEND else "Turn Off")

        if self.current_config().night_light != self._saved.night_light:
            self.schedule_info.setText("Apply to see the new schedule.")
        else:
            self.schedule_info.setText(schedule_description(self._controller))

    def _connect_preview(self, slider: TemperatureSlider) -> None:
        """While a temperature slider is held, show that temperature on screen, as KDE does."""
        inner = slider.slider
        inner.sliderPressed.connect(lambda: self._controller.preview(slider.value()))
        inner.sliderMoved.connect(lambda _: self._controller.preview(slider.value()))
        inner.sliderReleased.connect(lambda: self._controller.preview(None))


def _section(title: str, description: str = "") -> QWidget:
    """A section heading across the form, with an optional line under it."""
    widget = QWidget()
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 4)
    layout.setSpacing(2)
    heading = QLabel(title)
    set_font(heading, 1.1, HEADING_WEIGHT)
    layout.addWidget(heading)
    if description:
        layout.addWidget(SecondaryLabel(description))
    return widget


def _spaced(widget: QWidget, space: int = 6) -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, space, 0, space)
    layout.addWidget(widget)
    return holder


def _location_combo() -> QComboBox:
    """Every place in the time zone list, searchable by typing any part of
    its name, with "Custom location" first for typing in coordinates."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    # Otherwise the longest of 400+ names sets the window's width.
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(18)
    combo.setMaxVisibleItems(14)
    combo.addItem(CUSTOM_LOCATION_LABEL, CUSTOM_LOCATION)
    for place in locations.all_locations():
        combo.addItem(place.label, place.zone)
    combo.lineEdit().setPlaceholderText("Type a city…")

    completer = combo.completer()
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setCompletionMode(completer.CompletionMode.PopupCompletion)

    def settle() -> None:
        # Typed text that names a place selects it; anything else goes back
        # to the current choice rather than lingering in the box.
        index = combo.findText(combo.currentText().strip(), Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.setEditText(combo.itemText(combo.currentIndex()))

    combo.lineEdit().editingFinished.connect(settle)
    return combo
