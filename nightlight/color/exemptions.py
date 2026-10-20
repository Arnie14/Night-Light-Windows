# SPDX-License-Identifier: GPL-3.0-only
"""Pausing night light for particular apps and for fullscreen apps.

f.lux's "disable for" list and fullscreen option are a main reason people
prefer it to Windows Night Light: color-accurate work, games and videos
should show true colors. Gamma ramps belong to displays, not windows, so
night light is paused on the display showing the app, and the other displays
keep it. The controller fades that display out and back in.

Three independent switches: a list of apps; any fullscreen app; and an
automatic mode that goes by what Windows reports. Windows says that some app
keeps the display on (video players and browsers playing video, video calls,
presentations) or that a game runs exclusive fullscreen, but not which app
or display: the pause goes to the display showing the app in front. The
automatic mode also covers well-known photo, video and design apps.
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
AUTOMATIC = ", automatic"
# Apps where colour accuracy matters, paused for by the automatic mode.
COLOR_APPS = frozenset({
    "photoshop.exe", "lightroom.exe", "lightroomclassic.exe", "illustrator.exe", "indesign.exe", "afterfx.exe",
    "adobe premiere pro.exe", "premiere pro.exe", "resolve.exe", "affinityphoto.exe", "affinityphoto2.exe",
    "affinitydesigner.exe", "affinitydesigner2.exe", "captureone.exe", "darktable.exe", "rawtherapee.exe",
    "gimp-2.10.exe", "gimp-3.0.exe", "gimp.exe", "krita.exe", "clipstudiopaint.exe", "blender.exe",
})


def paused_displays(app: ForegroundApp | None, config: NightLightConfig, media: bool = False) -> dict[str, str]:
    """Display -> reason, for the display where night light should pause.
    `media`: Windows reports video, a call or a game (see ForegroundWatcher)."""
    if app is None or not app.display:
        return {}
    if config.pause_for_apps and app.exe and app.exe in config.exempt_apps:
        return {app.display: app.exe}
    if config.exempt_fullscreen and app.fullscreen:
        return {app.display: f"{app.exe or 'an app'}{FULLSCREEN}"}
    if config.pause_automatically and (media or app.exe in COLOR_APPS):
        return {app.display: f"{app.exe or 'an app'}{AUTOMATIC}"}
    return {}


def describe(reason: str) -> str:
    """A pause reason in a few words, for the log and reports."""
    if reason.endswith(FULLSCREEN):
        return "a fullscreen app"
    if reason.endswith(AUTOMATIC):
        return "video, a call, a game or a color-critical app"
    return "a listed app"


class ForegroundWatcher(QObject):
    """Watches the foreground app while any exemption is set."""

    pausedChanged = Signal(dict)  # display -> reason

    def __init__(self, probe: Callable[[], ForegroundApp | None], config: NightLightConfig, parent: QObject | None = None,
                 media: Callable[[], bool] = lambda: False) -> None:
        super().__init__(parent)
        self._probe = probe
        self._media = media
        self._config = config
        self._paused: dict[str, str] = {}
        self._timer = QTimer(self, interval=POLL_MS)
        self._timer.timeout.connect(self.check)
        self.set_config(config)

    def set_config(self, config: NightLightConfig) -> None:
        self._config = config
        if (config.pause_for_apps and config.exempt_apps) or config.exempt_fullscreen or config.pause_automatically:
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
        app = self._probe()
        # Asked only when it can matter: a call into Windows each time.
        media = bool(app and self._config.pause_automatically and self._media())
        self._publish(paused_displays(app, self._config, media))

    def stop(self) -> None:
        self._timer.stop()

    def _publish(self, paused: dict[str, str]) -> None:
        if paused != self._paused:
            if paused:
                kinds = {describe(why) for why in paused.values()}
                log.info("Pausing night light for %s", " and ".join(sorted(kinds)))
            self._paused = paused
            self.pausedChanged.emit(paused)
