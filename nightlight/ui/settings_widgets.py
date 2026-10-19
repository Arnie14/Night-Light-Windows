# SPDX-License-Identifier: GPL-3.0-only
"""Editors for the list-like settings: apps to pause night light for, and
display names and visibility. (The brightness schedule has its own,
schedule_editor.py.)"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nightlight.config import AppConfig, DisplayPrefs
from nightlight.hardware.brightness import MonitorInfo
from nightlight.ui.widgets import SecondaryLabel

class AppList(QWidget):
    """Apps chosen by executable name (to pause night light for, or never to
    dim for idle while in front)."""

    changed = Signal()

    def __init__(self, running_apps: Callable[[], list[str]], browse_title: str = "Pause night light for this app") -> None:
        super().__init__()
        self._running_apps = running_apps
        self._browse_title = browse_title
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
        path, _ = QFileDialog.getOpenFileName(self, self._browse_title, "", "Programs (*.exe)")
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
        # Looked up as the app does, so names saved under a key from an older
        # version (without the serial) show on the display they belong to.
        lookup = AppConfig(displays=prefs)
        saved = {m.key: lookup.display(m.key) for m in monitors}
        known = {m.key: m.name for m in monitors}
        stored = {p.key for p in prefs}
        moved = {m.key.split(":", 1)[0] for m in monitors if m.key not in stored}  # older keys now shown under the new
        for p in prefs:
            if p.key not in known and p.key not in moved:
                saved[p.key] = p
                known[p.key] = f"{p.key} (not connected)"
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
