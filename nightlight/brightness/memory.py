# SPDX-License-Identifier: GPL-3.0-only
"""Remembers each monitor's brightness and puts it back when it gets lost.

Monitors and laptop panels often come back at a different brightness after
the PC starts, wakes from sleep or the monitor is reconnected. Like Twinkle
Tray's "Apply brightness at startup" and its re-apply after hardware and power
events, the last level set in this app is restored then:

- when the app starts,
- after waking from sleep, or a display turning on (`restore_all_soon`),
- when a monitor appears that wasn't there at the previous detection.

Only those moments: a monitor that stays connected is never changed behind
the user's back, so its own buttons and Windows' adaptive or battery
brightness keep working. Only levels set in this app (by the user or the
brightness schedule) are remembered, not levels read from the monitor, so a
monitor that wakes up at the wrong level can't overwrite the remembered one
before it is restored. (Twinkle Tray fixed bugs where startup or hardware
events overwrote the current level, or sliders reverted.)

The brightness schedule (brightness_scheduler.py) decides when its points
apply; the levels it sets are remembered like the user's, so a monitor
reconnected in the evening comes back at the evening level, and a level the
user picks afterwards wins until the schedule's next point.

A restore waits until the monitor's current level has been read. Monitors
often don't answer DDC/CI for a few seconds after waking or being plugged in,
and detection runs again several times then, so a monitor that doesn't
answer yet is simply restored at a later detection. Unprompted changes go
through MonitorManager.restore_brightness, which skips a monitor that says
it is in standby (DDC/CI can wake or hang it); it is restored at a later
reading. Hidden displays are left alone, and nothing is restored while idle
dimming holds the displays down.

Displays are remembered by their stable key (EDID manufacturer, product and
serial, see win32.DisplayTarget.key). Levels saved by older versions under
the hardware id alone are carried over the first time the display is seen.

The levels live in their own small file next to the settings, with when each
was set, written a moment after the last change rather than on every step of
a slider drag.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from nightlight.brightness.monitors import RESTORE, MonitorManager
from nightlight.hardware.brightness import MonitorInfo, legacy_key
from nightlight.system.files import write_atomic

log = logging.getLogger(__name__)

SAVE_DELAY_MS = 2000


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
        self._hidden: Callable[[str], bool] = lambda _key: False
        self._held = False  # idle dimming holds the displays down: restore nothing until it ends
        self._present: set[str] = set()  # keys at the previous detection
        self._due: set[str] = set()  # keys to restore once their level has been read
        self._restore_everything = True  # at start-up, every monitor is due
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save)
        monitors.monitorsChanged.connect(self._on_monitors_changed)
        monitors.observed.connect(self._on_observed)
        monitors.brightnessSet.connect(self._on_set)
        monitors.restoreDeferred.connect(self._on_deferred)
        monitors.changedElsewhere.connect(self._on_changed_elsewhere)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Turning it on doesn't restore anything right away: the levels on
        screen now are what the user sees and has presumably accepted."""
        self._enabled = enabled
        if not enabled:
            self._due.clear()

    def set_hidden(self, hidden: Callable[[str], bool] | Iterable[str]) -> None:
        """Displays to leave alone: a predicate on keys (it may know older
        keys), or a set of keys."""
        if not callable(hidden):
            keys = frozenset(hidden)
            hidden = keys.__contains__
        self._hidden = hidden
        self._due = {key for key in self._due if not hidden(key)}

    def set_held(self, held: bool) -> None:
        """While displays are dimmed for idle, hold back restores; when it
        ends, IdleDimmer sets every display to target()."""
        self._held = held

    def target(self, monitor: MonitorInfo) -> int | None:
        """The level this display should be at, or None to leave it alone."""
        if self._hidden(monitor.key) or not self._enabled or monitor.key not in self.levels:
            return None
        return max(self.levels[monitor.key], monitor.minimum)  # e.g. a laptop panel isn't set to black

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
        if self._save_timer.isActive():
            self.save()

    def _on_monitors_changed(self, monitors: list[MonitorInfo]) -> None:
        keys = {m.key for m in monitors if m.supported}
        self._migrate(keys)
        if self._enabled:
            new = keys if self._restore_everything else keys - self._present
            due = (self._due | new) & keys & self.levels.keys()
            self._due = {key for key in due if not self._hidden(key)}
            if self._due:
                log.info("Restoring brightness of %s once read", ", ".join(sorted(self._due)))
        self._restore_everything = False
        self._present = keys

    def _migrate(self, keys: set[str]) -> None:
        """Carry levels saved under a display's old key (hardware id only) over to its key now."""
        for key in keys - self.levels.keys():
            old = legacy_key(key)
            if old != key and old in self.levels and not any(k != key and legacy_key(k) == old for k in keys):
                self.levels[key] = self.levels.pop(old)
                if old in self.set_at:
                    self.set_at[key] = self.set_at.pop(old)
                log.info("Remembered brightness of %s now kept under %s", old, key)
                self._save_timer.start()

    def _on_observed(self, key: str, value: int | None) -> None:
        monitor = self._monitors.monitor(key)
        if monitor is None or key not in self._due or value is None or self._held:
            return  # not due, not answering yet (a later detection retries), or dimmed for idle
        self._due.discard(key)
        target = self.target(monitor)
        if target is not None and value != target:
            log.info("Restoring %s brightness: %d%% -> %d%%", monitor.name, value, target)
            self._monitors.restore_brightness(key, target)

    def _on_deferred(self, key: str, source: str) -> None:
        """The monitor was asleep or the write failed: restore at a later reading."""
        if source == RESTORE and self._enabled and key in self.levels and not self._hidden(key):
            self._due.add(key)

    def _on_changed_elsewhere(self, key: str, value: int) -> None:
        """The level was changed outside the app (the laptop's keys): putting
        the older remembered one back at the next start or wake would undo
        that, so it is forgotten. (Windows keeps a laptop panel's level itself.)"""
        self._due.discard(key)
        if self.levels.pop(key, None) is not None:
            self.set_at.pop(key, None)
            log.info("%s was changed outside Winlight; its remembered brightness won't be put back over that", key)
            self._save_timer.start()

    def _on_set(self, key: str, value: int, source: str) -> None:
        self._due.discard(key)  # a level was just chosen; don't override it
        if self._enabled and self.levels.get(key) != value:
            self.levels[key] = value
            self.set_at[key] = self._clock()
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
    data = {"version": 2, "levels": levels, "set_at": {key: moment.isoformat() for key, moment in set_at.items() if key in levels}}
    write_atomic(path, json.dumps(data, indent=2, sort_keys=True))
