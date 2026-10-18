# SPDX-License-Identifier: GPL-3.0-only
from nightlight.config import NightLightConfig
from nightlight.exemptions import ForegroundWatcher, paused_displays
from nightlight.hardware.winsystem import ForegroundApp

D2 = r"\\.\DISPLAY2"


def test_listed_app_pauses_its_display():
    config = NightLightConfig(exempt_apps=("photoshop.exe",))
    assert paused_displays(ForegroundApp("photoshop.exe", D2, False), config) == {D2: "photoshop.exe"}
    assert paused_displays(ForegroundApp("notepad.exe", D2, False), config) == {}


def test_fullscreen_pauses_only_when_turned_on():
    game = ForegroundApp("game.exe", D2, True)
    assert paused_displays(game, NightLightConfig()) == {}
    assert paused_displays(game, NightLightConfig(exempt_fullscreen=True)) == {D2: "game.exe, fullscreen"}


def test_nothing_in_front():
    assert paused_displays(None, NightLightConfig(exempt_fullscreen=True)) == {}


def test_watcher_reports_changes_only():
    app = {"now": ForegroundApp("notepad.exe", D2, False)}
    seen = []
    watcher = ForegroundWatcher(lambda: app["now"], NightLightConfig(exempt_apps=("game.exe",)))
    watcher.pausedChanged.connect(seen.append)
    watcher.check()
    app["now"] = ForegroundApp("game.exe", D2, True)
    watcher.check()
    watcher.check()
    app["now"] = ForegroundApp("notepad.exe", D2, False)
    watcher.check()
    assert seen == [{D2: "game.exe"}, {}]
    watcher.stop()


def test_watcher_idles_without_exemptions():
    seen = []
    watcher = ForegroundWatcher(lambda: ForegroundApp("game.exe", D2, True), NightLightConfig(exempt_fullscreen=True))
    watcher.pausedChanged.connect(seen.append)
    watcher.check()  # already reported at start (before connecting): no change now
    watcher.set_config(NightLightConfig())  # exemptions removed: resume everywhere
    assert seen == [{}] and not watcher._timer.isActive()
