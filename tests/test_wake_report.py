# SPDX-License-Identifier: GPL-3.0-only
from PySide6.QtCore import QObject, Signal

from nightlight.hardware.brightness import MonitorInfo
from nightlight.wake_report import RESCAN_AFTER_WAKE_S, WakeReport

DELL = MonitorInfo(0, "DELL", True, key="DEL41A2")
LG = MonitorInfo(1, "LG", True, key="GSM5B7F")
OLD = MonitorInfo(2, "Old", False, key="ACI0001")


class FakeMonitors(QObject):
    refreshStarted = Signal()
    refreshed = Signal()

    def __init__(self):
        super().__init__()
        self.monitors = [DELL, LG, OLD]
        self.brightness = {0: 70, 1: 40}
        self.scheduled = []
        self.cancelled = 0

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
    monitors.scan({0: None, 1: None})
    clock["now"] += 3
    monitors.scan({0: 70, 1: None})
    assert report.lines[0].startswith("+2 s: 0 of 2 answering (none; not answering: DELL, LG)")
    assert report.lines[1].startswith("+5 s: 1 of 2 answering (DELL 70%; not answering: LG)")


def test_stops_early_once_everyone_is_back_twice():
    report, monitors, clock = make()
    report.woke()
    for after, readings in ((2, {0: None, 1: None}), (5, {0: 70, 1: 40}), (10, {0: 70, 1: 40})):
        clock["now"] = 1000 + after
        monitors.scan(readings)
    assert monitors.cancelled == 1
    assert report.lines[-1] == "answering again: DELL at +5 s, LG at +5 s (stopped checking at +10 s)"


def test_a_display_that_never_comes_back_is_named():
    report, monitors, clock = make()
    report.woke()
    for after in RESCAN_AFTER_WAKE_S:
        clock["now"] = 1000 + after
        monitors.scan({0: 70, 1: None})
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
    monitors.scan({0: 70, 1: 40})
    assert report.lines == []
