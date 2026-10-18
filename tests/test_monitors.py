# SPDX-License-Identifier: GPL-3.0-only
"""MonitorManager: the service on its thread, seen from the UI as per-display
state (desired, commanded, observed, status) and signals."""

import logging
import threading
import time

import pytest
from PySide6.QtTest import QTest

from nightlight import monitors as monitors_module
from nightlight.hardware.brightness import MonitorGoneError, MonitorInfo, nearest_level, to_percent, to_raw, valid_range
from nightlight.monitors import RESTORE, SCHEDULE, MonitorManager, Status


class SlowBackend:
    def __init__(self, delay=0.05, layout=(("A", True),)):
        self.delay = delay
        self.layout = list(layout)
        self.values = {}
        self.sets = []
        self.lists = 0
        self.gone_once = False
        self.ignores_writes = False
        self.awake = True
        self.release = threading.Event()
        self.release.set()
        self.entered = threading.Event()  # set once a detection is inside list_monitors

    def list_monitors(self):
        self.entered.set()
        self.release.wait(2)
        self.lists += 1
        return [MonitorInfo(i, key, ok, key=key, status="ok" if ok else "unsupported", reason="" if ok else "DDC/CI didn't answer")
                for i, (key, ok) in enumerate(self.layout)]

    def get_brightness(self, index):
        return self.values.get(self.layout[index][0], 50)

    def set_brightness(self, index, value):
        if self.gone_once:
            self.gone_once = False
            raise MonitorGoneError("A")
        time.sleep(self.delay)
        self.sets.append(value)
        if not self.ignores_writes:
            self.values[self.layout[index][0]] = value

    def is_powered_on(self, index):
        return self.awake


def wait_until(condition, timeout_ms=3000):
    for _ in range(timeout_ms // 10):
        if condition():
            return True
        QTest.qWait(10)
    return condition()


@pytest.fixture
def manager():
    managers = []

    def make(backend):
        managers.append(MonitorManager(backend))
        return managers[-1]

    yield make
    for m in managers:
        m.shutdown()


def ready(monitors, key="A"):
    monitors.refresh()
    assert wait_until(lambda: monitors.state(key) is not None and monitors.state(key).status is Status.OK)
    return monitors.state(key)


def test_detection_gives_each_display_a_state_with_its_reading(manager):
    backend = SlowBackend(layout=(("A", True), ("TV", False)))
    backend.values = {"A": 35}
    monitors = manager(backend)
    state = ready(monitors)
    assert (state.observed, state.value, state.controllable) == (35, 35, True)
    tv = monitors.state("TV")
    assert tv.status is Status.UNSUPPORTED and not tv.controllable and tv.message == "DDC/CI didn't answer"


def test_fast_slider_moves_send_only_the_latest_value(manager):
    backend = SlowBackend()
    monitors = manager(backend)
    ready(monitors)
    for value in range(10, 60, 5):
        monitors.set_brightness("A", value)
    assert wait_until(lambda: backend.sets and backend.sets[-1] == 55)
    assert len(backend.sets) <= 2


def test_desired_commanded_and_observed(manager):
    backend = SlowBackend(delay=0.1)
    monitors = manager(backend)
    state = ready(monitors)
    monitors.set_brightness("A", 70)
    assert state.desired == 70 and state.value == 70  # shown while being set
    assert wait_until(lambda: state.commanded == 70)
    assert state.desired is None and state.observed == 70


def test_a_display_that_ignores_writes_is_noticed(manager, monkeypatch, caplog):
    monkeypatch.setattr(monitors_module, "VERIFY_DELAY_MS", 50)
    backend = SlowBackend(delay=0)
    backend.ignores_writes = True
    monitors = MonitorManager(backend)
    try:
        state = ready(monitors)
        with caplog.at_level(logging.WARNING):
            monitors.set_brightness("A", 90)
            assert wait_until(lambda: state.commanded == 90 and state.observed == 50)
        assert "accepted 90% but reports 50%" in caplog.text  # the truth is shown, and logged once
    finally:
        monitors.shutdown()


def test_refresh_requests_during_a_refresh_are_merged(manager):
    backend = SlowBackend()
    backend.release.clear()
    monitors = manager(backend)
    monitors.refresh()
    assert backend.entered.wait(2)  # the first detection is running (and blocked)
    for _ in range(4):
        monitors.refresh()
    backend.release.set()
    # The four made while it ran become exactly one more (displays may have changed meanwhile).
    assert wait_until(lambda: backend.lists == 2, timeout_ms=int(monitors_module.MIN_DETECT_INTERVAL_S * 1000) + 3000)
    QTest.qWait(int(monitors_module.MIN_DETECT_INTERVAL_S * 1000) + 300)
    assert backend.lists == 2


def test_gone_monitor_triggers_detection(manager):
    backend = SlowBackend()
    monitors = manager(backend)
    ready(monitors)
    lists = backend.lists
    backend.gone_once = True
    monitors.set_brightness("A", 30)
    assert wait_until(lambda: backend.lists == lists + 1)


def test_scaling_helpers():
    assert to_percent(25, 0, 50) == 50
    assert to_raw(50, 0, 50) == 25
    assert to_raw(0, 10, 110) == 10 and to_percent(110, 10, 110) == 100
    assert valid_range(0, 30, 100) and not valid_range(0, 0, 0) and not valid_range(0, 120, 100)
    assert nearest_level([0, 25, 50, 75, 100], 60) == 50
    assert nearest_level([], 60) == 60


@pytest.mark.parametrize("awake", [True, None])  # None: the monitor can't tell, so assume awake
def test_restore_sets_an_awake_monitor(manager, awake):
    backend = SlowBackend(delay=0)
    backend.awake = awake
    monitors = manager(backend)
    state = ready(monitors)
    deferred = []
    monitors.restoreDeferred.connect(lambda key, source: deferred.append(key))
    monitors.restore_brightness("A", 30)
    assert wait_until(lambda: state.commanded == 30)
    assert backend.sets == [30] and deferred == []


def test_restore_leaves_a_sleeping_monitor_alone(manager):
    backend = SlowBackend(delay=0)
    backend.awake = False
    monitors = manager(backend)
    state = ready(monitors)
    deferred = []
    monitors.restoreDeferred.connect(lambda key, source: deferred.append((key, source)))
    monitors.restore_brightness("A", 30)
    assert wait_until(lambda: deferred)
    assert deferred == [("A", RESTORE)] and backend.sets == []  # nothing sent: it could wake or hang it
    assert state.status is Status.ASLEEP and not state.controllable and state.message == "In standby"


def test_schedule_changes_count_as_chosen_levels(manager):
    backend = SlowBackend(delay=0)
    monitors = manager(backend)
    ready(monitors)
    chosen = []
    monitors.brightnessSet.connect(lambda key, value, source: chosen.append((key, value, source)))
    monitors.set_brightness("A", 40, source=SCHEDULE)
    monitors.restore_brightness("A", 45)
    assert chosen == [("A", 40, SCHEDULE)]  # restores aren't choices


def test_levels_stay_within_the_displays_range(manager):
    backend = SlowBackend(delay=0)
    monitors = manager(backend)
    state = ready(monitors)
    monitors.set_brightness("A", 150)
    assert state.desired == 100


def test_suspend_pauses_and_resume_waits_for_detection(manager):
    backend = SlowBackend(delay=0)
    monitors = manager(backend)
    state = ready(monitors)
    monitors.suspend()
    assert state.status is Status.PAUSED and not state.controllable
    monitors.set_brightness("A", 20)
    QTest.qWait(100)
    assert backend.sets == []
    lists = backend.lists
    monitors.resume((0.05,))
    assert state.status is Status.DETECTING
    assert wait_until(lambda: backend.sets == [20])
    assert backend.lists == lists + 1  # detected again before writing


def test_changes_for_a_display_that_is_not_connected_are_ignored(manager):
    backend = SlowBackend()
    monitors = manager(backend)
    ready(monitors)
    monitors.set_brightness("not-there", 50)
    QTest.qWait(50)
    assert backend.sets == []


def test_a_hung_monitor_call_is_reported_and_quitting_does_not_wait_for_it(monkeypatch):
    monkeypatch.setattr(monitors_module, "DETECT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(monitors_module, "SHUTDOWN_WAIT_S", 0.3)
    backend = SlowBackend()
    backend.release.clear()  # list_monitors blocks, like a monitor that never answers
    monitors = MonitorManager(backend)
    errors = []
    monitors.error.connect(errors.append)
    monitors.refresh()
    assert wait_until(lambda: errors)
    assert "isn't responding" in errors[0] and "detecting displays" in errors[0]
    QTest.qWait(150)
    assert len(errors) == 1  # once per stuck call, not on every check
    started = time.monotonic()
    monitors.shutdown()
    assert time.monotonic() - started < 1 and monitors.hung
    backend.release.set()  # let the thread finish so the test run can end cleanly
