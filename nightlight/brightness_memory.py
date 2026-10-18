# SPDX-License-Identifier: GPL-3.0-only
"""Keeps each display at the brightness it should have: the level the user
set, or the level the brightness schedule asks for.

Monitors and laptop panels often come back at a different brightness after
the PC starts, wakes from sleep or the monitor is reconnected. Like Twinkle
Tray's "Apply brightness at startup" and its re-apply after hardware and power
events, the right level is put back then:

- when the app starts,
- after waking from sleep, or a display turning on (`restore_all_soon`),
- when a monitor appears that wasn't there at the previous detection.

What "the right level" is, in one place (`target`), so remembered and
scheduled levels never fight (Twinkle Tray fixed bugs where startup or
hardware events overwrote the current level, or sliders reverted):

1. With the brightness schedule on: the user's own level if they set it
   after the current step began (a manual change pauses the schedule until
   its next step), otherwise the scheduled level.
2. With the schedule off: the last level the user set, if remembering is on.
3. Hidden displays are left alone.

At each step of the schedule the new level is set on every (visible) display,
which also ends any manual pause. Nothing else changes a display behind the
user's back, so its own buttons and Windows' adaptive or battery brightness
keep working. Only levels set by the user are remembered, not levels read
from the monitor, so a monitor that wakes at the wrong level can't overwrite
the remembered one.

Unprompted changes go through MonitorManager.restore_brightness, which skips
a monitor that says it is asleep (DDC/CI can wake or hang it); it is then
done at a later reading. Monitors also often don't answer for a few seconds
after waking, and detection runs again several times then (see app.py).

The levels live in a small file next to the settings, with when each was
set, written a moment after the last change rather than on every step of a
slider drag.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from nightlight.config import BrightnessScheduleConfig
from nightlight.core.brightness_schedule import ScheduledLevel, scheduled_level
from nightlight.hardware.brightness import MonitorInfo
from nightlight.monitors import MonitorManager

log = logging.getLogger(__name__)

SAVE_DELAY_MS = 2000
SCHEDULE_CHECK_MS = 15_000


class BrightnessMemory(QObject):
    def __init__(
        self,
        monitors: MonitorManager,
        path: Path,
        enabled: bool = True,
        parent: QObject | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ) -> None:
        super().__init__(parent)
        self._monitors = monitors
        self._path = path
        self._enabled = enabled
        self._clock = clock
        self.levels, self.set_at = load(path)
        self._schedule = BrightnessScheduleConfig()
        self._location: tuple[float, float] | None = None
        self._hidden: frozenset[str] = frozenset()
        self._step_since: datetime | None = None  # start of the schedule step last applied
        self._present: set[str] = set()  # keys at the previous detection
        self._due: set[str] = set()  # keys to restore once their level has been read
        self._restore_everything = True  # at start-up, every monitor is due
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save)
        self._schedule_timer = QTimer(self, interval=SCHEDULE_CHECK_MS)
        self._schedule_timer.timeout.connect(self._check_schedule)
        monitors.monitorsChanged.connect(self._on_monitors_changed)
        monitors.brightnessChanged.connect(self._on_brightness)
        monitors.brightnessSet.connect(self._on_set)
        monitors.restoreDeferred.connect(self._on_deferred)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Turning it on doesn't restore anything right away: the levels on
        screen now are what the user sees and has presumably accepted."""
        self._enabled = enabled
        if not enabled and not self._schedule.enabled:
            self._due.clear()

    def set_schedule(self, schedule: BrightnessScheduleConfig, location: tuple[float, float] | None = None) -> None:
        """A changed schedule takes effect at once, as a new step would."""
        changed = (schedule, location) != (self._schedule, self._location)
        self._schedule, self._location = schedule, location
        if schedule.enabled:
            self._schedule_timer.start()
            if changed:
                self._step_since = None
                self._check_schedule(apply_now=True)
        else:
            self._schedule_timer.stop()
            self._step_since = None

    def set_hidden(self, keys: frozenset[str]) -> None:
        self._hidden = frozenset(keys)
        self._due -= self._hidden

    def target(self, monitor: MonitorInfo) -> int | None:
        """The level this display should be at now, or None to leave it alone."""
        if monitor.key in self._hidden:
            return None
        step = self._scheduled()
        if step is not None:
            set_at = self.set_at.get(monitor.key)
            manual = monitor.key in self.levels and set_at is not None and set_at >= step.since
            level = self.levels[monitor.key] if manual else step.level
        elif self._enabled and monitor.key in self.levels:
            level = self.levels[monitor.key]
        else:
            return None
        return max(level, monitor.minimum)  # e.g. a laptop panel isn't set to black

    def restore_all_soon(self) -> None:
        """After waking from sleep: restore every monitor at the next detection."""
        self._restore_everything = True

    def save(self) -> None:
        self._save_timer.stop()
        try:
            save(self.levels, self.set_at, self._path)
        except OSError:
            log.warning("Could not save brightness levels to %s", self._path, exc_info=True)

    def shutdown(self) -> None:
        self._schedule_timer.stop()
        if self._save_timer.isActive():
            self.save()

    def _scheduled(self) -> ScheduledLevel | None:
        return scheduled_level(self._schedule, self._clock(), self._location)

    def _check_schedule(self, apply_now: bool = False) -> None:
        """At a new step of the schedule, fade every visible display to its level."""
        step = self._scheduled()
        if step is None:
            return
        first = self._step_since is None
        new_step = step.since != self._step_since
        self._step_since = step.since
        if not (apply_now or (new_step and not first)):
            return  # at start-up, the restore after detection applies it
        log.info("Brightness schedule: %d%% since %s", step.level, f"{step.since:%H:%M}")
        for monitor in self._monitors.monitors:
            if monitor.supported and (level := self.target(monitor)) is not None:
                self._monitors.restore_brightness(monitor.index, level, fade=True)

    def _on_monitors_changed(self, monitors: list[MonitorInfo]) -> None:
        keys = {m.key for m in monitors if m.supported}
        new = keys if self._restore_everything else keys - self._present
        self._due = {m.key for m in monitors if m.key in (self._due | new) & keys and self.target(m) is not None}
        if self._due:
            log.info("Restoring brightness of %s once read", ", ".join(sorted(self._due)))
        self._restore_everything = False
        self._present = keys

    def _on_brightness(self, index: int, value: int | None) -> None:
        monitor = self._monitors.monitor(index)
        if monitor is None or monitor.key not in self._due or value is None:
            return  # not due, or not answering yet: a later detection retries
        self._due.discard(monitor.key)
        target = self.target(monitor)
        if target is not None and value != target:
            log.info("Restoring %s brightness: %d%% -> %d%%", monitor.name, value, target)
            self._monitors.restore_brightness(index, target)

    def _on_deferred(self, index: int) -> None:
        """The monitor was asleep or the write failed: restore at a later reading."""
        monitor = self._monitors.monitor(index)
        if monitor is not None and self.target(monitor) is not None:
            self._due.add(monitor.key)

    def _on_set(self, index: int, value: int) -> None:
        monitor = self._monitors.monitor(index)
        if monitor is None:
            return
        self._due.discard(monitor.key)  # the user just chose a level; don't override it
        # Kept even with remembering off: it is what pauses the schedule.
        self.levels[monitor.key] = value
        self.set_at[monitor.key] = self._clock()
        if self._enabled or self._schedule.enabled:
            self._save_timer.start()


def load(path: Path) -> tuple[dict[str, int], dict[str, datetime]]:
    """Levels and when they were set, by monitor key; anything unreadable is
    ignored, never fatal. Version 1 files have levels only."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, {}
    except (OSError, ValueError):
        log.warning("Could not read %s; starting without remembered brightness", path, exc_info=True)
        return {}, {}
    if not isinstance(raw, dict) or not isinstance(raw.get("levels"), dict):
        return {}, {}
    levels = {
        str(key): max(0, min(100, value))
        for key, value in raw["levels"].items()
        if isinstance(value, int) and not isinstance(value, bool)
    }
    set_at = {}
    for key, value in (raw.get("set_at") or {}).items() if isinstance(raw.get("set_at"), dict) else ():
        try:
            moment = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            continue
        if moment.tzinfo is not None and key in levels:
            set_at[str(key)] = moment
    return levels, set_at


def save(levels: dict[str, int], set_at: dict[str, datetime], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 2, "levels": levels, "set_at": {key: moment.isoformat() for key, moment in set_at.items()}}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
