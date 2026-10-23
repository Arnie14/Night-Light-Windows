# SPDX-License-Identifier: GPL-3.0-only
from datetime import UTC, datetime

import pytest

from nightlight.color.controller import NightLightController
from nightlight.config import BrightnessScheduleConfig, Mode, NightLightConfig
from nightlight.core.brightness_schedule import Boundary, ScheduledLevel
from nightlight.ui.tray import Tray


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


@pytest.fixture
def tray():
    controller = NightLightController(FakeGamma(), NightLightConfig(enabled=True, mode=Mode.ALWAYS), lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
    tray = Tray(controller)
    yield tray
    controller.shutdown()


def action(tray, text):
    return next(a for a in tray._menu.actions() if a.text() == text)


@pytest.mark.parametrize(("text", "signal"), [("Settings…", "configureRequested"), ("Quit", "quitRequested")])
def test_menu_items_work(tray, text, signal):
    # PySide6 6.8+ raises "signal instance is not callable" if a signal
    # itself is given to addAction, and the click then does nothing.
    received = []
    getattr(tray, signal).connect(lambda: received.append(True))
    action(tray, text).trigger()
    assert received == [True]


def test_tooltip_names_the_app_and_status(tray):
    assert tray.toolTip().splitlines() == ["Winlight", "Night Light: On · 4500K"]


class FakeScheduler:
    def __init__(self, state, enabled=True):
        from PySide6.QtCore import QObject, Signal

        class Emitter(QObject):
            changed = Signal()

        self._emitter = Emitter()
        self.changed = self._emitter.changed
        self.state = state
        self.config = BrightnessScheduleConfig(enabled=enabled)


def level(now, target, nxt=None, end=None):
    since = Boundary(datetime(2026, 6, 15, 20, 0, tzinfo=UTC), target, 1)
    following = Boundary(datetime(2026, 6, 16, 7, 0, tzinfo=UTC), nxt, 0) if nxt is not None else None
    return ScheduledLevel(now, since, following, end is not None, False, end)


def test_tooltip_shows_the_brightness_schedule(tray):
    scheduler = FakeScheduler(level(70, 70, nxt=100))
    tray.set_scheduler(scheduler)
    assert "Brightness: 70% → 100% at 07:00" in tray.toolTip()
    scheduler.state = level(55, 40, nxt=100, end=datetime(2026, 6, 15, 20, 30, tzinfo=UTC))
    scheduler.changed.emit()
    assert "Brightness: 55% → 40%, changing until 20:30" in tray.toolTip()
    assert "·" not in tray.toolTip().split("Brightness:")[1]
    assert len(tray.toolTip()) < 128  # Windows cuts tray tooltips at 127 characters


def test_tooltip_leaves_out_a_schedule_that_is_off(tray):
    tray.set_scheduler(FakeScheduler(level(70, 70), enabled=False))
    assert "Brightness" not in tray.toolTip()
    tray.set_scheduler(FakeScheduler(None))
    assert "Brightness" not in tray.toolTip()


def test_suspend_is_a_toggle_until_resumed(tray):
    """KDE's Suspend: checked, night light stays off until it is unchecked."""
    from nightlight.core.schedule import OverrideKind

    controller = tray._controller
    tray._update_menu()
    suspend = tray._suspend_action
    assert suspend.isVisible() and not suspend.isChecked()
    suspend.trigger()
    assert controller.override.kind is OverrideKind.SUSPEND and controller.override.until is None
    tray._update_menu()
    assert suspend.isChecked() and not tray._override_action.isVisible()  # unchecking it is how to resume
    suspend.trigger()
    assert controller.override is None
    tray._update_menu()
    assert not suspend.isChecked() and tray._override_action.isVisible()


def test_nothing_to_suspend_while_night_light_is_off():
    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
    tray = Tray(controller)
    tray._update_menu()
    assert not tray._suspend_action.isVisible() and tray._override_action.text() == "Activate Night Light"
    controller.shutdown()
