# SPDX-License-Identifier: GPL-3.0-only
from datetime import datetime, timezone

import pytest

from nightlight.config import Mode, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.ui.tray import Tray


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


@pytest.fixture
def tray():
    controller = NightLightController(FakeGamma(), NightLightConfig(enabled=True, mode=Mode.ALWAYS), lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
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
