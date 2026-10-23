# SPDX-License-Identifier: GPL-3.0-only
"""Keeping the built-in display's brightness when the laptop is plugged in
or unplugged (optional).

Windows' power plans keep one brightness for battery and one for mains, and
apply it when the power source changes, so the level the user or the
brightness schedule chose is replaced. Winlight would take that for a change
made outside the app (the laptop's keys): forget the remembered level, and,
with "follow the laptop's brightness keys", move the external displays too.

With the option on, a change of the built-in display reported in the few
seconds after the power source changed is Windows' own: it isn't followed,
and the level from before the change is put back. Power plans themselves
are left alone. Only the built-in display: Windows changes no other.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from nightlight.brightness.monitors import RESTORE, MonitorManager
from nightlight.hardware.brightness import MonitorInfo

log = logging.getLogger(__name__)

# Windows' own change comes this soon after the power source message.
SETTLE_S = 3.0
# A level seen this soon before the message may be Windows' change already.
BEFORE_S = 1.0
# The panel is read again, then put back, this long after the message.
READ_AFTER_MS = 1000
RESTORE_AFTER_MS = 2000


class PowerSourceKeeper(QObject):
    def __init__(self, monitors: MonitorManager, parent: QObject | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        super().__init__(parent)
        self._monitors = monitors
        self._clock = clock
        self._enabled = False
        self._levels: deque[tuple[float, int]] = deque(maxlen=8)  # (when, the panel's level), oldest first
        self._settle_until = 0.0
        self._target: int | None = None
        self._absorbed = False
        monitors.observed.connect(lambda key, value: self._seen(key, value))
        monitors.brightnessSet.connect(lambda key, value, _source: self._seen(key, value))
        monitors.changedElsewhere.connect(self._seen)
        monitors.monitorsChanged.connect(lambda _monitors: self._levels.clear())

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def settling(self) -> bool:
        """Windows may be applying its own level for the new power source now."""
        return self._clock() < self._settle_until

    def power_source_changed(self) -> None:
        panel = self._panel()
        if not self._enabled or panel is None:
            return
        now = self._clock()
        earlier = [level for at, level in self._levels if at <= now - BEFORE_S]
        self._target = earlier[-1] if earlier else self._monitors.value(panel.key)
        if self._target is None:
            return
        self._settle_until, self._absorbed = now + SETTLE_S, False
        QTimer.singleShot(READ_AFTER_MS, self, lambda: self._monitors.read(panel.key))
        QTimer.singleShot(RESTORE_AFTER_MS, self, self._restore)

    def absorbs(self, level: int) -> bool:
        """Windows reported the panel's level: True if it is the power plan's,
        to be put back rather than taken as the user's change."""
        if not self._enabled or not self.settling():
            return False
        self._absorbed = True
        log.debug("Windows set the built-in display to %d%% for the new power source", level)
        return True

    def _restore(self) -> None:
        panel, target = self._panel(), self._target
        self._target = None
        if panel is None or target is None:
            return
        if self._absorbed or self._monitors.value(panel.key) != target:
            log.info("The power source changed and Windows set the built-in display's brightness; putting %d%% back", target)
            self._monitors.set_brightness(panel.key, target, source=RESTORE)

    def _seen(self, key: str, value: int | None) -> None:
        panel = self._panel()
        if value is not None and panel is not None and key == panel.key:
            self._levels.append((self._clock(), value))

    def _panel(self) -> MonitorInfo | None:
        return next((m for m in self._monitors.monitors if m.internal and m.supported), None)
