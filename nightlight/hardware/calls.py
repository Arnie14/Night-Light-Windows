# SPDX-License-Identifier: GPL-3.0-only
"""Calls into hardware with a time limit.

A DDC/CI or WMI call can block forever: some monitors hang their firmware,
some drivers never return, and WMI blocks while Windows is busy after waking
(Twinkle Tray added a "safety system" for this, and notes that its WMI
calls simply block). A blocked native call can't be interrupted, so instead
of waiting for it, the caller gives up after a time limit and carries on:

- CallRunner runs calls one at a time on a worker thread. When a call takes
  too long it is abandoned: its thread is left to finish (or hang) on its
  own, and a fresh thread takes the next call. One stuck call therefore
  never blocks the calls behind it, and at most one thread is left behind
  per stuck call.
- run_with_timeout runs one call on its own thread, for the few calls that
  may run side by side (probing several monitors during detection).

Abandoned calls can't be undone, only waited out: `on_late` runs when one
finally returns, on its thread, to release what it held (a monitor handle
mustn't be destroyed while a call on it is still running).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


class CallTimeout(Exception):
    """A call didn't return within its time limit and was abandoned."""

    def __init__(self, what: str, seconds: float) -> None:
        super().__init__(f"{what} didn't return within {seconds:g} s")
        self.what = what
        self.seconds = seconds


@dataclass
class _Job:
    fn: Callable[[], Any]
    what: str
    on_late: Callable[[], None] | None
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None
    abandoned: bool = False
    started: float = 0.0


class _StuckRegistry:
    """Calls that were abandoned and haven't returned yet, for logging and shutdown."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: list[_Job] = []

    def add(self, job: _Job) -> None:
        with self._lock:
            self._jobs.append(job)

    def remove(self, job: _Job) -> None:
        with self._lock:
            if job in self._jobs:
                self._jobs.remove(job)

    def describe(self) -> list[str]:
        now = time.monotonic()
        with self._lock:
            return [f"{job.what} (stuck for {now - job.started:.0f} s)" for job in self._jobs]


def _finish(job: _Job, lock: threading.Lock, stuck: _StuckRegistry) -> None:
    """Run a job's call and hand back its outcome, or, if it was abandoned
    meanwhile, discard the outcome and run its clean-up instead."""
    try:
        result, error = job.fn(), None
    except BaseException as exc:  # noqa: BLE001 - handed to the caller
        result, error = None, exc
    with lock:
        abandoned = job.abandoned
        if not abandoned:
            job.result, job.error = result, error
            job.done.set()
    if abandoned:
        stuck.remove(job)
        log.info("%s returned after %.1f s, after being given up on", job.what, time.monotonic() - job.started)
        if job.on_late is not None:
            try:
                job.on_late()
            except Exception:
                log.warning("Cleaning up after %s failed", job.what, exc_info=True)


def _wait(job: _Job, timeout: float, lock: threading.Lock, stuck: _StuckRegistry) -> Any:
    if not job.done.wait(timeout):
        with lock:
            # The call may have finished just as the time ran out.
            if not job.done.is_set():
                job.abandoned = True
        if job.abandoned:
            stuck.add(job)
            raise CallTimeout(job.what, timeout)
    if job.error is not None:
        raise job.error
    return job.result


class CallRunner:
    """Runs calls one at a time on a worker thread, waiting at most a given
    time for each (see the module docstring). Used by one caller thread."""

    def __init__(self, name: str = "hardware-calls") -> None:
        self._name = name
        self._lock = threading.Lock()  # guards each job's abandoned/done hand-over
        self._stuck = _StuckRegistry()
        self._queue: queue.Queue[_Job | None] | None = None
        self._threads = 0

    def call(self, fn: Callable[[], Any], timeout: float, what: str = "a hardware call", on_late: Callable[[], None] | None = None) -> Any:
        """fn's result, or its exception; CallTimeout if it takes longer than `timeout` seconds."""
        if self._queue is None:
            self._start_thread()
        job = _Job(fn, what, on_late, started=time.monotonic())
        self._queue.put(job)
        try:
            return _wait(job, timeout, self._lock, self._stuck)
        except CallTimeout:
            # The worker is stuck in this call: retire it (it exits once the
            # call returns) and give later calls a fresh thread.
            self._queue.put(None)
            self._queue = None
            log.warning("%s didn't return within %g s; carrying on without it", what, timeout)
            raise

    @property
    def stuck(self) -> list[str]:
        """Calls that were given up on and are still running."""
        return self._stuck.describe()

    @property
    def threads_started(self) -> int:
        return self._threads

    def close(self) -> None:
        """Let the current worker exit after its queued calls; stuck ones are left behind."""
        if self._queue is not None:
            self._queue.put(None)
            self._queue = None

    def _start_thread(self) -> None:
        self._threads += 1
        jobs: queue.Queue[_Job | None] = queue.Queue()
        self._queue = jobs
        threading.Thread(target=self._work, args=(jobs,), name=f"{self._name}-{self._threads}", daemon=True).start()

    def _work(self, jobs: queue.Queue[_Job | None]) -> None:
        while (job := jobs.get()) is not None:
            _finish(job, self._lock, self._stuck)


class InlineRunner:
    """Runs calls straight away on the caller's thread, without a time limit.
    For tests that need calls to happen in a predictable order."""

    stuck: list[str] = []
    threads_started = 0

    def call(self, fn: Callable[[], Any], timeout: float, what: str = "", on_late: Callable[[], None] | None = None) -> Any:
        return fn()

    def close(self) -> None:
        pass


_side_lock = threading.Lock()
_side_stuck = _StuckRegistry()


class SideCall:
    """A call running on a thread of its own; see start_side_call."""

    def __init__(self, fn: Callable[[], Any], what: str, on_late: Callable[[], None] | None) -> None:
        self._job = _Job(fn, what, on_late, started=time.monotonic())
        threading.Thread(target=_finish, args=(self._job, _side_lock, _side_stuck), name=f"call: {what}"[:60], daemon=True).start()

    def result(self, timeout: float) -> Any:
        """fn's result or exception; CallTimeout after `timeout` seconds (fn
        keeps running, and its `on_late` runs when it returns)."""
        return _wait(self._job, max(0.0, timeout), _side_lock, _side_stuck)


def start_side_call(fn: Callable[[], Any], what: str, on_late: Callable[[], None] | None = None) -> SideCall:
    """Start fn on a thread of its own, so several calls can run side by side
    under one deadline (probing several monitors during detection)."""
    return SideCall(fn, what, on_late)


def run_with_timeout(fn: Callable[[], Any], timeout: float, what: str, on_late: Callable[[], None] | None = None) -> Any:
    """Run fn on a thread of its own and wait at most `timeout` seconds."""
    return start_side_call(fn, what, on_late).result(timeout)


def stuck_side_calls() -> list[str]:
    return _side_stuck.describe()
