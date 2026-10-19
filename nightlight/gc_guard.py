# SPDX-License-Identifier: GPL-3.0-only
"""Python's garbage collection, on the main thread only.

Python collects reference cycles in whichever thread happens to allocate
past a threshold. Winlight's monitor threads allocate all the time, so a
collection can run there, and if the garbage holds Qt objects, they are
destroyed from the wrong thread: Qt crashes with an access violation. Found
by the test suite, where a collection on the monitor-service thread crashed
about one run in two; pyqtgraph hit the same and solved it the same way.

Automatic collection is turned off, and a timer on the main thread collects
instead: the youngest generation when enough allocations have piled up, as
Python would, and everything now and then.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QTimer

CHECK_MS = 1000
FULL_EVERY = 60  # checks between full collections


class MainThreadGarbageCollector(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._threshold = gc.get_threshold()[0] or 700
        self._checks = 0
        gc.disable()
        self._timer = QTimer(self, interval=CHECK_MS)
        self._timer.timeout.connect(self.check)
        self._timer.start()

    def check(self) -> None:
        self._checks += 1
        if self._checks >= FULL_EVERY:
            self._checks = 0
            gc.collect()
        elif gc.get_count()[0] > self._threshold:
            gc.collect(0)

    def stop(self) -> None:
        """Back to Python's own collection (and a last one now)."""
        self._timer.stop()
        gc.enable()
        gc.collect()
