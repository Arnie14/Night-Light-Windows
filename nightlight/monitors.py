"""Runs all brightness backend calls on a background thread.

DDC/CI calls can take hundreds of milliseconds, so they must never run on the
UI thread. The UI talks to `MonitorManager`, which forwards requests to the
worker through Qt signals (delivered across threads safely and in order) and
caches the latest results so any widget can read them at any time.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal, Slot

from nightlight.hardware.brightness import BrightnessBackend, MonitorInfo

log = logging.getLogger(__name__)


class _Worker(QObject):
    monitorsFound = Signal(list)  # list[MonitorInfo]
    brightnessRead = Signal(int, object)  # index, int | None
    error = Signal(str)

    def __init__(self, backend: BrightnessBackend) -> None:
        super().__init__()
        self._backend = backend

    @Slot()
    def refresh(self) -> None:
        try:
            monitors = self._backend.list_monitors()
        except Exception as exc:
            log.exception("Monitor detection failed")
            self.error.emit(f"Monitor detection failed: {exc}")
            monitors = []
        self.monitorsFound.emit(monitors)
        for monitor in monitors:
            if monitor.supported:
                self.brightnessRead.emit(monitor.index, self._backend.get_brightness(monitor.index))

    @Slot(int, int)
    def set_brightness(self, index: int, value: int) -> None:
        try:
            self._backend.set_brightness(index, value)
        except Exception as exc:
            log.exception("Setting brightness failed")
            self.error.emit(f"Could not set brightness on monitor {index + 1}: {exc}")


class MonitorManager(QObject):
    monitorsChanged = Signal(list)  # list[MonitorInfo]
    brightnessChanged = Signal(int, object)  # index, int | None
    error = Signal(str)

    # Requests to the worker thread.
    _refreshRequested = Signal()
    _setBrightnessRequested = Signal(int, int)

    def __init__(self, backend: BrightnessBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.monitors: list[MonitorInfo] = []
        self.brightness: dict[int, int | None] = {}

        self._thread = QThread(self)
        self._worker = _Worker(backend)
        self._worker.moveToThread(self._thread)
        self._refreshRequested.connect(self._worker.refresh)
        self._setBrightnessRequested.connect(self._worker.set_brightness)
        self._worker.monitorsFound.connect(self._on_monitors_found)
        self._worker.brightnessRead.connect(self._on_brightness_read)
        self._worker.error.connect(self.error)
        self._thread.start()

    def refresh(self) -> None:
        self._refreshRequested.emit()

    def set_brightness(self, index: int, value: int) -> None:
        self.brightness[index] = value
        self._setBrightnessRequested.emit(index, value)

    def shutdown(self) -> None:
        self._thread.quit()
        self._thread.wait()

    def _on_monitors_found(self, monitors: list[MonitorInfo]) -> None:
        self.monitors = monitors
        self.brightness = {}
        self.monitorsChanged.emit(monitors)

    def _on_brightness_read(self, index: int, value: int | None) -> None:
        self.brightness[index] = value
        self.brightnessChanged.emit(index, value)
