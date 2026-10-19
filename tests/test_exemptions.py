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


def test_chosen_apps_have_a_switch_of_their_own():
    """Off, the list is kept but pauses nothing; fullscreen is separate."""
    listed = ForegroundApp("photoshop.exe", D2, False)
    off = NightLightConfig(exempt_apps=("photoshop.exe",), pause_for_apps=False)
    assert paused_displays(listed, off) == {}
    assert off.exempt_apps == ("photoshop.exe",)
    both_off_but_fullscreen = NightLightConfig(exempt_apps=("photoshop.exe",), pause_for_apps=False, exempt_fullscreen=True)
    assert paused_displays(ForegroundApp("photoshop.exe", D2, True), both_off_but_fullscreen) == {D2: "photoshop.exe, fullscreen"}
    assert paused_displays(listed, both_off_but_fullscreen) == {}


def test_chosen_apps_pause_fullscreen_or_not_and_only_them():
    config = NightLightConfig(exempt_apps=("photoshop.exe",))
    assert paused_displays(ForegroundApp("photoshop.exe", D2, True), config) == {D2: "photoshop.exe"}
    assert paused_displays(ForegroundApp("game.exe", D2, True), config) == {}  # any fullscreen app needs the other switch
