# SPDX-License-Identifier: GPL-3.0-only
"""Remembers each monitor's brightness and puts it back when it gets lost.

Monitors and laptop panels often come back at a different brightness after
the PC starts, wakes from sleep or the monitor is reconnected. Like Twinkle
Tray's "Apply brightness at startup" and its re-apply after hardware and power
events, the last level set in this app is restored then:

- when the app starts,
- after waking from sleep (`restore_all_soon`),
- when a monitor appears that wasn't there at the previous detection.

Only those moments: a monitor that stays connected is never changed behind
the user's back, so its own buttons and Windows' adaptive or battery
brightness keep working. Only levels set in this app are remembered, not
levels read from the monitor, so a monitor that wakes up at the wrong level
can't overwrite the remembered one before it is restored.

A restore waits until the monitor's current level has been read. Monitors
often don't answer DDC/CI for a few seconds after waking or being plugged in,
and detection runs again several times then (see app.py), so a monitor that
doesn't answer yet is simply restored at a later detection.

The levels live in their own small file next to the settings, keyed by
MonitorInfo.key, and are written a moment after the last change rather than
on every step of a slider drag.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from nightlight.hardware.brightness import MonitorInfo
from nightlight.monitors import MonitorManager

log = logging.getLogger(__name__)

SAVE_DELAY_MS = 2000
# Laptop panels are never restored below this: on some laptops 0 turns the
# backlight off completely, which looks like a dead screen (KDE keeps the
# built-in display's minimum above zero for the same reason).
MIN_INTERNAL = 1


class BrightnessMemory(QObject):
    def __init__(self, monitors: MonitorManager, path: Path, enabled: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._monitors = monitors
        self._path = path
        self._enabled = enabled
        self.levels: dict[str, int] = load(path)
        self._present: set[str] = set()  # keys at the previous detection
        self._due: set[str] = set()  # keys to restore once their level has been read
        self._restore_everything = True  # at start-up, every monitor is due
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self.save)
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
        if not enabled:
            self._due.clear()

    def restore_all_soon(self) -> None:
        """After waking from sleep: restore every monitor at the next detection."""
        self._restore_everything = True

    def save(self) -> None:
        self._save_timer.stop()
        try:
            save(self.levels, self._path)
        except OSError:
            log.warning("Could not save brightness levels to %s", self._path, exc_info=True)

    def shutdown(self) -> None:
        if self._save_timer.isActive():
            self.save()

    def _on_monitors_changed(self, monitors: list[MonitorInfo]) -> None:
        keys = {m.key for m in monitors if m.supported}
        if self._enabled:
            new = keys if self._restore_everything else keys - self._present
            self._due = (self._due | new) & keys & self.levels.keys()
            if self._due:
                log.info("Restoring brightness of %s once read", ", ".join(sorted(self._due)))
        self._restore_everything = False
        self._present = keys

    def _on_brightness(self, index: int, value: int | None) -> None:
        monitor = self._monitors.monitor(index)
        if monitor is None or monitor.key not in self._due or value is None:
            return  # not due, or not answering yet: a later detection retries
        self._due.discard(monitor.key)
        target = self.levels[monitor.key]
        if monitor.internal:
            target = max(target, MIN_INTERNAL)
        if value != target:
            log.info("Restoring %s brightness: %d%% -> %d%%", monitor.name, value, target)
            self._monitors.restore_brightness(index, target)

    def _on_deferred(self, index: int) -> None:
        """The monitor was asleep or the write failed: restore at a later reading."""
        monitor = self._monitors.monitor(index)
        if monitor is not None and self._enabled and monitor.key in self.levels:
            self._due.add(monitor.key)

    def _on_set(self, index: int, value: int) -> None:
        monitor = self._monitors.monitor(index)
        if monitor is None:
            return
        self._due.discard(monitor.key)  # the user just chose a level; don't override it
        if self._enabled and self.levels.get(monitor.key) != value:
            self.levels[monitor.key] = value
            self._save_timer.start()


def load(path: Path) -> dict[str, int]:
    """Levels by monitor key; anything unreadable is ignored, never fatal."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("Could not read %s; starting without remembered brightness", path, exc_info=True)
        return {}
    levels = raw.get("levels") if isinstance(raw, dict) else None
    if not isinstance(levels, dict):
        return {}
    return {
        str(key): max(0, min(100, value))
        for key, value in levels.items()
        if isinstance(value, int) and not isinstance(value, bool)
    }


def save(levels: dict[str, int], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "levels": levels}, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)
