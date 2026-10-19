# SPDX-License-Identifier: GPL-3.0-only
"""Pausing night light for particular apps and for fullscreen apps.

f.lux's "disable for" list and fullscreen option are a main reason people
prefer it to Windows Night Light: color-accurate work, games and videos
should show true colors. Gamma ramps belong to displays, not windows, so
night light is paused on the display showing the app, and the other displays
keep it. The controller fades that display out and back in.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from nightlight.config import NightLightConfig
from nightlight.hardware.winsystem import ForegroundApp

log = logging.getLogger(__name__)

# Checking the foreground window is cheap; this is quick enough that
# switching to a game pauses night light before it has drawn much.
POLL_MS = 400
FULLSCREEN = ", fullscreen"


def paused_displays(app: ForegroundApp | None, config: NightLightConfig) -> dict[str, str]:
    """Display -> reason, for the display where night light should pause."""
    if app is None or not app.display:
        return {}
    if config.pause_for_apps and app.exe and app.exe in config.exempt_apps:
        return {app.display: app.exe}
    if config.exempt_fullscreen and app.fullscreen:
        return {app.display: f"{app.exe or 'an app'}{FULLSCREEN}"}
    return {}


class ForegroundWatcher(QObject):
    """Watches the foreground app while any exemption is set."""

    pausedChanged = Signal(dict)  # display -> reason

    def __init__(self, probe: Callable[[], ForegroundApp | None], config: NightLightConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._probe = probe
        self._config = config
        self._paused: dict[str, str] = {}
        self._timer = QTimer(self, interval=POLL_MS)
        self._timer.timeout.connect(self.check)
        self.set_config(config)

    def set_config(self, config: NightLightConfig) -> None:
        self._config = config
        if (config.pause_for_apps and config.exempt_apps) or config.exempt_fullscreen:
            self._timer.start()
            self.check()
        else:
            self._timer.stop()
            self._publish({})

    @property
    def paused(self) -> dict[str, str]:
        """Display -> reason, as last published."""
        return dict(self._paused)

    def check(self) -> None:
        self._publish(paused_displays(self._probe(), self._config))

    def stop(self) -> None:
        self._timer.stop()

    def _publish(self, paused: dict[str, str]) -> None:
        if paused != self._paused:
            if paused:
                kinds = {"a fullscreen app" if why.endswith(FULLSCREEN) else "a listed app" for why in paused.values()}
                log.info("Pausing night light for %s", " and ".join(sorted(kinds)))
            self._paused = paused
            self.pausedChanged.emit(paused)
