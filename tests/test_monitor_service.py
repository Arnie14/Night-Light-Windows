# SPDX-License-Identifier: GPL-3.0-only
"""The monitor service's queue, step by step: races between slider changes,
detection, sleep and waking, displays that vanish, change identity or hang.

The service runs on the test's thread (InlineRunner, run_pending), so events
can be interleaved exactly; the hang tests use the real CallRunner with short
time limits. Every backend call is recorded by display key, so a call that
reaches the wrong monitor shows up directly.
"""

import threading
import time

import pytest

from nightlight import monitors as monitors_module
from nightlight.hardware.brightness import MonitorGoneError, MonitorInfo
from nightlight.hardware.calls import CallRunner, InlineRunner
from nightlight.monitors import (
    RESTORE,
    SCHEDULE,
    USER,
    Commanded,
    Deferred,
    Detected,
    DetectionStuck,
    Failed,
    Gone,
    MonitorService,
    Observed,
    Write,
)


class ScriptedBackend:
    """Displays by key; each detection takes the next list given (the last repeats)."""

    def __init__(self, *layouts):
        self.layouts = [list(layout) for layout in layouts]
        self.current: list[MonitorInfo] = []
        self.calls: list[tuple] = []
        self.values: dict[str, int] = {}
        self.gone: set[str] = set()
        self.asleep: set[str] = set()
        self.failing: set[str] = set()
        self.blocked: dict[str, threading.Event] = {}  # key -> released when set
        self.block_detection: threading.Event | None = None
        self.during_set = None  # called inside a set, to interleave events

    def list_monitors(self):
        if self.block_detection is not None:
            self.block_detection.wait(5)
        layout = self.layouts.pop(0) if len(self.layouts) > 1 else self.layouts[0]
        self.current = [MonitorInfo(i, key, supported, key=key, reason="" if supported else "DDC/CI didn't answer",
                                    status="ok" if supported else "unsupported") for i, (key, supported) in enumerate(layout)]
        self.calls.append(("list", tuple(m.key for m in self.current)))
        return list(self.current)

    def _key(self, index):
        return self.current[index].key

    def get_brightness(self, index):
        key = self._key(index)
        self.calls.append(("get", key))
        self._maybe_block(key)
        if key in self.gone:
            raise MonitorGoneError(key)
        if key in self.failing:
            raise OSError("read failed")
        return self.values.get(key, 50)

    def set_brightness(self, index, value):
        key = self._key(index)
        self._maybe_block(key)
        if self.during_set is not None:
            hook, self.during_set = self.during_set, None
            hook()
        if key in self.gone:
            raise MonitorGoneError(key)
        if key in self.failing:
            raise OSError("write failed")
        self.calls.append(("set", key, value))
        self.values[key] = value

    def is_powered_on(self, index):
        key = self._key(index)
        self.calls.append(("power", key))
        return key not in self.asleep

    def _maybe_block(self, key):
        if (event := self.blocked.get(key)) is not None:
            event.wait(5)

    def sets(self):
        return [c[1:] for c in self.calls if c[0] == "set"]


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(backend, runner=None):
    events = []
    clock = FakeClock()
    service = MonitorService(backend, events.append, runner or InlineRunner(), clock=clock)
    return service, events, clock


def detect(service, clock):
    clock.now += 10  # past the merge interval
    service.request_detect()
    return service.run_pending()


def of(events, kind):
    return [e for e in events if isinstance(e, kind)]


# --- Addressing: commands reach the right display, or none -------------------


def test_nothing_is_sent_before_the_first_detection():
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    service.request_write(Write("A", 30))
    assert service.run_pending() == []  # waits: there is nothing to address yet
    assert backend.calls == []
    detect(service, clock)
    assert backend.sets() == [("A", 30)]


def test_a_write_queued_during_reordering_reaches_the_same_display():
    # The user drags B's slider; before the change is sent, a detection finds
    # the displays in the other order (B is now first). It must still be B.
    backend = ScriptedBackend([("A", True), ("B", True)], [("B", True), ("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("B", 70))
    clock.now += 10
    service.request_detect()
    assert service.run_pending()[0] == "detect"  # detection runs first, then the write
    assert backend.sets() == [("B", 70)]
    assert backend.current[0].key == "B"


def test_a_display_that_disappeared_gets_nothing():
    backend = ScriptedBackend([("A", True), ("B", True)], [("B", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("A", 20))
    detect(service, clock)  # A unplugged meanwhile
    assert backend.sets() == []
    assert of(events, Deferred)[-1] == Deferred("A", 20, USER, "not connected")


def test_a_different_display_on_the_same_port_is_not_the_old_one():
    # Same position in the list, different identity (monitor swapped on the port).
    backend = ScriptedBackend([("DEL41A2:111", True)], [("GSM5B7F:222", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("DEL41A2:111", 40))
    detect(service, clock)
    assert backend.sets() == []
    assert of(events, Deferred)[-1].why == "not connected"


def test_unsupported_displays_are_never_written_or_read():
    backend = ScriptedBackend([("A", True), ("TV", False)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("TV", 50))
    service.run_pending()
    assert ("get", "TV") not in backend.calls and backend.sets() == []
    assert of(events, Deferred)[-1].why == "no control"


# --- The queue: coalescing and priority ----------------------------------------


def test_rapid_slider_changes_send_only_the_latest():
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    for value in range(10, 60, 5):
        service.request_write(Write("A", value))
    service.run_pending()
    assert backend.sets() == [("A", 55)]
    assert of(events, Commanded) == [Commanded("A", 55, USER)]


def test_each_display_keeps_its_own_latest_value():
    backend = ScriptedBackend([("A", True), ("B", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("A", 10))
    service.request_write(Write("B", 20))
    service.request_write(Write("A", 30))
    service.run_pending()
    assert backend.sets() == [("B", 20), ("A", 30)]  # A's newest replaced its older one


def test_a_users_level_wins_over_a_pending_restore_or_schedule():
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("A", 80))  # the user, just now
    service.request_write(Write("A", 30, RESTORE, require_awake=True))
    service.request_write(Write("A", 40, SCHEDULE, require_awake=True))
    service.run_pending()
    assert backend.sets() == [("A", 80)]
    assert [d.why for d in of(events, Deferred)] == ["superseded", "superseded"]


def test_a_later_user_change_replaces_a_pending_restore():
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("A", 30, RESTORE, require_awake=True))
    service.request_write(Write("A", 90))
    service.run_pending()
    assert backend.sets() == [("A", 90)]


def test_detection_requests_close_together_are_merged():
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    for _ in range(5):
        service.request_detect()
    service.run_pending()  # within the merge interval: deferred, not dropped
    assert sum(1 for c in backend.calls if c[0] == "list") == 1
    clock.now += monitors_module.MIN_DETECT_INTERVAL_S
    service.run_pending()
    assert sum(1 for c in backend.calls if c[0] == "list") == 2  # the five became one


def test_a_forced_detection_is_not_held_back():
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_detect(force=True)
    assert service.run_pending()[0] == "detect"


def test_detection_reads_every_supported_display():
    backend = ScriptedBackend([("A", True), ("B", True), ("TV", False)])
    backend.values = {"A": 30, "B": 70}
    service, events, clock = make(backend)
    detect(service, clock)
    assert of(events, Observed) == [Observed("A", 30), Observed("B", 70)]


def test_one_unreadable_display_does_not_stop_the_others():
    backend = ScriptedBackend([("A", True), ("B", True)])
    backend.failing = {"A"}
    service, events, clock = make(backend)
    detect(service, clock)
    assert of(events, Observed) == [Observed("A", None), Observed("B", 50)]


# --- Sleep and waking ---------------------------------------------------------


def test_nothing_is_sent_while_suspended():
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    calls = len(backend.calls)
    service.suspend()
    service.request_write(Write("A", 25))
    service.request_read()
    clock.now += 10
    service.request_detect()
    assert service.run_pending() == []  # not even detection: monitors may be asleep
    assert len(backend.calls) == calls


def test_after_waking_changes_wait_for_a_fresh_detection():
    # Handles from before sleep are stale; the change made while asleep (or
    # just after waking) goes out only once displays have been detected again.
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.suspend()
    service.request_write(Write("A", 25))
    service.resume()
    assert service.run_pending() == []
    lists_before = sum(1 for c in backend.calls if c[0] == "list")
    detect(service, clock)
    assert sum(1 for c in backend.calls if c[0] == "list") == lists_before + 1
    assert backend.sets() == [("A", 25)]


def test_suspending_during_a_write_holds_back_the_rest():
    backend = ScriptedBackend([("A", True), ("B", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("A", 10))
    service.request_write(Write("B", 20))
    backend.during_set = service.suspend  # the PC starts suspending while A is being written
    service.run_pending()
    assert backend.sets() == [("A", 10)]  # in flight: completes; B waits
    service.resume()
    detect(service, clock)
    assert backend.sets() == [("A", 10), ("B", 20)]


def test_unprompted_changes_leave_a_monitor_in_standby_alone():
    backend = ScriptedBackend([("A", True)])
    backend.asleep = {"A"}
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("A", 30, RESTORE, require_awake=True))
    service.run_pending()
    assert backend.sets() == [] and ("power", "A") in backend.calls
    assert of(events, Deferred)[-1] == Deferred("A", 30, RESTORE, "asleep")


def test_the_users_own_change_skips_the_power_check():
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("A", 30))
    service.run_pending()
    assert ("power", "A") not in backend.calls


# --- Displays that vanish ---------------------------------------------------------


def test_a_display_unplugged_during_a_write_triggers_detection_once():
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    backend.gone = {"A"}
    service.request_write(Write("A", 40))
    done = service.run_pending()
    assert of(events, Gone)[0] == Gone("A") and of(events, Deferred)[-1].why == "gone"
    assert done.count("detect") == 1  # re-detection followed at once (its reading finds A still gone)
    service.request_write(Write("A", 41))
    done = service.run_pending()
    assert "detect" not in done  # rate-limited: a failing handle can't loop detection


def test_a_display_that_comes_back_gets_new_changes():
    backend = ScriptedBackend([("A", True)], [], [("A", True)])
    service, events, clock = make(backend)
    detect(service, clock)
    backend.gone = {"A"}
    service.request_write(Write("A", 40))
    service.run_pending()  # gone: detection finds nothing
    backend.gone = set()
    detect(service, clock)  # plugged back in
    service.request_write(Write("A", 45))
    service.run_pending()
    assert backend.sets() == [("A", 45)]


# --- Hangs: bounded waits, isolated displays --------------------------------


@pytest.fixture
def short_timeouts(monkeypatch):
    for name in ("READ_TIMEOUT_S", "WRITE_TIMEOUT_S", "POWER_TIMEOUT_S", "DETECT_TIMEOUT_S", "CLOSE_TIMEOUT_S"):
        monkeypatch.setattr(monitors_module, name, 0.2)


def test_a_hung_display_does_not_block_the_others(short_timeouts):
    backend = ScriptedBackend([("A", True), ("B", True)])
    service, events, clock = make(backend, CallRunner("test"))
    detect(service, clock)
    release = backend.blocked["A"] = threading.Event()
    service.request_write(Write("A", 10))
    service.request_write(Write("B", 20))
    started = time.monotonic()
    service.run_pending()
    assert time.monotonic() - started < 1.5  # bounded: A's call was given up on
    assert ("B", 20) in backend.sets()
    assert of(events, Failed)[0] == Failed("A", of(events, Failed)[0].message, timeout=True)
    # While A's call is stuck, A gets nothing more (at most one stuck call per display).
    service.request_write(Write("A", 11))
    service.run_pending()
    assert of(events, Deferred)[-1] == Deferred("A", 11, USER, "not responding")
    # The stuck call returns: A is checked again, and works.
    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and service.stuck:
        time.sleep(0.02)
    del backend.blocked["A"]
    detect(service, clock)
    service.request_write(Write("A", 12))
    service.run_pending()
    assert ("A", 12) in backend.sets()
    service.close()
    service.step()


def test_a_display_with_a_stuck_call_is_listed_as_not_responding(short_timeouts):
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend, CallRunner("test"))
    detect(service, clock)
    release = backend.blocked["A"] = threading.Event()
    service.request_write(Write("A", 10))
    service.run_pending()
    detect(service, clock)
    [info] = of(events, Detected)[-1].monitors
    assert not info.supported and info.status == "not responding"
    release.set()


def test_a_hung_detection_is_bounded_and_retried(short_timeouts):
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend, CallRunner("test"))
    backend.block_detection = threading.Event()
    service.request_write(Write("A", 30))
    started = time.monotonic()
    service.request_detect()
    service.run_pending()
    assert time.monotonic() - started < 1.5
    assert of(events, DetectionStuck)
    assert backend.sets() == []  # no index can be trusted while detection is stuck
    backend.block_detection.set()  # the driver finally answers
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not of(events, Detected):
        clock.now += 10
        service.run_pending()
        time.sleep(0.05)
    assert of(events, Detected), "detection runs again once the stuck one returns"
    service.run_pending()
    assert backend.sets() == [("A", 30)]  # the change made meanwhile was kept and sent
    service.close()
    service.step()


def test_closing_with_a_stuck_call_does_not_wait_for_it(short_timeouts):
    backend = ScriptedBackend([("A", True)])
    service, events, clock = make(backend, CallRunner("test"))
    detect(service, clock)
    release = backend.blocked["A"] = threading.Event()
    service.request_write(Write("A", 10))
    service.run_pending()
    started = time.monotonic()
    service.close()
    assert service.step() is None
    assert time.monotonic() - started < 0.5
    release.set()


def test_a_failed_write_is_reported_and_the_queue_carries_on():
    backend = ScriptedBackend([("A", True), ("B", True)])
    backend.failing = {"A"}
    service, events, clock = make(backend)
    detect(service, clock)
    service.request_write(Write("A", 10))
    service.request_write(Write("B", 20))
    service.run_pending()
    assert of(events, Failed)[0].key == "A" and not of(events, Failed)[0].timeout
    assert backend.sets() == [("B", 20)]
