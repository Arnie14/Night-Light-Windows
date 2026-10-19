# SPDX-License-Identifier: GPL-3.0-only
"""Skipping a display whose DDC/CI crashed or hung the app last time.

A native DDC/CI call that crashes (in the driver or monitor firmware) takes
the whole process with it, and one that hangs can only be escaped by
quitting. Twinkle Tray's answer: write a marker before risky calls, remove it
after, and if a marker survives to the next start, the call it names went
wrong, so take the safe path. Here the safe path is not talking DDC/CI to
that display until the user asks to try again (the panel's ⟳ button).

This complements the time limits in hardware/calls.py: those keep the app
running when a call hangs, while a crash can only be learnt from afterwards.

Detection probes several monitors side by side, so the marker lists every
display being talked to at once; a crash skips all of them, the safe choice
when it isn't known which one did it. Displays are named by their stable
identity (MonitorInfo.key) where known, else by Windows' GDI name; names
saved by 0.1.0 beta 3 and 4 (GDI names) still count.

Writing a marker is a small file write, so only detection does it, which is
when monitors are probed and when Twinkle Tray's crashes happened.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from nightlight.files import write_atomic

log = logging.getLogger(__name__)

SKIPPED_REASON = (
    "Skipped: Winlight stopped while talking to this display last time. "
    "Click ⟳ to try it again; if it keeps happening, turn DDC/CI off in its menu."
)
SKIPPED_SHORT = "Skipped after a crash; click ⟳ to retry"


class SafeMode:
    def __init__(self, folder: Path) -> None:
        self._marker = folder / "ddc-in-progress.json"
        self._skip_file = folder / "ddc-skip.json"
        self._lock = threading.Lock()
        self._active: dict[str, str] = {}  # display -> what is being done with it
        self.skipped: set[str] = self._load()
        leftover = self._read_marker()
        if leftover:
            for display, what in leftover.items():
                log.warning("Winlight stopped last time while %s; not using DDC/CI with %s until it's retried", what, display)
            self.skipped |= set(leftover)
            self._save()
        self._marker.unlink(missing_ok=True)

    def is_skipped(self, *names: str) -> bool:
        """Whether any of a display's names (its key, its GDI name) is skipped."""
        return any(name and name in self.skipped for name in names)

    @contextmanager
    def risky(self, display: str, what: str) -> Iterator[None]:
        """Mark `display` as being talked to for the duration (thread-safe)."""
        with self._lock:
            self._active[display] = what
            self._write_marker()
        try:
            yield
        finally:
            with self._lock:
                self._active.pop(display, None)
                self._write_marker()

    def clear(self, names: Iterable[str] | None = None) -> None:
        """Try displays again (the user clicked ⟳): all, or the named ones."""
        with self._lock:
            retry = set(self.skipped) if names is None else self.skipped & set(names)
            if retry:
                log.info("Trying DDC/CI with %d display(s) again", len(retry))
                self.skipped -= retry
                self._save()

    def _read_marker(self) -> dict[str, str]:
        try:
            data = json.loads(self._marker.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}
        if isinstance(data, dict) and isinstance(data.get("in_progress"), dict):
            return {str(k): str(v) for k, v in data["in_progress"].items()}
        if isinstance(data, dict) and data.get("display"):  # 0.1.0 beta 3/4: one display at a time
            return {str(data["display"]): str(data.get("what", "talking to a display"))}
        return {}

    def _write_marker(self) -> None:
        try:
            if self._active:
                self._marker.parent.mkdir(parents=True, exist_ok=True)
                self._marker.write_text(json.dumps({"in_progress": self._active}), encoding="utf-8")
            else:
                self._marker.unlink(missing_ok=True)
        except OSError:
            log.debug("Could not write the DDC/CI marker", exc_info=True)

    def _load(self) -> set[str]:
        try:
            data = json.loads(self._skip_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {str(d) for d in data} if isinstance(data, list) else set()

    def _save(self) -> None:
        try:
            write_atomic(self._skip_file, json.dumps(sorted(self.skipped)))
        except OSError:
            log.warning("Could not save %s", self._skip_file, exc_info=True)
