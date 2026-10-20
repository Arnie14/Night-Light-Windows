# SPDX-License-Identifier: GPL-3.0-only
import json
import zipfile
from datetime import UTC, datetime

from nightlight.color.controller import NightLightController
from nightlight.config import AppConfig, DisplayPrefs, GeneralConfig, NightLightConfig
from nightlight.hardware.brightness import MonitorInfo
from nightlight.support import diagnostics


class FakeGamma:
    def apply(self, kelvin):
        pass

    def reset(self):
        pass


class FakeMonitors:
    """MonitorManager as diagnostics reads it (by display key)."""

    monitors = [MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2"), MonitorInfo(1, "Old TV", False, key="SAM0001", reason="Didn't answer over DDC/CI")]
    brightness = {"DEL41A2": 70}
    states = {}

    def value(self, key):
        return self.brightness.get(key)

    def state(self, key):
        return self.states.get(key)


def sources(tmp_path, log_dir=None):
    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
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
    from nightlight.config import NightLightConfig
    from nightlight.support import privacy

    monkeypatch.setattr(diagnostics, "scrub", privacy.Scrubber(user="jsmith", computer="JSMITH-PC", home=r"C:\Users\Jsmith"))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "night-light.log").write_text(
        "INFO Settings file: C:\\Users\\Jsmith\\AppData\\Local\\night-light\\settings.json\n"
        "INFO pipe night-light-jsmith on JSMITH-PC, mail jsmith@example.com\n"
    )
    controller = NightLightController(FakeGamma(), NightLightConfig(), lambda: datetime(2026, 6, 15, 12, tzinfo=UTC))
    settings = AppConfig(
        NightLightConfig(latitude=50.4501, longitude=30.5234, timezone="Europe/Kyiv", exempt_apps=("secret-project.exe",)),
        GeneralConfig(idle_exempt_apps=("hidden-call.exe",)),
        displays=(DisplayPrefs("DEL41A2", "Jsmith's desk"),),
    )
    src = diagnostics.Sources(FakeMonitors(), controller, lambda: settings, tmp_path / "settings.json", logs)
    path, _ = diagnostics.write_bundle(src, tmp_path / "out")
    with zipfile.ZipFile(path) as bundle:
        everything = "\n".join(bundle.read(name).decode() for name in bundle.namelist())
    for secret in ("Jsmith", "jsmith", "JSMITH-PC", "example.com", "Kyiv", "50.45", "30.52", "secret-project", "hidden-call", "desk"):
        assert secret not in everything, secret
    assert "<home>" in everything and "<user>" in everything and "<computer>" in everything
    controller.shutdown()


def test_the_last_wake_is_in_the_summary(tmp_path):
    src, controller = sources(tmp_path)
    src.last_wake = lambda: ["+5 s: 1 of 1 answering (DELL U2720Q 70%)", "answering again: DELL U2720Q at +5 s"]
    text = diagnostics.summary(src)
    assert "Last wake from sleep" in text and "+5 s: 1 of 1 answering" in text
    controller.shutdown()


def test_a_display_that_does_not_answer_now_says_so(tmp_path):
    from nightlight.brightness.monitors import DisplayState, Status

    src, controller = sources(tmp_path)
    monitors = FakeMonitors()
    monitors.states = {"DEL41A2": DisplayState(FakeMonitors.monitors[0], Status.NOT_RESPONDING, message="Not responding")}
    src.monitors = monitors
    assert "70% via hardware (not responding: Not responding)" in diagnostics.summary(src)
    controller.shutdown()


def test_the_compatibility_report_is_included_and_scrubbed(tmp_path, monkeypatch):
    from nightlight.support import privacy

    monkeypatch.setattr(diagnostics, "scrub", privacy.Scrubber(user="jsmith", computer="JSMITH-PC", home=r"C:\Users\Jsmith"))
    src, controller = sources(tmp_path)
    src.compatibility = lambda: "1. DELL\n   Identity:     DEL41A2:7XKJ9F3\n   saved by jsmith\n"
    path, _ = diagnostics.write_bundle(src, tmp_path / "out")
    with zipfile.ZipFile(path) as bundle:
        report = bundle.read("compatibility.txt").decode()
    assert "DEL41A2:…J9F3" in report and "7XKJ9F3" not in report and "jsmith" not in report
    controller.shutdown()


def test_a_failing_compatibility_report_does_not_stop_the_bundle(tmp_path):
    src, controller = sources(tmp_path)

    def broken():
        raise RuntimeError("no displays")

    src.compatibility = broken
    path, _ = diagnostics.write_bundle(src, tmp_path / "out")
    with zipfile.ZipFile(path) as bundle:
        assert "summary.txt" in bundle.namelist() and "compatibility.txt" not in bundle.namelist()
    controller.shutdown()


def test_old_bundles_are_deleted_and_nothing_else(tmp_path):
    import os

    now = 1_800_000_000.0
    old = tmp_path / "Winlight-diagnostics-20260101-120000.zip"
    recent = tmp_path / "Winlight-diagnostics-20260110-120000.zip"
    lookalike = tmp_path / "Winlight-diagnostics-20260101-120000 (1).zip"  # the user's copy: not ours to delete
    other = tmp_path / "holiday.zip"
    for path in (old, recent, lookalike, other):
        path.write_bytes(b"zip")
        os.utime(path, (now - 8 * 86400, now - 8 * 86400))
    os.utime(recent, (now - 6 * 86400, now - 6 * 86400))
    (tmp_path / "Winlight-diagnostics-20260101-000000.zip").mkdir()  # a folder by that name stays too
    assert diagnostics.remove_old_bundles(tmp_path, now) == [old]
    assert not old.exists() and recent.exists() and lookalike.exists() and other.exists()


def test_cleaning_a_missing_folder_does_not_raise(tmp_path):
    assert diagnostics.remove_old_bundles(tmp_path / "gone") == []


def test_bundle_names_match_what_cleanup_deletes(tmp_path):
    src, controller = sources(tmp_path)
    path, _ = diagnostics.write_bundle(src, tmp_path / "out")
    assert diagnostics.BUNDLE_NAME.fullmatch(path.name)
    assert diagnostics.remove_old_bundles(tmp_path / "out", days=-1) == [path]  # anything counts as old
    controller.shutdown()
