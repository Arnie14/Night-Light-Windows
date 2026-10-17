from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from nightlight.backends import DisplayBackend, MonitorInfo
from nightlight.worker import DisplayWorker

# Wait this long after the slider stops moving before sending a DDC/CI write.
DEBOUNCE_MS = 150


class MonitorRow(QGroupBox):
    """Brightness controls for one monitor."""

    brightnessRequested = Signal(int, int)  # index, value

    def __init__(self, info: MonitorInfo) -> None:
        super().__init__(info.name)
        self.info = info
        layout = QHBoxLayout(self)

        if not info.supported:
            label = QLabel("Brightness control not supported (DDC/CI unavailable or disabled in the monitor's menu)")
            label.setWordWrap(True)
            label.setEnabled(False)
            layout.addWidget(label)
            return

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setEnabled(False)  # until the current value has been read
        self.value_label = QLabel("…")
        self.value_label.setMinimumWidth(40)
        layout.addWidget(QLabel("🔆"))
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.value_label)

        self._debounce = QTimer(self, singleShot=True, interval=DEBOUNCE_MS)
        self._debounce.timeout.connect(self._send)
        self.slider.valueChanged.connect(self._on_slider_changed)

    def set_brightness(self, value: int | None) -> None:
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


class MainWindow(QWidget):
    # Cross-thread requests to the worker.
    refreshRequested = Signal()
    setBrightnessRequested = Signal(int, int)

    def __init__(self, backend: DisplayBackend) -> None:
        super().__init__()
        self.setWindowTitle("Night Light")
        self.setMinimumWidth(420)
        self._rows: dict[int, MonitorRow] = {}

        layout = QVBoxLayout(self)
        self._status = QLabel("Detecting monitors…")
        self._status.setWordWrap(True)
        self._monitor_layout = QVBoxLayout()
        refresh_button = QPushButton("Refresh monitors")
        refresh_button.clicked.connect(self._refresh)
        layout.addWidget(self._status)
        layout.addLayout(self._monitor_layout)
        layout.addStretch(1)
        layout.addWidget(refresh_button)

        self._thread = QThread(self)
        self._worker = DisplayWorker(backend)
        self._worker.moveToThread(self._thread)
        self.refreshRequested.connect(self._worker.refresh)
        self.setBrightnessRequested.connect(self._worker.set_brightness)
        self._worker.monitorsFound.connect(self._on_monitors_found)
        self._worker.brightnessRead.connect(self._on_brightness_read)
        self._worker.error.connect(self._status.setText)
        self._thread.start()

        self._refresh()

    def _refresh(self) -> None:
        self._status.setText("Detecting monitors…")
        self.refreshRequested.emit()

    def _on_monitors_found(self, monitors: list[MonitorInfo]) -> None:
        for row in self._rows.values():
            row.deleteLater()
        self._rows.clear()

        for info in monitors:
            row = MonitorRow(info)
            row.brightnessRequested.connect(self.setBrightnessRequested)
            self._monitor_layout.addWidget(row)
            self._rows[info.index] = row

        if monitors:
            self._status.setText(f"Found {len(monitors)} monitor(s).")
        else:
            self._status.setText("No DDC/CI monitors found.")

    def _on_brightness_read(self, index: int, value: int | None) -> None:
        if row := self._rows.get(index):
            row.set_brightness(value)

    def closeEvent(self, event) -> None:
        self._thread.quit()
        self._thread.wait()
        super().closeEvent(event)
