# SPDX-License-Identifier: GPL-3.0-only
"""Editing the brightness schedule: a preset, or your own list of times."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox, QTimeEdit, QToolButton, QVBoxLayout, QWidget

from nightlight.config import MAX_SCHEDULE_POINTS, MAX_SUN_OFFSET_MINUTES, MAX_TRANSITION_MINUTES, Anchor, BrightnessScheduleConfig, SchedulePoint
from nightlight.core.brightness_schedule import CUSTOM, PRESETS
from nightlight.ui.settings_parts import Card, SettingRow, ToggleSwitch
from nightlight.ui.widgets import SecondaryLabel

ANCHOR_LABELS = {Anchor.CLOCK: "Time of day", Anchor.SUNRISE: "Sunrise", Anchor.SUNSET: "Sunset"}
CONTROL_WIDTH = 230  # the right-hand controls of the card end on one line


class PointRow(QWidget):
    """One schedule time: when (a time of day, or sunrise/sunset with an
    offset), and the brightness from then on."""

    changed = Signal()
    removeRequested = Signal(object)

    def __init__(self, point: SchedulePoint) -> None:
        super().__init__()
        self.anchor = QComboBox()
        for anchor, label in ANCHOR_LABELS.items():
            self.anchor.addItem(label, anchor)
        self.anchor.setFixedWidth(118)
        self.time = QTimeEdit(displayFormat="HH:mm")
        self.time.setFixedWidth(96)
        self.offset = QSpinBox(minimum=-MAX_SUN_OFFSET_MINUTES, maximum=MAX_SUN_OFFSET_MINUTES, singleStep=15, suffix=" min")
        self.offset.setToolTip("Minutes before (-) or after (+) sunrise or sunset")
        # Room for the longest value and the arrows (Windows 11's style draws both side by side).
        self.offset.setFixedWidth(max(96, self.offset.fontMetrics().horizontalAdvance(f"-{MAX_SUN_OFFSET_MINUTES} min") + 64))
        self.level = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100, singleStep=5, pageStep=10)
        self.level.setToolTip("Brightness from this time on")
        self.level.setFixedWidth(150)
        self._level_label = QLabel()
        self._level_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._level_label.setMinimumWidth(self._level_label.fontMetrics().horizontalAdvance("100%") + 4)
        self.remove = QToolButton(text="✕", toolTip="Remove this time", autoRaise=True)
        self.remove.clicked.connect(lambda: self.removeRequested.emit(self))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.anchor)
        row.addWidget(self.time)
        row.addWidget(self.offset)
        row.addStretch(1)
        row.addWidget(self.level)
        row.addWidget(self._level_label)
        row.addWidget(self.remove)
        self.set_point(point)
        for signal in (self.anchor.currentIndexChanged, self.time.timeChanged, self.offset.valueChanged, self.level.valueChanged):
            signal.connect(self._on_changed)

    def set_point(self, point: SchedulePoint) -> None:
        self.anchor.setCurrentIndex(self.anchor.findData(point.anchor))
        self.time.setTime(QTime.fromString(point.at, "HH:mm"))
        self.offset.setValue(point.offset_minutes)
        self.level.setValue(point.level)
        self._sync()

    def point(self) -> SchedulePoint:
        anchor = Anchor(self.anchor.currentData())
        return SchedulePoint(
            level=self.level.value(),
            at=self.time.time().toString("HH:mm") if anchor is Anchor.CLOCK else "07:00",
            anchor=anchor,
            offset_minutes=self.offset.value() if anchor is not Anchor.CLOCK else 0,
        )

    def _sync(self) -> None:
        clock = self.anchor.currentData() == Anchor.CLOCK
        self.time.setVisible(clock)
        self.offset.setVisible(not clock)
        self._level_label.setText(f"{self.level.value()}%")

    def _on_changed(self) -> None:
        self._sync()
        self.changed.emit()


class ScheduleEditor(Card):
    """A card: the schedule's On/Off, a preset (or custom), the times and
    levels, and an optional gradual change. Details hide while it's off."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.enabled = ToggleSwitch("Change brightness by time of day")
        self.preset = QComboBox()
        for key, preset in PRESETS.items():
            self.preset.addItem(preset.name, key)
        self.preset.addItem("Custom", CUSTOM)
        self.preset.setFixedWidth(CONTROL_WIDTH)
        self.transition = QSpinBox(minimum=0, maximum=MAX_TRANSITION_MINUTES, singleStep=5, suffix=" min")
        self.transition.setSpecialValueText("None")
        self.transition.setFixedWidth(120)
        self.add_button = QPushButton("Add a time")
        self.add_button.clicked.connect(self._add_point)

        self._rows: list[PointRow] = []
        times = QWidget()
        self._rows_layout = QVBoxLayout(times)
        self._rows_layout.setContentsMargins(0, 2, 0, 0)
        self._rows_layout.setSpacing(6)

        self.add(SettingRow("Change brightness by time of day", "Adjusts every display's brightness on a schedule", self.enabled))
        self._preset_row = self.add(SettingRow("Preset", "", self.preset))
        self._times_row = self.add(SettingRow("Times", "At each time, displays change to its brightness", self.add_button, below=times))
        self._transition_row = self.add(SettingRow("Transition", "How long each change takes, starting at its time",
                                                   self.transition))
        self.status = SecondaryLabel(scale=0.92)
        self.status.setWordWrap(True)
        status_holder = QWidget()
        status_layout = QVBoxLayout(status_holder)
        status_layout.setContentsMargins(16, 8, 16, 10)
        status_layout.addWidget(self.status)
        self._status_row = self.add(status_holder)
        self._details = (self._preset_row, self._times_row, self._transition_row, self._status_row)

        self.enabled.toggled.connect(self._on_enabled)
        self.preset.currentIndexChanged.connect(self._on_preset)
        self.transition.valueChanged.connect(self._on_edited)

    def config(self) -> BrightnessScheduleConfig:
        return BrightnessScheduleConfig(
            enabled=self.enabled.isChecked(),
            preset=self.preset.currentData(),
            points=tuple(row.point() for row in self._rows),
            transition_minutes=self.transition.value(),
        )

    def set_config(self, config: BrightnessScheduleConfig) -> None:
        for widget in (self.enabled, self.preset, self.transition):
            widget.blockSignals(True)
        self.enabled.setChecked(config.enabled)
        index = self.preset.findData(config.preset)
        preset = PRESETS.get(config.preset)
        if preset is not None and (config.points, config.transition_minutes) != (preset.points, preset.transition_minutes):
            index = -1  # saved from an earlier version of the preset: shown as the custom schedule it now is
        self.preset.setCurrentIndex(index if index >= 0 else self.preset.findData(CUSTOM))
        self.transition.setValue(config.transition_minutes)
        for widget in (self.enabled, self.preset, self.transition):
            widget.blockSignals(False)
        self._set_points(config.points)
        self._on_enabled(config.enabled, emit=False)
        self._describe()

    def set_status(self, text: str) -> None:
        self.status.setText(text)
        self.set_row_visible(self._status_row, bool(text) and self.enabled.isChecked())

    def _set_points(self, points) -> None:
        for row in self._rows:
            row.deleteLater()
        self._rows = []
        for point in points:
            self._append(point)
        self._update_buttons()

    def _append(self, point: SchedulePoint) -> None:
        row = PointRow(point)
        row.changed.connect(self._on_edited)
        row.removeRequested.connect(self._remove)
        row.setEnabled(self.enabled.isChecked())
        self._rows.append(row)
        self._rows_layout.addWidget(row)

    def _add_point(self) -> None:
        last = self._rows[-1].point() if self._rows else SchedulePoint()
        self._append(SchedulePoint(level=last.level, at="12:00"))
        self._update_buttons()
        self._on_edited()

    def _remove(self, row: PointRow) -> None:
        if len(self._rows) <= 1:
            return  # a schedule needs at least one time
        self._rows.remove(row)
        row.deleteLater()
        self._update_buttons()
        self._on_edited()

    def _update_buttons(self) -> None:
        self.add_button.setEnabled(self.enabled.isChecked() and len(self._rows) < MAX_SCHEDULE_POINTS)
        for row in self._rows:
            row.remove.setVisible(len(self._rows) > 1)

    def _on_enabled(self, enabled: bool, emit: bool = True) -> None:
        for widget in (self.preset, self.transition, *self._rows):
            widget.setEnabled(enabled)
        for row in self._details:
            self.set_row_visible(row, enabled and (row is not self._status_row or bool(self.status.text())))
        self._update_buttons()
        if emit:
            self.changed.emit()

    def _on_preset(self) -> None:
        key = self.preset.currentData()
        if key in PRESETS:
            preset = PRESETS[key]
            self.transition.blockSignals(True)
            self.transition.setValue(preset.transition_minutes)
            self.transition.blockSignals(False)
            self._set_points(preset.points)
            self._on_enabled(self.enabled.isChecked(), emit=False)
        self._describe()
        self.changed.emit()

    def _on_edited(self) -> None:
        # Edited away from the preset: it's a custom schedule now.
        key = self.preset.currentData()
        if key in PRESETS:
            preset = PRESETS[key]
            if tuple(row.point() for row in self._rows) != preset.points or self.transition.value() != preset.transition_minutes:
                self.preset.blockSignals(True)
                self.preset.setCurrentIndex(self.preset.findData(CUSTOM))
                self.preset.blockSignals(False)
                self._describe()
        self.changed.emit()

    def _describe(self) -> None:
        key = self.preset.currentData()
        self._preset_row.set_description(PRESETS[key].description if key in PRESETS else "Your own times and brightness levels")
