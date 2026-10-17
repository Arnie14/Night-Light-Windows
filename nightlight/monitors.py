# SPDX-License-Identifier: GPL-3.0-only
"""Runs all brightness backend calls on a background thread.

DDC/CI calls can take hundreds of milliseconds, so they must never run on the
UI thread. The UI talks to `MonitorManager`, which forwards requests to the
worker through Qt signals (delivered across threads safely and in order) and
caches the latest results so any widget can read them at any time.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from nightlight.hardware.brightness import BrightnessBackend, MonitorGoneError, MonitorInfo

log = logging.getLogger(__name__)

# Re-read brightness when the panel opens, in case the monitor's own buttons
# changed it, but not more often than this (Monitorian and Twinkle Tray do the same).
BRIGHTNESS_REREAD_S = 5.0
# At most one automatic re-detection per this long when a monitor stops answering.
GONE_REFRESH_INTERVAL_S = 30.0


class _Worker(QObject):
    monitorsFound = Signal(list)  # list[MonitorInfo]
    brightnessRead = Signal(int, object)  # index, int | None
    refreshFinished = Signal()
    monitorGone = Signal()
    error = Signal(str)

    def __init__(self, backend: BrightnessBackend, take_pending: Callable[[int], int | None]) -> None:
        super().__init__()
        self._backend = backend
        self._take_pending = take_pending
        self._monitors: list[MonitorInfo] = []

    @Slot()
    def refresh(self) -> None:
        try:
            self._monitors = self._backend.list_monitors()
        except Exception as exc:
            log.exception("Monitor detection failed")
            self.error.emit(f"Monitor detection failed: {exc}")
            self._monitors = []
        self.monitorsFound.emit(self._monitors)
        self.read_brightness()
        self.refreshFinished.emit()

    @Slot()
    def read_brightness(self) -> None:
        for monitor in self._monitors:
            if monitor.supported:
                try:
                    self.brightnessRead.emit(monitor.index, self._backend.get_brightness(monitor.index))
                except MonitorGoneError:
                    log.info("%s is gone; detecting monitors again", monitor.name)
                    self.monitorGone.emit()
                    return
                except Exception:
                    log.exception("Reading brightness failed")
                    self.brightnessRead.emit(monitor.index, None)

    @Slot(int)
    def set_brightness(self, index: int) -> None:
        # Only the latest value matters: while a slow DDC/CI write runs, newer
        # slider positions replace older ones instead of queueing up behind it.
        value = self._take_pending(index)
        if value is None:
            return
        try:
            self._backend.set_brightness(index, value)
        except MonitorGoneError:
            log.info("Monitor %d is gone; detecting monitors again", index + 1)
            self.monitorGone.emit()
        except Exception as exc:
            log.exception("Setting brightness failed")
            self.error.emit(f"Could not set brightness on monitor {index + 1}: {exc}")

    @Slot()
    def close(self) -> None:
        if close := getattr(self._backend, "close", None):
            close()


class MonitorManager(QObject):
    monitorsChanged = Signal(list)  # list[MonitorInfo]
    brightnessChanged = Signal(int, object)  # index, int | None
    error = Signal(str)

    # Requests to the worker thread.
    _refreshRequested = Signal()
    _readRequested = Signal()
    _setBrightnessRequested = Signal(int)
    _closeRequested = Signal()

    def __init__(self, backend: BrightnessBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.monitors: list[MonitorInfo] = []
        self.brightness: dict[int, int | None] = {}
        self._pending: dict[int, int] = {}  # index -> latest value not yet sent
        self._pending_lock = threading.Lock()
        self._refreshing = False
        self._refresh_again = False
        self._read_at = 0.0
        self._gone_at: float | None = None
        self._scheduled: list[QTimer] = []

        self._thread = QThread(self)
        self._worker = _Worker(backend, self._take_pending)
        self._worker.moveToThread(self._thread)
        self._refreshRequested.connect(self._worker.refresh)
        self._readRequested.connect(self._worker.read_brightness)
        self._setBrightnessRequested.connect(self._worker.set_brightness)
        self._closeRequested.connect(self._worker.close)
        self._worker.monitorsFound.connect(self._on_monitors_found)
        self._worker.brightnessRead.connect(self._on_brightness_read)
        self._worker.refreshFinished.connect(self._on_refresh_finished)
        self._worker.monitorGone.connect(self._on_monitor_gone)
        self._worker.error.connect(self.error)
        self._thread.start()

    def refresh(self) -> None:
        """Detect monitors again. Requests made while one runs are merged into one more."""
        if self._refreshing:
            self._refresh_again = True
            return
        self._refreshing = True
        self._read_at = time.monotonic()
        self._refreshRequested.emit()

    def refresh_later(self, delays_s: Iterable[float]) -> None:
        """Detect monitors after each delay, replacing any earlier schedule.

        Monitors often don't answer DDC/CI until a few seconds after being
        plugged in, waking up or the system starting, so both Monitorian and
        Twinkle Tray look again several times rather than once.
        """
        for timer in self._scheduled:
            timer.stop()
            timer.deleteLater()
        self._scheduled = []
        for delay in delays_s:
            timer = QTimer(self, singleShot=True, interval=int(delay * 1000))
            timer.timeout.connect(self.refresh)
            timer.start()
            self._scheduled.append(timer)

    def reread_brightness(self) -> None:
        """Read the current brightness again, unless it was read very recently."""
        if self._refreshing or time.monotonic() - self._read_at < BRIGHTNESS_REREAD_S:
            return
        self._read_at = time.monotonic()
        self._readRequested.emit()

    def set_brightness(self, index: int, value: int) -> None:
        self.brightness[index] = value
        with self._pending_lock:
            queued = index in self._pending
            self._pending[index] = value
        if not queued:
            self._setBrightnessRequested.emit(index)

    def shutdown(self) -> None:
        self._closeRequested.emit()
        self._thread.quit()
        self._thread.wait()

    def _take_pending(self, index: int) -> int | None:
        with self._pending_lock:
            return self._pending.pop(index, None)

    def _on_monitors_found(self, monitors: list[MonitorInfo]) -> None:
        self.monitors = monitors
        self.brightness = {}
        self.monitorsChanged.emit(monitors)

    def _on_brightness_read(self, index: int, value: int | None) -> None:
        self.brightness[index] = value
        self.brightnessChanged.emit(index, value)

    def _on_monitor_gone(self) -> None:
        # Rate-limited, so a monitor whose fresh handle fails straight away
        # can't keep detection running in a loop.
        now = time.monotonic()
        if self._gone_at is not None and now - self._gone_at < GONE_REFRESH_INTERVAL_S:
            return
        self._gone_at = now
        self.refresh()

    def _on_refresh_finished(self) -> None:
        self._refreshing = False
        if self._refresh_again:
            self._refresh_again = False
            self.refresh()
