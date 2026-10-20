# SPDX-License-Identifier: GPL-3.0-only
from datetime import UTC

from nightlight.color.exemptions import ForegroundWatcher, paused_displays
from nightlight.config import NightLightConfig
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


def test_automatic_pausing_follows_what_windows_reports():
    config = NightLightConfig(pause_automatically=True)
    browser = ForegroundApp("msedge.exe", D2, False)
    assert paused_displays(browser, config) == {}
    assert paused_displays(browser, config, media=True) == {D2: "msedge.exe, automatic"}  # video or a call keeps the screen on
    assert paused_displays(browser, NightLightConfig(), media=True) == {}  # only when turned on


def test_automatic_pausing_covers_color_critical_apps():
    config = NightLightConfig(pause_automatically=True)
    assert paused_displays(ForegroundApp("photoshop.exe", D2, False), config) == {D2: "photoshop.exe, automatic"}
    assert paused_displays(ForegroundApp("resolve.exe", D2, False), config) == {D2: "resolve.exe, automatic"}
    assert paused_displays(ForegroundApp("notepad.exe", D2, False), config) == {}


def test_a_listed_app_or_fullscreen_takes_precedence_over_automatic():
    config = NightLightConfig(pause_automatically=True, exempt_apps=("photoshop.exe",), exempt_fullscreen=True)
    assert paused_displays(ForegroundApp("photoshop.exe", D2, False), config, media=True) == {D2: "photoshop.exe"}
    assert paused_displays(ForegroundApp("game.exe", D2, True), config, media=True) == {D2: "game.exe, fullscreen"}


def test_the_watcher_asks_windows_only_when_automatic_is_on():
    asked = []

    def media():
        asked.append(True)
        return True

    app = ForegroundApp("vlc.exe", D2, False)
    watcher = ForegroundWatcher(lambda: app, NightLightConfig(exempt_fullscreen=True), media=media)
    assert watcher.paused == {} and asked == []
    watcher.set_config(NightLightConfig(pause_automatically=True))
    assert watcher.paused == {D2: "vlc.exe, automatic"} and asked
    watcher.stop()


def test_automatic_pauses_read_well():
    from datetime import datetime

    from nightlight.color.controller import NightLightController
    from nightlight.color.exemptions import describe
    from nightlight.ui.night_light_text import paused_text

    class Gamma:
        def apply(self, kelvin):
            pass

        def reset(self):
            pass

        def set_override(self, display, kelvin):
            pass

    from nightlight.config import Mode

    controller = NightLightController(Gamma(), NightLightConfig(enabled=True, mode=Mode.ALWAYS),
                                      lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
    controller.set_paused({D2: "msedge.exe, automatic"})
    assert paused_text(controller) == "Paused on display 2 for msedge.exe (automatic)"
    assert describe("msedge.exe, automatic").startswith("video")
    controller.shutdown()
