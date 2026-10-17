"""Runs all backend calls on a background thread.

DDC/CI calls can take hundreds of milliseconds, so they must never run on the
UI thread. The UI talks to this worker only through Qt signals, which Qt
delivers across threads safely and in order.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

from nightlight.backends import DisplayBackend

log = logging.getLogger(__name__)


class DisplayWorker(QObject):
    monitorsFound = Signal(list)  # list[MonitorInfo]
    brightnessRead = Signal(int, object)  # index, int | None
    error = Signal(str)

    def __init__(self, backend: DisplayBackend) -> None:
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
