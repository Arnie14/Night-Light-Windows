# SPDX-License-Identifier: GPL-3.0-only
import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest

from nightlight.hardware.brightness import MonitorInfo
from nightlight.idle_dim import IdleDimmer

D1, D2, D3, D4, D5 = (rf"\\.\DISPLAY{n}" for n in range(1, 6))
DELL = MonitorInfo(0, "DELL", True, key="DEL41A2", display=D1)  # DDC/CI
PANEL = MonitorInfo(1, "Built-in", True, key="BOE0812", internal=True, minimum=1, display=D2)  # WMI
TV = MonitorInfo(2, "TV", True, key="SAM7136", control="software", minimum=20, display=D3)
HDR_SDR = MonitorInfo(3, "HDR TV", True, key="LGE0001", control="sdr", display=D4, hdr=True)
HDR_DDC = MonitorInfo(4, "HDR monitor", True, key="GSM0001", display=D5, hdr=True)
OLD = MonitorInfo(5, "Old", False, key="ACI0001", display=r"\\.\DISPLAY6")


class FakeMonitors(QObject):
    brightnessChanged = Signal(int, object)

    def __init__(self):
        super().__init__()
        self.monitors = [DELL, PANEL, TV, HDR_SDR, HDR_DDC, OLD]
        self.brightness = {0: 70, 1: 60, 2: 100, 3: 50, 4: 80}
        self.sent = []

    def restore_brightness(self, index, value, fade=False):
        self.sent.append((index, value))
        self.brightness[index] = value


class FakeMemory:
    def __init__(self):
        self.held = []
        self.targets = {}

    def set_held(self, held):
        self.held.append(held)

    def target(self, monitor):
        return self.targets.get(monitor.key)


class FakeGamma:
    def __init__(self):
        self.idle = []  # every set_idle_dim call

    def set_idle_dim(self, levels):
        self.idle.append(dict(levels))


@pytest.fixture
def dimmer():
    state = {"idle": 0.0, "display_on": False, "hidden": frozenset()}
    monitors, memory, gamma = FakeMonitors(), FakeMemory(), FakeGamma()
    dimmer = IdleDimmer(monitors, memory, lambda: state["idle"], lambda: state["display_on"], lambda: state["hidden"], gamma)
    dimmer.configure(True, 5, 30)
    yield dimmer, monitors, memory, gamma, state
    dimmer.configure(False, 5, 30)


def settle():
    QTest.qWait(700)  # the software fade's steps


def test_each_kind_of_display_is_dimmed_the_safe_way(dimmer):
    dimmer, monitors, memory, gamma, state = dimmer
    state["idle"] = 4 * 60
    dimmer.check()
    assert monitors.sent == [] and gamma.idle == []  # not yet
    state["idle"] = 5 * 60
    dimmer.check()
    settle()
    # Laptop panel through WMI (saves power), the HDR display through its SDR level:
    assert monitors.sent == [(1, 30), (3, 30)]
    # DDC/CI monitor, TV and the display without control: in software, no DDC/CI writes;
    # the HDR monitor controlled over DDC/CI is left alone (gamma does nothing in HDR).
    assert gamma.idle[-1] == {D1: 30, D3: 30, r"\\.\DISPLAY6": 30}
    assert len(gamma.idle) > 3  # faded, not jumped
    assert dimmer.dimmed and memory.held == [True]


def test_input_brings_everything_back(dimmer):
    dimmer, monitors, memory, gamma, state = dimmer
    state["idle"] = 600
    dimmer.check()
    settle()
    monitors.sent.clear()
    state["idle"] = 0.2  # a key was pressed
    dimmer.check()
    settle()
    assert monitors.sent == [(1, 60), (3, 50)]
    assert gamma.idle[-1] == {}  # the software layer is gone
    assert not dimmer.dimmed and memory.held == [True, False]


def test_never_brightens(dimmer):
    dimmer, monitors, _, _, state = dimmer
    monitors.brightness[1] = 10  # the laptop panel is already darker than 30%
    state["idle"] = 600
    dimmer.check()
    assert (1, 30) not in monitors.sent


def test_coming_back_follows_the_schedule_that_moved_on_meanwhile(dimmer):
    dimmer, monitors, memory, _, state = dimmer
    state["idle"] = 600
    dimmer.check()
    memory.targets = {"BOE0812": 40}  # a schedule step passed while away
    state["idle"] = 0
    dimmer.check()
    assert (1, 40) in monitors.sent


def test_not_while_something_keeps_the_display_on(dimmer):
    dimmer, monitors, _, gamma, state = dimmer
    state["idle"], state["display_on"] = 3600, True  # a video, a fullscreen game, a presentation
    dimmer.check()
    assert monitors.sent == [] and gamma.idle == [] and not dimmer.dimmed


def test_hidden_displays_are_left_alone(dimmer):
    dimmer, monitors, _, gamma, state = dimmer
    state["idle"], state["hidden"] = 600, frozenset({"DEL41A2", "BOE0812"})
    dimmer.check()
    settle()
    assert monitors.sent == [(3, 30)]
    assert D1 not in gamma.idle[-1]


def test_quitting_while_dimmed_undoes_it_at_once(dimmer):
    dimmer, monitors, _, gamma, state = dimmer
    state["idle"] = 600
    dimmer.check()
    monitors.sent.clear()
    dimmer.release()
    assert monitors.sent == [(1, 60), (3, 50)] and gamma.idle[-1] == {}
    assert not dimmer._fade_timer.isActive()


def test_off_by_default_does_nothing():
    dimmer = IdleDimmer(FakeMonitors(), FakeMemory(), lambda: 3600, lambda: False)
    assert not dimmer._timer.isActive()


def test_without_a_gamma_backend_only_the_safe_hardware_paths_dim():
    monitors = FakeMonitors()
    dimmer = IdleDimmer(monitors, FakeMemory(), lambda: 3600, lambda: False)
    dimmer.configure(True, 5, 30)
    dimmer.check()
    assert monitors.sent == [(1, 30), (3, 30)]  # never DDC/CI, even without software dimming
    dimmer.configure(False, 5, 30)
