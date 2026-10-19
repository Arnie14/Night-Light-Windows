# SPDX-License-Identifier: GPL-3.0-only
from PySide6.QtCore import QObject, Signal

from nightlight.hardware.brightness import MonitorInfo
from nightlight.monitors import DisplayState, Status
from nightlight.wake_report import RESCAN_AFTER_WAKE_S, WakeReport

DELL = MonitorInfo(0, "DELL", True, key="DEL41A2")
LG = MonitorInfo(1, "LG", True, key="GSM5B7F")
OLD = MonitorInfo(2, "Old", False, key="ACI0001")


class FakeMonitors(QObject):
    """MonitorManager as WakeReport reads it (by display key): a reading
    counts only if it came in this pass (status OK)."""

    refreshStarted = Signal()
    refreshed = Signal()

    def __init__(self):
        super().__init__()
        self.monitors = [DELL, LG, OLD]
        self.brightness = {"DEL41A2": 70, "GSM5B7F": 40}
        self.scheduled = []
        self.cancelled = 0

    def value(self, key):
        return self.brightness.get(key)

    def state(self, key):
        info = next(m for m in self.monitors if m.key == key)
        value = self.brightness.get(key)
        return DisplayState(info, Status.OK if value is not None else Status.NOT_RESPONDING, observed=value)

    def refresh_later(self, delays):
        self.scheduled.append(tuple(delays))

    def cancel_scheduled(self):
        self.cancelled += 1

    def scan(self, readings):
        self.refreshStarted.emit()
        self.brightness = readings
        self.refreshed.emit()


def make():
    clock = {"now": 1000.0}
    monitors = FakeMonitors()
    return WakeReport(monitors, clock=lambda: clock["now"]), monitors, clock


def test_each_pass_after_waking_is_logged_with_its_timing():
    report, monitors, clock = make()
    assert report.woke()
    assert monitors.scheduled == [RESCAN_AFTER_WAKE_S]
    clock["now"] += 2
    monitors.scan({"DEL41A2": None, "GSM5B7F": None})
    clock["now"] += 3
    monitors.scan({"DEL41A2": 70, "GSM5B7F": None})
    assert report.lines[0].startswith("+2 s: 0 of 2 answering (none; not answering: DELL, LG)")
    assert report.lines[1].startswith("+5 s: 1 of 2 answering (DELL 70%; not answering: LG)")


def test_stops_early_once_everyone_is_back_twice():
    report, monitors, clock = make()
    report.woke()
    for after, readings in ((2, {"DEL41A2": None, "GSM5B7F": None}), (5, {"DEL41A2": 70, "GSM5B7F": 40}),
                            (10, {"DEL41A2": 70, "GSM5B7F": 40})):
        clock["now"] = 1000 + after
        monitors.scan(readings)
    assert monitors.cancelled == 1
    assert report.lines[-1] == "answering again: DELL at +5 s, LG at +5 s (stopped checking at +10 s)"


def test_a_display_that_never_comes_back_is_named():
    report, monitors, clock = make()
    report.woke()
    for after in RESCAN_AFTER_WAKE_S:
        clock["now"] = 1000 + after
        monitors.scan({"DEL41A2": 70, "GSM5B7F": None})
    assert monitors.cancelled == 0
    assert report.lines[-1] == "answering again: DELL at +2 s; never answered: LG"


def test_the_same_wake_reported_twice_counts_once():
    report, monitors, clock = make()
    assert report.woke()
    clock["now"] += 5
    assert not report.woke()  # the clock check noticing the same wake
    assert monitors.scheduled == [RESCAN_AFTER_WAKE_S]


def test_ordinary_detections_are_not_reported():
    report, monitors, _ = make()
    monitors.scan({"DEL41A2": 70, "GSM5B7F": 40})
    assert report.lines == []


def test_a_level_from_before_sleep_is_not_an_answer():
    # After resume every display is "detecting", still showing its old level:
    # only a reading in this pass counts as answering.
    report, monitors, clock = make()
    report.woke()
    stale = DisplayState(DELL, Status.DETECTING, observed=70)
    monitors.state = lambda key: stale if key == "DEL41A2" else DisplayState(LG, Status.OK, observed=40)
    clock["now"] += 2
    monitors.scan({"DEL41A2": 70, "GSM5B7F": 40})
    assert report.lines[0].startswith("+2 s: 1 of 2 answering (LG 40%; not answering: DELL)")


def test_displays_that_did_not_answer_before_sleep_are_not_waited_for():
    report, monitors, clock = make()
    monitors.brightness = {"DEL41A2": 70}  # the LG wasn't answering before sleep either
    report.woke()
    monitors.brightness = {"DEL41A2": 70}
    for after in (2, 5):
        clock["now"] = 1000 + after
        monitors.scan({"DEL41A2": 70})
    assert monitors.cancelled == 1  # everyone expected is back: stop early
