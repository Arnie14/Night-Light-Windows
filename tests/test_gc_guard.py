# SPDX-License-Identifier: GPL-3.0-only
"""Garbage is collected on the main thread only (nightlight/gc_guard.py)."""

import gc
import threading

from PySide6.QtCore import QObject

from nightlight import gc_guard


class Cycle:
    def __init__(self):
        self.me = self
        self.qt = QObject()  # a Qt object in reference cycle garbage


def test_a_busy_worker_thread_never_collects_garbage_holding_qt_objects():
    collector = gc_guard.MainThreadGarbageCollector()
    threads = []

    def note(phase, info):
        if phase == "start":
            threads.append(threading.current_thread().name)

    gc.callbacks.append(note)
    try:
        for _ in range(50):
            Cycle()  # garbage, waiting for a collection
        worker = threading.Thread(target=lambda: [[{}] for _ in range(200_000)], name="monitor-service")
        worker.start()  # allocates far past every threshold
        worker.join()
        assert "monitor-service" not in threads
        collector._checks = gc_guard.FULL_EVERY - 1
        collector.check()  # the main thread's timer
        assert threads and set(threads) == {threading.current_thread().name}
    finally:
        gc.callbacks.remove(note)
        collector.stop()
        gc.disable()  # as the test session keeps it (conftest)
