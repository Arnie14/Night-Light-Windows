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
| Per-monitor brightness adjustment (DDC/CI for external monitors, WMI for built-in laptop panels) | 🚧 In progress | DDC/CI is built but untested on a real external monitor (🧪). WMI for laptop panels is 📋 planned. |
| Night light with an adjustable color temperature slider (warm color shift) | 🧪 Needs Windows testing | Color maths, slider, live preview and animated changes are tested. The Windows gamma-ramp output has never run on Windows. |
| Scheduling: manual times and sunrise/sunset, with gradual transitions | ✅ Ready | Custom times, and sunrise/sunset from typed-in coordinates. Temporary Suspend/Activate is included. Automatic location is 📋 planned. |
| Multi-display support with automatic detection of connected or disconnected monitors | 🚧 In progress | Multiple monitors are supported. Automatic detection of plugging/unplugging is 📋 planned (there is a manual refresh button). |
| System tray integration with a quick-access flyout | 🧪 Needs Windows testing | Works on Linux. On Windows the flyout is a popup next to the tray, which has not been seen yet. |
| Settings persistence | ✅ Ready | JSON in the user's config folder. Corrupt or hand-edited files fall back to defaults. |
| Clear "not supported" state for monitors that don't expose DDC/CI | ✅ Ready | Shown in the panel; checked with the fake backend. |

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
| External monitor brightness | `monitorcontrol` (DDC/CI) |
| Laptop panel brightness | `wmi` (fallback for built-in displays) |
| Night light | Windows gamma ramp via `ctypes` (optional DDC/CI RGB gain later) |
| System tray | `QSystemTrayIcon` (built into Qt) |
| Scheduling | `QTimer` + `astral` (sunrise/sunset) |
| Settings | JSON stored with `platformdirs` |
| Packaging | PyInstaller |

On a KDE Plasma desktop the app drives Plasma's own brightness and Night Light services (via `jeepney`) so it can be developed and tried without Windows. See [Development](#-development).

## ⚠️ Known Challenges

- DDC/CI support varies by monitor, cable, and dock, and some monitors have it disabled in their on-screen menu.
- DDC/CI calls are slow, so they run off the UI thread and are debounced.
- Gamma ramps can be reset by sleep, resolution changes, or fullscreen apps, so they are reapplied on display events.
- Gamma-based tinting may not work properly in HDR mode.
- Running alongside Windows Night Light or f.lux may cause conflicts.
- Some monitors report malformed DDC/CI capability data, so capability queries are handled defensively.

## 🗺️ Roadmap

1. Brightness control for a single external monitor
2. Multi-monitor support and hotplug detection
3. Gamma-based night light with temperature slider
4. Manual and sunrise/sunset scheduling with smooth transitions
5. Tray flyout and settings persistence
6. Packaging and testing on different hardware

## 🔗 Related Projects

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

### Project layout

```
main.py                  entry point and command line
nightlight/
  app.py                 wires services to the UI
  config.py              settings and JSON storage
  controller.py          applies the schedule to the displays (animation, overrides)
  monitors.py            runs slow monitor calls on a background thread
  core/                  pure logic, no Qt or hardware: colortemp.py, schedule.py
  hardware/              brightness.py, gamma.py (+ Windows), kde.py (Linux dev backend)
  ui/                    tray, flyout, settings_window, widgets, night_light_text, icons
tests/                   pytest suite for core, config and controller
```

## 📄 License

This project is licensed under the **GNU General Public License v3.0**. See [LICENSE](LICENSE). Source files carry an `SPDX-License-Identifier: GPL-3.0-only` header, and the license is also declared in [pyproject.toml](pyproject.toml).

## 🙏 Acknowledgements

Behavior, wording and some data are based on the open-source work of other projects, used under licenses compatible with the GPL-3.0:

- **[KDE Plasma](https://kde.org/plasma-desktop/)**: the design of the Brightness and Color panel and the Night Light settings page, KWin's Night Light transition behavior and timings, and the schedule rules from [knighttime](https://invent.kde.org/plasma/knighttime). KWin and PowerDevil are GPL-2.0-or-later; knighttime is LGPL.
- **[Redshift](https://github.com/jonls/redshift)** (GPL-3.0) and KWin: the blackbody color table by Ingo Thies (2013) used in [nightlight/core/colortemp.py](nightlight/core/colortemp.py).
- Python libraries: [PySide6](https://doc.qt.io/qtforpython-6/) (LGPL-3.0), [monitorcontrol](https://github.com/newAM/monitorcontrol) (MIT), [astral](https://github.com/sffjunkie/astral) (Apache-2.0), [platformdirs](https://github.com/tox-dev/platformdirs) (MIT), [jeepney](https://gitlab.com/takluyver/jeepney) (MIT).
