# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timezone

import pytest
from PySide6.QtTest import QTest

from nightlight import controller as controller_module
from nightlight.config import Mode, NightLightConfig
from nightlight.controller import NightLightController, step_towards
from nightlight.core.schedule import OverrideKind

NOON = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)


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
    QTest.qWait(600)
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


def test_unlocking_checks_the_ramps_several_times():
    gamma = PausingGamma()
    controller = make(gamma)
    gamma.verified = 0
    controller.reapply_now()
    QTest.qWait(50)
    assert gamma.verified >= 1  # straight away
    QTest.qWait(3200)
    assert gamma.verified >= len(controller_module.REAPPLY_BURST_MS)
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
