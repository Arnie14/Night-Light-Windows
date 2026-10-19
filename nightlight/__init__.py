# SPDX-License-Identifier: GPL-3.0-only
"""Night Light for Windows: per-monitor brightness and night light control."""

__version__ = "0.1.0-beta.6"

APP_NAME = "Winlight"  # shown to users; files and folders keep the original "night-light" names
AUTHOR = "Anurag Jha"
REPO_URL = "https://github.com/Arnie14/Night-Light-Windows"


def display_version(version: str = __version__) -> str:
    """"0.1.0-beta.5" -> "0.1.0 Beta 5", as release titles write it."""
    base, _, pre = version.partition("-")
    if not pre:
        return base
    name, _, number = pre.partition(".")
    return f"{base} {name.capitalize()} {number}".strip()
