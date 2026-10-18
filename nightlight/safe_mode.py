# SPDX-License-Identifier: GPL-3.0-only
"""Skipping a display whose DDC/CI crashed or hung the app last time.

A native DDC/CI call that crashes (in the driver or monitor firmware) takes
the whole process with it, and one that hangs can only be escaped by
quitting. Twinkle Tray's answer: write a marker before risky calls, remove it
after, and if a marker survives to the next start, the call it names went
wrong, so take the safe path. Here the safe path is not talking DDC/CI to
that display until the user asks to try again (the panel's ⟳ button).

Markers are per display (its GDI name and description). Writing one is a
small file write, so only detection does it, which is when monitors are
probed and when Twinkle Tray's crashes happened.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

SKIPPED_REASON = (
    "Skipped: Winlight stopped while talking to this display last time. "
    "Click ⟳ to try it again; if it keeps happening, turn DDC/CI off in its menu."
)


class SafeMode:
    def __init__(self, folder: Path) -> None:
        self._marker = folder / "ddc-in-progress.json"
        self._skip_file = folder / "ddc-skip.json"
        self.skipped: set[str] = self._load()
        try:
            leftover = json.loads(self._marker.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            leftover = {}
        display = leftover.get("display") if isinstance(leftover, dict) else None
        if display:
            log.warning(
                "Winlight stopped last time while %s; not using DDC/CI with %s until it's retried",
                leftover.get("what", "talking to a display"),
                display,
            )
            self.skipped.add(display)
            self._save()
        self._marker.unlink(missing_ok=True)

    def is_skipped(self, display: str) -> bool:
        return display in self.skipped

    @contextmanager
    def risky(self, display: str, what: str) -> Iterator[None]:
        """Mark `display` as being talked to for the duration."""
        try:
            self._marker.parent.mkdir(parents=True, exist_ok=True)
            self._marker.write_text(json.dumps({"display": display, "what": what}), encoding="utf-8")
        except OSError:
            log.debug("Could not write the DDC/CI marker", exc_info=True)
        try:
            yield
        finally:
            self._marker.unlink(missing_ok=True)

    def clear(self) -> None:
        """Try every display again (the user clicked ⟳)."""
        if self.skipped:
            log.info("Trying DDC/CI with %s again", ", ".join(sorted(self.skipped)))
            self.skipped.clear()
            self._save()

    def _load(self) -> set[str]:
        try:
            data = json.loads(self._skip_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {str(d) for d in data} if isinstance(data, list) else set()

    def _save(self) -> None:
        try:
            self._skip_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._skip_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(sorted(self.skipped)), encoding="utf-8")
            os.replace(tmp, self._skip_file)
        except OSError:
            log.warning("Could not save %s", self._skip_file, exc_info=True)
