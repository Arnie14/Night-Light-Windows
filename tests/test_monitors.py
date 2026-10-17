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
