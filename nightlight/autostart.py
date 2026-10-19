# SPDX-License-Identifier: GPL-3.0-only
"""Starting the app when the user signs in.

On Windows this is a value under the current user's
HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run key, as Monitorian,
Twinkle Tray and most tray apps use: no administrator rights needed, and it
shows up in Task Manager's Startup tab. On Linux (for development) it is an
XDG autostart .desktop file.

The installer creates the Run value (its "Start Winlight when I sign in"
task, ticked by default) and the uninstaller removes it. The app itself
only turns the entry on and off the way Task Manager does, through
Explorer's StartupApproved\\Run key, and it never touches either key at
start. Beta 5 rewrote the Run value at every start, and Microsoft Defender's
behavior monitoring took that, from an unsigned program, for malware
setting itself up to persist (Behavior:Win32/Persistence.A!ml), then
quarantined the app. The Run value is written by the app only when the user
turns the setting on and there is none for this program (the portable zip,
or the task unticked at install).

The registry is the source of truth: the installer, Task Manager or the
Settings app may change it, so the app reads it at start (read()).
"""

from __future__ import annotations

import logging
import os
import shlex
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol

from nightlight import APP_NAME

log = logging.getLogger(__name__)

APP_ID = APP_NAME  # the value's name in the Run key
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Task Manager's on/off switch for each Run value: 12 bytes, an odd first
# byte meaning disabled, then when it was disabled. No value means enabled.
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
DISABLED = 0x03
_FILETIME_EPOCH = 11644473600  # seconds from 1601 to 1970


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


def disabled_value(now: float | None = None) -> bytes:
    """StartupApproved data for "disabled", as Task Manager writes it."""
    filetime = int(((time.time() if now is None else now) + _FILETIME_EPOCH) * 10_000_000)
    return struct.pack("<IQ", DISABLED, filetime)


def _arguments(command: str) -> list[str]:
    """A Run value's command split into its parts, for comparing paths."""
    return [os.path.normcase(part.strip('"')) for part in shlex.split(command, posix=False)]


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
        return self._run_value() is not None and self._approved()

    def set_enabled(self, enabled: bool) -> None:
        reg = self._reg
        if enabled:
            value = self._run_value()
            if value is None or _arguments(value) != _arguments(self._command):
                # None for this program (portable, or unticked at install),
                # or one for a copy somewhere else: only ever on the user's say.
                with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
                    reg.SetValueEx(key, APP_ID, 0, reg.REG_SZ, self._command)
                log.info("Added %s to the programs that start at sign-in", APP_NAME)
            if not self._approved():
                try:
                    with reg.OpenKey(reg.HKEY_CURRENT_USER, APPROVED_KEY, 0, reg.KEY_SET_VALUE) as key:
                        reg.DeleteValue(key, APP_ID)
                except FileNotFoundError:
                    pass
        elif self.is_enabled():
            # Off as Task Manager does it: the Run value stays, so turning it
            # back on is no new start-up entry.
            with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, APPROVED_KEY, 0, reg.KEY_SET_VALUE) as key:
                reg.SetValueEx(key, APP_ID, 0, reg.REG_BINARY, disabled_value())

    def _run_value(self) -> str | None:
        try:
            with self._reg.OpenKey(self._reg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _kind = self._reg.QueryValueEx(key, APP_ID)
                return str(value)
        except OSError:
            return None

    def _approved(self) -> bool:
        try:
            with self._reg.OpenKey(self._reg.HKEY_CURRENT_USER, APPROVED_KEY) as key:
                data, _kind = self._reg.QueryValueEx(key, APP_ID)
        except OSError:
            return True  # Explorer starts entries it has no switch for
        return not (isinstance(data, bytes) and data and data[0] & 1)


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


def read(autostart: Autostart | None) -> bool | None:
    """Whether the app starts at sign-in now, or None if that can't be told.
    Only reads: nothing is written at start."""
    if autostart is None:
        return None
    try:
        return autostart.is_enabled()
    except OSError:
        log.warning("Could not tell whether %s starts at sign-in", APP_NAME, exc_info=True)
        return None


def change(autostart: Autostart | None, enabled: bool) -> None:
    """Turn starting at sign-in on or off, when the user changes the setting;
    failures are logged, not raised."""
    if autostart is None:
        return
    try:
        if autostart.is_enabled() != enabled:
            autostart.set_enabled(enabled)
    except OSError:
        log.warning("Could not %s starting at sign-in", "turn on" if enabled else "turn off", exc_info=True)
