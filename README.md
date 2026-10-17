# 🌙 Night Light for Windows

A lightweight Windows app that gives users control over screen brightness and night light settings across all connected displays, including external monitors, with scheduling and a clean, intuitive GUI.

Inspired by the display controls in KDE Plasma on Linux, this app brings similar functionality to Windows, especially for multi-monitor setups where native support falls short.

## ✨ Features (Planned & In Progress)

This list is the plan for the project; the status column is kept up to date as work lands.

| Status | Meaning |
|---|---|
| ✅ Ready | Built and tested |
| 🧪 Needs Windows testing | Built and unit-tested, or checked against a KDE Plasma desktop, but not yet run on Windows or real monitors |
| 🚧 In progress | Partly built |
| 📋 Planned | Not started |

| Feature | Status | Notes |
|---|---|---|
| Custom GUI built with Python and Qt | ✅ Ready | Modelled on KDE Plasma's "Brightness and Color" panel and Night Light settings. Runs on Linux. |
| Per-monitor brightness adjustment (DDC/CI for external monitors, WMI for built-in laptop panels) | 🧪 Needs Windows testing | Both are built, using the Windows APIs directly the way Monitorian and Twinkle Tray do, and tested against a fake of those APIs. Not yet run on real monitors. |
| Night light with an adjustable color temperature slider (warm color shift) | 🧪 Needs Windows testing | Color maths, slider, live preview and animated changes are tested. The Windows gamma ramp keeps a monitor's calibration, works around Windows' limit on very warm ramps, and skips HDR displays; this has only run against a fake. |
| Scheduling: manual times and sunrise/sunset, with gradual transitions | ✅ Ready | Custom times, and sunrise/sunset for a city picked from the time zone list (searchable, with Detect for this PC's time zone) or a custom latitude and longitude. Temporary Suspend/Activate is included. Automatic location is 📋 planned. |
| Multi-display support with automatic detection of connected or disconnected monitors | 🧪 Needs Windows testing | Plugging, unplugging, display changes and waking from sleep trigger re-detection and re-apply night light. Works on Linux. |
| System tray integration with a quick-access flyout | 🧪 Needs Windows testing | Works on Linux. On Windows the flyout opens against the taskbar, next to the tray icon, which has not been seen yet. A second launch opens the running copy's flyout. |
| Settings persistence | ✅ Ready | JSON in the user's config folder. Corrupt or hand-edited files fall back to defaults. |
| Remember brightness | 🧪 Needs Windows testing | Each display's level is restored when the app starts, after sleep and when a display is reconnected, as Twinkle Tray does; can be turned off. Laptop panels never go to 0%. Tested against fake monitors. |
| Change all displays together | 🧪 Needs Windows testing | One slider moves every display, like Monitorian's "unison" and Twinkle Tray's "linked levels". |
| Start at sign-in and start minimized in the tray | 🧪 Needs Windows testing | Uses the current user's Run registry key, like Monitorian and Twinkle Tray; no administrator rights needed. Tested against a fake registry. |
| Clear "not supported" state for monitors that don't expose DDC/CI | ✅ Ready | Shown in the panel, with the reason when it is known (for example a DisplayLink dock). Checked with the fake backends. |

## 🎯 Goals

- Bring KDE-style brightness and night light control to Windows users.
- Support external displays through DDC/CI where hardware allows.
- Provide a minimal but elegant interface suitable for daily use.
- Fail gracefully when a monitor, dock, or driver doesn't support a feature.

## ❌ The Problem on Windows

- **Brightness:** Windows has no native brightness control for most external monitors connected via HDMI, DisplayPort, or USB-C. The built-in slider mainly works for internal laptop screens, so external monitors usually require their physical buttons or on-screen menu.
- **Night light:** Windows' built-in Night Light works on many external monitors, but it is unreliable in some setups, such as certain docking stations (e.g. DisplayLink) and some Arm-based laptops. It also offers limited per-monitor control.

This project addresses those gaps with DDC/CI for hardware brightness and software gamma adjustment for night light, all from a single GUI.

## 🛠️ Built With

| Purpose | Tool |
|---|---|
| Language | Python 3.12+ |
| GUI | PySide6 (Qt for Python) |
| External monitor brightness | DDC/CI through Windows' Monitor Configuration API (`dxva2`) via `ctypes`; `monitorcontrol` on Linux |
| Display names, connectors, HDR state | `QueryDisplayConfig` via `ctypes` |
| Laptop panel brightness | `wmi` (WMI `WmiMonitorBrightness`) |
| Night light | Windows gamma ramp via `ctypes` (optional DDC/CI RGB gain later) |
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
- Gamma ramps don't apply to displays in HDR mode, so night light skips them.
- Running alongside Windows Night Light, f.lux or GPU color settings (such as AMD's "Custom Colors") may cause conflicts.

## 🗺️ Roadmap

1. Brightness control for a single external monitor
2. Multi-monitor support and hotplug detection
3. Gamma-based night light with temperature slider
4. Manual and sunrise/sunset scheduling with smooth transitions
5. Tray flyout and settings persistence
6. Packaging and testing on different hardware

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

## 🚀 Getting Started

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
docs/                    Windows research notes and test plan
tests/                   pytest suite (Windows backends run against a fake of the Windows APIs)
```

## 📄 License

This project is licensed under the **GNU General Public License v3.0**. See [LICENSE](LICENSE). Source files carry an `SPDX-License-Identifier: GPL-3.0-only` header, and the license is also declared in [pyproject.toml](pyproject.toml).

## 🙏 Acknowledgements

Behavior, wording and some data are based on the open-source work of other projects, used under licenses compatible with the GPL-3.0:

- **[KDE Plasma](https://kde.org/plasma-desktop/)**: the design of the Brightness and Color panel and the Night Light settings page, KWin's Night Light transition behavior and timings, and the schedule rules from [knighttime](https://invent.kde.org/plasma/knighttime). KWin and PowerDevil are GPL-2.0-or-later; knighttime is LGPL.
- **[Monitorian](https://github.com/emoacht/Monitorian)** (MIT) and **[Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray)** (MIT): how the Windows display, DDC/CI, WMI and gamma APIs are used, the order of fallbacks, flyout placement and tray behavior, retry and re-detection timings, and many Windows quirks. No code is copied; the approach is re-implemented in Python, as described in [docs/windows-notes.md](docs/windows-notes.md).
- **[Redshift](https://github.com/jonls/redshift)** (GPL-3.0) and KWin: the blackbody color table by Ingo Thies (2013) used in [nightlight/core/colortemp.py](nightlight/core/colortemp.py).
- Python libraries: [PySide6](https://doc.qt.io/qtforpython-6/) (LGPL-3.0), [monitorcontrol](https://github.com/newAM/monitorcontrol) (MIT), [WMI](https://github.com/tjguk/wmi) (MIT) with [pywin32](https://github.com/mhammond/pywin32) (PSF), [astral](https://github.com/sffjunkie/astral) (Apache-2.0), [platformdirs](https://github.com/tox-dev/platformdirs) (MIT), [jeepney](https://gitlab.com/takluyver/jeepney) (MIT).
