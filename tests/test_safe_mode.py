# SPDX-License-Identifier: GPL-3.0-only
import subprocess
import sys
from pathlib import Path

from nightlight.hardware import win32, windows
from nightlight.hardware.win32 import DisplayTarget, PhysicalMonitor
from nightlight.safe_mode import SKIPPED_REASON, SafeMode

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
        "import os, sys; from pathlib import Path; from nightlight.safe_mode import SafeMode\n"
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
    assert not bad.supported and bad.reason == SKIPPED_REASON and 2 in destroyed
    backend.retry_skipped()
    assert all(m.supported for m in backend.list_monitors())
