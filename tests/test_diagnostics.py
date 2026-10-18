# SPDX-License-Identifier: GPL-3.0-only
import json
import zipfile
from datetime import datetime, timezone

from nightlight import diagnostics
from nightlight.config import AppConfig, DisplayPrefs, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.hardware.brightness import MonitorInfo


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


class FakeMonitors:
    monitors = [MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2"), MonitorInfo(1, "Old TV", False, key="SAM0001", reason="Didn't answer over DDC/CI")]
    brightness = {0: 70}


def sources(tmp_path, log_dir=None):
    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    settings = AppConfig(NightLightConfig(latitude=50.4501, longitude=30.5234, timezone="Europe/Kyiv"), displays=(DisplayPrefs("DEL41A2", "Left"),))
    return diagnostics.Sources(FakeMonitors(), controller, lambda: settings, tmp_path / "settings.json", log_dir, lambda: {r"\\.\DISPLAY3"}), controller


def test_summary_has_what_a_report_needs(tmp_path):
    src, controller = sources(tmp_path)
    text = diagnostics.summary(src)
    assert "Winlight" in text and "Python" in text
    assert "1. DELL U2720Q [DEL41A2, renamed]: 70% via hardware" in text  # not the custom name
    assert "Left" not in text
    assert "2. Old TV [SAM0001]: not supported: Didn't answer over DDC/CI" in text
    assert "Night light:" in text and "Windows Night Light:" in text
    assert "DISPLAY3" in text  # skipped by safe mode
    controller.shutdown()


def test_bundle_has_the_summary_logs_and_no_location(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "night-light.log").write_text("log line\n")
    (logs / "night-light.log.1").write_text("older\n")
    (logs / "unrelated.txt").write_text("not ours\n")
    src, controller = sources(tmp_path, logs)
    path, text = diagnostics.write_bundle(src, tmp_path / "out")
    with zipfile.ZipFile(path) as bundle:
        names = set(bundle.namelist())
        settings = json.loads(bundle.read("settings.json"))
        assert bundle.read("summary.txt").decode() == text
    assert names == {"summary.txt", "settings.json", "logs/night-light.log", "logs/night-light.log.1"}
    night_light = settings["night_light"]
    assert "latitude" not in night_light and "timezone" not in night_light and night_light["location"] == "a city"
    assert settings["displays"] == {"DEL41A2": {"renamed": True, "hidden": False}}
    assert path.name.startswith("Winlight-diagnostics-") and path.suffix == ".zip"
    controller.shutdown()


def test_nothing_personal_gets_into_the_bundle(tmp_path, monkeypatch):
    from nightlight import privacy
    from nightlight.config import NightLightConfig

    monkeypatch.setattr(diagnostics, "scrub", privacy.Scrubber(user="anurag", computer="ANURAG-PC", home=r"C:\Users\Anurag"))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "night-light.log").write_text(
        "INFO Settings file: C:\\Users\\Anurag\\AppData\\Local\\night-light\\settings.json\n"
        "INFO pipe night-light-anurag on ANURAG-PC, mail anurag@example.com\n"
    )
    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=timezone.utc))
    settings = AppConfig(
        NightLightConfig(latitude=50.4501, longitude=30.5234, timezone="Europe/Kyiv", exempt_apps=("secret-project.exe",)),
        displays=(DisplayPrefs("DEL41A2", "Anurag's desk"),),
    )
    src = diagnostics.Sources(FakeMonitors(), controller, lambda: settings, tmp_path / "settings.json", logs)
    path, _ = diagnostics.write_bundle(src, tmp_path / "out")
    with zipfile.ZipFile(path) as bundle:
        everything = "\n".join(bundle.read(name).decode() for name in bundle.namelist())
    for secret in ("Anurag", "anurag", "ANURAG-PC", "example.com", "Kyiv", "50.45", "30.52", "secret-project", "desk"):
        assert secret not in everything, secret
    assert "<home>" in everything and "<user>" in everything and "<computer>" in everything
    controller.shutdown()


def test_the_last_wake_is_in_the_summary(tmp_path):
    src, controller = sources(tmp_path)
    src.last_wake = lambda: ["+5 s: 1 of 1 answering (DELL U2720Q 70%)", "answering again: DELL U2720Q at +5 s"]
    text = diagnostics.summary(src)
    assert "Last wake from sleep" in text and "+5 s: 1 of 1 answering" in text
    controller.shutdown()
