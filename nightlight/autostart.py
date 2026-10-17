# SPDX-License-Identifier: GPL-3.0-only
"""Starting the app when the user signs in.

On Windows this is a value under the current user's
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run key, as Monitorian,
Twinkle Tray and most tray apps do: no administrator rights needed, and it
shows up in Task Manager's Startup tab. On Linux (for development) it is an
XDG autostart .desktop file.

The settings file is the source of truth: at every start the app re-writes
or removes the entry to match it, so moving the app's folder or switching
Python installs doesn't leave a stale command behind.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from nightlight import APP_NAME

log = logging.getLogger(__name__)

APP_ID = APP_NAME  # the value's name in the Run key
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> list[str]:
    """How to start this app again: the executable of a packaged build, or
    Python running main.py, preferring pythonw.exe so no console window opens."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    if windowless.exists():
        python = windowless
    main = Path(__file__).resolve().parent.parent / "main.py"
    return [str(python), str(main)]


class Autostart(Protocol):
    def is_enabled(self) -> bool: ...

    def set_enabled(self, enabled: bool) -> None: ...


class WindowsAutostart:
    def __init__(self, command: list[str] | None = None, registry=None) -> None:
        if registry is None:
            import winreg as registry
        self._reg = registry
        self._command = subprocess.list2cmdline(command or launch_command())

    @property
    def command(self) -> str:
        return self._command

    def is_enabled(self) -> bool:
        try:
            with self._reg.OpenKey(self._reg.HKEY_CURRENT_USER, RUN_KEY) as key:
                self._reg.QueryValueEx(key, APP_ID)
                return True
        except OSError:
            return False

    def set_enabled(self, enabled: bool) -> None:
        with self._reg.CreateKeyEx(self._reg.HKEY_CURRENT_USER, RUN_KEY, 0, self._reg.KEY_SET_VALUE) as key:
            if enabled:
                self._reg.SetValueEx(key, APP_ID, 0, self._reg.REG_SZ, self._command)
            else:
                try:
                    self._reg.DeleteValue(key, APP_ID)
                except FileNotFoundError:
                    pass


class XdgAutostart:
    def __init__(self, command: list[str] | None = None, folder: Path | None = None) -> None:
        config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.path = (folder or config_home / "autostart") / "winlight.desktop"
        self._command = shlex.join(command or launch_command())

    def is_enabled(self) -> bool:
        return self.path.exists()

    def set_enabled(self, enabled: bool) -> None:
        if not enabled:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nComment=Brightness and night light\nExec={self._command}\n"
            "X-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )


def create() -> Autostart | None:
    """The autostart mechanism for this platform, or None where there is none."""
    if sys.platform == "win32":
        return WindowsAutostart()
    if sys.platform.startswith("linux"):
        return XdgAutostart()
    return None


def sync(autostart: Autostart | None, enabled: bool) -> None:
    """Make the sign-in entry match the setting; failures are logged, not raised."""
    if autostart is None:
        return
    try:
        if enabled or autostart.is_enabled():
            autostart.set_enabled(enabled)
    except OSError:
        log.warning("Could not %s starting at sign-in", "turn on" if enabled else "turn off", exc_info=True)
