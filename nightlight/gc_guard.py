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

The rules that keep it safe, checked in the 0.2.0 audit:

- Nothing calls gc.collect() off the main thread: a collection there frees
  every cycle in the process, Qt objects included.
- Worker threads create no Qt objects; they pass plain Python objects to
  the main thread through a queued signal (monitors._Bridge).
- Objects tied to a worker thread (COM objects from WMI must be released in
  the apartment that made them) are not left to the collector: their
  cycles are broken on that thread with release_cycles(), so reference
  counting frees them there and then.
"""

from __future__ import annotations

import gc
from collections.abc import Iterable

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


def release_cycles(root: object, modules: Iterable[str]) -> int:
    """Break the reference cycles among the objects reachable from `root`
    whose classes come from `modules` (e.g. "wmi", "win32com"), by clearing
    their attributes, so they are freed now, on this thread, without a
    collection. Containers on the way are followed; classes, modules and
    functions aren't, so nothing outside `root`'s own objects is touched.
    Returns how many objects were cleared."""
    prefixes = tuple(modules)
    seen: set[int] = set()
    stack, owners = [root], []
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, (dict, list, tuple, set, frozenset)):
            stack.extend(gc.get_referents(obj))
        elif not isinstance(obj, type) and (type(obj).__module__ or "").startswith(prefixes):
            owners.append(obj)
            stack.extend(gc.get_referents(obj))
    for obj in owners:
        attributes = getattr(obj, "__dict__", None)
        if isinstance(attributes, dict):
            attributes.clear()
    return len(owners)
