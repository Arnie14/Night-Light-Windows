# SPDX-License-Identifier: GPL-3.0-only
"""Writing the app's small state files safely.

Each file is written to a temporary file next to it and then renamed over
it, so a crash or power cut mid-write leaves the old file, never half a new
one. On Windows the rename fails with "Access is denied" while another
program briefly holds the file open (antivirus scanning it, the search
indexer, a sync client), so it is tried again a few times before giving up.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

ATTEMPTS = 5
RETRY_DELAY_S = 0.05


def write_atomic(path: Path, text: str) -> None:
    """Replace `path`'s contents with `text`; raises OSError if it can't."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == ATTEMPTS - 1:
                raise
            time.sleep(RETRY_DELAY_S * (attempt + 1))
