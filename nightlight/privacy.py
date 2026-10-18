# SPDX-License-Identifier: GPL-3.0-only
"""Keeping personal details out of logs and diagnostics.

Logs and "Report a Problem" zips are meant to be posted publicly in GitHub
issues, so they must not carry who or where the user is. Two rules:

1. What the app writes doesn't include it in the first place: no
   coordinates, cities or time zones, no custom display names, no names of
   apps the user chose to pause night light for (logged as "a listed app").
2. Everything that does get written passes through `scrub`, which replaces
   the user name, the computer name, home folder paths (which contain the
   user name) and anything that looks like an email address, in messages
   and tracebacks alike. Display models, Windows versions and error codes
   stay: they're what a report needs.
"""

from __future__ import annotations

import getpass
import logging
import os
import re
import socket
from pathlib import Path

USER = "<user>"
COMPUTER = "<computer>"
HOME = "<home>"
EMAIL = "<email>"
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Words too common to replace safely if they happen to be the user or computer name.
_TOO_SHORT = 3


class Scrubber:
    def __init__(self, user: str | None = None, computer: str | None = None, home: str | None = None) -> None:
        self._replacements: list[tuple[re.Pattern[str], str]] = []
        home = home if home is not None else str(Path.home())
        if home and home not in ("/", "\\"):
            # Both slash styles; Windows paths are case-insensitive.
            for variant in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
                self._replacements.append((re.compile(re.escape(variant), re.IGNORECASE), HOME))
        names = [(user if user is not None else _user(), USER), (computer if computer is not None else _computer(), COMPUTER)]
        # Longest first, so a computer name like "ANNA-PC" isn't half-replaced as the user "anna".
        for name, placeholder in sorted(names, key=lambda pair: -len(pair[0] or "")):
            if name and len(name) >= _TOO_SHORT:
                self._replacements.append((re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])", re.IGNORECASE), placeholder))

    def __call__(self, text: str) -> str:
        # Emails first: once a name inside one is replaced it no longer looks like an email.
        text = _EMAIL.sub(EMAIL, text)
        for pattern, placeholder in self._replacements:
            text = pattern.sub(placeholder, text)
        return text


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME", "")


def _computer() -> str:
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


scrub = Scrubber()


class ScrubbingFormatter(logging.Formatter):
    """A formatter whose whole output, tracebacks included, goes through scrub."""

    def format(self, record: logging.LogRecord) -> str:
        return scrub(super().format(record))
