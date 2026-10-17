# 🌙 Winlight

Brightness and night light for every display on Windows: external monitors, TVs and the laptop panel, from one tray panel, with a night light schedule. Inspired by KDE Plasma's display controls, for multi-monitor setups where Windows' own controls fall short.

<p align="center">
  <img src="docs/screenshots/flyout-light.png" alt="The Winlight panel in light mode: a slider per display and Night Light" width="340">
  &nbsp;
  <img src="docs/screenshots/flyout-dark.png" alt="The Winlight panel in dark mode" width="340">
</p>

- **Every display gets a slider.** DDC/CI for external monitors, the laptop's own control for its panel, Windows' SDR brightness for HDR displays, and software dimming for TVs and anything else.
- **Night light** with a warm color shift, by custom times or sunrise and sunset at your city, with smooth transitions.
- **Remembers each display's brightness** and puts it back after restarting, sleep, or reconnecting a monitor.
- **Small and native:** a tray icon and a panel, light and dark mode, starts with Windows if you like.

<p align="center">
  <img src="docs/screenshots/settings-light.png" alt="Winlight settings: Night Light schedule and temperatures, startup and brightness options" width="520">
</p>

[⬇️ Download the latest release](https://github.com/Arnie14/Night-Light-Windows/releases) · Windows 10 and 11, 64-bit

## ✨ Features

| Status | Meaning |
|---|---|
| ✅ Ready | Built, and tested on Windows |
| 🧪 Needs Windows testing | Built and unit-tested against a fake of the Windows APIs, not yet run on that hardware |
| 📋 Planned | Not started |

| Feature | Status | Notes |
|---|---|---|
| Tray icon and quick-access panel, settings window | ✅ Ready | Modelled on KDE Plasma's "Brightness and Color" panel and Night Light settings. Light and dark mode follow Windows. |
| Night light with an adjustable color temperature | ✅ Ready | Tested on a laptop panel. Keeps a monitor's calibration, works around Windows' limit on very warm colors, and skips HDR displays (Windows ignores gamma there). |
| Scheduling: custom times, or sunrise and sunset at a city or coordinates | ✅ Ready | Cities from the time zone list (searchable, with Detect), with gradual transitions and temporary Suspend/Activate. |
| Laptop panel brightness | ✅ Ready | Through WMI. Never goes fully dark. |
| External monitor brightness over DDC/CI | 🧪 Needs Windows testing | The Windows APIs are used directly, the way Monitorian and Twinkle Tray do. Sleeping and hung monitors are handled carefully (see [docs/windows-notes.md](docs/windows-notes.md)). |
| SDR brightness for HDR displays | 🧪 Needs Windows testing | Windows' "SDR content brightness" (80–480 nits), as in Monitorian and Twinkle Tray, for HDR displays without DDC/CI. |
| Software dimming for TVs and displays without brightness control | 🧪 Needs Windows testing | Dims the colors through the gamma ramp, together with night light, using Twinkle Tray's driver-safe curve. Can be turned off. |
| Remember brightness, change all displays together | 🧪 Needs Windows testing | Restored after start, sleep and reconnecting, as Twinkle Tray does; linked sliders like Monitorian's "unison". |
| Multi-display detection, hotplug and sleep | 🧪 Needs Windows testing | Re-detection after display changes and waking, several times, as monitors are often slow to answer. |
| Start at sign-in, start minimized | ✅ Ready | The current user's Run registry key; no administrator rights. |
| Installer, portable zip, uninstaller | ✅ Ready | Per-user, built by GitHub Actions. |

## 🎯 Goals

- Bring KDE-style brightness and night light control to Windows users.
- Support external displays through DDC/CI where hardware allows.
- Provide a minimal but elegant interface suitable for daily use.
- Fail gracefully when a monitor, dock, or driver doesn't support a feature.

## ❌ The Problem on Windows

- **Brightness:** Windows has no native brightness control for most external monitors connected via HDMI, DisplayPort, or USB-C. The built-in slider mainly works for internal laptop screens, so external monitors usually require their physical buttons or on-screen menu.
- **Night light:** Windows' built-in Night Light works on many external monitors, but it is unreliable in some setups, such as certain docking stations (e.g. DisplayLink) and some Arm-based laptops. It also offers limited per-monitor control.

This project addresses those gaps with DDC/CI for hardware brightness, Windows' SDR brightness for HDR displays, and the gamma ramp for night light and for dimming displays that have no brightness control, all from a single GUI.

## 🛠️ Built With

| Purpose | Tool |
|---|---|
| Language | Python 3.12+ |
| GUI | PySide6 (Qt for Python) |
| External monitor brightness | DDC/CI through Windows' Monitor Configuration API (`dxva2`) via `ctypes`; `monitorcontrol` on Linux |
| Display names, connectors, HDR state | `QueryDisplayConfig` via `ctypes` |
| Laptop panel brightness | `wmi` (WMI `WmiMonitorBrightness`) |
| HDR displays | Windows' SDR white level (`DisplayConfigSetDeviceInfo`) via `ctypes` |
| Night light, software dimming | Windows gamma ramp via `ctypes`, one composed ramp per display |
| System tray | `QSystemTrayIcon` (built into Qt) |
| Scheduling | `QTimer` + `astral` (sunrise/sunset) |
| Settings | JSON stored with `platformdirs` |
| Packaging | PyInstaller |

On a KDE Plasma desktop the app drives Plasma's own brightness and Night Light services (via `jeepney`) so it can be developed and tried without Windows. See [Development](#-development).

## ⚠️ Known Challenges

Most of these were learned from [Monitorian](https://github.com/emoacht/Monitorian) and [Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray); [docs/windows-notes.md](docs/windows-notes.md) has the details and what this app does about each.

- DDC/CI support varies by monitor, cable, and dock. Some monitors have it disabled in their on-screen menu, and DisplayLink-based USB docks can't pass it at all.
- DDC/CI calls are slow and sometimes fail for a moment, so they run off the UI thread, are retried on transient errors, and only the latest slider value is sent.
- Monitors often don't answer DDC/CI for several seconds after being plugged in or waking from sleep, so detection runs again a few times.
- Requesting a monitor's DDC/CI capabilities string is slow and can hang on some monitors, so the Windows backend never asks for it; names come from the EDID.
- Windows refuses gamma ramps that are too far from neutral, which limits how warm night light can go unless the `GdiIcmGammaRange` registry value is raised.
- Gamma ramps can be reset by sleep, resolution changes, or fullscreen apps, so they are re-applied on display events.
- Gamma ramps don't apply to displays in HDR mode, so night light skips them, and their brightness uses Windows' SDR level instead.
- Software dimming can't go as dark as a backlight (Windows rejects very dark ramps), and saves no power: it's for displays with no other control.
- Most TVs don't support DDC/CI.
- Running alongside Windows Night Light, f.lux or GPU color settings (such as AMD's "Custom Colors") may cause conflicts.

## 🗺️ Roadmap

1. ✅ Night light, scheduling, tray panel and settings
2. ✅ Laptop panel brightness, installer, first beta
3. 🧪 External monitors, TVs and HDR displays: testing now ([test plan](docs/windows-testing.md))
4. 📋 Code signing, then a 1.0 release
5. 📋 Hide and rename displays, hotkeys, brightness by time of day

## 🔗 Related Projects

- **[Monitorian](https://github.com/emoacht/Monitorian):** a Windows tray app for adjusting the brightness of multiple monitors over DDC/CI and WMI. Its handling of monitor detection, fallbacks and flyout placement shaped this project's Windows backend.
- **[Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray):** a Windows tray app for monitor brightness with a Windows-style flyout, scheduling and DDC/CI features. Its notes on DDC/CI reliability and gamma ramps shaped the night light and error handling here.
- **PowerToys Power Display:** Microsoft's PowerToys includes a monitor control utility that uses DDC/CI. This project aims to be a lighter, simpler tool with a focus on night light and scheduling.
- **f.lux / Windows Night Light:** existing color temperature tools that this project complements or replaces for per-monitor control.

## 💡 Why This Project?

This project targets a real usability gap on Windows: display controls that should work consistently across all monitors, not just laptop screens.

It is also a learning-focused project designed to:

- Practice GUI programming with Python and Qt
- Work with real Windows APIs
- Handle cross-device compatibility (DDC/CI, multi-monitor)
- Use AI tools (like ChatGPT or Copilot) effectively in software development

## ⬇️ Download details

Get the latest build from [Releases](https://github.com/Arnie14/Night-Light-Windows/releases) (Windows 10 and 11, 64-bit):

- **`Winlight-<version>-setup.exe`**: installs for your user only (no administrator rights), with a Start menu entry and an uninstaller.
- **`Winlight-<version>-portable.zip`**: unzip anywhere and run `Winlight.exe`.

Builds are beta and not yet code-signed, so Windows SmartScreen may show "Windows protected your PC". Choose **More info → Run anyway**. External monitors need DDC/CI switched on in their own on-screen menu.

## 🚀 Getting Started (from source)

```bash
# Clone the repo
git clone https://github.com/Arnie14/Night-Light-Windows.git
cd Night-Light-Windows

# Create a virtual environment and install dependencies
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Run the app
python main.py
```

## 🧑‍💻 Development

The app is built so most of it can be developed on Linux. The hardware layer sits behind small interfaces with several implementations, chosen with `--backend`:

| Backend | Used for |
|---|---|
| `real` (default on Windows) | DDC/CI monitors and Windows gamma ramps |
| `kde` (default on a KDE Plasma desktop) | Plasma's own brightness and Night Light services, so the UI visibly works on a Linux laptop |
| `fake` (default elsewhere) | Pretend monitors and a logged "gamma", no hardware needed |

```bash
python main.py --backend fake --debug
pip install -r requirements-dev.txt
pytest
```

Other options:

- `python main.py --probe` prints what each Windows API reports about every display, and saves it as `probe.txt` in the log folder. Use it when a monitor misbehaves.
- `--allow-multiple` lets a second copy start. Normally a second launch just opens the running copy's flyout.
- `--quit` asks the running copy to quit cleanly (restoring normal colors). The installer uses it before upgrading or uninstalling.

### Building the Windows app

On Windows, with [Inno Setup 6](https://jrsoftware.org/isdl.php) installed for the installer:

```powershell
pip install -r requirements-dev.txt -r requirements-build.txt
python packaging/build.py --version 0.1.0-beta.1
```

The README screenshots come from `python tools/screenshots.py` (demo displays, light and dark); run it on Windows for the native look.

This writes `dist\Winlight\` (the app), the portable zip and the installer. [`packaging/`](packaging/) has the PyInstaller spec (one folder, no UPX, unused Qt modules left out), the Inno Setup script and the icon generator.

Releases are built by GitHub Actions ([`.github/workflows/release.yml`](.github/workflows/release.yml)): pushing a tag such as `v0.1.0-beta.1` runs the tests, builds on Windows and publishes a release (a pre-release when the tag has a `-`). Running the workflow by hand from the Actions tab builds the files without a release.

Logs go to the terminal and to `night-light.log` in the user's log folder (`%LOCALAPPDATA%\night-light\Logs` on Windows).

Testing on Windows follows [docs/windows-testing.md](docs/windows-testing.md).

### Project layout

```
main.py                  entry point, command line and logging
nightlight/
  app.py                 wires services to the UI
  config.py              settings and JSON storage
  controller.py          applies the schedule to the displays (animation, overrides)
  monitors.py            runs slow monitor calls on a background thread
  watcher.py             notices display changes, hotplug and waking from sleep
  single_instance.py     keeps one copy running
  probe.py               the --probe diagnostic report
  core/                  pure logic, no Qt or hardware: colortemp.py, schedule.py
  hardware/              backend interfaces (brightness.py, gamma.py), windows.py + win32.py
                         (Windows APIs), kde.py (Linux dev backend)
  ui/                    tray, flyout, placement, settings_window, widgets, night_light_text, icons
  autostart.py           starting at sign-in (Run key)
  brightness_memory.py   remembers and restores each monitor's brightness
  data/                  time zone table for picking a location (from tools/make_timezones.py)
packaging/               Windows build: PyInstaller spec, Inno Setup script, icon, build.py
tools/                   screenshots.py (README images), make_timezones.py
docs/                    Windows research notes and test plan; screenshots/
tests/                   pytest suite (Windows backends run against a fake of the Windows APIs)
```

## 📄 License

This project is licensed under the **GNU General Public License v3.0**. See [LICENSE](LICENSE). Source files carry an `SPDX-License-Identifier: GPL-3.0-only` header, and the license is also declared in [pyproject.toml](pyproject.toml).

## 🙏 Acknowledgements

Behavior, wording and some data are based on the open-source work of other projects, used under licenses compatible with the GPL-3.0:

- **[KDE Plasma](https://kde.org/plasma-desktop/)**: the design of the Brightness and Color panel and the Night Light settings page, KWin's Night Light transition behavior and timings, KWin's composition of brightness and color temperature into one transform per display, and the schedule rules from [knighttime](https://invent.kde.org/plasma/knighttime). KWin and PowerDevil are GPL-2.0-or-later; knighttime is LGPL.
- **[Monitorian](https://github.com/emoacht/Monitorian)** (MIT) and **[Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray)** (MIT): how the Windows display, DDC/CI, WMI, SDR white level and gamma APIs are used, one class per kind of brightness control, the order of fallbacks, Twinkle Tray's driver-safe software dimming curve, flyout placement and tray behavior, retry and re-detection timings, and many Windows quirks. No code is copied; the approach is re-implemented in Python, as described in [docs/windows-notes.md](docs/windows-notes.md).
- **[Redshift](https://github.com/jonls/redshift)** (GPL-3.0) and KWin: the blackbody color table by Ingo Thies (2013) used in [nightlight/core/colortemp.py](nightlight/core/colortemp.py).
- Python libraries: [PySide6](https://doc.qt.io/qtforpython-6/) (LGPL-3.0), [monitorcontrol](https://github.com/newAM/monitorcontrol) (MIT), [WMI](https://github.com/tjguk/wmi) (MIT) with [pywin32](https://github.com/mhammond/pywin32) (PSF), [astral](https://github.com/sffjunkie/astral) (Apache-2.0), [platformdirs](https://github.com/tox-dev/platformdirs) (MIT), [jeepney](https://gitlab.com/takluyver/jeepney) (MIT).
