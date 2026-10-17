# 🌙 Night Light for Windows

A lightweight Windows app that gives users control over screen brightness and night light settings across all connected displays, including external monitors, with scheduling and a clean, intuitive GUI.

Inspired by the display controls in KDE Plasma on Linux, this app brings similar functionality to Windows, especially for multi-monitor setups where native support falls short.

## ✨ Features (Planned & In Progress)

- ✅ Custom GUI built with Python and Qt
- 🔆 Per-monitor brightness adjustment (DDC/CI for external monitors, WMI for built-in laptop panels)
- 🌙 Night light with an adjustable color temperature slider (warm color shift)
- ⏰ Scheduling: manual times and sunrise/sunset, with gradual transitions
- 🖥️ Multi-display support with automatic detection of connected or disconnected monitors
- 📌 System tray integration with a quick-access flyout
- 💾 Settings persistence
- ⚠️ Clear "not supported" state for monitors that don't expose DDC/CI

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

## 🚀 Getting Started (For Devs)

```bash
# Clone the repo
git clone https://github.com/arnie14/night-light-windows.git
cd night-light-windows

# Install dependencies
pip install -r requirements.txt

# Run the app
python main.py
