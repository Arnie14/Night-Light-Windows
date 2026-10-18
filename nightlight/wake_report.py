# SPDX-License-Identifier: GPL-3.0-only
"""Following the displays back after sleep, with timings in the log.

Monitors that don't answer DDC/CI for a while after waking are the most
reported problem in Monitorian and Twinkle Tray (Monitorian #50: "not
controllable" after hibernation; Twinkle Tray's repeated fixes for restoring
after sleep). Detection runs again on a long schedule after waking, and each
pass is logged with how long after waking it was, which displays answered,
and how long detection took, so a report shows exactly when each display came
back, or that one never did.

Once every display that answered before sleep has answered in two passes in
a row, the rest of the schedule is skipped.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PySide6.QtCore import QObject

from nightlight.monitors import MonitorManager

log = logging.getLogger(__name__)

# Seconds after waking. Monitorian re-detects at 5-60 s; some monitors take
# longer still, so this goes on to 2 minutes.
RESCAN_AFTER_WAKE_S = (2, 5, 10, 20, 30, 60, 120)
# A second "woke up" within this long is the same wake (Windows' event and
# the clock check both report it).
SAME_WAKE_S = 60.0
STABLE_PASSES = 2


class WakeReport(QObject):
    def __init__(self, monitors: MonitorManager, clock: Callable[[], float] = time.monotonic, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._monitors = monitors
        self._clock = clock
        self._woke_at: float | None = None
        self._expected: set[str] = set()  # displays answering before sleep
        self._first_answer: dict[str, float] = {}
        self._stable = 0
        self._scan_started: float | None = None
        self.lines: list[str] = []  # the last wake's report, for diagnostics
        monitors.refreshStarted.connect(self._on_scan_started)
        monitors.refreshed.connect(self._on_scan)

    def woke(self) -> bool:
        """Start following a wake; False if it's the same wake reported again."""
        now = self._clock()
        if self._woke_at is not None and now - self._woke_at < SAME_WAKE_S:
            return False
        self._woke_at = now
        monitors = self._monitors
        self._expected = {m.key for m in monitors.monitors if m.supported and monitors.brightness.get(m.index) is not None}
        self._first_answer = {}
        self._stable = 0
        self.lines = []
        log.info("Woke up; detecting displays again at %s s", ", ".join(map(str, RESCAN_AFTER_WAKE_S)))
        monitors.refresh_later(RESCAN_AFTER_WAKE_S)
        return True

    def _on_scan_started(self) -> None:
        self._scan_started = self._clock()

    def _on_scan(self) -> None:
        if self._woke_at is None:
            return
        now = self._clock()
        after = now - self._woke_at
        monitors = self._monitors
        answering, silent = [], []
        for monitor in monitors.monitors:
            if not monitor.supported:
                continue
            value = monitors.brightness.get(monitor.index)
            if value is None:
                silent.append(monitor.name)
            else:
                answering.append(f"{monitor.name} {value}%")
                self._first_answer.setdefault(monitor.key, after)
        took = f", detection took {now - self._scan_started:.1f} s" if self._scan_started is not None else ""
        line = f"+{after:.0f} s: {len(answering)} of {len(answering) + len(silent)} answering"
        line += f" ({', '.join(answering) or 'none'}" + (f"; not answering: {', '.join(silent)}" if silent else "") + f"){took}"
        self.lines.append(line)
        log.info("After waking %s", line)

        answered = {m.key for m in monitors.monitors if m.supported and monitors.brightness.get(m.index) is not None}
        self._stable = self._stable + 1 if self._expected <= answered else 0
        if self._stable >= STABLE_PASSES:
            self._finish(after, early=True)
        elif after >= RESCAN_AFTER_WAKE_S[-1] - 1:
            self._finish(after, early=False)

    def _finish(self, after: float, early: bool) -> None:
        names = {m.key: m.name for m in self._monitors.monitors}
        back = [f"{names.get(key, key)} at +{seconds:.0f} s" for key, seconds in sorted(self._first_answer.items(), key=lambda kv: kv[1])]
        missing = [names.get(key, key) for key in self._expected if key not in self._first_answer]
        summary = "answering again: " + (", ".join(back) or "none") + (f"; never answered: {', '.join(missing)}" if missing else "")
        if early:
            summary += f" (stopped checking at +{after:.0f} s)"
            self._monitors.cancel_scheduled()
        self.lines.append(summary)
        log.info("Wake summary: %s", summary)
        self._woke_at = None
