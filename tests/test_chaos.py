# SPDX-License-Identifier: GPL-3.0-only
"""Everything going wrong at once, against the real MonitorManager and BrightnessMemory.

Unit tests check one failure at a time; real ones come together: a monitor
hangs while the user drags a slider, the PC sleeps during a detection, a
display is unplugged mid-write and another takes its port. Here a fake
backend hangs past the time limits, fails, reports displays gone and changes
the layout (ports swapped, displays unplugged, a new serial in the same
port), while the test drags sliders, restores levels, and sleeps and wakes.
Afterwards these must hold:

1. No write reached the wrong display. Each display has its own range of
   levels, so a level outside its range means a write meant for another.
2. Nothing was sent while asleep (beyond the call already under way), nor
   after waking before the displays had been detected again (stale handles).
3. Nothing blocked: the test finishes, shutting down is bounded.
4. Once the trouble stops, every connected display settles to a true, fresh
   state, and nothing is left listed as stuck.
"""

import random
import threading
import time

import pytest
from PySide6.QtTest import QTest

from nightlight import monitors as monitors_module
from nightlight.brightness_memory import BrightnessMemory
from nightlight.hardware.brightness import MonitorGoneError, MonitorInfo
from nightlight.monitors import IDLE, MonitorManager, Status

KEYS = ["AAA1:111", "BBB2:222", "CCC3:333", "DDD4:444", "AAA1:999"]  # the last: the same model, another serial
TIMEOUT_S = 0.06
HANG_S = 0.25
# A call already under way when sleep starts may still go out this long after.
IN_FLIGHT_S = 2 * TIMEOUT_S + 0.4  # generous: CI runners can stall a thread for a while


def level_range(key):
    base = 10 + 15 * KEYS.index(key)
    return range(base, base + 10)


class ChaosBackend:
    def __init__(self, rng):
        self.rng = rng
        self.lock = threading.Lock()
        self.layout = KEYS[:3]  # index -> key
        self.values = {key: level_range(key)[0] for key in KEYS}
        self.events = []  # (start time, kind, key or None, value)
        self.detected_at = []  # when each list_monitors returned
        self.chaos = True

    def _trouble(self):
        if not self.chaos:
            return
        roll = self.rng.random()
        if roll < 0.06:
            time.sleep(HANG_S)  # past the time limit: given up on, returns later
        elif roll < 0.10:
            raise OSError("DDC/CI said no")

    def _record(self, kind, index=None, value=None):
        with self.lock:
            key = self.layout[index] if index is not None and index < len(self.layout) else None
            self.events.append((time.monotonic(), kind, key, value))
            return key

    def list_monitors(self):
        self._record("detect")
        self._trouble()
        with self.lock:
            if self.chaos and self.rng.random() < 0.5:
                present = self.rng.sample(KEYS, self.rng.randint(1, 4))
                if "AAA1:111" in present and "AAA1:999" in present:
                    present.remove("AAA1:999")
                self.layout = present
            layout = list(self.layout)
        self.detected_at.append(time.monotonic())
        return [MonitorInfo(i, key.split(":")[0], True, key=key, channel="ddc") for i, key in enumerate(layout)]

    def get_brightness(self, index):
        key = self._record("read", index)
        self._trouble()
        if key is None or (self.chaos and self.rng.random() < 0.03):
            raise MonitorGoneError(f"display {index}")
        return self.values[key]

    def set_brightness(self, index, value):
        key = self._record("write", index, value)
        self._trouble()
        if key is None or (self.chaos and self.rng.random() < 0.03):
            raise MonitorGoneError(f"display {index}")
        with self.lock:
            self.values[key] = value

    def is_powered_on(self, index):
        self._record("power", index)
        self._trouble()
        return True


def wait_until(condition, timeout_ms=5000):
    for _ in range(timeout_ms // 20):
        if condition():
            return True
        QTest.qWait(20)
    return condition()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_everything_going_wrong_at_once(seed, tmp_path, monkeypatch):
    for name in ("DETECT_TIMEOUT_S", "READ_TIMEOUT_S", "WRITE_TIMEOUT_S", "POWER_TIMEOUT_S", "CLOSE_TIMEOUT_S"):
        monkeypatch.setattr(monitors_module, name, TIMEOUT_S)
    monkeypatch.setattr(monitors_module, "MIN_DETECT_INTERVAL_S", 0.05)
    monkeypatch.setattr(monitors_module, "VERIFY_DELAY_MS", 100)
    monkeypatch.setattr(monitors_module, "SHUTDOWN_WAIT_S", 0.5)
    rng = random.Random(seed)
    backend = ChaosBackend(random.Random(seed + 100))
    monitors = MonitorManager(backend)
    memory = BrightnessMemory(monitors, tmp_path / "brightness.json")
    sleeps = []  # (suspended at, resumed at or None)
    started = time.monotonic()
    try:
        monitors.refresh()
        while time.monotonic() - started < 3.0:
            action = rng.random()
            keys = [s.key for s in monitors.states.values()]
            if action < 0.45 and keys:
                key = rng.choice(keys)
                monitors.set_brightness(key, rng.choice(level_range(key)))  # a slider drag
            elif action < 0.55 and keys:
                key = rng.choice(keys)
                monitors.restore_brightness(key, rng.choice(level_range(key)), source=rng.choice(["restore", IDLE]))
            elif action < 0.65:
                monitors.refresh(force=rng.random() < 0.5)
            elif action < 0.70:
                monitors.retry()
            elif action < 0.75:
                memory.restore_all_soon()
            elif action < 0.82 and not monitors.paused:
                monitors.suspend()
                sleeps.append([time.monotonic(), None])
            elif action < 0.90 and monitors.paused:
                sleeps[-1][1] = time.monotonic()
                monitors.resume((0.05,))
            QTest.qWait(rng.randint(1, 25))

        # The trouble stops; the PC is awake; displays are detected once more.
        backend.chaos = False
        if monitors.paused:
            sleeps[-1][1] = time.monotonic()
            monitors.resume(())
        assert wait_until(lambda: not monitors._service.stuck, 3000), monitors._service.stuck
        monitors.refresh(force=True)

        def settled():
            states = monitors.states
            return (set(states) == set(backend.layout) and all(
                s.status is Status.OK and s.desired is None and s.observed == backend.values[k] for k, s in states.items()))

        assert wait_until(settled, 5000), {k: (s.status, s.desired, s.observed, backend.values.get(k)) for k, s in monitors.states.items()}
    finally:
        t0 = time.monotonic()
        memory.shutdown()
        monitors.shutdown()
        assert time.monotonic() - t0 < 2  # 3. bounded, even with calls still stuck

    # 1. No write reached the wrong display.
    writes = [(t, key, value) for t, kind, key, value in backend.events if kind == "write"]
    assert writes, "the test should have written something"
    wrong = [(key, value) for _t, key, value in writes if key is None or value not in level_range(key)]
    assert wrong == []

    # 2. Nothing sent while asleep (beyond the call under way), nor after waking before a detection.
    calls = [(t, kind) for t, kind, _key, _value in backend.events if kind in ("read", "write", "power")]
    for asleep, awake in sleeps:
        during = [(t, kind) for t, kind in calls if asleep + IN_FLIGHT_S < t < (awake or float("inf"))]
        assert during == [], f"sent while asleep: {during}"
        if awake is not None:
            detected = next((d for d in backend.detected_at if d > awake), None)
            if detected is not None:
                # (A call already under way when sleep began may start late if the PC woke at once.)
                stale = [(t, kind) for t, kind in calls if max(awake, asleep + IN_FLIGHT_S) < t < detected]
                assert stale == [], f"sent after waking before detecting again: {stale}"
