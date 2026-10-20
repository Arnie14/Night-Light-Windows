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
    """winsystem.SystemEvents' signals (test_the_fake_matches_the_real_events checks)."""

    suspending = Signal()
    resumed = Signal()
    displayOff = Signal()
    displayOn = Signal()
    locked = Signal()
    unlocked = Signal()
    displayChanged = Signal()
    clockChanged = Signal()
    endingSession = Signal()
    panelBrightness = Signal(int)


def signal_names(cls):
    from PySide6.QtCore import QMetaMethod

    meta = cls.staticMetaObject
    return {bytes(meta.method(i).name()).decode() for i in range(meta.methodOffset(), meta.methodCount())
            if meta.method(i).methodType() == QMetaMethod.MethodType.Signal}


def test_the_fake_matches_the_real_events():
    from nightlight.hardware.winsystem import SystemEvents

    assert signal_names(FakeEvents) == signal_names(SystemEvents)


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
    events.displayChanged.emit()
    assert not w._settle.isActive()
    events.resumed.emit()
    events.displayChanged.emit()
    assert w._settle.isActive()


def test_power_and_session_events_pass_through():
    events = FakeEvents()
    w, clock, seen = watcher(events)
    events.displayOff.emit()
    events.displayOn.emit()
    events.unlocked.emit()  # the app puts night light back itself; displays weren't off
    events.locked.emit()
    events.clockChanged.emit()
    assert seen == ["displaysOff", "displaysOn", "clockChanged"]


# --- From the audit: what the watcher must not get stuck on --------------------


def test_a_sleep_that_never_happened_does_not_leave_the_displays_asleep():
    # A sleep that failed or was cancelled sends no resume: the displays stayed
    # "asleep" (nothing sent to them, display changes ignored) for the session.
    events = FakeEvents()
    w, clock, seen = watcher(events)
    events.suspending.emit()
    for _ in range(18):  # the clock checks keep running normally for 90 s: the PC is awake
        clock.now += 5
        w.check_clock()
    assert seen == ["suspending"]  # not yet: Windows may still be on its way to sleep
    clock.now += 5
    w.check_clock()
    assert seen == ["suspending", "resumed"]
    events.displayChanged.emit()
    assert w._settle.isActive()  # display changes count again


def test_changing_the_clock_is_not_taken_for_waking_up():
    events = FakeEvents()
    w, clock, seen = watcher(events)
    clock.now += 3600  # the clock was set an hour forward...
    events.clockChanged.emit()  # ...and Windows said so
    w.check_clock()
    assert seen == ["clockChanged"]


def test_input_while_the_displays_are_reported_off_means_they_are_on():
    # A missed "display on" notification left them "off" until the next sleep.
    events = FakeEvents()
    idle = {"s": 300.0}
    clock = Clock()
    w = DisplayWatcher(QApplication.instance(), clock=clock, events=events, idle_seconds=lambda: idle["s"])
    seen = []
    for name in ("displaysOff", "displaysOn"):
        getattr(w, name).connect(lambda name=name: seen.append(name))
    events.displayOff.emit()
    clock.now += 5
    w.check_clock()
    assert seen == ["displaysOff"]  # no input: still off
    idle["s"] = 1.0  # a key was pressed
    clock.now += 5
    w.check_clock()
    assert seen == ["displaysOff", "displaysOn"]
    clock.now += 5
    w.check_clock()
    assert seen == ["displaysOff", "displaysOn"]  # once
