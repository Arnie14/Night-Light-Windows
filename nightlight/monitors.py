# SPDX-License-Identifier: GPL-3.0-only
"""Runs all brightness backend calls on one background thread, one at a time.

DDC/CI calls take tens to hundreds of milliseconds, can fail for a moment,
and on some monitors never return. The design, drawn from Monitorian's and
Twinkle Tray's bug reports:

- One serialized queue (MonitorService). The UI thread only asks; a single
  worker thread does every backend call in turn. Repeated requests merge:
  a display's newest brightness replaces any older one not yet sent
  (Twinkle Tray's update throttle), and detection requests coalesce.
- Displays are addressed by their stable key (MonitorInfo.key), never by a
  list position the UI might hold from an older detection. Each detection
  is a new generation; a command is resolved against the current one when
  it runs, so it can't reach the wrong monitor after a re-detection, and a
  display that disappeared simply doesn't get it (Twinkle Tray drops queued
  commands from an older refresh generation for the same reason).
- Every call has a time limit (calls.CallRunner). A call that doesn't
  return is abandoned and its display left alone until it does; the other
  displays carry on. Nothing waits forever, quitting included.
- Sleep: while the PC is suspending or the displays are off, nothing is
  sent (DDC/CI can wake a sleeping monitor or hang its firmware). After
  waking, commands wait for a fresh detection, since old handles are stale.
- Each display has a desired level (what the user or schedule wants), a
  commanded one (what it last accepted) and an observed one (what it last
  reported), so the UI shows the truth and a write that is acknowledged but
  ignored is noticed.

MonitorService holds that logic in plain Python, so tests can drive it step
by step and interleave events deterministically. MonitorManager runs it on a
thread and turns its events into Qt signals and per-display state.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

from nightlight.hardware.brightness import BrightnessBackend, MonitorGoneError, MonitorInfo, with_unique_keys
from nightlight.hardware.calls import CallRunner, CallTimeout

log = logging.getLogger(__name__)

# Time limits per call. Healthy DDC/CI answers in well under a second, even
# with retries; detection probes all monitors side by side.
DETECT_TIMEOUT_S = 20.0
READ_TIMEOUT_S = 5.0
WRITE_TIMEOUT_S = 5.0
POWER_TIMEOUT_S = 3.0
CLOSE_TIMEOUT_S = 2.0
# Detections closer together than this are merged (unless forced).
MIN_DETECT_INTERVAL_S = 1.0
# At most one automatic re-detection per this long when a monitor stops answering.
GONE_REFRESH_INTERVAL_S = 30.0
# Re-read brightness when the panel opens, in case the monitor's own buttons
# changed it, but not more often than this (Monitorian and Twinkle Tray do the same).
BRIGHTNESS_REREAD_S = 5.0
# Read a display back this long after the last change to it, to see whether it obeyed.
VERIFY_DELAY_MS = 1500
VERIFY_TOLERANCE = 3  # percent; monitors and WMI levels round
# For a moment after a change, some displays still report the old level (a
# laptop panel through WMI does: Windows eases the backlight to it). A reading
# that soon is not taken as the truth; the verification read decides.
SETTLE_S = 1.0
# How long quitting waits for the call in progress.
SHUTDOWN_WAIT_S = 3.0
# Changes that aren't a slider drag (the brightness schedule, the command
# line, idle dimming) fade in a few steps rather than jump; DDC/CI is slow,
# so few steps. A newer change to the same display cuts a fade short.
FADE_STEPS = 6
FADE_STEP_S = 0.08

# Who asked for a level. USER and SCHEDULE are choices (remembered);
# RESTORE and IDLE are unprompted (checked for standby first, not remembered).
USER, SCHEDULE, RESTORE, IDLE = "user", "schedule", "restore", "idle"


# --- Service: the worker side -------------------------------------------------


@dataclass(frozen=True)
class Write:
    key: str
    value: int
    source: str = USER
    # Unprompted changes (restore, schedule) check the monitor is awake first.
    require_awake: bool = False
    fade: bool = False  # from the current level, in FADE_STEPS


# Events from the service to its owner.
@dataclass(frozen=True)
class DetectionStarted:
    pass


@dataclass(frozen=True)
class Refreshed:
    """A detection and its first readings are done."""


@dataclass(frozen=True)
class Detected:
    generation: int
    monitors: list[MonitorInfo]


@dataclass(frozen=True)
class DetectionStuck:
    what: str


@dataclass(frozen=True)
class Observed:
    key: str
    value: int | None


@dataclass(frozen=True)
class Commanded:
    key: str
    value: int
    source: str


@dataclass(frozen=True)
class Deferred:
    """A write that wasn't sent, and why: "asleep", "gone", "not responding",
    "not connected", "no control", "failed" or "superseded"."""

    key: str
    value: int
    source: str
    why: str


@dataclass(frozen=True)
class Failed:
    key: str
    message: str
    timeout: bool = False


@dataclass(frozen=True)
class Gone:
    key: str


@dataclass(frozen=True)
class Error:
    message: str


def _summary(result: object) -> str:
    """A backend call's result, short enough for the log."""
    if isinstance(result, list):
        return f"{len(result)} item(s)"
    return "done" if result is None else repr(result)


class MonitorService:
    """The single serialized queue of backend calls (see the module docstring).

    Requests may come from any thread; step() and run() belong to the one
    worker thread. `emit` receives the events above, on the worker thread.
    """

    def __init__(self, backend: BrightnessBackend, emit: Callable[[object], None], runner=None, clock: Callable[[], float] = time.monotonic) -> None:
        self._backend = backend
        self._emit = emit
        self._runner = runner or CallRunner("monitor-calls")
        self._clock = clock
        self._cond = threading.Condition()
        # Pending work, guarded by _cond.
        self._detect = False
        self._detect_forced = False
        self._writes: dict[str, Write] = {}
        self._reads: set[str] | None = None  # None: nothing to read
        self._dimming: bool | None = None
        self._retry = False  # displays skipped after a crash (safe mode) are to be probed again
        self._reads_after_detection = False  # the next reads finish a detection (Refreshed)
        self._closing = False
        self._suspended = False
        self._awaiting_detection = True  # nothing to address until the first detection
        # Worker state.
        self._by_key: dict[str, MonitorInfo] = {}
        self.generation = 0
        self._detected_at: float | None = None
        self._gone_at: float | None = None
        self._detection_stuck = False
        self._detect_failures = 0  # in a row
        self._hung: dict[str, str] = {}  # key -> the call stuck in it; guarded by _cond
        self.busy: str | None = None  # the call in progress, for the log and shutdown

    # Requests (any thread) ------------------------------------------------

    def request_detect(self, force: bool = False) -> None:
        with self._cond:
            self._detect = True
            self._detect_forced |= force
            self._cond.notify()

    def request_read(self, keys: Iterable[str] | None = None) -> None:
        """Read the given displays' brightness again (all if None)."""
        with self._cond:
            wanted = set(self._by_key) if keys is None else set(keys)
            self._reads = (self._reads or set()) | wanted
            self._cond.notify()

    def request_write(self, write: Write) -> None:
        with self._cond:
            pending = self._writes.get(write.key)
            if pending is not None and pending.source == USER and write.source != USER:
                # A level the user just picked wins over an unprompted one.
                self._emit(Deferred(write.key, write.value, write.source, "superseded"))
                return
            self._writes.pop(write.key, None)
            self._writes[write.key] = write  # newest last: written in the order they were last changed
            self._cond.notify()

    def request_software_dimming(self, enabled: bool) -> None:
        with self._cond:
            self._dimming = enabled
            self._cond.notify()

    def request_retry(self) -> None:
        """Probe displays skipped after a crash again, then detect (the panel's ⟳)."""
        with self._cond:
            self._retry = True
            self._detect = self._detect_forced = True
            self._cond.notify()

    def write_pending(self, key: str) -> bool:
        """Whether a newer change for this display is waiting (a fade stops for it)."""
        with self._cond:
            return key in self._writes

    def suspend(self) -> None:
        """The PC is going to sleep or the displays turned off: send nothing until resume()."""
        with self._cond:
            self._suspended = True

    def resume(self) -> None:
        """Awake again. Handles from before are stale, so commands wait for the
        next detection (which the owner schedules)."""
        with self._cond:
            self._suspended = False
            self._awaiting_detection = True
            self._cond.notify()

    def close(self) -> None:
        with self._cond:
            self._closing = True
            self._cond.notify()

    @property
    def stuck(self) -> list[str]:
        return list(self._runner.stuck)

    @property
    def monitors(self) -> list[MonitorInfo]:
        """The displays found at the last detection."""
        return list(self._by_key.values())

    # Worker ------------------------------------------------------------------

    def run(self) -> None:
        while True:
            try:
                if self.step(block=True) is None:
                    return
            except Exception:
                # step() catches what the work raises; this is for anything
                # else. The thread must not die: brightness control would stop
                # for the rest of the session without a word.
                log.exception("The monitor service hit an unexpected error; carrying on")
                time.sleep(0.5)

    def step(self, block: bool = False) -> str | None:
        """Do one piece of pending work and say which ("detect", "write",
        "read", "dimming"); "" if there was none, None once closed."""
        with self._cond:
            while True:
                if self._closing:
                    break
                work = self._take_work()
                if work is not None or not block:
                    break
                self._cond.wait(self._wait_time())
        if self._closing:
            self._close_backend()
            return None
        if work is None:
            return ""
        kind, payload = work
        try:
            getattr(self, f"_do_{kind}")(payload)
        except Exception:
            log.exception("Monitor service step %s failed", kind)
        finally:
            self.busy = None
        return kind

    def run_pending(self, limit: int = 100) -> list[str]:
        """Do all pending work now, on this thread (tests, and --report)."""
        done = []
        while len(done) < limit and (kind := self.step()):
            done.append(kind)
        return done

    def _take_work(self):
        """The next piece of work, by priority; called with _cond held."""
        if self._dimming is not None:
            enabled, self._dimming = self._dimming, None
            return "dimming", enabled
        if self._retry and not self._suspended:
            self._retry = False
            return "retry", None
        if self._detect and not self._suspended and not self._detection_stuck and self._detection_due():
            self._detect = self._detect_forced = False
            return "detect", None
        if self._suspended or self._awaiting_detection:
            return None
        if self._writes:
            key = next(iter(self._writes))
            return "write", self._writes.pop(key)
        if self._reads:
            reads, self._reads = self._reads, None
            return "read", sorted(reads)
        return None

    def _detect_interval(self) -> float:
        """Detections are at least this far apart; longer after failed ones (1, 2, 4… up to 64 s)."""
        return MIN_DETECT_INTERVAL_S * 2 ** min(self._detect_failures, 6)

    def _detection_due(self) -> bool:
        return self._detect_forced or self._detected_at is None or self._clock() - self._detected_at >= self._detect_interval()

    def _wait_time(self) -> float | None:
        if self._detect and self._detected_at is not None and not self._detection_due():
            return max(0.01, self._detect_interval() - (self._clock() - self._detected_at))
        return None

    def _call(self, fn, timeout: float, what: str, key: str | None = None):
        """A backend call with a time limit. On timeout, `key`'s display is
        left alone until the call returns."""
        self.busy = what
        on_late = (lambda: self._call_returned(key)) if key else None
        started = time.perf_counter()
        try:
            result = self._runner.call(fn, timeout, what, on_late=on_late)
        except CallTimeout:
            if key:
                with self._cond:
                    self._hung[key] = what
                log.info("%s [%s]: left alone until that call returns; the other displays carry on", what, key)
            raise  # the runner logs the time-out itself
        except Exception as exc:
            # Every backend call is traced at debug level: what, which display,
            # how long, and what came back, so a report isolates the call
            # and the display that misbehaved.
            log.debug("%s%s failed after %.0f ms: %s: %s", what, f" [{key}]" if key else "",
                      1000 * (time.perf_counter() - started), type(exc).__name__, exc)
            raise
        log.debug("%s%s: %s in %.0f ms", what, f" [{key}]" if key else "", _summary(result), 1000 * (time.perf_counter() - started))
        return result

    def _call_returned(self, key: str) -> None:
        with self._cond:
            if self._hung.pop(key, None) is not None:
                self._detect = True  # check on it again
                self._cond.notify()

    def _hung_call(self, key: str) -> str | None:
        with self._cond:
            return self._hung.get(key)

    def _do_dimming(self, enabled: bool) -> None:
        if set_dimming := getattr(self._backend, "set_software_dimming", None):
            try:
                self._call(lambda: set_dimming(enabled), WRITE_TIMEOUT_S, "changing software dimming")
            except Exception as exc:
                log.warning("Changing software dimming failed: %s", exc)

    def _do_retry(self, _payload) -> None:
        if retry := getattr(self._backend, "retry_skipped", None):
            try:
                self._call(retry, WRITE_TIMEOUT_S, "clearing skipped displays")
            except Exception as exc:
                log.warning("Clearing skipped displays failed: %s", exc)

    def _do_detect(self, _payload) -> None:
        self._emit(DetectionStarted())
        try:
            monitors = self._call(self._backend.list_monitors, DETECT_TIMEOUT_S, "detecting displays")
        except CallTimeout as exc:
            # The backend may still be rebuilding its list on the abandoned
            # thread, so no index can be trusted until it returns.
            self._detection_stuck = True
            self._by_key = {}
            self._runner_late_detection()
            self._emit(DetectionStuck(exc.what))
            return
        except Exception as exc:
            # Windows' display APIs can fail for a moment (e.g. right after
            # waking). That isn't "no displays": keep the ones found before
            # (after waking, changes keep waiting for a detection that works)
            # and try again, less often each time.
            self._detect_failures += 1
            self._detected_at = self._clock()
            if self._detect_failures == 1:
                log.exception("Monitor detection failed; keeping the displays found before and trying again")
                self._emit(Error(f"Monitor detection failed: {exc}"))
            else:
                (log.warning if self._detect_failures <= 3 else log.debug)(
                    "Monitor detection failed again (%d in a row; next try in %g s): %s", self._detect_failures, self._detect_interval(), exc)
            with self._cond:
                self._detect = True
            return
        if self._detect_failures:
            log.info("Monitor detection works again, after %d failure(s)", self._detect_failures)
            self._detect_failures = 0
        self._detected_at = self._clock()
        monitors = with_unique_keys(monitors)
        with self._cond:
            hung = dict(self._hung)
            self._awaiting_detection = False
        # A display with a call still stuck in it stays unreachable, whatever the backend says.
        monitors = [
            replace(m, supported=False, reason="Not responding", status="not responding", detail=f"Still waiting for {hung[m.key]} to return.")
            if m.key in hung and m.supported else m
            for m in monitors
        ]
        self._by_key = {m.key: m for m in monitors}
        self.generation += 1
        self._emit(Detected(self.generation, monitors))
        supported = {m.key for m in monitors if m.supported}
        if not supported:
            self._emit(Refreshed())
            return
        with self._cond:
            self._reads = (self._reads or set()) | supported
            self._reads_after_detection = True

    def _runner_late_detection(self) -> None:
        """Detect again once the stuck detection returns."""
        def watch() -> None:
            while self._runner.stuck and not self._closing:
                time.sleep(0.2)
            with self._cond:
                self._detection_stuck = False
                self._detect = self._detect_forced = True
                self._cond.notify()

        threading.Thread(target=watch, name="watch stuck detection", daemon=True).start()

    def _target(self, write: Write) -> MonitorInfo | None:
        """The display a write is for, in this generation; None (and why) if it can't take it."""
        info = self._by_key.get(write.key)
        why = None
        if info is None:
            why = "not connected"
        elif self._hung_call(write.key):
            why = "not responding"
        elif not info.supported:
            why = "no control"
        if why:
            log.info("Not setting %s to %d%% (%s): %s", write.key, write.value, write.source, why)
            self._emit(Deferred(write.key, write.value, write.source, why))
            return None
        return info

    def _do_write(self, write: Write) -> None:
        info = self._target(write)
        if info is None:
            return
        try:
            if write.require_awake and (is_powered_on := getattr(self._backend, "is_powered_on", None)):
                awake = self._call(lambda: is_powered_on(info.index), POWER_TIMEOUT_S, f"reading the power state of {info.name}", info.key)
                if awake is False:
                    log.info("%s is in standby; setting its brightness later", info.name)
                    self._emit(Deferred(write.key, write.value, write.source, "asleep"))
                    return
                if self._held_back():  # the PC went to sleep during that check
                    with self._cond:
                        self._writes.setdefault(write.key, write)
                    return
            if not self._set_level(info, write):
                return  # a newer change for this display came while fading; it goes next
        except CallTimeout as exc:
            self._emit(Failed(write.key, str(exc), timeout=True))
            self._emit(Deferred(write.key, write.value, write.source, "not responding"))
            self._emit(Error(f"{info.name} isn't responding (still {exc.what}). If this keeps happening, turn DDC/CI off in its menu, "
                             "or connect it without an adapter or dock."))
            return
        except MonitorGoneError:
            self._gone(info)
            self._emit(Deferred(write.key, write.value, write.source, "gone"))
            return
        except Exception as exc:
            log.warning("Setting the brightness of %s failed: %s", info.name, exc)
            self._emit(Failed(write.key, str(exc)))
            self._emit(Deferred(write.key, write.value, write.source, "failed"))
            return
        self._emit(Commanded(write.key, write.value, write.source))

    def _set_level(self, info: MonitorInfo, write: Write) -> bool:
        """Set a display's level, fading if asked. False if a newer change for
        it arrived mid-fade (the fade stops where it is; the newer one runs next)."""
        steps = [write.value]
        # Windows eases a laptop panel's backlight to each level set through
        # WMI by itself; fading on top of that makes it bounce.
        if write.fade and info.channel != "wmi":
            start = self._call(lambda: self._backend.get_brightness(info.index), READ_TIMEOUT_S, f"reading the brightness of {info.name}", info.key)
            if start is not None and start != write.value:
                steps = [round(start + (write.value - start) * i / FADE_STEPS) for i in range(1, FADE_STEPS + 1)]
        for i, step in enumerate(steps):
            if i:
                if self.write_pending(write.key):
                    log.debug("A newer change for %s came while fading; stopping at %d%%", info.name, steps[i - 1])
                    return False
                time.sleep(FADE_STEP_S)
                if self._held_back():
                    # Sleep came mid-fade: the level still goes out, after
                    # waking and detecting again (unless a newer one replaces it).
                    with self._cond:
                        self._writes.setdefault(write.key, replace(write, fade=False))
                    log.debug("Sleep came while fading %s; setting %d%% after waking", info.name, write.value)
                    return False
            self._call(lambda step=step: self._backend.set_brightness(info.index, step), WRITE_TIMEOUT_S,
                       f"setting the brightness of {info.name}", info.key)
            if step != write.value:
                self._emit(Commanded(write.key, step, write.source))  # the fade so far, for the slider
        return True

    def _held_back(self) -> bool:
        """Asleep, or awake but not detected again yet: nothing may be sent now.
        Work that spans several calls checks this between them."""
        with self._cond:
            return self._suspended or self._awaiting_detection or self._closing

    def _do_read(self, keys: list[str]) -> None:
        for key in keys:
            if self._held_back():
                # Sleep came while reading several displays: the rest would go
                # to sleeping monitors, or after waking to stale handles. The
                # detection after waking reads them all again.
                log.debug("Not reading the remaining displays now (sleep, or not detected again yet)")
                break
            info = self._by_key.get(key)
            if info is None or not info.supported or self._hung_call(key):
                continue
            try:
                value = self._call(lambda: self._backend.get_brightness(info.index), READ_TIMEOUT_S, f"reading the brightness of {info.name}", key)
            except CallTimeout as exc:
                self._emit(Failed(key, str(exc), timeout=True))
                continue  # the others are read regardless
            except MonitorGoneError:
                self._gone(info)
                continue
            except Exception:
                log.exception("Reading the brightness of %s failed", info.name)
                value = None
            self._emit(Observed(key, value))
        with self._cond:
            finished, self._reads_after_detection = self._reads_after_detection, False
        if finished:
            self._emit(Refreshed())

    def _gone(self, info: MonitorInfo) -> None:
        log.info("%s is gone; detecting displays again", info.name)
        self._emit(Gone(info.key))
        now = self._clock()
        # Rate-limited, so a monitor whose fresh handle fails straight away
        # can't keep detection running in a loop.
        if self._gone_at is None or now - self._gone_at >= GONE_REFRESH_INTERVAL_S:
            self._gone_at = now
            self.request_detect(force=True)

    def _close_backend(self) -> None:
        close = getattr(self._backend, "close", None)
        release = getattr(self._backend, "release_thread_resources", None)  # e.g. WMI's COM objects

        def finish() -> None:
            if close is not None:
                close()
            if release is not None:
                release()

        if self._runner.stuck:
            log.warning("Not releasing monitor handles: %s", "; ".join(self._runner.stuck))
        else:
            try:
                self._call(finish, CLOSE_TIMEOUT_S, "releasing monitor handles")
            except Exception as exc:
                log.warning("Releasing monitor handles failed: %s", exc)
        self._runner.close()


# --- Manager: the UI side -------------------------------------------------------


class Status(StrEnum):
    """What a display's slider can do now; anything but OK disables it."""

    DETECTING = "detecting"
    READING = "reading"
    OK = "ok"
    UNREADABLE = "unreadable"
    UNSUPPORTED = "unsupported"
    NOT_RESPONDING = "not responding"
    RETRYING = "retrying"
    ASLEEP = "asleep"
    PAUSED = "paused"
    GONE = "gone"
    FAILING = "failing"


# Short explanations shown under a disabled slider, and the longer detail.
STATUS_TEXT = {
    Status.DETECTING: ("Checking this display…", ""),
    Status.READING: ("Reading its brightness…", ""),
    Status.UNREADABLE: ("Couldn't read its brightness", "It is read again when the panel opens, and at the next detection."),
    Status.ASLEEP: ("In standby", "The display says it is in standby, so Winlight waits until it is on before changing it."),
    Status.PAUSED: ("Paused while asleep or the screens are off", "Nothing is sent to displays while the PC sleeps or the screens are off."),
    Status.GONE: ("Disconnected", "It stopped answering as if unplugged; Winlight is detecting displays again."),
    Status.FAILING: ("Changes aren't working", "The last changes failed. Winlight checks the display again shortly."),
    Status.NOT_RESPONDING: ("Not responding", "A call to this display didn't return. Winlight leaves it alone until it does."),
}
_FROM_INFO_STATUS = {
    "unsupported": Status.UNSUPPORTED,
    "skipped": Status.UNSUPPORTED,  # safe mode, after a crash; info.reason says so
    "not responding": Status.NOT_RESPONDING,
    "retrying": Status.RETRYING,
}


@dataclass
class DisplayState:
    info: MonitorInfo
    status: Status = Status.READING
    desired: int | None = None  # the level wanted and not yet confirmed
    desired_source: str = ""
    commanded: int | None = None  # the level the display last accepted
    observed: int | None = None  # the level it last reported
    observed_at: float | None = None
    message: str = ""  # short, for under the slider
    detail: str = ""
    failures: int = 0
    verify: bool = field(default=False, repr=False)
    commanded_at: float | None = field(default=None, repr=False)

    @property
    def key(self) -> str:
        return self.info.key

    @property
    def controllable(self) -> bool:
        return self.status is Status.OK

    @property
    def value(self) -> int | None:
        """What to show: the level being set, else the last one read or set."""
        if self.desired is not None:
            return self.desired
        return self.observed if self.observed is not None else self.commanded


class _Bridge(QObject):
    """Carries service events from the worker thread to the UI thread."""

    event = Signal(object)


class MonitorManager(QObject):
    monitorsChanged = Signal(list)  # list[MonitorInfo]
    displayChanged = Signal(str)  # key: its DisplayState changed
    # A level was chosen by the user or the schedule (key, value, source):
    # what BrightnessMemory remembers.
    brightnessSet = Signal(str, int, str)
    # A level set outside the app (key, value): the laptop's brightness keys,
    # Windows' slider, battery saver. Not remembered as a choice (Windows
    # changes it by itself too), but nothing older is put back over it.
    changedElsewhere = Signal(str, int)
    # A reading (key, value or None), the first after detection included.
    observed = Signal(str, object)
    # An unprompted change (restore or schedule) wasn't made: key, source.
    restoreDeferred = Signal(str, str)
    refreshStarted = Signal()
    refreshed = Signal()  # a detection finished, its first readings included
    error = Signal(str)

    def __init__(self, backend: BrightnessBackend, parent: QObject | None = None, runner=None) -> None:
        super().__init__(parent)
        self.monitors: list[MonitorInfo] = []
        self.states: dict[str, DisplayState] = {}
        self.generation = 0
        self.hung = False  # calls were still stuck when shutting down
        self._read_at = 0.0
        self._paused = False
        self._scheduled: list[tuple[float, QTimer]] = []  # (when due, timer)
        self._written_at: dict[str, float] = {}  # key -> when the app last changed it
        self._verify_keys: set[str] = set()
        self._warned_ignored: set[str] = set()
        self._verify_timer = QTimer(self, singleShot=True, interval=VERIFY_DELAY_MS)
        self._verify_timer.timeout.connect(self._verify)

        self._bridge = _Bridge(self)
        self._bridge.event.connect(self._on_event)
        self._service = MonitorService(backend, self._bridge.event.emit, runner)
        self._thread = threading.Thread(target=self._service.run, name="monitor-service", daemon=True)
        self._thread.start()

    # Queries ---------------------------------------------------------------------

    def state(self, key: str) -> DisplayState | None:
        return self.states.get(key)

    def monitor(self, key: str) -> MonitorInfo | None:
        state = self.states.get(key)
        return state.info if state else None

    @property
    def stuck(self) -> list[str]:
        return self._service.stuck

    @property
    def paused(self) -> bool:
        """Suspended or displays off: nothing is sent to displays."""
        return self._paused

    def value(self, key: str) -> int | None:
        """A display's level as best known (being set, else last read or set)."""
        state = self.states.get(key)
        return state.value if state is not None else None

    def recently_written(self, key: str, seconds: float) -> bool:
        """Whether the app itself changed this display's brightness lately, to
        tell its own changes from the keys or Windows' slider."""
        return time.monotonic() - self._written_at.get(key, -1e9) < seconds

    # Requests --------------------------------------------------------------------

    def refresh(self, force: bool = False) -> None:
        """Detect displays again. Requests made close together are merged."""
        self._read_at = time.monotonic()
        self._service.request_detect(force)

    def retry(self) -> None:
        """Detect again, including displays skipped after they crashed or hung
        the app (the panel's ⟳ button and `winlight-cli --refresh`)."""
        self._read_at = time.monotonic()
        self._service.request_retry()

    def refresh_later(self, delays_s: Iterable[float]) -> None:
        """Detect displays after each delay.

        Monitors often don't answer DDC/CI until a few seconds after being
        plugged in, waking up or the system starting, so both Monitorian and
        Twinkle Tray look again several times rather than once. Schedules add
        up (a display change soon after waking keeps the wake's long
        schedule); a detection already due within a second of a new one
        covers it.
        """
        now = time.monotonic()
        self._scheduled = [(due, timer) for due, timer in self._scheduled if timer.isActive()]
        for delay in delays_s:
            due = now + delay
            if any(abs(due - other) < 1.0 for other, _ in self._scheduled):
                continue
            timer = QTimer(self, singleShot=True, interval=int(delay * 1000))
            timer.timeout.connect(self.refresh)
            timer.start()
            self._scheduled.append((due, timer))

    def cancel_scheduled(self) -> None:
        """Drop detections scheduled by refresh_later."""
        for _due, timer in self._scheduled:
            timer.stop()
            timer.deleteLater()
        self._scheduled = []

    def reread_brightness(self) -> None:
        """Read the current brightness again, unless it was read very recently."""
        if time.monotonic() - self._read_at < BRIGHTNESS_REREAD_S:
            return
        self._read_at = time.monotonic()
        self._service.request_read()

    def read(self, key: str) -> None:
        """Read one display's brightness again (`observed` follows)."""
        self._service.request_read([key])

    def note_level(self, key: str, value: int) -> None:
        """A level set outside the app, learnt without reading the display
        (Windows reported the laptop panel's): handled like a reading."""
        self._on_Observed(Observed(key, value))
        self.changedElsewhere.emit(key, value)

    def set_brightness(self, key: str, value: int, source: str = USER, fade: bool = False) -> None:
        """Set a display's level, chosen by the user or the schedule."""
        state = self.states.get(key)
        if state is None:
            log.debug("Ignoring a brightness for %s, which isn't connected", key)
            return
        value = max(state.info.minimum, min(100, value))
        state.desired, state.desired_source = value, source
        self._written_at[key] = time.monotonic()
        # brightnessSet first: it tells BrightnessMemory this level was
        # chosen, before anything else could make a pending restore undo it.
        self.brightnessSet.emit(key, value, source)
        self.displayChanged.emit(key)
        self._service.request_write(Write(key, value, source, require_awake=source != USER, fade=fade))

    def fade_brightness(self, key: str, value: int) -> None:
        """Like set_brightness, the user's choice (the command line), but fading."""
        self.set_brightness(key, value, USER, fade=True)

    def restore_brightness(self, key: str, value: int, fade: bool = False, source: str = RESTORE) -> None:
        """Set a level the user didn't just choose (remembered, or idle
        dimming), only if the monitor is awake: unprompted DDC/CI can wake a
        sleeping monitor or hang some models' firmware (Twinkle Tray's
        "monitor power state detection" exists for this). It doesn't count as
        a choice (no brightnessSet); restoreDeferred follows if it doesn't happen."""
        state = self.states.get(key)
        if state is not None and not (state.desired is not None and state.desired_source == USER):
            state.desired, state.desired_source = max(state.info.minimum, min(100, value)), source
            self.displayChanged.emit(key)
        self._written_at[key] = time.monotonic()
        self._service.request_write(Write(key, value, source, require_awake=True, fade=fade))

    def set_software_dimming(self, enabled: bool) -> None:
        """Turn software dimming of displays without brightness control on or
        off, then detect monitors again so they're listed accordingly."""
        self._service.request_software_dimming(enabled)
        self.refresh(force=True)

    def suspend(self) -> None:
        """The PC is about to sleep or the displays turned off: stop sending."""
        if self._paused:
            return
        self._paused = True
        pending = sum(1 for state in self.states.values() if state.desired is not None)
        log.info("Display control paused (sleep or displays off)%s", f"; {pending} change(s) wait" if pending else "")
        self._service.suspend()
        self.cancel_scheduled()
        for state in self.states.values():
            if state.status in (Status.OK, Status.READING):
                self._set_status(state, Status.PAUSED)

    def resume(self, rescan_after_s: Iterable[float] = ()) -> None:
        """Awake again: what was read before may be stale, and handles too.
        Detection follows at the given delays (or as the caller schedules it)."""
        rescan_after_s = tuple(rescan_after_s)
        when = f" (at {', '.join(f'{d:g}' for d in rescan_after_s)} s)" if rescan_after_s else ""
        log.info("Display control resumed; changes wait for the next detection%s", when)
        self._paused = False
        self._service.resume()
        for state in self.states.values():
            state.observed_at = None
            if state.status in (Status.OK, Status.PAUSED, Status.READING, Status.ASLEEP):
                self._set_status(state, Status.DETECTING)
        self.refresh_later(rescan_after_s)

    def shutdown(self) -> None:
        for _due, timer in self._scheduled:
            timer.stop()
        self._verify_timer.stop()
        self._service.close()
        self._thread.join(SHUTDOWN_WAIT_S)
        if self._thread.is_alive() or self._service.stuck:
            busy = self._service.busy or "; ".join(self._service.stuck) or "busy"
            log.warning("Quitting while still %s; a monitor or its driver isn't responding", busy)
            self.hung = True

    # Events from the service ---------------------------------------------------

    def _on_event(self, event: object) -> None:
        handler = getattr(self, f"_on_{type(event).__name__}", None)
        if handler is not None:
            handler(event)

    def _on_DetectionStarted(self, _event: DetectionStarted) -> None:
        self.refreshStarted.emit()

    def _on_Refreshed(self, _event: Refreshed) -> None:
        self.refreshed.emit()

    def _on_Detected(self, event: Detected) -> None:
        self.generation = event.generation
        old = self.states
        states: dict[str, DisplayState] = {}
        for info in event.monitors:
            previous = old.get(info.key)
            # The same display (by identity) keeps what is known about it.
            state = DisplayState(info) if previous is None else replace(previous, info=info, failures=0, message="", detail="", verify=False)
            if info.supported:
                state.status = Status.PAUSED if self._paused else Status.READING
            else:
                state.status = _FROM_INFO_STATUS.get(info.status, Status.UNSUPPORTED)
                state.message, state.detail = info.reason, info.detail
                state.desired = None
            states[info.key] = state
        self.states = states
        self.monitors = event.monitors
        self.monitorsChanged.emit(event.monitors)

    def _on_DetectionStuck(self, event: DetectionStuck) -> None:
        for state in self.states.values():
            state.status, state.message = Status.NOT_RESPONDING, "Detection is stuck"
            state.detail = f"Still {event.what}; a display or driver isn't responding. Winlight tries again once it returns."
            self.displayChanged.emit(state.key)
        self.error.emit(f"A display isn't responding (still {event.what}). If this keeps happening, turn DDC/CI off "
                        "in that display's menu, or connect it without an adapter or dock.")

    def _on_Observed(self, event: Observed) -> None:
        state = self.states.get(event.key)
        if state is None:
            return
        now, value = time.monotonic(), event.value
        settling = (
            value is not None and state.commanded is not None and state.commanded_at is not None
            and now - state.commanded_at < SETTLE_S and abs(value - state.commanded) > VERIFY_TOLERANCE
        )
        if settling:
            log.debug("%s still reports %d%% just after accepting %d%%; checking again once it has settled",
                      state.info.name, value, state.commanded)
            value = state.commanded
            self._verify_keys.add(event.key)
            self._verify_timer.start()
        state.observed, state.observed_at = value, now
        if state.status in (Status.READING, Status.OK, Status.UNREADABLE, Status.DETECTING, Status.FAILING):
            self._set_status(state, Status.OK if value is not None else Status.UNREADABLE, emit=False)
        if state.verify and not settling and value is not None and state.commanded is not None and state.desired is None:
            state.verify = False
            if abs(value - state.commanded) > VERIFY_TOLERANCE and state.key not in self._warned_ignored:
                self._warned_ignored.add(state.key)
                log.warning("%s accepted %d%% but reports %d%%; it may ignore DDC/CI changes in its current mode",
                            state.info.name, state.commanded, value)
        self.observed.emit(event.key, value)
        self.displayChanged.emit(event.key)

    def _on_Commanded(self, event: Commanded) -> None:
        state = self.states.get(event.key)
        if state is None:
            return
        state.commanded, state.failures, state.message = event.value, 0, ""
        state.commanded_at = time.monotonic()
        state.observed = event.value  # until read back
        if state.desired == event.value:
            state.desired = None
        if state.status in (Status.FAILING, Status.ASLEEP, Status.READING, Status.UNREADABLE):
            self._set_status(state, Status.OK, emit=False)
        state.verify = True
        self._verify_keys.add(event.key)
        self._verify_timer.start()
        self.displayChanged.emit(event.key)

    def _on_Deferred(self, event: Deferred) -> None:
        state = self.states.get(event.key)
        if state is not None:
            if state.desired == event.value:
                state.desired = None
            if event.why == "asleep":
                self._set_status(state, Status.ASLEEP, emit=False)
            self.displayChanged.emit(event.key)
        if event.source in (RESTORE, SCHEDULE) and event.why != "superseded":
            self.restoreDeferred.emit(event.key, event.source)

    def _on_Failed(self, event: Failed) -> None:
        state = self.states.get(event.key)
        if state is None:
            return
        state.failures += 1
        if event.timeout:
            self._set_status(state, Status.NOT_RESPONDING, emit=False)
            self.refresh_later((10, 30))  # see whether it recovers once the call returns
        elif state.failures >= 2:
            self._set_status(state, Status.FAILING, emit=False)
            self.refresh_later((5,))
        else:
            state.message, state.detail = "The last change failed", event.message
        self.displayChanged.emit(event.key)

    def _on_Gone(self, event: Gone) -> None:
        if (state := self.states.get(event.key)) is not None:
            self._set_status(state, Status.GONE)

    def _on_Error(self, event: Error) -> None:
        self.error.emit(event.message)

    def _set_status(self, state: DisplayState, status: Status, emit: bool = True) -> None:
        state.status = status
        if status is not Status.OK:
            state.message, state.detail = STATUS_TEXT.get(status, (state.info.reason, state.info.detail))
        else:
            state.message = state.detail = ""
        if emit:
            self.displayChanged.emit(state.key)

    def _verify(self) -> None:
        keys, self._verify_keys = self._verify_keys, set()
        if keys and not self._paused:
            self._service.request_read(keys)
