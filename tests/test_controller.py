# SPDX-License-Identifier: GPL-3.0-only
from datetime import UTC, datetime

import pytest
from PySide6.QtTest import QTest

from nightlight.color import controller as controller_module
from nightlight.color.controller import NightLightController, step_towards
from nightlight.config import Mode, NightLightConfig
from nightlight.core.schedule import OverrideKind

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


class RecordingGamma:
    def __init__(self, animates: bool = False) -> None:
        self.animates = animates
        self.applied: list[int] = []
        self.resets = 0

    def apply(self, kelvin: int) -> None:
        self.applied.append(kelvin)

    def reset(self) -> None:
        self.resets += 1


@pytest.fixture(autouse=True)
def fast_animation(monkeypatch):
    monkeypatch.setattr(controller_module, "QUICK_ADJUST_MS", 200)


def make(gamma, config=NightLightConfig()):
    return NightLightController(gamma, config, clock=lambda: NOON)


def test_step_towards():
    assert step_towards(6500, 4500) == 6450
    assert step_towards(4480, 4500) == 4500
    assert step_towards(4500, 6500) == 4550


def test_starts_without_fighting_other_tools_when_idle():
    gamma = RecordingGamma()
    make(gamma)
    assert gamma.applied == []


def test_large_change_is_animated_in_steps():
    gamma = RecordingGamma()
    controller = make(gamma)
    controller.set_config(NightLightConfig(enabled=True, mode=Mode.ALWAYS, night_temperature=4500))
    assert gamma.applied == []  # nothing yet: the animation runs on a timer
    # Until it ends, with a deadline: a fixed wait failed on a busy machine.
    for _ in range(300):
        if gamma.applied and gamma.applied[-1] == 4500:
            break
        QTest.qWait(10)
    assert gamma.applied[0] == 6450
    assert gamma.applied[-1] == 4500
    assert len(gamma.applied) == 40  # (6500 - 4500) / 50


def test_backend_that_animates_itself_gets_target_directly():
    gamma = RecordingGamma(animates=True)
    controller = make(gamma)
    controller.set_config(NightLightConfig(enabled=True, mode=Mode.ALWAYS, night_temperature=4500))
    assert gamma.applied == [4500]


def test_suspend_returns_to_neutral_with_reset():
    gamma = RecordingGamma(animates=True)
    controller = make(gamma, NightLightConfig(enabled=True, mode=Mode.ALWAYS, night_temperature=4500))
    controller.set_override(OverrideKind.SUSPEND)
    assert gamma.resets == 1
    assert controller.current_temperature == 6500
    controller.clear_override()
    assert gamma.applied[-1] == 4500


class DimmingGamma(RecordingGamma):
    """A gamma backend with a display dimmed in software."""

    dimmed = True

    def __init__(self) -> None:
        super().__init__()
        self.released = 0

    def release(self) -> None:
        self.released += 1


def test_daytime_ticks_keep_a_dimmed_ramp_applied():
    gamma = DimmingGamma()
    controller = make(gamma)  # night light off: neutral
    resets = gamma.resets
    controller.update()  # a periodic tick, or after a display change
    assert gamma.resets == resets + 1  # Windows may have reset the dimmed ramp
    controller.shutdown()


def test_daytime_ticks_leave_undimmed_displays_alone():
    gamma = RecordingGamma()
    controller = make(gamma)
    resets = gamma.resets
    controller.update()
    assert gamma.resets == resets  # don't fight Windows Night Light while ours is idle
    controller.shutdown()


def test_quitting_releases_dimming_too():
    gamma = DimmingGamma()
    make(gamma).shutdown()
    assert gamma.released == 1


class PausingGamma(RecordingGamma):
    def __init__(self) -> None:
        super().__init__()
        self.overrides: dict[str, int | None] = {}
        self.verified = 0

    def set_override(self, display, kelvin):
        self.overrides[display] = kelvin

    def verify(self):
        self.verified += 1
        return False


ALWAYS_ON = NightLightConfig(enabled=True, mode=Mode.ALWAYS, night_temperature=3500)


def test_starting_at_night_fades_in_instead_of_snapping():
    gamma = RecordingGamma()
    controller = make(gamma, ALWAYS_ON)
    assert gamma.applied == [] or gamma.applied[0] > 6000  # starts near neutral
    QTest.qWait(1200)
    assert gamma.applied[-1] == 3500 and len(gamma.applied) > 10
    controller.shutdown()


def test_quitting_fades_out():
    gamma = RecordingGamma()
    controller = make(gamma, ALWAYS_ON)
    QTest.qWait(1200)
    before = len(gamma.applied)
    controller.shutdown()
    fade = gamma.applied[before:]
    assert len(fade) >= 5 and fade == sorted(fade) and fade[-1] < 6500  # warmer to cooler, then reset
    assert gamma.resets >= 1


def test_pausing_a_display_fades_it_to_neutral_and_back():
    gamma = PausingGamma()
    controller = make(gamma, ALWAYS_ON)
    QTest.qWait(1200)
    controller.set_paused({"DISPLAY2": "game.exe"})
    assert 3500 < gamma.overrides["DISPLAY2"] < 6500  # fading, not jumping
    QTest.qWait(400)
    assert gamma.overrides["DISPLAY2"] == 6500
    assert controller.paused == {"DISPLAY2": "game.exe"}
    controller.set_paused({})
    QTest.qWait(400)
    assert gamma.overrides["DISPLAY2"] is None  # back to following night light
    controller.shutdown()


def test_unlocking_sets_the_ramps_again_then_keeps_checking():
    """Set again whatever they read back (a driver may report the ramp it was
    sent while the screen shows a reset one), then checked for a while."""
    gamma = PausingGamma()
    gamma.reapplied = 0
    gamma.reapply = lambda: setattr(gamma, "reapplied", gamma.reapplied + 1)
    controller = make(gamma)
    gamma.verified = 0
    controller.reapply_now()
    QTest.qWait(50)
    assert gamma.reapplied == 1  # straight away
    QTest.qWait(2100)
    assert gamma.reapplied == len(controller_module.REAPPLY_FORCE_MS)
    QTest.qWait(1100)
    assert gamma.verified >= 1  # then checks, besides the regular ones
    controller.shutdown()


def test_without_forced_setting_unlocking_checks_the_ramps():
    gamma = PausingGamma()
    controller = make(gamma)
    gamma.verified = 0
    controller.reapply_now()
    QTest.qWait(50)
    assert gamma.verified >= 1
    controller.shutdown()


def test_pausing_while_night_light_is_off_touches_nothing():
    gamma = PausingGamma()
    controller = make(gamma)  # night light off: neutral
    controller.set_paused({"DISPLAY2": "game.exe"})
    QTest.qWait(100)
    assert gamma.overrides == {}  # nothing to pause, so the ramp is left alone
    controller.shutdown()


def test_a_paused_display_stays_neutral_when_night_light_starts():
    gamma = PausingGamma()
    controller = make(gamma)
    controller.set_paused({"DISPLAY2": "game.exe"})
    controller.set_override(controller_module.OverrideKind.ACTIVATE)  # night light comes on
    QTest.qWait(1200)
    assert gamma.applied[-1] == 4500 and gamma.overrides["DISPLAY2"] == 6500
    controller.shutdown()


# --- From the 0.2.0 audit -------------------------------------------------------


def test_nothing_touches_the_displays_after_shutdown():
    """Signing out keeps the event loop running after the app shut down: a
    late display change re-tinted the screen through verify()."""

    class Gamma:
        def __init__(self):
            self.calls = []

        def apply(self, kelvin):
            self.calls.append(("apply", kelvin))

        def reset(self):
            self.calls.append(("reset",))

        def verify(self):
            self.calls.append(("verify",))

        def set_override(self, display, kelvin):
            self.calls.append(("override", display))

    gamma = Gamma()
    controller = NightLightController(gamma, NightLightConfig(enabled=True, mode=Mode.ALWAYS, night_temperature=3400),
                                      lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
    controller.shutdown()
    gamma.calls.clear()
    controller.verify()
    controller.update()
    controller.preview(2500)
    controller.set_paused({"DISPLAY1": "game.exe, fullscreen"})
    assert gamma.calls == []


# --- Windows resetting the ramps later (LightBulb #223, #448, #462) -----------


class ScreenGdi:
    """A display's gamma ramp as GDI reports it and as the screen shows it.
    An honest driver reports what the screen shows; a dishonest one keeps
    reporting the last ramp it was sent after Windows reset the screen's."""

    def __init__(self, honest=True):
        from nightlight.core.colortemp import identity_ramp

        self.screen = identity_ramp()
        self.reported = identity_ramp()
        self.honest = honest

    def install(self, monkeypatch):
        from nightlight.hardware import win32
        from tests.test_windows_backend import D1, target

        monkeypatch.setattr(win32, "display_targets", lambda: [target(D1, "A")])
        monkeypatch.setattr(win32, "get_gamma_ramp", lambda name: [list(c) for c in (self.screen if self.honest else self.reported)])
        monkeypatch.setattr(win32, "set_gamma_ramp", self.set)

    def set(self, name, ramp):
        self.screen = self.reported = [list(c) for c in ramp]
        return True

    def windows_resets(self):
        """The lock screen, Quick Settings opened for the first time..."""
        from nightlight.core.colortemp import identity_ramp

        self.screen = identity_ramp()
        if self.honest:
            self.reported = identity_ramp()

    @property
    def tinted(self):
        return self.screen[2][-1] < 60000


@pytest.fixture
def fast_reapply(monkeypatch):
    """The reapply timings, ten times faster."""
    monkeypatch.setattr(controller_module, "REAPPLY_FORCE_MS", (0, 20, 60, 120, 200))
    monkeypatch.setattr(controller_module, "REAPPLY_CHECK_MS", (300, 500, 800, 1200))
    monkeypatch.setattr(controller_module, "VERIFY_MS", 60_000)  # only the burst and its checks


def night_light_on_real_backend(monkeypatch, gdi):
    from nightlight.hardware import windows

    gdi.install(monkeypatch)
    controller = make(windows.WindowsGammaBackend(), ALWAYS_ON)
    QTest.qWait(QUICK_FADE_WAIT_MS)
    assert gdi.tinted
    return controller


QUICK_FADE_WAIT_MS = controller_module.QUICK_ADJUST_MS + 300


@pytest.mark.parametrize("honest", [True, False])
def test_a_reset_during_the_burst_is_undone_whatever_the_driver_reports(monkeypatch, fast_reapply, honest):
    gdi = ScreenGdi(honest)
    controller = night_light_on_real_backend(monkeypatch, gdi)
    controller.reapply_now()  # unlocked
    QTest.qWait(40)
    gdi.windows_resets()  # Windows resets it again while the desktop comes back
    assert not gdi.tinted
    QTest.qWait(250)
    assert gdi.tinted  # set again by the burst, even where the driver claims nothing changed
    controller.shutdown()


def test_a_reset_after_the_burst_is_caught_by_the_checks_when_the_driver_is_honest(monkeypatch, fast_reapply):
    gdi = ScreenGdi(honest=True)
    controller = night_light_on_real_backend(monkeypatch, gdi)
    controller.reapply_now()
    QTest.qWait(250)  # the forced sets are over
    gdi.windows_resets()  # Quick Settings, opened for the first time, a moment later (LightBulb #448)
    QTest.qWait(400)
    assert gdi.tinted  # the next check saw it and put it back
    controller.shutdown()


def test_the_regular_check_catches_a_reset_without_any_event(monkeypatch):
    """No event at all (Action Center, LightBulb #223): the 1.5 s check."""
    monkeypatch.setattr(controller_module, "VERIFY_MS", 100)
    gdi = ScreenGdi(honest=True)
    controller = night_light_on_real_backend(monkeypatch, gdi)
    gdi.windows_resets()
    QTest.qWait(250)
    assert gdi.tinted
    controller.shutdown()
