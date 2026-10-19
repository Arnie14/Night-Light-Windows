# SPDX-License-Identifier: GPL-3.0-only
"""Calls with a time limit: nothing waits forever on a stuck call."""

import threading
import time

import pytest

from nightlight.hardware.calls import CallRunner, CallTimeout, run_with_timeout, start_side_call


def test_results_and_exceptions_come_back():
    runner = CallRunner("test")
    assert runner.call(lambda: 42, 1) == 42
    with pytest.raises(ZeroDivisionError):
        runner.call(lambda: 1 / 0, 1)
    runner.close()


def test_calls_run_one_at_a_time_on_one_thread():
    runner = CallRunner("test")
    threads = {runner.call(lambda: threading.get_ident(), 1) for _ in range(5)}
    assert len(threads) == 1 and threading.get_ident() not in threads
    runner.close()


def test_a_stuck_call_is_given_up_on_and_the_next_one_still_runs():
    runner = CallRunner("test")
    release = threading.Event()
    started = time.monotonic()
    with pytest.raises(CallTimeout) as raised:
        runner.call(lambda: release.wait(5), 0.1, "reading a monitor")
    assert time.monotonic() - started < 0.5
    assert raised.value.what == "reading a monitor"
    assert runner.call(lambda: "next", 1) == "next"  # on a fresh thread
    assert runner.threads_started == 2
    assert runner.stuck and "reading a monitor" in runner.stuck[0]
    release.set()
    for _ in range(100):
        if not runner.stuck:
            break
        time.sleep(0.01)
    assert runner.stuck == []
    runner.close()


def test_clean_up_runs_when_a_given_up_call_finally_returns():
    runner = CallRunner("test")
    release, cleaned = threading.Event(), threading.Event()
    results = []
    with pytest.raises(CallTimeout):
        runner.call(lambda: (release.wait(5), results.append("finished")), 0.05, on_late=cleaned.set)
    assert not cleaned.is_set()  # the handle is still in use: not released yet
    release.set()
    assert cleaned.wait(2) and results == ["finished"]
    runner.close()


def test_a_call_finishing_right_at_the_deadline_is_not_lost():
    runner = CallRunner("test")
    for _ in range(20):
        try:
            assert runner.call(lambda: time.sleep(0.01) or "done", 0.01) == "done"
        except CallTimeout:
            pass  # either outcome is fine; what matters is that nothing hangs or crashes
    assert runner.call(lambda: "still works", 1) == "still works"
    runner.close()


def test_side_calls_share_one_deadline():
    release = threading.Event()
    slow = start_side_call(lambda: release.wait(5), "slow monitor")
    fast = start_side_call(lambda: "fast", "fast monitor")
    deadline = time.monotonic() + 0.2
    with pytest.raises(CallTimeout):
        slow.result(deadline - time.monotonic())
    assert fast.result(deadline - time.monotonic()) == "fast"  # already done: no extra wait
    release.set()


def test_run_with_timeout():
    assert run_with_timeout(lambda: 7, 1, "x") == 7
    with pytest.raises(CallTimeout):
        run_with_timeout(lambda: time.sleep(1), 0.05, "x")
