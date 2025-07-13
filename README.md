# Night-Light-Windows
# 🌙 Night Light for Windows

A modern, lightweight Windows app that gives users full control over screen **brightness** and **night light settings** — including support for **external monitors**, **scheduling**, and a clean, intuitive GUI.

Inspired by the built-in controls found in KDE Plasma on Linux, this app brings similar functionality to Windows users — especially for those using multi-monitor setups where native support falls short.

---

## ✨ Features (Planned & In Progress)

- ✅ Custom GUI built with Python
- 🔆 Manual brightness adjustment
- 🌙 Toggle night light mode (warm color shift)
- ⏰ Schedule-based control (auto on/off at set times)
- 🖥️ Support for multiple displays / external monitors
- 📌 Optional system tray integration
- 💾 Config file or settings persistence

---

## 🎯 Goals

- Bring Linux-style brightness and night light control to Windows users.
- Ensure external display support using reliable APIs or hardware backends.
- Provide a minimal but elegant interface suitable for daily use.

---

## ❌ The Problem on Windows

Windows offers no native way to control **brightness** or **night light** on **external displays** connected via HDMI, DisplayPort, or USB-C.  

While internal laptop screens support these features, external monitors are left out — making multi-monitor setups frustrating, especially at night.

This project addresses that gap by using Windows APIs and DDC/CI-compatible approaches (when supported by hardware) to bring full brightness and night light control to **all displays**, from a single user-friendly GUI.

---

## 🛠️ Built With

- **Python 3.12+**
- **PyQt5** or **PyQt6** – for building a modern, native-feeling GUI
- `wmi`, `pywin32`, or `monitorcontrol` – to interface with Windows and monitor hardware
- `pystray` – for system tray icon support (planned)
- `schedule` – for timer-based automation (planned)

---

## 💡 Why This Project?

This project solves a real usability issue on Windows — giving users control over display settings that should have existed natively.

In addition, it's a learning-focused project designed to:
- Practice GUI programming with Python
- Work with real Windows APIs
- Handle cross-device compatibility (DDC/CI, multi-monitor)
- Use AI tools (like ChatGPT or Copilot) effectively in software development

---

## 📷 Screenshots (Coming Soon)

_TODO:GUI mockups or screenshots here once built._

---

## 🚀 Getting Started (For Devs)

```bash
# Clone the repo
git clone https://github.com/arnie14/night-light-windows.git
cd night-light-windows

# Install dependencies
pip install -r requirements.txt

# Run the app
python main.py
