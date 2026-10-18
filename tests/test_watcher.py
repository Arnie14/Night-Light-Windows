# SPDX-License-Identifier: GPL-3.0-only
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from nightlight.watcher import DisplayWatcher


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeEvents(QObject):
    suspending = Signal()
    resumed = Signal()
    displaysOff = Signal()
    displaysOn = Signal()
    locked = Signal()
    unlocked = Signal()
    displaysChanged = Signal()
    clockChanged = Signal()
    endingSession = Signal()


def watcher(events=None):
    clock = Clock()
    w = DisplayWatcher(QApplication.instance(), clock=clock, events=events)
    seen = []
    for name in ("suspending", "resumed", "displaysOff", "displaysOn", "clockChanged"):
        getattr(w, name).connect(lambda name=name: seen.append(name))
    return w, clock, seen


def test_resume_is_noticed_from_a_clock_jump():
    w, clock, seen = watcher()
    clock.now += 5
    w.check_clock()
    assert seen == []
    clock.now += 600  # slept for ten minutes
    w.check_clock()
    assert seen == ["resumed"]


def test_one_wake_gives_one_resume_even_with_both_sources():
    events = FakeEvents()
    w, clock, seen = watcher(events)
    events.suspending.emit()
    clock.now += 3600
    events.resumed.emit()  # Windows says so...
    clock.now += 5
    w.check_clock()  # ...and the clock jump is the same wake
    assert seen == ["suspending", "resumed"]


def test_the_clock_still_notices_a_wake_windows_did_not_report():
    events = FakeEvents()
    w, clock, seen = watcher(events)
    clock.now += 3600
    w.check_clock()
    assert seen == ["resumed"]


def test_display_changes_are_ignored_while_suspending():
    events = FakeEvents()
    w, clock, seen = watcher(events)
    events.suspending.emit()
    events.displaysChanged.emit()
    assert not w._settle.isActive()
    events.resumed.emit()
    events.displaysChanged.emit()
    assert w._settle.isActive()


def test_power_and_session_events_pass_through():
    events = FakeEvents()
    w, clock, seen = watcher(events)
    events.displaysOff.emit()
    events.displaysOn.emit()
    events.unlocked.emit()
    events.clockChanged.emit()
    assert seen == ["displaysOff", "displaysOn", "displaysOn", "clockChanged"]
