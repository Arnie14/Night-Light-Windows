# SPDX-License-Identifier: GPL-3.0-only
"""Brightness by time of day: the schedule's maths (pure), and the scheduler
that applies it idempotently across restarts, sleep, overrides and daylight
saving changes."""

import itertools
import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QObject, Signal

from nightlight.brightness.monitors import SCHEDULE, USER, DisplayState, Status
from nightlight.brightness.scheduler import BrightnessScheduler
from nightlight.config import Anchor, BrightnessScheduleConfig, SchedulePoint
from nightlight.core.brightness_schedule import PRESETS, boundaries, describe, scheduled_level
from nightlight.hardware.brightness import MonitorInfo

UTC = UTC
NEW_YORK = ZoneInfo("America/New_York")
# The point logic without a transition (the default has one, as its preset does).
DAY_NIGHT = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(100, "07:00"), SchedulePoint(50, "20:00")), transition_minutes=0)


def at(day, hour, minute=0, zone=UTC, month=6, fold=0):
    return datetime(2026, month, day, hour, minute, tzinfo=zone, fold=fold)


# --- The schedule itself -------------------------------------------------------


def test_presets_are_valid():
    for key, preset in PRESETS.items():
        config = BrightnessScheduleConfig(enabled=True, preset=key, points=preset.points, transition_minutes=preset.transition_minutes)
        assert config.normalized() == config, key
        assert len(preset.points) >= 2 and preset.name and preset.description
        assert scheduled_level(config, at(15, 12)) is not None


@pytest.mark.parametrize(("hour", "level", "since"), [(12, 100, 7), (23, 50, 20), (3, 50, 20), (7, 100, 7), (6, 50, 20)])
def test_the_latest_point_that_has_passed_sets_the_level(hour, level, since):
    state = scheduled_level(DAY_NIGHT, at(15, hour))
    assert state.level == level and state.since.at.hour == since and not state.transitioning


def test_overnight_the_point_from_yesterday_still_applies():
    state = scheduled_level(DAY_NIGHT, at(15, 3))
    assert state.since.at == at(14, 20) and state.next.at == at(15, 7)


def test_off_or_empty_gives_nothing():
    assert scheduled_level(BrightnessScheduleConfig(enabled=False), at(15, 12)) is None


def test_two_points_at_the_same_time_fire_once_and_the_later_one_wins():
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(100, "07:00"), SchedulePoint(60, "20:00"), SchedulePoint(40, "20:00")))
    laid_out, _ = boundaries(config, at(15, 12))
    assert sum(1 for b in laid_out if b.at == at(15, 20)) == 1
    assert scheduled_level(config, at(15, 21)).level == 40


def test_a_sunset_point_and_a_clock_point_on_the_same_instant_merge():
    # With no location, sunset falls back to 18:00: the same instant as the clock point.
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(100, "07:00"), SchedulePoint(70, "18:00"), SchedulePoint(30, anchor=Anchor.SUNSET)))
    laid_out, approximate = boundaries(config, at(15, 12))
    assert approximate and sum(1 for b in laid_out if b.at == at(15, 18)) == 1
    assert scheduled_level(config, at(15, 20)).level == 30


def test_sun_points_follow_the_location():
    points = (SchedulePoint(100, anchor=Anchor.SUNRISE), SchedulePoint(40, anchor=Anchor.SUNSET, offset_minutes=30))
    config = BrightnessScheduleConfig(enabled=True, points=points)
    london = (51.5, -0.13)
    state = scheduled_level(config, at(21, 23), location=london)  # midsummer: sunset about 20:21 UTC
    assert not state.approximate_sun and state.level == 40
    assert at(21, 20, 40) < state.since.at < at(21, 21, 10)


def test_sun_points_without_a_location_use_6_and_18():
    # The same fallback times as 0.1.0 beta 4.
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(100, anchor=Anchor.SUNRISE), SchedulePoint(40, anchor=Anchor.SUNSET)))
    state = scheduled_level(config, at(15, 20))
    assert state.approximate_sun and state.since.at == at(15, 18)
    assert scheduled_level(config, at(15, 12)).since.at == at(15, 6)


def test_polar_day_falls_back_too():
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(100, anchor=Anchor.SUNRISE), SchedulePoint(40, anchor=Anchor.SUNSET)))
    assert scheduled_level(config, at(21, 20), location=(78.2, 15.6)).approximate_sun  # Svalbard in June


def test_gradual_transition():
    config = BrightnessScheduleConfig(enabled=True, points=DAY_NIGHT.points, transition_minutes=30)
    halfway = scheduled_level(config, at(15, 20, 15))
    assert halfway.level == 75 and halfway.transitioning
    done = scheduled_level(config, at(15, 20, 30))
    assert done.level == 50 and not done.transitioning


def test_a_transition_never_runs_into_the_next_point():
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(100, "07:00"), SchedulePoint(50, "20:00"), SchedulePoint(20, "20:10")),
                                      transition_minutes=60)
    assert scheduled_level(config, at(15, 20, 5)).level == 75  # halfway over the 10 minutes it has
    assert scheduled_level(config, at(15, 20, 10)).since.level == 20


def test_describe():
    assert describe(DAY_NIGHT) == "100% at 07:00, 50% at 20:00"
    assert describe(BrightnessScheduleConfig(points=(SchedulePoint(40, anchor=Anchor.SUNSET, offset_minutes=-30),))) == "40% at 30 min before sunset"
    assert describe(BrightnessScheduleConfig(points=(SchedulePoint(100, anchor=Anchor.SUNRISE),))) == "100% at sunrise"


# --- Daylight saving time (America/New_York: 8 March and 1 November 2026) ---


def test_spring_forward_a_point_in_the_skipped_hour_happens_right_after_it():
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(30, "02:30"), SchedulePoint(100, "09:00")))
    laid_out, _ = boundaries(config, at(8, 12, zone=NEW_YORK, month=3), NEW_YORK)
    on_the_day = [b for b in laid_out if b.at.astimezone(NEW_YORK).date().day == 8 and b.level == 30]
    assert len(on_the_day) == 1
    assert on_the_day[0].at.astimezone(UTC) == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)  # 03:30 EDT
    before = datetime(2026, 3, 8, 7, 15, tzinfo=UTC)  # 03:15 EDT, before it
    assert scheduled_level(config, before, NEW_YORK).level == 100


def test_fall_back_a_point_in_the_repeated_hour_happens_once():
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(30, "01:30"), SchedulePoint(100, "09:00")))
    first = datetime(2026, 11, 1, 5, 40, tzinfo=UTC)  # 01:40 EDT
    second = datetime(2026, 11, 1, 6, 40, tzinfo=UTC)  # 01:40 EST, the hour again
    a, b = scheduled_level(config, first, NEW_YORK), scheduled_level(config, second, NEW_YORK)
    assert a.since.id == b.since.id  # the same point: not applied a second time
    assert a.since.at.astimezone(UTC) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_points_stay_at_their_wall_clock_time_across_the_change():
    config = DAY_NIGHT
    saturday = scheduled_level(config, datetime(2026, 3, 8, 3, 0, tzinfo=UTC), NEW_YORK)  # Sat 22:00 EST
    # The next morning point is 07:00 EDT (11:00 UTC), not 07:00 EST (12:00 UTC).
    assert saturday.next.at.astimezone(UTC) == datetime(2026, 3, 8, 11, 0, tzinfo=UTC)
    assert saturday.next.at.astimezone(NEW_YORK).hour == 7


def test_a_transition_across_the_change_lasts_its_real_length():
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(100, "09:00"), SchedulePoint(40, "01:00")), transition_minutes=60)
    start = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)  # 01:00 EDT; an hour later it is 01:00 EST
    assert scheduled_level(config, start + timedelta(minutes=30), NEW_YORK).level == 70
    assert not scheduled_level(config, start + timedelta(minutes=60), NEW_YORK).transitioning


# --- The scheduler: idempotent application --------------------------------------


class FakeMonitors(QObject):
    monitorsChanged = Signal(list)
    displayChanged = Signal(str)
    brightnessSet = Signal(str, int, str)
    restoreDeferred = Signal(str, str)
    changedElsewhere = Signal(str, int)

    def __init__(self):
        super().__init__()
        self.states = {}
        self.sent = []
        self.faded = []  # whether each change faded

    def state(self, key):
        return self.states.get(key)

    def detect(self, *infos, status=Status.READING):
        self.states = {i.key: DisplayState(i, status if i.supported else Status.UNSUPPORTED) for i in infos}
        self.monitorsChanged.emit(list(infos))

    def set_brightness(self, key, value, source=USER, fade=False):
        self.sent.append((key, value, source))
        self.faded.append(fade)
        self.brightnessSet.emit(key, value, source)

    def set_status(self, key, status):
        self.states[key].status = status
        self.displayChanged.emit(key)


DELL = MonitorInfo(0, "DELL", True, key="DEL")
LG = MonitorInfo(1, "LG", True, key="GSM")
PANEL = MonitorInfo(2, "Panel", True, key="BOE", internal=True, minimum=1)
TV = MonitorInfo(3, "TV", False, key="TV")


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def scheduler(tmp_path):
    made = []

    def make(config=DAY_NIGHT, now=at(15, 12), monitors=None, state=None, zone=None):
        path = tmp_path / "schedule.json"
        if state is not None:
            path.write_text(json.dumps(state))
        monitors = monitors or FakeMonitors()
        clock = Clock(now)
        sched = BrightnessScheduler(monitors, config, path, clock=clock, zone=zone)
        made.append(sched)
        return sched, monitors, clock

    yield make
    for sched in made:
        sched.shutdown()


def test_the_current_point_is_applied_to_every_display_once(scheduler):
    sched, monitors, clock = scheduler()
    monitors.detect(DELL, LG, TV)
    assert monitors.sent == [("DEL", 100, SCHEDULE), ("GSM", 100, SCHEDULE)]  # not the TV: no control
    for _ in range(5):
        sched.evaluate("tick")
    assert len(monitors.sent) == 2  # idempotent: asking again changes nothing


def test_a_user_change_stands_until_the_next_point(scheduler):
    sched, monitors, clock = scheduler()
    monitors.detect(DELL)
    monitors.set_brightness("DEL", 60)  # the user, at 12:00
    clock.now = at(15, 19, 59)
    sched.evaluate("tick")
    assert monitors.sent[-1] == ("DEL", 60, USER)
    clock.now = at(15, 20, 1)
    sched.evaluate("timer")
    assert monitors.sent[-1] == ("DEL", 50, SCHEDULE)  # the evening point applies, override or not


def test_restarting_within_a_point_leaves_restored_levels_alone(scheduler, tmp_path):
    first, monitors, clock = scheduler()
    monitors.detect(DELL)
    saved = json.loads((tmp_path / "schedule.json").read_text())
    first.shutdown()
    # Restart at 15:00: the 07:00 point was applied before, so the level the
    # user picked since (restored by BrightnessMemory) stands.
    sched, monitors2, _ = scheduler(now=at(15, 15), state=saved)
    sched.evaluate("start")  # before any detection
    monitors2.detect(DELL)
    assert monitors2.sent == []
    monitors2.detect(DELL, LG)  # plugged in later: it gets the level
    assert monitors2.sent == [("GSM", 100, SCHEDULE)]


def test_starting_after_a_point_passed_while_not_running_applies_it(scheduler):
    yesterday_evening = {"version": 1, "applied": at(14, 20).isoformat()}
    sched, monitors, _ = scheduler(now=at(15, 9), state=yesterday_evening)
    monitors.detect(DELL)
    assert monitors.sent == [("DEL", 100, SCHEDULE)]


def test_sleeping_through_several_points_applies_only_the_latest(scheduler):
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(100, "07:00"), SchedulePoint(70, "18:00"), SchedulePoint(40, "22:00")))
    sched, monitors, clock = scheduler(config=config, now=at(15, 12))
    monitors.detect(DELL)
    clock.now = at(15, 23, 30)  # slept from 12:00 to 23:30
    sched.evaluate("wake")
    assert monitors.sent == [("DEL", 100, SCHEDULE), ("DEL", 40, SCHEDULE)]


def test_a_display_unreachable_at_the_point_gets_it_when_it_answers(scheduler):
    sched, monitors, clock = scheduler()
    monitors.detect(DELL)
    monitors.set_status("DEL", Status.NOT_RESPONDING)
    clock.now = at(15, 20, 1)
    sched.evaluate("timer")
    assert monitors.sent == [("DEL", 100, SCHEDULE)]  # nothing new while it doesn't answer
    monitors.set_status("DEL", Status.OK)
    assert monitors.sent[-1] == ("DEL", 50, SCHEDULE)
    monitors.set_status("DEL", Status.OK)
    assert len(monitors.sent) == 2  # once


def test_a_deferred_change_is_given_again_once_reachable(scheduler):
    sched, monitors, clock = scheduler()
    monitors.detect(DELL)
    monitors.restoreDeferred.emit("DEL", SCHEDULE)  # it was in standby
    monitors.set_status("DEL", Status.OK)
    assert monitors.sent == [("DEL", 100, SCHEDULE), ("DEL", 100, SCHEDULE)]


def test_the_laptop_panel_is_never_scheduled_to_black(scheduler):
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(0, "00:00"),))
    sched, monitors, _ = scheduler(config=config)
    monitors.detect(PANEL, DELL)
    assert ("BOE", 1, SCHEDULE) in monitors.sent and ("DEL", 0, SCHEDULE) in monitors.sent


def test_turning_on_or_editing_applies_at_once(scheduler):
    sched, monitors, _ = scheduler(config=BrightnessScheduleConfig(enabled=False))
    monitors.detect(DELL)
    assert monitors.sent == []
    sched.set_config(DAY_NIGHT)
    assert monitors.sent == [("DEL", 100, SCHEDULE)]
    sched.set_config(BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(80, "06:00"), SchedulePoint(30, "21:00"))))
    assert monitors.sent[-1] == ("DEL", 80, SCHEDULE)
    sched.set_config(BrightnessScheduleConfig(enabled=False))
    assert sched.state is None and len(monitors.sent) == 2


def test_a_transition_moves_each_minute_until_the_user_takes_over(scheduler):
    config = BrightnessScheduleConfig(enabled=True, points=DAY_NIGHT.points, transition_minutes=30)
    sched, monitors, clock = scheduler(config=config, now=at(15, 20, 0))
    monitors.detect(DELL)
    for minute in (10, 20):
        clock.now = at(15, 20, minute)
        sched.evaluate("tick")
    assert [v for _k, v, _s in monitors.sent] == [100, 83, 67]
    monitors.set_brightness("DEL", 90)
    clock.now = at(15, 20, 25)
    sched.evaluate("tick")
    assert monitors.sent[-1] == ("DEL", 90, USER)


def test_the_repeated_hour_in_autumn_does_not_apply_twice(scheduler):
    config = BrightnessScheduleConfig(enabled=True, points=(SchedulePoint(30, "01:30"), SchedulePoint(100, "09:00")))
    sched, monitors, clock = scheduler(config=config, now=datetime(2026, 11, 1, 5, 40, tzinfo=UTC), zone=NEW_YORK)
    monitors.detect(DELL)
    monitors.set_brightness("DEL", 55)  # the user, during the first 01:30-02:00
    clock.now = datetime(2026, 11, 1, 6, 40, tzinfo=UTC)  # 01:40 again, an hour later
    sched.evaluate("tick")
    assert monitors.sent[-1] == ("DEL", 55, USER)


def test_a_corrupt_state_file_starts_afresh(scheduler, tmp_path):
    (tmp_path / "schedule.json").write_text("{nope")
    sched, monitors, _ = scheduler()
    monitors.detect(DELL)
    assert monitors.sent == [("DEL", 100, SCHEDULE)]


def test_a_new_point_fades_but_transition_steps_do_not(scheduler):
    sched, monitors, clock = scheduler()
    monitors.detect(DELL)
    clock.now = at(15, 20, 1)
    sched.evaluate("timer")
    assert monitors.faded == [True, True]


def test_steps_of_a_gradual_transition_do_not_fade(scheduler):
    config = BrightnessScheduleConfig(enabled=True, points=DAY_NIGHT.points, transition_minutes=30)
    sched, monitors, clock = scheduler(config=config, now=at(15, 20, 10))
    monitors.detect(DELL)
    clock.now = at(15, 20, 40)  # the transition is over: the point's own level
    sched.evaluate("tick")
    assert monitors.sent == [("DEL", 83, SCHEDULE), ("DEL", 50, SCHEDULE)] and monitors.faded == [False, True]


def test_hidden_displays_are_left_alone(scheduler):
    sched, monitors, clock = scheduler()
    sched.set_hidden(lambda key: key == "GSM")
    monitors.detect(DELL, LG)
    clock.now = at(15, 20, 1)
    sched.evaluate("timer")
    assert [k for k, _v, _s in monitors.sent] == ["DEL", "DEL"]


def test_points_wait_while_dimmed_for_idle_and_apply_when_back(scheduler):
    sched, monitors, clock = scheduler()
    monitors.detect(DELL)
    sched.set_held(True)  # IdleDimmer dimmed the displays
    clock.now = at(15, 20, 1)
    sched.evaluate("timer")  # the evening point while away
    assert monitors.sent == [("DEL", 100, SCHEDULE)]  # nothing brightened (or changed) the idle screen
    sched.set_held(False)  # back: after IdleDimmer's own restores
    assert monitors.sent[-1] == ("DEL", 50, SCHEDULE)


def test_coming_back_from_idle_within_the_same_point_changes_nothing(scheduler):
    # IdleDimmer restores each display's level itself; the schedule must not
    # then overwrite a level the user chose during this point.
    sched, monitors, clock = scheduler()
    monitors.detect(DELL)
    monitors.set_brightness("DEL", 70)
    sched.set_held(True)
    clock.now = at(15, 13)
    sched.set_held(False)
    assert monitors.sent[-1] == ("DEL", 70, USER)


# --- With BrightnessMemory, on the real MonitorManager ---------------------------


def wait_for(condition, timeout_ms=3000):
    from PySide6.QtTest import QTest

    for _ in range(timeout_ms // 10):
        if condition():
            return True
        QTest.qWait(10)
    return condition()


@pytest.fixture
def together(tmp_path):
    """Memory and schedule on one MonitorManager, created in the app's order."""
    from nightlight.brightness import memory as brightness_memory
    from nightlight.brightness.memory import BrightnessMemory
    from nightlight.brightness.monitors import MonitorManager
    from nightlight.hardware.brightness import FakeBrightnessBackend

    made = []

    def make(remembered, now, schedule_state=None):
        brightness_memory.save(remembered, {}, tmp_path / "brightness.json")
        if schedule_state is not None:
            (tmp_path / "schedule.json").write_text(json.dumps(schedule_state))
        backend = FakeBrightnessBackend(latency=0)
        monitors = MonitorManager(backend)
        memory = BrightnessMemory(monitors, tmp_path / "brightness.json")
        clock = Clock(now)
        sched = BrightnessScheduler(monitors, DAY_NIGHT, tmp_path / "schedule.json", clock=clock)
        made.append((monitors, memory, sched))
        return backend, monitors, memory, sched, clock

    yield make
    for monitors, memory, sched in made:
        sched.shutdown()
        memory.shutdown()
        monitors.shutdown()


def test_a_new_point_wins_over_an_old_remembered_level_at_start(together):
    backend, monitors, _, _, _ = together({"DEL41A2:7XKJ9F3": 30}, at(15, 12))  # the 07:00 point not applied yet
    monitors.refresh()
    assert wait_for(lambda: backend._brightness[0] == 100 and monitors.state("DEL41A2:7XKJ9F3").desired is None)
    wait_for(lambda: False, 200)
    assert backend._brightness[0] == 100  # the restore to 30 doesn't come after and undo it


def test_within_a_point_the_users_level_comes_back_after_a_restart(together):
    applied = {"version": 1, "applied": at(15, 7).isoformat()}
    backend, monitors, _, _, _ = together({"DEL41A2:7XKJ9F3": 55}, at(15, 12), schedule_state=applied)
    monitors.refresh()
    assert wait_for(lambda: backend._brightness[0] == 55)
    wait_for(lambda: False, 200)
    assert backend._brightness[0] == 55  # the user chose 55 after the 07:00 point: it stands


def test_a_level_set_outside_the_app_stands_until_the_next_point(scheduler):
    # The laptop's keys are the user's choice too.
    sched, monitors, clock = scheduler()
    monitors.detect(DELL)
    monitors.changedElsewhere.emit("DEL", 30)
    clock.now = at(15, 19, 59)
    sched.evaluate("tick")
    assert monitors.sent == [("DEL", 100, SCHEDULE)]
    clock.now = at(15, 20, 1)
    sched.evaluate("timer")
    assert monitors.sent[-1] == ("DEL", 50, SCHEDULE)


def test_every_preset_changes_gradually_and_fits_between_its_times():
    """Beta 5's presets mostly jumped at once; each now has a transition that
    a day's gaps between its clock times leave room for."""
    for key, preset in PRESETS.items():
        assert preset.transition_minutes >= 30, key
        clock = sorted(p.at for p in preset.points if p.anchor is Anchor.CLOCK)
        minutes = [int(t[:2]) * 60 + int(t[3:]) for t in clock]
        gaps = [b - a for a, b in itertools.pairwise(minutes)] + ([minutes[0] + 1440 - minutes[-1]] if minutes else [])
        assert all(gap >= preset.transition_minutes for gap in gaps), (key, gaps)


def test_the_default_schedule_is_the_day_and_night_preset():
    default = BrightnessScheduleConfig()
    preset = PRESETS[default.preset]
    assert (default.points, default.transition_minutes) == (preset.points, preset.transition_minutes)


def test_a_preset_reaches_its_level_after_its_transition():
    preset = PRESETS["office"]
    config = BrightnessScheduleConfig(True, "office", preset.points, preset.transition_minutes)
    halfway = scheduled_level(config, at(15, 17, 22))
    assert halfway.transitioning and 70 < halfway.level < 100
    assert scheduled_level(config, at(15, 17, 45)).level == 70


def test_sunrise_and_sunset_in_a_white_night_are_the_real_ones():
    """Reykjavik in June: astral's sun() gives up (no civil dusk), and these
    fell back to 06:00 and 18:00."""
    from zoneinfo import ZoneInfo

    from nightlight.core.brightness_schedule import sun_times

    sunrise, sunset, approximate = sun_times(date(2026, 6, 20), ZoneInfo("Atlantic/Reykjavik"), (64.15, -21.94))
    assert not approximate
    assert f"{sunrise:%H:%M}" == "02:54" and f"{sunset:%m-%d %H:%M}" == "06-21 00:03"
