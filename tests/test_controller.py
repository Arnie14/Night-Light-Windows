# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timezone

import pytest
from PySide6.QtCore import QCoreApplication
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


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


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
