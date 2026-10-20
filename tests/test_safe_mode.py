# SPDX-License-Identifier: GPL-3.0-only
import json
import subprocess
import sys
from pathlib import Path

from nightlight.hardware import win32, windows
from nightlight.hardware.win32 import DisplayTarget, PhysicalMonitor
from nightlight.system.safe_mode import SKIPPED_REASON, SKIPPED_SHORT, SafeMode

ROOT = Path(__file__).resolve().parent.parent
D1, D2 = r"\\.\DISPLAY1", r"\\.\DISPLAY2"


def test_a_call_that_finishes_leaves_nothing_behind(tmp_path):
    safe = SafeMode(tmp_path)
    with safe.risky(D1, "detecting it"):
        assert (tmp_path / "ddc-in-progress.json").exists()
    assert not (tmp_path / "ddc-in-progress.json").exists()
    assert SafeMode(tmp_path).skipped == set()


def test_a_crash_during_a_call_skips_that_display_next_time(tmp_path):
    # A real crash: the process dies inside the risky call.
    code = (
        "import os, sys; from pathlib import Path; from nightlight.system.safe_mode import SafeMode\n"
        f"safe = SafeMode(Path({str(tmp_path)!r}))\n"
        f"with safe.risky({D2!r}, 'detecting DELL on DISPLAY2'):\n"
        "    os._exit(3)\n"
    )
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT).returncode == 3
    safe = SafeMode(tmp_path)
    assert safe.is_skipped(D2) and not safe.is_skipped(D1)
    assert SafeMode(tmp_path).is_skipped(D2)  # stays skipped across starts...
    safe.clear()
    assert not SafeMode(tmp_path).is_skipped(D2)  # ...until retried


def test_the_backend_does_not_probe_a_skipped_display(tmp_path, monkeypatch):
    probed, destroyed = [], []
    monkeypatch.setattr(win32, "display_targets", lambda: [DisplayTarget(D1, "OK", "", 10, False), DisplayTarget(D2, "Bad", "", 10, False)])
    monkeypatch.setattr(win32, "physical_monitors", lambda: [PhysicalMonitor(D1, 1, "x"), PhysicalMonitor(D2, 2, "y")])
    monkeypatch.setattr(win32, "destroy_physical_monitor", destroyed.append)

    def get_vcp(handle, code):
        probed.append(handle)
        return 50, 100

    monkeypatch.setattr(win32, "get_vcp", get_vcp)
    safe = SafeMode(tmp_path)
    safe.skipped.add(D2)
    backend = windows.WindowsBrightnessBackend()
    backend.safe_mode = safe
    ok, bad = backend.list_monitors()
    assert ok.supported and probed == [1]
    assert not bad.supported and (bad.reason, bad.detail, bad.status) == (SKIPPED_SHORT, SKIPPED_REASON, "skipped") and 2 in destroyed
    backend.retry_skipped()
    assert all(m.supported for m in backend.list_monitors())


def test_displays_probed_side_by_side_are_all_skipped_after_a_crash(tmp_path):
    # Detection probes displays in parallel: which one crashed is unknown, so all are skipped.
    code = (
        "import os, threading; from pathlib import Path; from nightlight.system.safe_mode import SafeMode\n"
        f"safe = SafeMode(Path({str(tmp_path)!r}))\n"
        "inside = threading.Barrier(3)\n"
        "def probe(name):\n"
        "    with safe.risky(name, 'detecting ' + name):\n"
        "        inside.wait(); inside.wait()\n"
        "threads = [threading.Thread(target=probe, args=(n,)) for n in ('DEL41A2:7XKJ9F3', 'GSM5B7F:ABCDE')]\n"
        "[t.start() for t in threads]\n"
        "inside.wait()\n"
        "os._exit(3)\n"
    )
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT, timeout=30).returncode == 3
    safe = SafeMode(tmp_path)
    assert safe.skipped == {"DEL41A2:7XKJ9F3", "GSM5B7F:ABCDE"}
    safe.clear(["GSM5B7F:ABCDE"])  # retrying one keeps the other skipped
    assert SafeMode(tmp_path).skipped == {"DEL41A2:7XKJ9F3"}


def test_one_call_finishing_keeps_the_others_marked(tmp_path):
    safe = SafeMode(tmp_path)
    marker = tmp_path / "ddc-in-progress.json"
    with safe.risky("A", "detecting A"):
        with safe.risky("B", "detecting B"):
            pass
        assert json.loads(marker.read_text()) == {"in_progress": {"A": "detecting A"}}
    assert not marker.exists()


def test_a_marker_left_by_beta_4_is_understood(tmp_path):
    (tmp_path / "ddc-in-progress.json").write_text(json.dumps({"display": D2, "what": "detecting DELL"}))
    safe = SafeMode(tmp_path)
    assert safe.is_skipped("DEL41A2:7XKJ9F3", D2)  # beta 4 named displays by GDI name: still matched
    assert not (tmp_path / "ddc-in-progress.json").exists()


def test_odd_files_are_ignored(tmp_path):
    (tmp_path / "ddc-in-progress.json").write_text("{nope")
    (tmp_path / "ddc-skip.json").write_text(json.dumps({"not": "a list"}))
    assert SafeMode(tmp_path).skipped == set()
