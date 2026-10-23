# SPDX-License-Identifier: GPL-3.0-only
import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest

from nightlight.brightness import power_source
from nightlight.brightness.monitors import RESTORE
from nightlight.brightness.power_source import PowerSourceKeeper
from nightlight.hardware.brightness import MonitorInfo

PANEL = MonitorInfo(0, "Built-in display", True, key="BOE0812", internal=True)
DELL = MonitorInfo(1, "DELL", True, key="DEL41A2")


class FakeMonitors(QObject):
    observed = Signal(str, object)
    brightnessSet = Signal(str, int, str)
    changedElsewhere = Signal(str, int)
    monitorsChanged = Signal(list)

    def __init__(self):
        super().__init__()
        self.monitors = [PANEL, DELL]
        self.levels = {"BOE0812": 60, "DEL41A2": 60}
        self.sent, self.reads = [], []

    def value(self, key):
        return self.levels.get(key)

    def read(self, key):
        self.reads.append(key)

    def set_brightness(self, key, value, source="user", fade=False):
        self.sent.append((key, value, source))
        self.levels[key] = value


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def keeper(monkeypatch):
    monkeypatch.setattr(power_source, "READ_AFTER_MS", 10)
    monkeypatch.setattr(power_source, "RESTORE_AFTER_MS", 30)
    monitors, clock = FakeMonitors(), Clock()
    keeper = PowerSourceKeeper(monitors, clock=clock)
    keeper.set_enabled(True)
    monitors.observed.emit("BOE0812", 60)  # seen a while ago
    clock.now += 60
    return keeper, monitors, clock


def test_windows_changing_the_panel_for_the_power_source_is_put_back(keeper):
    keeper, monitors, clock = keeper
    keeper.power_source_changed()  # unplugged
    assert keeper.settling() and keeper.absorbs(40)  # the power plan's battery level: not the user's
    monitors.levels["BOE0812"] = 40
    QTest.qWait(80)
    assert monitors.reads == ["BOE0812"]  # read again first
    assert monitors.sent == [("BOE0812", 60, RESTORE)]  # the level from before; external displays untouched
    clock.now += power_source.SETTLE_S
    assert not keeper.settling() and not keeper.absorbs(30)  # later changes are the user's again


def test_a_change_reported_just_before_the_message_is_windows_too(keeper):
    keeper, monitors, clock = keeper
    monitors.changedElsewhere.emit("BOE0812", 40)  # Windows' change came first
    monitors.levels["BOE0812"] = 40
    clock.now += 0.2
    keeper.power_source_changed()
    QTest.qWait(80)
    assert monitors.sent == [("BOE0812", 60, RESTORE)]


def test_nothing_is_written_when_windows_kept_the_level(keeper):
    keeper, monitors, _clock = keeper
    keeper.power_source_changed()
    QTest.qWait(80)
    assert monitors.sent == []


def test_off_by_default_nothing_is_held_or_put_back():
    monitors = FakeMonitors()
    keeper = PowerSourceKeeper(monitors)
    keeper.power_source_changed()
    assert not keeper.settling() and not keeper.absorbs(40)
    QTest.qWait(50)
    assert monitors.sent == [] and monitors.reads == []
