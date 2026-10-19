# SPDX-License-Identifier: GPL-3.0-only
"""External displays following the laptop's own brightness (its keys).

Laptop brightness keys are handled by the firmware and Windows, which change
the built-in panel; they don't reach apps as key presses, and intercepting
them with a low-level keyboard hook would be fragile, laptop-specific and
the kind of thing game anti-cheat flags. Twinkle Tray's settings warn about
the same limits, and its users asked for this instead (#570): follow the
panel's level when it changes and set the external displays to match.

The panel's changes come from Windows (a power-setting notification, when it
sends one) and from a light check of the panel every 2 seconds while the
option is on; the log says which noticed. Changes the app made itself (a
slider, a restore) are recognised and not passed on. It also keeps the
panel's slider in the flyout up to date.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer

from nightlight.hardware.brightness import MonitorInfo
from nightlight.monitors import MonitorManager

log = logging.getLogger(__name__)

POLL_MS = 2000
# A change within this long of the app's own write to the panel is its own echo.
OWN_CHANGE_S = 2.0


class BrightnessKeys(QObject):
    def __init__(self, monitors: MonitorManager, hidden: Callable[[], frozenset[str]] = frozenset, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._monitors = monitors
        self._hidden = hidden
        self._enabled = False
        self._known: int | None = None  # the panel's level as last seen
        self._poll = QTimer(self, interval=POLL_MS)
        self._poll.timeout.connect(self._read_panel)
        monitors.observed.connect(self._on_observed)
        monitors.monitorsChanged.connect(lambda _: setattr(self, "_known", None))

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled and self._panel() is not None:
            self._poll.start()
        elif not enabled:
            self._poll.stop()

    def on_windows_event(self, level: int) -> None:
        """Windows reported the panel's level (SystemEvents.panelBrightness)."""
        panel = self._panel()
        if panel is not None and level != self._monitors.value(panel.key):
            self._changed(panel, level, "Windows")

    def _panel(self) -> MonitorInfo | None:
        return next((m for m in self._monitors.monitors if m.internal and m.supported), None)

    def _read_panel(self) -> None:
        if panel := self._panel():
            self._monitors.read(panel.key)
        else:
            self._poll.stop()

    def _on_observed(self, key: str, value: int | None) -> None:
        panel = self._panel()
        if panel is None or key != panel.key or value is None:
            return
        if self._known is not None and value != self._known and not self._monitors.recently_written(key, OWN_CHANGE_S):
            self._follow(panel, value, "a check of the panel")
        self._known = value

    def _changed(self, panel: MonitorInfo, level: int, source: str) -> None:
        if self._monitors.recently_written(panel.key, OWN_CHANGE_S):
            return  # the app's own change, echoed back
        self._known = level
        # Shows the new level on the panel's slider, without counting as the user's choice.
        self._monitors.note_level(panel.key, level)
        self._follow(panel, level, source)

    def _follow(self, panel: MonitorInfo, level: int, source: str) -> None:
        if not self._enabled:
            return
        hidden = self._hidden()
        others = [m for m in self._monitors.monitors if m.supported and not m.internal and m.key not in hidden]
        if not others:
            return
        log.info("The built-in display changed to %d%% (noticed by %s); external displays follow", level, source)
        for monitor in others:
            self._monitors.set_brightness(monitor.key, max(monitor.minimum, min(100, level)))
