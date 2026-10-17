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
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from nightlight.hardware.brightness import BrightnessBackend, MonitorGoneError, MonitorInfo, with_unique_keys

log = logging.getLogger(__name__)

# Re-read brightness when the panel opens, in case the monitor's own buttons
# changed it, but not more often than this (Monitorian and Twinkle Tray do the same).
BRIGHTNESS_REREAD_S = 5.0
# At most one automatic re-detection per this long when a monitor stops answering.
GONE_REFRESH_INTERVAL_S = 30.0
# A DDC/CI call normally takes well under a second. Some monitors and drivers
# never return from one (Twinkle Tray added a "safety system" for monitors
# that hang during detection); say so instead of failing silently.
HANG_WARNING_S = 10.0
WATCHDOG_INTERVAL_MS = 2000
# How long quitting waits for a monitor call in progress. A hung call can't
# be interrupted, so after this the app exits without waiting for it.
SHUTDOWN_WAIT_MS = 3000


class _Worker(QObject):
    monitorsFound = Signal(list)  # list[MonitorInfo]
    brightnessRead = Signal(int, object)  # index, int | None
    refreshFinished = Signal()
    monitorGone = Signal()
    restoreDeferred = Signal(int)  # index: not restored now (asleep, or failed); try later
    error = Signal(str)

    def __init__(self, backend: BrightnessBackend, take_pending: Callable[[int], int | None]) -> None:
        super().__init__()
        self._backend = backend
        self._take_pending = take_pending
        self._monitors: list[MonitorInfo] = []
        # (what, since) of the backend call in progress; read by the watchdog
        # on the UI thread. A single attribute assignment, so no lock needed.
        self.busy: tuple[str, float] | None = None

    @contextmanager
    def _calling(self, what: str) -> Iterator[None]:
        self.busy = (what, time.monotonic())
        try:
            yield
        finally:
            self.busy = None

    def _name(self, index: int) -> str:
        return next((m.name for m in self._monitors if m.index == index), f"monitor {index + 1}")

    @Slot()
    def refresh(self) -> None:
        try:
            with self._calling("detecting monitors"):
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
                    with self._calling(f"reading the brightness of {monitor.name}"):
                        value = self._backend.get_brightness(monitor.index)
                    self.brightnessRead.emit(monitor.index, value)
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
            with self._calling(f"setting the brightness of {self._name(index)}"):
                self._backend.set_brightness(index, value)
        except MonitorGoneError:
            log.info("Monitor %d is gone; detecting monitors again", index + 1)
            self.monitorGone.emit()
        except Exception as exc:
            log.exception("Setting brightness failed")
            self.error.emit(f"Could not set brightness on monitor {index + 1}: {exc}")

    @Slot(int, int)
    def restore(self, index: int, value: int) -> None:
        """Set a remembered level, unprompted, so only if the monitor is awake:
        DDC/CI commands can wake a sleeping monitor, and hang some models'
        firmware until they are unplugged (Twinkle Tray's "monitor power
        state detection" exists for this). Monitors that can't tell are
        assumed awake."""
        name = self._name(index)
        try:
            if is_powered_on := getattr(self._backend, "is_powered_on", None):
                with self._calling(f"reading the power state of {name}"):
                    awake = is_powered_on(index)
                if awake is False:
                    log.info("%s is asleep; restoring its brightness later", name)
                    self.restoreDeferred.emit(index)
                    return
            with self._calling(f"restoring the brightness of {name}"):
                self._backend.set_brightness(index, value)
            self.brightnessRead.emit(index, value)
        except MonitorGoneError:
            log.info("%s is gone; detecting monitors again", name)
            self.restoreDeferred.emit(index)
            self.monitorGone.emit()
        except Exception:
            log.warning("Restoring the brightness of %s failed; trying again later", name, exc_info=True)
            self.restoreDeferred.emit(index)

    @Slot(bool)
    def set_software_dimming(self, enabled: bool) -> None:
        if set_dimming := getattr(self._backend, "set_software_dimming", None):
            with self._calling("changing software dimming"):
                set_dimming(enabled)

    @Slot()
    def close(self) -> None:
        if close := getattr(self._backend, "close", None):
            with self._calling("releasing monitor handles"):
                close()


class MonitorManager(QObject):
    monitorsChanged = Signal(list)  # list[MonitorInfo]
    # A monitor's brightness as last read or set: index, int | None.
    brightnessChanged = Signal(int, object)
    # Brightness was set by the user (a slider, or linked monitors): index, value.
    brightnessSet = Signal(int, int)
    # A restore_brightness() didn't happen (the monitor was asleep or failed): index.
    restoreDeferred = Signal(int)
    error = Signal(str)

    # Requests to the worker thread.
    _refreshRequested = Signal()
    _readRequested = Signal()
    _setBrightnessRequested = Signal(int)
    _restoreRequested = Signal(int, int)
    _softwareDimmingRequested = Signal(bool)
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
        self._warned_hang: tuple[str, float] | None = None
        self.hung = False  # a monitor call was still running when shutting down

        self._thread = QThread(self)
        self._worker = _Worker(backend, self._take_pending)
        self._worker.moveToThread(self._thread)
        self._refreshRequested.connect(self._worker.refresh)
        self._readRequested.connect(self._worker.read_brightness)
        self._setBrightnessRequested.connect(self._worker.set_brightness)
        self._restoreRequested.connect(self._worker.restore)
        self._softwareDimmingRequested.connect(self._worker.set_software_dimming)
        self._closeRequested.connect(self._worker.close)
        self._worker.monitorsFound.connect(self._on_monitors_found)
        self._worker.brightnessRead.connect(self._on_brightness_read)
        self._worker.refreshFinished.connect(self._on_refresh_finished)
        self._worker.monitorGone.connect(self._on_monitor_gone)
        self._worker.restoreDeferred.connect(self.restoreDeferred)
        self._worker.error.connect(self.error)
        self._thread.start()

        self._watchdog = QTimer(self, interval=WATCHDOG_INTERVAL_MS)
        self._watchdog.timeout.connect(self._check_hang)
        self._watchdog.start()

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
        # brightnessSet first: it tells BrightnessMemory the user picked this
        # level, before brightnessChanged could make a pending restore undo it.
        self.brightnessSet.emit(index, value)
        self.brightnessChanged.emit(index, value)
        with self._pending_lock:
            queued = index in self._pending
            self._pending[index] = value
        if not queued:
            self._setBrightnessRequested.emit(index)

    def set_software_dimming(self, enabled: bool) -> None:
        """Turn software dimming of displays without brightness control on or
        off, then detect monitors again so they're listed accordingly."""
        self._softwareDimmingRequested.emit(enabled)
        self.refresh()

    def restore_brightness(self, index: int, value: int) -> None:
        """Put back a remembered level if the monitor is awake (see
        _Worker.restore); brightnessChanged follows if it was set, and
        restoreDeferred if not."""
        self._restoreRequested.emit(index, value)

    def shutdown(self) -> None:
        self._watchdog.stop()
        self._closeRequested.emit()
        self._thread.quit()
        if not self._thread.wait(SHUTDOWN_WAIT_MS):
            busy = self._worker.busy
            log.warning("Quitting while still %s; a monitor or its driver isn't responding", busy[0] if busy else "busy")
            self.hung = True

    def _check_hang(self) -> None:
        busy = self._worker.busy
        if busy is None or busy == self._warned_hang or time.monotonic() - busy[1] < HANG_WARNING_S:
            return
        self._warned_hang = busy
        log.warning("Still %s after %.0f s: the display or its driver isn't responding over DDC/CI", busy[0], time.monotonic() - busy[1])
        self.error.emit(
            f"A display isn't responding (still {busy[0]}). If this keeps happening, turn DDC/CI off "
            "in that display's menu, or connect it without an adapter or dock."
        )

    def _take_pending(self, index: int) -> int | None:
        with self._pending_lock:
            return self._pending.pop(index, None)

    def monitor(self, index: int) -> MonitorInfo | None:
        return next((m for m in self.monitors if m.index == index), None)

    def _on_monitors_found(self, monitors: list[MonitorInfo]) -> None:
        monitors = with_unique_keys(monitors)
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
