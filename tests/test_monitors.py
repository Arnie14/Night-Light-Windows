# SPDX-License-Identifier: GPL-3.0-only
"""MonitorManager: the service on its thread, seen from the UI as per-display
state (desired, commanded, observed, status) and signals."""

import logging
import threading
import time

import pytest
from PySide6.QtTest import QTest

from nightlight import monitors as monitors_module
from nightlight.hardware.brightness import MonitorGoneError, MonitorInfo, nearest_level, to_percent, to_raw, valid_range
from nightlight.monitors import RESTORE, SCHEDULE, MonitorManager, Status


class SlowBackend:
    def __init__(self, delay=0.05, layout=(("A", True),)):
        self.delay = delay
        self.layout = list(layout)
        self.values = {}
        self.sets = []
        self.lists = 0
        self.gone_once = False
        self.ignores_writes = False
        self.awake = True
        self.release = threading.Event()
        self.release.set()
        self.entered = threading.Event()  # set once a detection is inside list_monitors

    def list_monitors(self):
        self.entered.set()
        self.release.wait(2)
        self.lists += 1
        return [MonitorInfo(i, key, ok, key=key, status="ok" if ok else "unsupported", reason="" if ok else "DDC/CI didn't answer")
                for i, (key, ok) in enumerate(self.layout)]

    def get_brightness(self, index):
        return self.values.get(self.layout[index][0], 50)

    def set_brightness(self, index, value):
        if self.gone_once:
            self.gone_once = False
            raise MonitorGoneError("A")
        time.sleep(self.delay)
        self.sets.append(value)
        if not self.ignores_writes:
            self.values[self.layout[index][0]] = value

    def is_powered_on(self, index):
        return self.awake


def wait_until(condition, timeout_ms=3000):
    for _ in range(timeout_ms // 10):
        if condition():
            return True
        QTest.qWait(10)
    return condition()


@pytest.fixture
def manager():
    managers = []

    def make(backend):
        managers.append(MonitorManager(backend))
        return managers[-1]

    yield make
    for m in managers:
        m.shutdown()


def ready(monitors, key="A"):
    monitors.refresh()
    assert wait_until(lambda: monitors.state(key) is not None and monitors.state(key).status is Status.OK)
    return monitors.state(key)


def test_detection_gives_each_display_a_state_with_its_reading(manager):
    backend = SlowBackend(layout=(("A", True), ("TV", False)))
    backend.values = {"A": 35}
    monitors = manager(backend)
    state = ready(monitors)
    assert (state.observed, state.value, state.controllable) == (35, 35, True)
    tv = monitors.state("TV")
    assert tv.status is Status.UNSUPPORTED and not tv.controllable and tv.message == "DDC/CI didn't answer"


def test_fast_slider_moves_send_only_the_latest_value(manager):
    backend = SlowBackend()
    monitors = manager(backend)
    ready(monitors)
    for value in range(10, 60, 5):
        monitors.set_brightness("A", value)
    assert wait_until(lambda: backend.sets and backend.sets[-1] == 55)
    assert len(backend.sets) <= 2


def test_desired_commanded_and_observed(manager):
    backend = SlowBackend(delay=0.1)
    monitors = manager(backend)
    state = ready(monitors)
    monitors.set_brightness("A", 70)
    assert state.desired == 70 and state.value == 70  # shown while being set
    assert wait_until(lambda: state.commanded == 70)
    assert state.desired is None and state.observed == 70


def test_a_display_that_ignores_writes_is_noticed(manager, monkeypatch, caplog):
    monkeypatch.setattr(monitors_module, "VERIFY_DELAY_MS", 50)
    backend = SlowBackend(delay=0)
    backend.ignores_writes = True
    monitors = MonitorManager(backend)
    try:
        state = ready(monitors)
        with caplog.at_level(logging.WARNING):
            monitors.set_brightness("A", 90)
            assert wait_until(lambda: state.commanded == 90 and state.observed == 50)
        assert "accepted 90% but reports 50%" in caplog.text  # the truth is shown, and logged once
    finally:
        monitors.shutdown()


def test_refresh_requests_during_a_refresh_are_merged(manager):
    backend = SlowBackend()
    backend.release.clear()
    monitors = manager(backend)
    monitors.refresh()
    assert backend.entered.wait(2)  # the first detection is running (and blocked)
    for _ in range(4):
        monitors.refresh()
    backend.release.set()
    # The four made while it ran become exactly one more (displays may have changed meanwhile).
    assert wait_until(lambda: backend.lists == 2, timeout_ms=int(monitors_module.MIN_DETECT_INTERVAL_S * 1000) + 3000)
    QTest.qWait(int(monitors_module.MIN_DETECT_INTERVAL_S * 1000) + 300)
    assert backend.lists == 2


def test_gone_monitor_triggers_detection(manager):
    backend = SlowBackend()
    monitors = manager(backend)
    ready(monitors)
    lists = backend.lists
    backend.gone_once = True
    monitors.set_brightness("A", 30)
    assert wait_until(lambda: backend.lists == lists + 1)


def test_scaling_helpers():
    assert to_percent(25, 0, 50) == 50
    assert to_raw(50, 0, 50) == 25
    assert to_raw(0, 10, 110) == 10 and to_percent(110, 10, 110) == 100
    assert valid_range(0, 30, 100) and not valid_range(0, 0, 0) and not valid_range(0, 120, 100)
    assert nearest_level([0, 25, 50, 75, 100], 60) == 50
    assert nearest_level([], 60) == 60


@pytest.mark.parametrize("awake", [True, None])  # None: the monitor can't tell, so assume awake
def test_restore_sets_an_awake_monitor(manager, awake):
    backend = SlowBackend(delay=0)
    backend.awake = awake
    monitors = manager(backend)
    state = ready(monitors)
    deferred = []
    monitors.restoreDeferred.connect(lambda key, source: deferred.append(key))
    monitors.restore_brightness("A", 30)
    assert wait_until(lambda: state.commanded == 30)
    assert backend.sets == [30] and deferred == []


def test_restore_leaves_a_sleeping_monitor_alone(manager):
    backend = SlowBackend(delay=0)
    backend.awake = False
    monitors = manager(backend)
    state = ready(monitors)
    deferred = []
    monitors.restoreDeferred.connect(lambda key, source: deferred.append((key, source)))
    monitors.restore_brightness("A", 30)
    assert wait_until(lambda: deferred)
    assert deferred == [("A", RESTORE)] and backend.sets == []  # nothing sent: it could wake or hang it
    assert state.status is Status.ASLEEP and not state.controllable and state.message == "In standby"


def test_schedule_changes_count_as_chosen_levels(manager):
    backend = SlowBackend(delay=0)
    monitors = manager(backend)
    ready(monitors)
    chosen = []
    monitors.brightnessSet.connect(lambda key, value, source: chosen.append((key, value, source)))
    monitors.set_brightness("A", 40, source=SCHEDULE)
    monitors.restore_brightness("A", 45)
    assert chosen == [("A", 40, SCHEDULE)]  # restores aren't choices


def test_levels_stay_within_the_displays_range(manager):
    backend = SlowBackend(delay=0)
    monitors = manager(backend)
    state = ready(monitors)
    monitors.set_brightness("A", 150)
    assert state.desired == 100


def test_suspend_pauses_and_resume_waits_for_detection(manager):
    backend = SlowBackend(delay=0)
    monitors = manager(backend)
    state = ready(monitors)
    monitors.suspend()
    assert state.status is Status.PAUSED and not state.controllable
    monitors.set_brightness("A", 20)
    QTest.qWait(100)
    assert backend.sets == []
    lists = backend.lists
    monitors.resume((0.05,))
    assert state.status is Status.DETECTING
    assert wait_until(lambda: backend.sets == [20])
    assert backend.lists == lists + 1  # detected again before writing


def test_changes_for_a_display_that_is_not_connected_are_ignored(manager):
    backend = SlowBackend()
    monitors = manager(backend)
    ready(monitors)
    monitors.set_brightness("not-there", 50)
    QTest.qWait(50)
    assert backend.sets == []


def test_a_hung_monitor_call_is_reported_and_quitting_does_not_wait_for_it(monkeypatch):
    monkeypatch.setattr(monitors_module, "DETECT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(monitors_module, "SHUTDOWN_WAIT_S", 0.3)
    backend = SlowBackend()
    backend.release.clear()  # list_monitors blocks, like a monitor that never answers
    monitors = MonitorManager(backend)
    errors = []
    monitors.error.connect(errors.append)
    monitors.refresh()
    assert wait_until(lambda: errors)
    assert "isn't responding" in errors[0] and "detecting displays" in errors[0]
    QTest.qWait(150)
    assert len(errors) == 1  # once per stuck call, not on every check
    started = time.monotonic()
    monitors.shutdown()
    assert time.monotonic() - started < 1 and monitors.hung
    backend.release.set()  # let the thread finish so the test run can end cleanly


def test_schedules_add_up_instead_of_replacing(manager):
    monitors = manager(SlowBackend())
    monitors.refresh_later((5, 30, 60))  # after waking
    monitors.refresh_later((2, 8))  # a display change soon after
    monitors.refresh_later((2.5,))  # covered by the one at 2 s
    due = sorted(round(d - time.monotonic()) for d, _ in monitors._scheduled)
    assert due == [2, 5, 8, 30, 60]
    monitors.cancel_scheduled()
    assert monitors._scheduled == []


# --- From 0.1.0 beta 3 and 4: fades, retry, refresh signals ----------------------


def test_a_fade_steps_to_the_level(manager):
    backend = SlowBackend(delay=0)
    backend.values = {"A": 20}
    monitors = manager(backend)
    state = ready(monitors)
    shown = []
    monitors.displayChanged.connect(lambda key: shown.append(state.commanded))
    monitors.fade_brightness("A", 80)
    assert wait_until(lambda: state.commanded == 80 and state.desired is None)
    assert backend.sets == [30, 40, 50, 60, 70, 80]  # FADE_STEPS steps from where it was
    assert 50 in shown  # the slider follows the fade


def test_a_newer_change_cuts_a_fade_short(manager):
    backend = SlowBackend(delay=0)
    backend.values = {"A": 20}
    monitors = manager(backend)
    state = ready(monitors)
    monitors.fade_brightness("A", 80)
    assert wait_until(lambda: len(backend.sets) >= 2)
    monitors.set_brightness("A", 25)  # the user drags while it fades
    assert wait_until(lambda: state.commanded == 25 and state.desired is None)
    assert backend.sets[-1] == 25 and 80 not in backend.sets


def test_idle_dimming_is_not_a_choice_and_skips_displays_in_standby(manager):
    from nightlight.monitors import IDLE

    backend = SlowBackend(delay=0)
    monitors = manager(backend)
    state = ready(monitors)
    chosen, deferred = [], []
    monitors.brightnessSet.connect(lambda *args: chosen.append(args))
    monitors.restoreDeferred.connect(lambda key, source: deferred.append((key, source)))
    monitors.restore_brightness("A", 20, source=IDLE)
    assert wait_until(lambda: state.commanded == 20)
    assert chosen == [] and monitors.recently_written("A", 5)
    backend.awake = False
    monitors.restore_brightness("A", 60, source=IDLE)
    assert wait_until(lambda: state.status is Status.ASLEEP)
    QTest.qWait(50)
    # Nothing sent to a display in standby, and not retried: dimming a sleeping
    # display is pointless, and coming back restores with RESTORE, which is.
    assert backend.sets == [20] and deferred == []


def test_a_detection_announces_its_start_and_end_after_reading(manager):
    backend = SlowBackend(delay=0)
    backend.values = {"A": 35}
    monitors = manager(backend)
    events = []
    monitors.refreshStarted.connect(lambda: events.append("started"))
    monitors.observed.connect(lambda key, value: events.append(("observed", key, value)))
    monitors.refreshed.connect(lambda: events.append("refreshed"))
    monitors.refresh()
    assert wait_until(lambda: "refreshed" in events)
    assert events[0] == "started" and events[-1] == "refreshed"
    assert ("observed", "A", 35) in events  # the readings come before "refreshed" (WakeReport counts on it)


def test_a_detection_without_controllable_displays_still_ends(manager):
    monitors = manager(SlowBackend(delay=0, layout=(("TV", False),)))
    ended = []
    monitors.refreshed.connect(lambda: ended.append(True))
    monitors.refresh()
    assert wait_until(lambda: ended)


def test_retry_detects_again_including_skipped_displays(manager):
    backend = SlowBackend(delay=0)
    retried = []
    backend.retry_skipped = lambda: retried.append(backend.lists)
    monitors = manager(backend)
    ready(monitors)
    lists = backend.lists
    monitors.retry()
    assert wait_until(lambda: backend.lists == lists + 1)
    assert retried == [lists]  # cleared before detecting


def test_a_level_windows_reported_counts_as_a_reading(manager):
    backend = SlowBackend(delay=0)
    monitors = manager(backend)
    state = ready(monitors)
    monitors.note_level("A", 64)  # e.g. the laptop's brightness keys
    assert state.observed == 64 and monitors.value("A") == 64
    assert backend.sets == []  # nothing sent


class LaggingBackend(SlowBackend):
    """Like a laptop panel through WMI: right after a change, reads still give the old level."""

    def __init__(self):
        super().__init__(delay=0)
        self.previous = None

    def set_brightness(self, index, value):
        self.previous = self.values.get(self.layout[index][0], 50)
        super().set_brightness(index, value)

    def get_brightness(self, index):
        if self.previous is not None:
            stale, self.previous = self.previous, None
            return stale
        return super().get_brightness(index)


def test_a_reading_that_lags_just_after_a_change_is_not_taken_as_the_truth(manager, monkeypatch, caplog):
    # Found on a real WMI panel: a change made while asleep goes out right
    # after the wake's detection, and the detection's own reading, a moment
    # later, still reports the old level.
    monkeypatch.setattr(monitors_module, "VERIFY_DELAY_MS", 200)
    backend = LaggingBackend()
    backend.values = {"A": 60}
    monitors = manager(backend)
    ready(monitors)
    observed = []
    monitors.observed.connect(lambda key, value: observed.append(value))
    monitors.suspend()
    monitors.set_brightness("A", 100)
    monitors.resume((0.05,))
    with caplog.at_level(logging.WARNING):
        assert wait_until(lambda: monitors.state("A").commanded == 100 and monitors.state("A").status is Status.OK)
        QTest.qWait(400)  # the verification read, once it has settled
    state = monitors.state("A")
    assert state.observed == 100 and state.value == 100
    assert 60 not in observed  # memory and the laptop-key follower never saw the stale level
    assert "accepted" not in caplog.text  # no false "ignores changes" warning


def test_a_display_that_really_ignores_a_change_is_still_noticed_after_settling(manager, monkeypatch, caplog):
    monkeypatch.setattr(monitors_module, "VERIFY_DELAY_MS", 1200)
    backend = SlowBackend(delay=0)
    backend.ignores_writes = True
    monitors = manager(backend)
    state = ready(monitors)
    with caplog.at_level(logging.WARNING):
        monitors.set_brightness("A", 90)
        assert wait_until(lambda: state.commanded == 90)
        monitors.read("A")  # an early reading: not trusted yet
        QTest.qWait(100)
        assert state.observed == 90
        assert wait_until(lambda: state.observed == 50, 3000)  # the verification read: the truth
    assert "accepted 90% but reports 50%" in caplog.text


class PanelBackend(SlowBackend):
    """A laptop panel through WMI, which Windows itself eases to each new level."""

    def list_monitors(self):
        from dataclasses import replace

        return [replace(m, channel="wmi", internal=True) for m in super().list_monitors()]


def test_a_wmi_panel_is_not_faded_on_top_of_windows_own_easing(manager):
    # Seen on a real laptop: Windows eases the backlight on every WMI write, so
    # six fade steps made it bounce (Windows reported 85, 80, 85, 75, 70, 75…).
    backend = PanelBackend(delay=0)
    backend.values = {"A": 20}
    monitors = manager(backend)
    ready(monitors)
    monitors.fade_brightness("A", 80)
    assert wait_until(lambda: monitors.state("A").commanded == 80)
    assert backend.sets == [80]


def test_a_level_windows_reported_is_announced_as_changed_elsewhere(manager):
    monitors = manager(SlowBackend(delay=0))
    ready(monitors)
    seen = []
    monitors.changedElsewhere.connect(lambda key, value: seen.append((key, value)))
    chosen = []
    monitors.brightnessSet.connect(lambda *args: chosen.append(args))
    monitors.note_level("A", 45)
    assert seen == [("A", 45)] and chosen == []  # not a choice made in the app


# --- HDR monitors that lock their brightness ------------------------------------


class HdrBackend(SlowBackend):
    """One HDR monitor on DDC/CI whose brightness is locked in HDR mode (writes
    accepted and ignored, or refused), and Windows' SDR content brightness."""

    def __init__(self, refuses=False):
        super().__init__(delay=0)
        self.ignores_writes = not refuses
        self.refuses = refuses
        self.locked = set()
        self.sdr_sets = []
        self.values = {"A": 50}

    def list_monitors(self):
        if "A" in self.locked:
            return [MonitorInfo(0, "HDR monitor", True, key="A", channel="sdr", control="sdr", hdr=True)]
        return [MonitorInfo(0, "HDR monitor", True, key="A", channel="ddc", hdr=True)]

    def set_brightness(self, index, value):
        if "A" in self.locked:
            self.sdr_sets.append(value)
            return
        if self.refuses:
            raise OSError("Setting VCP 0x10 failed")
        super().set_brightness(index, value)

    def mark_brightness_locked(self, key):
        self.locked.add(key)
        return True


@pytest.mark.parametrize("refuses", [False, True], ids=["ignores changes", "refuses changes"])
def test_an_hdr_monitor_that_locks_its_brightness_moves_to_sdr_brightness(manager, monkeypatch, refuses):
    monkeypatch.setattr(monitors_module, "VERIFY_DELAY_MS", 1100)
    backend = HdrBackend(refuses=refuses)
    monitors = manager(backend)
    ready(monitors)
    monitors.set_brightness("A", 30)
    if refuses:
        assert wait_until(lambda: monitors.state("A").failures == 1)
        monitors.set_brightness("A", 31)  # refused twice
    assert wait_until(lambda: monitors.state("A").info.channel == "sdr", 5000)
    assert wait_until(lambda: backend.sdr_sets, 3000)
    assert backend.sdr_sets[-1] == (31 if refuses else 30)  # what the user chose last, now through SDR
    assert backend.locked == {"A"}


def test_an_hdr_monitor_that_takes_changes_stays_on_ddc(manager, monkeypatch):
    monkeypatch.setattr(monitors_module, "VERIFY_DELAY_MS", 1100)
    backend = HdrBackend()
    backend.ignores_writes = False  # this one works in HDR
    monitors = manager(backend)
    ready(monitors)
    monitors.set_brightness("A", 30)
    assert wait_until(lambda: monitors.state("A").commanded == 30)
    for _ in range(32):  # past the verification read (short waits: a long QTest.qWait starves the monitor thread)
        QTest.qWait(50)
    assert monitors.state("A").info.channel == "ddc" and backend.locked == set() and monitors.state("A").observed == 30
