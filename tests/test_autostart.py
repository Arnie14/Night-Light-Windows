# SPDX-License-Identifier: GPL-3.0-only
from nightlight import autostart
from nightlight.autostart import RUN_KEY, WindowsAutostart, XdgAutostart


class FakeKey:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeRegistry:
    """Just enough of winreg for one key under HKEY_CURRENT_USER."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self, exists=True):
        self.keys = {("HKCU", RUN_KEY): {}} if exists else {}

    def OpenKey(self, root, path):
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
        key.values[name] = value

    def DeleteValue(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        del key.values[name]

    @property
    def run(self):
        return self.keys.get(("HKCU", RUN_KEY), {})


COMMAND = [r"C:\Program Files\Python313\pythonw.exe", r"C:\Users\me\Winlight\main.py"]


def test_windows_enable_writes_a_quoted_command():
    registry = FakeRegistry()
    entry = WindowsAutostart(COMMAND, registry)
    assert not entry.is_enabled()
    entry.set_enabled(True)
    assert entry.is_enabled()
    assert registry.run["Winlight"] == r'"C:\Program Files\Python313\pythonw.exe" C:\Users\me\Winlight\main.py'


def test_windows_disable_removes_only_our_value():
    registry = FakeRegistry()
    registry.run["OneDrive"] = "onedrive.exe"
    entry = WindowsAutostart(COMMAND, registry)
    entry.set_enabled(True)
    entry.set_enabled(False)
    entry.set_enabled(False)  # already off: no error
    assert registry.run == {"OneDrive": "onedrive.exe"}


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


def test_sync_rewrites_when_on_and_removes_when_off():
    registry = FakeRegistry()
    old = WindowsAutostart(["old.exe"], registry)
    old.set_enabled(True)
    autostart.sync(WindowsAutostart(COMMAND, registry), True)
    assert "main.py" in registry.run["Winlight"]  # a moved app gets its new path
    autostart.sync(WindowsAutostart(COMMAND, registry), False)
    assert "Winlight" not in registry.run


def test_sync_survives_errors():
    class Broken:
        def is_enabled(self):
            raise PermissionError("denied")

        def set_enabled(self, enabled):
            raise PermissionError("denied")

    autostart.sync(Broken(), True)
    autostart.sync(Broken(), False)
    autostart.sync(None, True)


def test_launch_command_runs_main_py():
    command = autostart.launch_command()
    assert command[-1].endswith("main.py")
