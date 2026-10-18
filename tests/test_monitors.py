# SPDX-License-Identifier: GPL-3.0-only
import threading
import time

import pytest
from PySide6.QtTest import QTest

from nightlight.hardware.brightness import MonitorGoneError, MonitorInfo, nearest_level, to_percent, to_raw, valid_range
from nightlight.monitors import MonitorManager


class SlowBackend:
    def __init__(self, delay=0.05):
        self.delay = delay
        self.sets = []
        self.lists = 0
        self.gone_once = False
        self.release = threading.Event()
        self.release.set()

    def list_monitors(self):
        self.release.wait(2)
        self.lists += 1
        return [MonitorInfo(0, "A", True)]

    def get_brightness(self, index):
        return 50

    def set_brightness(self, index, value):
        if self.gone_once:
            self.gone_once = False
            raise MonitorGoneError("A")
        time.sleep(self.delay)
        self.sets.append(value)


def wait_until(condition, timeout_ms=2000):
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


def test_fast_slider_moves_send_only_the_latest_value(manager):
    backend = SlowBackend()
    monitors = manager(backend)
    for value in range(10, 60, 5):
        monitors.set_brightness(0, value)
    assert wait_until(lambda: backend.sets and backend.sets[-1] == 55)
    assert len(backend.sets) <= 2


def test_refresh_requests_during_a_refresh_are_merged(manager):
    backend = SlowBackend()
    backend.release.clear()
    monitors = manager(backend)
    for _ in range(5):
        monitors.refresh()
    backend.release.set()
    assert wait_until(lambda: backend.lists == 2)
    QTest.qWait(100)
    assert backend.lists == 2


def test_gone_monitor_triggers_detection(manager):
    backend = SlowBackend()
    monitors = manager(backend)
    monitors.refresh()
    assert wait_until(lambda: backend.lists == 1 and monitors.monitors)
    backend.gone_once = True
    monitors.set_brightness(0, 30)
    assert wait_until(lambda: backend.lists == 2)


def test_scaling_helpers():
    assert to_percent(25, 0, 50) == 50
    assert to_raw(50, 0, 50) == 25
    assert to_raw(0, 10, 110) == 10 and to_percent(110, 10, 110) == 100
    assert valid_range(0, 30, 100) and not valid_range(0, 0, 0) and not valid_range(0, 120, 100)
    assert nearest_level([0, 25, 50, 75, 100], 60) == 50
    assert nearest_level([], 60) == 60


class PoweredBackend(SlowBackend):
    def __init__(self, awake):
        super().__init__(delay=0)
        self.awake = awake

    def is_powered_on(self, index):
        return self.awake


@pytest.mark.parametrize("awake", [True, None])  # None: the monitor can't tell, so assume awake
def test_restore_sets_an_awake_monitor(manager, awake):
    backend = PoweredBackend(awake)
    monitors = manager(backend)
    monitors.refresh()
    assert wait_until(lambda: 0 in monitors.brightness)  # detection, including its first reading, is done
    changed, deferred = [], []
    monitors.brightnessChanged.connect(lambda i, v: changed.append((i, v)))
    monitors.restoreDeferred.connect(deferred.append)
    monitors.restore_brightness(0, 30)
    assert wait_until(lambda: changed)
    assert backend.sets == [30] and changed == [(0, 30)] and deferred == []


def test_restore_leaves_a_sleeping_monitor_alone(manager):
    backend = PoweredBackend(False)
    monitors = manager(backend)
    monitors.refresh()
    assert wait_until(lambda: 0 in monitors.brightness)
    deferred = []
    monitors.restoreDeferred.connect(deferred.append)
    monitors.restore_brightness(0, 30)
    assert wait_until(lambda: deferred)
    assert deferred == [0] and backend.sets == []  # nothing sent: it could wake or hang it


def test_a_hung_monitor_call_is_reported_and_quitting_does_not_wait_for_it(monkeypatch):
    from nightlight import monitors as monitors_module

    monkeypatch.setattr(monitors_module, "HANG_WARNING_S", 0.1)
    monkeypatch.setattr(monitors_module, "WATCHDOG_INTERVAL_MS", 50)
    monkeypatch.setattr(monitors_module, "SHUTDOWN_WAIT_MS", 200)
    backend = SlowBackend()
    backend.release.clear()  # list_monitors blocks, like a monitor that never answers
    monitors = MonitorManager(backend)
    errors = []
    monitors.error.connect(errors.append)
    monitors.refresh()
    assert wait_until(lambda: errors)
    assert "isn't responding" in errors[0] and "detecting monitors" in errors[0]
    assert len(errors) == 1  # once per stuck call, not on every check
    QTest.qWait(150)
    assert len(errors) == 1
    started = time.monotonic()
    monitors.shutdown()
    assert time.monotonic() - started < 1 and monitors.hung
    backend.release.set()  # let the thread finish so the test run can end cleanly
    monitors._thread.wait(2000)


def test_schedules_add_up_instead_of_replacing(manager):
    monitors = manager(SlowBackend())
    monitors.refresh_later((5, 30, 60))  # after waking
    monitors.refresh_later((2, 8))  # a display change soon after
    monitors.refresh_later((2.5,))  # covered by the one at 2 s
    due = sorted(round(d - time.monotonic()) for d, _ in monitors._scheduled)
    assert due == [2, 5, 8, 30, 60]
    monitors.cancel_scheduled()
    assert monitors._scheduled == []
