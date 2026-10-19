# SPDX-License-Identifier: GPL-3.0-only
import struct

from nightlight import autostart
from nightlight.autostart import APPROVED_KEY, RUN_KEY, WindowsAutostart, XdgAutostart


class FakeKey:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRegistry:
    """Just enough of winreg for keys under HKEY_CURRENT_USER, counting writes."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1
    REG_BINARY = 3

    def __init__(self, exists=True):
        self.keys = {("HKCU", RUN_KEY): {}} if exists else {}
        self.writes: list[tuple[str, str]] = []

    def OpenKey(self, root, path, reserved=0, access=0):
        if (root, path) not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(self.keys[(root, path)])

    def CreateKeyEx(self, root, path, reserved, access):
        return FakeKey(self.keys.setdefault((root, path), {}))

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):
        self.writes.append(("set", name))
        key.values[name] = value

    def DeleteValue(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        self.writes.append(("delete", name))
        del key.values[name]

    @property
    def run(self):
        return self.keys.get(("HKCU", RUN_KEY), {})

    @property
    def approved(self):
        return self.keys.get(("HKCU", APPROVED_KEY), {})


COMMAND = [r"C:\Program Files\Python313\pythonw.exe", r"C:\Users\me\Winlight\main.py"]
EXE = [r"C:\Users\me\AppData\Local\Programs\Winlight\Winlight.exe"]
INSTALLED = r'"C:\Users\me\AppData\Local\Programs\Winlight\Winlight.exe"'  # as the installer writes it


def installed():
    registry = FakeRegistry()
    registry.run["Winlight"] = INSTALLED
    return registry, WindowsAutostart(EXE, registry)


def test_the_installers_entry_counts_as_on():
    registry, entry = installed()
    assert entry.is_enabled()


def test_off_and_on_as_task_manager_does_without_touching_the_run_value():
    registry, entry = installed()
    entry.set_enabled(False)
    assert not entry.is_enabled()
    assert registry.run["Winlight"] == INSTALLED  # kept, so on again writes no new entry
    data = registry.approved["Winlight"]
    assert len(data) == 12 and data[0] == 3  # Task Manager's "disabled"
    filetime = struct.unpack("<Q", data[4:])[0]
    assert filetime > 133_000_000_000_000_000  # after 2022, in 100 ns steps since 1601
    entry.set_enabled(True)
    assert entry.is_enabled()
    assert "Winlight" not in registry.approved
    assert registry.run["Winlight"] == INSTALLED
    assert ("set", "Winlight") in registry.writes and registry.writes.count(("set", "Winlight")) == 1  # only the switch


def test_task_managers_enabled_value_counts_as_on():
    registry, entry = installed()
    registry.keys[("HKCU", APPROVED_KEY)] = {"Winlight": bytes([2]) + bytes(11)}
    assert entry.is_enabled()
    registry.approved["Winlight"] = bytes([3]) + bytes(11)
    assert not entry.is_enabled()


def test_without_an_entry_turning_it_on_writes_one_quoted():
    """The portable zip, or the task unticked at install: only on the user's say."""
    registry = FakeRegistry()
    entry = WindowsAutostart(COMMAND, registry)
    assert not entry.is_enabled()
    entry.set_enabled(True)
    assert entry.is_enabled()
    assert registry.run["Winlight"] == r'"C:\Program Files\Python313\pythonw.exe" C:\Users\me\Winlight\main.py'


def test_an_entry_for_a_copy_elsewhere_is_replaced_when_turned_on():
    registry = FakeRegistry()
    registry.run["Winlight"] = r'"D:\Old\Winlight.exe"'
    entry = WindowsAutostart(EXE, registry)
    entry.set_enabled(True)
    assert registry.run["Winlight"] == EXE[0]


def test_the_same_program_quoted_or_not_isnt_rewritten():
    registry, entry = installed()
    registry.run["Winlight"] = INSTALLED.upper()  # paths compare without case, as Windows does
    entry.set_enabled(True)
    assert registry.writes == []


def test_off_without_an_entry_writes_nothing():
    registry = FakeRegistry()
    registry.run["OneDrive"] = "onedrive.exe"
    entry = WindowsAutostart(COMMAND, registry)
    entry.set_enabled(False)
    assert registry.writes == [] and registry.run == {"OneDrive": "onedrive.exe"}


def test_windows_missing_run_key():
    registry = FakeRegistry(exists=False)
    entry = WindowsAutostart(COMMAND, registry)
    assert not entry.is_enabled()
    entry.set_enabled(True)
    assert entry.is_enabled()


def test_xdg_desktop_file(tmp_path):
    entry = XdgAutostart(["/usr/bin/python3", "/home/me/my app/main.py"], tmp_path)
    entry.set_enabled(True)
    text = entry.path.read_text()
    assert "Exec=/usr/bin/python3 '/home/me/my app/main.py'" in text
    assert entry.is_enabled()
    entry.set_enabled(False)
    assert not entry.path.exists()


def test_read_only_reads():
    registry, entry = installed()
    assert autostart.read(entry) is True
    registry.keys[("HKCU", APPROVED_KEY)] = {"Winlight": autostart.disabled_value()}
    assert autostart.read(entry) is False
    assert registry.writes == []
    assert autostart.read(None) is None


def test_change_writes_only_what_differs():
    registry, entry = installed()
    autostart.change(entry, True)  # already on
    assert registry.writes == []
    autostart.change(entry, False)
    assert not entry.is_enabled()
    autostart.change(None, True)


def test_errors_are_logged_not_raised():
    class Broken:
        def is_enabled(self):
            raise PermissionError("denied")

        def set_enabled(self, enabled):
            raise PermissionError("denied")

    autostart.change(Broken(), True)
    autostart.change(Broken(), False)
    assert autostart.read(Broken()) is None


def test_launch_command_runs_main_py():
    command = autostart.launch_command()
    assert command[-1].endswith("main.py")
