# SPDX-License-Identifier: GPL-3.0-only
"""Applies the brightness schedule to the displays, idempotently.

The schedule isn't a list of commands to replay; it is reconciled. At any
moment core/brightness_schedule.py says which point is in effect, identified
by the instant it began. Each display gets that point's level once:

- When a new point begins, every display gets its level, whatever the user
  did during the previous one.
- Within a point, a level the user picks stands until the next point
  (Twinkle Tray's time adjustments behave the same), and evaluating again,
  however often, changes nothing.
- A display that wasn't reachable when the point began (asleep, not yet
  answering after waking, plugged in later) gets the level when it becomes
  reachable, unless the user has set it meanwhile.
- The point last applied is saved, so after a restart the schedule only acts
  if a new point began while the app wasn't running (or the PC slept through
  it), and otherwise leaves the restored levels alone. Sleeping through
  several points applies only the latest.

Evaluation runs at each point (a timer), every minute during gradual
transitions, after waking, when the clock or time zone changes, and when
displays change. Being idempotent, extra evaluations are harmless.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import datetime, tzinfo
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from nightlight.config import BrightnessScheduleConfig
from nightlight.core.brightness_schedule import ScheduledLevel, scheduled_level
from nightlight.monitors import SCHEDULE, USER, MonitorManager, Status
from nightlight.files import write_atomic

log = logging.getLogger(__name__)

TICK_MS = 60_000  # a safety net, and the step of gradual transitions
# Displays in these states get the level as soon as the change can be queued:
# detection (and resume) let queued writes through once they are reachable.
_APPLICABLE = {Status.OK, Status.READING, Status.PAUSED, Status.DETECTING, Status.UNREADABLE}


class BrightnessScheduler(QObject):
    changed = Signal()  # the scheduled level or point changed

    def __init__(
        self,
        monitors: MonitorManager,
        config: BrightnessScheduleConfig,
        state_path: Path | None,
        location: Callable[[], tuple[float, float] | None] = lambda: None,
        clock: Callable[[], datetime] = lambda: datetime.now().astimezone(),
        zone: tzinfo | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._monitors = monitors
        self._config = config
        self._path = state_path
        self._location = location
        self._clock = clock
        self.zone = zone
        self.state: ScheduledLevel | None = None
        self._applied_id: str | None = self._load()  # the point applied last, possibly before a restart
        self._carried_over = self._applied_id is not None  # displays found at start already have it
        self._done: dict[str, int] = {}  # display key -> level given for the current point
        self._overridden: set[str] = set()  # displays the user set during the current point
        self._hidden: Callable[[str], bool] = lambda _key: False
        self._held = False  # idle dimming holds the displays down
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(lambda: self.evaluate("timer"))
        self._tick = QTimer(self, interval=TICK_MS)
        self._tick.timeout.connect(lambda: self.evaluate("tick"))
        self._tick.start()
        monitors.monitorsChanged.connect(lambda _monitors: self.evaluate("displays"))
        monitors.displayChanged.connect(self._on_display_changed)
        monitors.brightnessSet.connect(self._on_set)
        monitors.restoreDeferred.connect(self._on_deferred)
        monitors.changedElsewhere.connect(lambda key, _value: self._overridden.add(key))  # like the user's own change

    @property
    def config(self) -> BrightnessScheduleConfig:
        return self._config

    def set_config(self, config: BrightnessScheduleConfig) -> None:
        """A changed schedule applies at once, as Twinkle Tray does after editing."""
        if config == self._config:
            return
        self._config = config
        self._forget_point()
        self.evaluate("settings")

    def set_hidden(self, hidden: Callable[[str], bool]) -> None:
        """Hidden displays are left alone (their key may be an older one, so a predicate)."""
        self._hidden = hidden

    def set_held(self, held: bool) -> None:
        """While idle dimming holds the displays down, nothing is brightened;
        a point that began meanwhile applies when the user is back."""
        if held == self._held:
            return
        self._held = held
        if not held:
            self.evaluate("back from idle")

    def evaluate(self, reason: str = "") -> None:
        now = self._clock()
        state = scheduled_level(self._config, now, self.zone, self._location())
        if state is None:
            self._timer.stop()
            if self.state is not None:
                self.state = None
                self.changed.emit()
            return
        if state.since.id != self._applied_id:
            self._start_point(state)
        elif self._carried_over and self._monitors.states:
            # Applied before a restart: the displays found at the first
            # detection were given it then, and BrightnessMemory restores what
            # the user changed afterwards. Displays plugged in later still get it.
            self._carried_over = False
            for key, display in self._monitors.states.items():
                self._done[key] = max(display.info.minimum, min(100, state.level))
        previous, self.state = self.state, state
        # A new point fades (as Twinkle Tray's steps do); a gradual transition's
        # small per-minute steps don't need to.
        self._apply(state.level, fade=not state.transitioning)
        self._arm(state, now)
        if previous is None or (previous.level, previous.since) != (state.level, state.since):
            log.debug("Brightness schedule: %d%% (%s)", state.level, reason)
            self.changed.emit()

    def shutdown(self) -> None:
        self._timer.stop()
        self._tick.stop()

    def _start_point(self, state: ScheduledLevel) -> None:
        log.info("Brightness schedule: %d%% from %s", state.since.level, state.since.at.strftime("%Y-%m-%d %H:%M"))
        self._applied_id = state.since.id
        self._carried_over = False
        self._done.clear()
        self._overridden.clear()
        self._save()

    def _forget_point(self) -> None:
        self._applied_id = None
        self._carried_over = False
        self._done.clear()
        self._overridden.clear()

    def _apply(self, level: int, fade: bool = True) -> None:
        if self._held:
            return  # dimmed for idle: applied when the user is back (set_held(False))
        for key, display in list(self._monitors.states.items()):
            if key in self._overridden or not display.info.supported or display.status not in _APPLICABLE or self._hidden(key):
                continue
            target = max(display.info.minimum, min(100, level))
            if self._done.get(key) == target:
                continue
            self._done[key] = target
            self._monitors.set_brightness(key, target, source=SCHEDULE, fade=fade)

    def _arm(self, state: ScheduledLevel, now: datetime) -> None:
        """Wake at the next point, or in a minute during a transition."""
        if state.transitioning:
            delay_ms = TICK_MS
        elif state.next is not None:
            delay_ms = int((state.next.at - now).total_seconds() * 1000) + 500
        else:
            delay_ms = TICK_MS
        self._timer.start(max(1000, min(delay_ms, 6 * 3600 * 1000)))  # re-armed at least every 6 h

    def _on_display_changed(self, key: str) -> None:
        display = self._monitors.state(key)
        if self.state is not None and display is not None and display.status in _APPLICABLE and key not in self._done:
            self._apply(self.state.level)

    def _on_set(self, key: str, value: int, source: str) -> None:
        if source == USER:
            self._overridden.add(key)  # until the next point

    def _on_deferred(self, key: str, source: str) -> None:
        if source == SCHEDULE:
            self._done.pop(key, None)  # asleep or failed: give it the level once it's reachable

    def _load(self) -> str | None:
        if self._path is None:
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            applied = data.get("applied") if isinstance(data, dict) else None
            return applied if isinstance(applied, str) else None
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            log.warning("Could not read %s; the brightness schedule starts afresh", self._path, exc_info=True)
            return None

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            write_atomic(self._path, json.dumps({"version": 1, "applied": self._applied_id}))
        except OSError:
            log.warning("Could not save %s", self._path, exc_info=True)
