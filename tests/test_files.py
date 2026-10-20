# SPDX-License-Identifier: GPL-3.0-only
import os

import pytest

from nightlight.system import files


def test_writes_and_replaces(tmp_path):
    path = tmp_path / "sub" / "state.json"
    files.write_atomic(path, "one")
    files.write_atomic(path, "two")
    assert path.read_text(encoding="utf-8") == "two"
    assert [p.name for p in path.parent.iterdir()] == ["state.json"]  # no temporary file left behind


def test_a_file_held_open_for_a_moment_is_retried(tmp_path, monkeypatch):
    # Antivirus or the search indexer holding the file: "Access is denied" for a moment.
    path = tmp_path / "state.json"
    path.write_text("old", encoding="utf-8")
    real_replace, refusals = os.replace, [2]

    def busy_replace(src, dst):
        if refusals[0]:
            refusals[0] -= 1
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(files.os, "replace", busy_replace)
    monkeypatch.setattr(files, "RETRY_DELAY_S", 0)
    files.write_atomic(path, "new")
    assert path.read_text(encoding="utf-8") == "new"


def test_a_file_that_stays_locked_keeps_its_old_contents(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("old", encoding="utf-8")

    def locked(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(files.os, "replace", locked)
    monkeypatch.setattr(files, "RETRY_DELAY_S", 0)
    with pytest.raises(PermissionError):
        files.write_atomic(path, "new")
    assert path.read_text(encoding="utf-8") == "old"  # never half-written
