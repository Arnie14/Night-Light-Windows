# SPDX-License-Identifier: GPL-3.0-only
"""Editors for the list-like settings: brightness schedule steps, apps to
pause night light for, and display names and visibility."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QSpinBox,
    QTimeEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nightlight.config import SUNRISE, SUNSET, BrightnessStep, DisplayPrefs
from nightlight.hardware.brightness import MonitorInfo
from nightlight.ui.widgets import SecondaryLabel

AT_TIME = "time"
STEP_KINDS = {AT_TIME: "At", SUNRISE: "Sunrise", SUNSET: "Sunset"}


class StepRow(QWidget):
    """One step of the brightness schedule: when, and which level."""

    changed = Signal()
    removeRequested = Signal(object)  # self

    def __init__(self, step: BrightnessStep) -> None:
        super().__init__()
        self.kind = QComboBox()
        for key, label in STEP_KINDS.items():
            self.kind.addItem(label, key)
        self.time = QTimeEdit(displayFormat="HH:mm")
        self.offset = QSpinBox(minimum=-180, maximum=180, singleStep=15, suffix=" min")
        self.offset.setToolTip("Minutes before (negative) or after the sun event")
        self.offset.setPrefix("+")
        self.level = QSpinBox(minimum=0, maximum=100, singleStep=5, suffix=" %")
        remove = QToolButton(text="✕", toolTip="Remove this step", autoRaise=True)
        remove.clicked.connect(lambda: self.removeRequested.emit(self))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for widget in (self.kind, self.time, self.offset):
            layout.addWidget(widget)
        layout.addWidget(QLabel("→"))
        layout.addWidget(self.level)
        layout.addStretch(1)
        layout.addWidget(remove)

        sun = step.at in (SUNRISE, SUNSET)
        self.kind.setCurrentIndex(self.kind.findData(step.at if sun else AT_TIME))
        self.time.setTime(QTime.fromString(step.at, "HH:mm") if not sun else QTime(7, 0))
        self.offset.setValue(step.offset_minutes)
        self.level.setValue(step.level)
        self._update()
        for signal in (self.kind.currentIndexChanged, self.time.timeChanged, self.offset.valueChanged, self.level.valueChanged):
            signal.connect(self._update)
            signal.connect(self.changed)

    def step(self) -> BrightnessStep:
        kind = self.kind.currentData()
        if kind == AT_TIME:
            return BrightnessStep(self.time.time().toString("HH:mm"), self.level.value())
        return BrightnessStep(kind, self.level.value(), self.offset.value())

    def _update(self) -> None:
        sun = self.kind.currentData() != AT_TIME
        self.time.setVisible(not sun)
        self.offset.setVisible(sun)
        self.offset.setPrefix("+" if self.offset.value() >= 0 else "")


class StepList(QWidget):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[StepRow] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._add = QPushButton("Add a step")
        self._add.clicked.connect(lambda: self._append(BrightnessStep("12:00", 60)))
        self._layout.addWidget(self._add)

    def steps(self) -> tuple[BrightnessStep, ...]:
        return tuple(row.step() for row in self._rows)

    def set_steps(self, steps: tuple[BrightnessStep, ...]) -> None:
        for row in self._rows:
            row.deleteLater()
        self._rows = []
        for step in steps:
            self._append(step, notify=False)
        self.changed.emit()

    def _append(self, step: BrightnessStep, notify: bool = True) -> None:
        row = StepRow(step)
        row.changed.connect(self.changed)
        row.removeRequested.connect(self._remove)
        self._layout.insertWidget(len(self._rows), row)
        self._rows.append(row)
        if notify:
            self.changed.emit()

    def _remove(self, row: StepRow) -> None:
        self._rows.remove(row)
        row.deleteLater()
        self.changed.emit()


class AppList(QWidget):
    """Apps to pause night light for, by executable name."""

    changed = Signal()

    def __init__(self, running_apps: Callable[[], list[str]]) -> None:
        super().__init__()
        self._running_apps = running_apps
        self.list = QListWidget()
        self.list.setFixedHeight(96)
        self.list.setSortingEnabled(True)
        self._add = QPushButton("Add an app")
        self._menu = QMenu(self)
        self._menu.aboutToShow.connect(self._fill_menu)
        self._add.setMenu(self._menu)
        self._remove = QPushButton("Remove")
        self._remove.clicked.connect(self._remove_selected)
        self.list.itemSelectionChanged.connect(lambda: self._remove.setEnabled(bool(self.list.selectedItems())))
        self._remove.setEnabled(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self._add)
        buttons.addWidget(self._remove)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def apps(self) -> tuple[str, ...]:
        return tuple(sorted(self.list.item(i).text() for i in range(self.list.count())))

    def set_apps(self, apps: tuple[str, ...]) -> None:
        self.list.clear()
        self.list.addItems(list(apps))
        self.changed.emit()

    def add(self, exe: str) -> None:
        exe = exe.strip().lower()
        if exe and exe not in self.apps():
            self.list.addItem(exe)
            self.changed.emit()

    def _fill_menu(self) -> None:
        self._menu.clear()
        running = [app for app in self._running_apps() if app not in self.apps()]
        for app in running:
            self._menu.addAction(app, lambda app=app: self.add(app))
        if running:
            self._menu.addSeparator()
        self._menu.addAction("Browse for an app…", self._browse)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Pause night light for this app", "", "Programs (*.exe)")
        if path:
            self.add(path.replace("\\", "/").rsplit("/", 1)[-1])

    def _remove_selected(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self.changed.emit()


class DisplayList(QWidget):
    """A name field and a "show" box per display, as Twinkle Tray's "Rename
    Monitors" and "Hide Displays": keyed by display id, so they follow the
    display between ports, and an empty name keeps its own."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(8)
        self._rows: dict[str, tuple[QLineEdit, QCheckBox]] = {}
        self._empty = SecondaryLabel("No displays detected yet.")
        self._grid.addWidget(self._empty, 0, 0)

    def prefs(self) -> tuple[DisplayPrefs, ...]:
        prefs = []
        for key, (name, show) in self._rows.items():
            if name.text().strip() or not show.isChecked():
                prefs.append(DisplayPrefs(key, name.text().strip(), not show.isChecked()))
        return tuple(prefs)

    def set_displays(self, monitors: list[MonitorInfo], prefs: tuple[DisplayPrefs, ...]) -> None:
        """Rows for the displays connected now, plus any with saved settings."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget() is not self._empty:
                item.widget().deleteLater()
        self._rows = {}
        saved = {p.key: p for p in prefs}
        known = {m.key: m.name for m in monitors}
        for p in prefs:
            known.setdefault(p.key, f"{p.key} (not connected)")
        self._empty.setVisible(not known)
        if not known:
            self._grid.addWidget(self._empty, 0, 0)
        for row, (key, original) in enumerate(known.items()):
            pref = saved.get(key, DisplayPrefs(key))
            name = QLineEdit(pref.name, placeholderText=original)
            name.setToolTip(f"Display id {key}. Leave empty to use its own name.")
            name.setFixedWidth(220)
            show = QCheckBox("Show in panel")
            show.setChecked(not pref.hidden)
            name.textChanged.connect(self.changed)
            show.toggled.connect(self.changed)
            self._grid.addWidget(name, row, 0)
            self._grid.addWidget(show, row, 1)
            self._rows[key] = (name, show)
        self.changed.emit()
