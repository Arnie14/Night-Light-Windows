# SPDX-License-Identifier: GPL-3.0-only
import pytest
from PySide6.QtCore import QObject, Signal

from nightlight.hardware.brightness import MonitorInfo
from nightlight.idle_dim import IdleDimmer

DELL = MonitorInfo(0, "DELL", True, key="DEL41A2")
PANEL = MonitorInfo(1, "Built-in", True, key="BOE0812", internal=True, minimum=1)
TV = MonitorInfo(2, "TV", True, key="SAM7136", control="software", minimum=20)
OLD = MonitorInfo(3, "Old", False, key="ACI0001")


class FakeMonitors(QObject):
    brightnessChanged = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.monitors = [DELL, PANEL, TV, OLD]
        self.brightness = {0: 70, 1: 10, 2: 100}
        self.sent = []

    def restore_brightness(self, index, value, fade=False):
        self.sent.append((index, value, fade))
        self.brightness[index] = value


class FakeMemory:
    def __init__(self):
        self.held = []
        self.targets = {}

    def set_held(self, held):
        self.held.append(held)

    def target(self, monitor):
        return self.targets.get(monitor.key)


@pytest.fixture
def dimmer():
    state = {"idle": 0.0, "display_on": False, "hidden": frozenset()}
    monitors, memory = FakeMonitors(), FakeMemory()
    dimmer = IdleDimmer(monitors, memory, lambda: state["idle"], lambda: state["display_on"], lambda: state["hidden"])
    dimmer.configure(True, 5, 30)
    yield dimmer, monitors, memory, state
    dimmer.configure(False, 5, 30)


def test_dims_after_the_idle_time_and_never_brightens(dimmer):
    dimmer, monitors, memory, state = dimmer
    state["idle"] = 4 * 60
    dimmer.check()
    assert monitors.sent == []  # not yet
    state["idle"] = 5 * 60
    dimmer.check()
    # DELL 70 -> 30, TV 100 -> 30; the laptop panel is already at 10: left alone
    assert monitors.sent == [(0, 30, True), (2, 30, True)]
    assert dimmer.dimmed and memory.held == [True]


def test_input_brings_back_the_levels_from_before(dimmer):
    dimmer, monitors, memory, state = dimmer
    state["idle"] = 600
    dimmer.check()
    monitors.sent.clear()
    state["idle"] = 0.2  # a key was pressed
    dimmer.check()
    assert monitors.sent == [(0, 70, True), (2, 100, True)]
    assert not dimmer.dimmed and memory.held == [True, False]


def test_coming_back_follows_the_schedule_that_moved_on_meanwhile(dimmer):
    dimmer, monitors, memory, state = dimmer
    state["idle"] = 600
    dimmer.check()
    memory.targets = {"DEL41A2": 40}  # a schedule step passed while away
    state["idle"] = 0
    dimmer.check()
    assert (0, 40, True) in monitors.sent and (2, 100, True) in monitors.sent


def test_not_while_something_keeps_the_display_on(dimmer):
    dimmer, monitors, _, state = dimmer
    state["idle"], state["display_on"] = 3600, True  # a long video, or a fullscreen game
    dimmer.check()
    assert monitors.sent == [] and not dimmer.dimmed


def test_hidden_displays_are_left_alone(dimmer):
    dimmer, monitors, _, state = dimmer
    state["idle"], state["hidden"] = 600, frozenset({"DEL41A2"})
    dimmer.check()
    assert [index for index, _, _ in monitors.sent] == [2]


def test_turning_it_off_or_quitting_while_dimmed_restores(dimmer):
    dimmer, monitors, _, state = dimmer
    state["idle"] = 600
    dimmer.check()
    monitors.sent.clear()
    dimmer.configure(False, 5, 30)
    assert monitors.sent == [(0, 70, True), (2, 100, True)]
    dimmer.wake()  # nothing left to restore
    assert len(monitors.sent) == 2


def test_off_by_default_does_nothing():
    monitors, memory = FakeMonitors(), FakeMemory()
    dimmer = IdleDimmer(monitors, memory, lambda: 3600, lambda: False)
    assert not dimmer._timer.isActive()
