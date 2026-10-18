# SPDX-License-Identifier: GPL-3.0-only
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PySide6.QtTest import QTest

from nightlight.commands import CommandHandler
from nightlight.config import AppConfig, DisplayPrefs, Mode, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.hardware.brightness import MonitorInfo
from nightlight.single_instance import SingleInstance

ROOT = Path(__file__).resolve().parent.parent
MONITORS = [
    MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2"),
    MonitorInfo(1, "Built-in display", True, key="BOE0812", internal=True, minimum=1),
    MonitorInfo(2, "Living room TV", True, key="SAM7136", control="software", minimum=20),
    MonitorInfo(3, "Old projector", False, key="EPS0000"),
]


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


class FakeMonitors:
    def __init__(self):
        self.monitors = MONITORS
        self.brightness = {0: 70, 1: 50, 2: 100}
        self.faded = []
        self.retried = 0

    def fade_brightness(self, index, value):
        self.faded.append((index, value))
        self.brightness[index] = value

    def retry(self):
        self.retried += 1


@pytest.fixture
def handler():
    controller = NightLightController(FakeGamma(), NightLightConfig(enabled=True, mode=Mode.TIMES), lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    monitors = FakeMonitors()
    settings = {"config": AppConfig(displays=(DisplayPrefs("BOE0812", "Laptop"), DisplayPrefs("SAM7136", "", True)))}
    yield CommandHandler(monitors, controller, lambda: settings["config"]), monitors, controller
    controller.shutdown()


def test_list_numbers_displays_as_the_panel_does(handler):
    commands, _, _ = handler
    reply = commands.handle({"command": "list"})
    assert reply["ok"]
    displays = reply["data"]["displays"]
    assert [(d["number"], d["name"], d["brightness"]) for d in displays] == [
        (1, "DELL U2720Q", 70),
        (2, "Laptop", 50),  # its custom name
        (3, "Living room TV", 100),
        (4, "Old projector", None),
    ]
    assert displays[2]["hidden"] and displays[2]["control"] == "software"
    assert "Night light:" in reply["output"]


@pytest.mark.parametrize(("display", "index"), [("1", 0), ("dell", 0), ("DEL41A2", 0), ("laptop", 1), ("2", 1)])
def test_set_one_display_by_number_name_or_id(handler, display, index):
    commands, monitors, _ = handler
    assert commands.handle({"command": "set", "value": "40", "display": display})["ok"]
    assert monitors.faded == [(index, 40)]


def test_set_all_skips_hidden_and_unsupported_displays(handler):
    commands, monitors, _ = handler
    assert commands.handle({"command": "set", "value": "40"})["ok"]
    assert monitors.faded == [(0, 40), (1, 40)]


def test_relative_changes_and_limits(handler):
    commands, monitors, _ = handler
    commands.handle({"command": "set", "value": "+50", "display": "1"})
    commands.handle({"command": "set", "value": "-80", "display": "laptop"})
    commands.handle({"command": "set", "value": "-90", "display": "tv"})  # hidden, but asked for by name
    assert monitors.faded == [(0, 100), (1, 1), (2, 20)]  # each display's own floor


@pytest.mark.parametrize(
    ("request_", "message"),
    [
        ({"command": "set", "value": "bright"}, "must be a number"),
        ({"command": "set", "value": "50", "display": "9"}, "No display matches"),
        ({"command": "set", "value": "50", "display": "projector"}, "no brightness control"),
        ({"command": "set", "value": "50", "display": "l"}, "matches several"),
        ({"command": "dance"}, "Unknown command"),
    ],
)
def test_errors_say_what_is_wrong(handler, request_, message):
    commands, _, _ = handler
    reply = commands.handle(request_)
    assert not reply["ok"] and message in reply["output"]


def test_get_one_display_prints_just_the_number(handler):
    commands, _, _ = handler
    assert commands.handle({"command": "get", "display": "1"})["output"] == "70"


def test_night_light_commands(handler):
    commands, _, controller = handler
    assert commands.handle({"command": "night-light", "action": "on"})["data"]["night_light"]["override"] == "activate"
    assert commands.handle({"command": "night-light", "action": "toggle"})["data"]["night_light"]["override"] == "suspend"
    assert commands.handle({"command": "night-light", "action": "auto"})["data"]["night_light"]["override"] is None
    assert not commands.handle({"command": "night-light", "action": "dim"})["ok"]


def test_refresh_retries_skipped_displays(handler):
    commands, monitors, _ = handler
    assert commands.handle({"command": "refresh"})["ok"] and monitors.retried == 1


CLI = """
import sys
from PySide6.QtCore import QCoreApplication
app = QCoreApplication([])
import main, argparse
from nightlight import cli
from nightlight.single_instance import SingleInstance
parser = argparse.ArgumentParser()
cli.add_arguments(parser)
args = parser.parse_args(sys.argv[2:])
sys.exit(cli.run(args, SingleInstance(sys.argv[1])))
"""


def run_cli(name, *args, wait_while=None):
    process = subprocess.Popen([sys.executable, "-c", CLI, name, *args], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for _ in range(1500):  # run the event loop, so the "running app" can answer
        if process.poll() is not None:
            break
        QTest.qWait(10)
    out, err = process.communicate(timeout=5)
    return process.returncode, out, err


def test_the_command_line_talks_to_the_running_app(handler):
    commands, monitors, _ = handler
    name = f"night-light-test-{uuid.uuid4().hex}"
    running = SingleInstance(name)
    assert running.notify_running() is False
    running.requestReceived.connect(lambda request, reply: reply(commands.handle(request)))
    try:
        code, out, _ = run_cli(name, "--set", "+5", "--display", "dell")
        assert code == 0 and "DELL U2720Q" in out and monitors.faded == [(0, 75)]
        code, out, _ = run_cli(name, "--list", "--json")
        assert code == 0 and json.loads(out)["displays"][0]["brightness"] == 75
        code, _, err = run_cli(name, "--set", "50", "--display", "nope")
        assert code == 1 and "No display matches" in err
    finally:
        running.close()
        QTest.qWait(50)


def test_the_command_line_without_the_app_running():
    code, _, err = run_cli(f"night-light-test-{uuid.uuid4().hex}", "--list")
    assert code == 2 and "isn't running" in err
