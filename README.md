# Winlight

**Brightness, color and comfort for every display on Windows.** Winlight brings each screen you use into one native tray app: external monitors over DDC/CI, the laptop's own panel, HDR displays through Windows' SDR brightness, and TVs through software dimming. It adds a night light that warms each display on your schedule, brightness that follows the time of day, and sleep-friendly evenings. Gaming, watching video, calls and color work keep true colors on the display you're using. Winlight is built to be dependable: it survives sleep, hotplugging and misbehaving monitors, puts your colors back even after a crash, and keeps your personal details out of every log and report. It's inspired by KDE Plasma's display controls, and it's free and open source.

<p align="center">
  <img src="docs/screenshots/flyout-light.png" alt="The Winlight panel in light mode: a slider per display and Night Light" width="340">
  &nbsp;
  <img src="docs/screenshots/flyout-dark.png" alt="The Winlight panel in dark mode" width="340">
</p>

- **One slider for every display.** DDC/CI for external monitors, Windows' own control for the laptop panel, SDR content brightness for HDR displays, and software dimming for TVs and anything else. Each display says how it's controlled, and why when it can't be.
- **A night light that fits your evening.** Custom times, or sunset to sunrise at your city, with twilight-length or chosen transitions, a color temperature from neutral to as warm as Windows allows, and a one-click sleep schedule built from your bedtime and wake-up time.
- **True colors when they matter.** Night light pauses on the display showing a fullscreen app, an app you choose, or, automatically, video, calls, games and photo, video and design apps. Your other displays stay warm.
- **Brightness that follows your day.** Six gradual presets or your own times (clock, sunrise or sunset), dimming while you're away, and each display's level remembered across restarts, sleep and reconnecting.
- **Dependable on real hardware.** Nothing is sent to sleeping displays. A monitor that hangs is set aside without freezing the others. Colors are restored after a crash, and Winlight backs off rather than fight another color tool.
- **Private by design.** Logs and the one-click problem report leave out your name, computer name, location and the apps you list; the report file deletes itself after a week.
- **Native and scriptable.** A tray icon, a Windows 11 style panel and settings, light, dark or Windows' own theme with a choice of accent colors, and a command line for AutoHotkey, Task Scheduler or a Stream Deck.

> **Winlight replaces Windows Night Light; it doesn't add to it.** If both are on, the screen is tinted twice and looks far too orange. Winlight shows a notice with a button to Windows' setting when it finds Windows Night Light on: turn that off and let Winlight do it.

<p align="center">
  <img src="docs/screenshots/settings-light.png" alt="Winlight settings: pages in a navigation pane, with the Night Light schedule, temperatures, sleep recommendation and pausing for apps" width="520">
</p>

**[Download the latest release](https://github.com/Arnie14/Night-Light-Windows/releases/latest)** · Windows 10 and 11, 64-bit · [Why Winlight?](docs/why-winlight.md) How it compares with Windows' own controls and similar apps, and how to switch.

## Features

| Status | Meaning |
|---|---|
| ● Ready | Built, and tested on Windows |
| ◐ In testing | Built and tested against simulated hardware and the Windows APIs; waiting for a run on that hardware |
| ○ Planned | Not started |

### Displays and brightness

| Feature | Status | Notes |
|---|---|---|
| Laptop panel brightness | ● Ready | Through WMI. Never goes fully dark. |
| External monitor brightness over DDC/CI | ◐ In testing | The Windows API bindings and their error handling are verified on Windows; a real DDC/CI monitor hasn't been driven yet. Monitors that hang are given up on after a few seconds and left alone, without affecting the others (see [docs/windows-notes.md](docs/windows-notes.md)). |
| HDR displays | ◐ In testing | DDC/CI is kept while the monitor takes it in HDR mode (it changes the backlight). A monitor that locks its brightness in HDR mode is noticed, from an ignored or refused change, and moved to Windows' "SDR content brightness" (80–480 nits), Monitorian's choice for HDR. Reading the SDR level is verified; the rest needs an HDR display. |
| Software dimming for TVs and displays without brightness control | ◐ In testing | Verified on a laptop's gamma ramp (20–100%, with night light on top); a TV hasn't been tried. Uses Twinkle Tray's driver-safe curve, never darker than 20%. Can be turned off. |
| Brightness by time of day | ● Ready | Six presets, each changing gradually (Day and night, Follow the sun, Office hours, Evening wind-down, Night owl, Dark room), or your own times by clock or sunrise/sunset ± minutes. Applied once per point: a level you pick stands until the next one, and restarts or sleeping through a point don't repeat or miss it. The tray tooltip and panel show the level now and the next change ("70% → 40% at 20:00"). |
| Dim when away | ◐ In testing | Optional: displays dim after a few minutes without input and come back on the first key or mouse move, never brighter than before. Not while an app keeps the screen on (videos, calls, presentations), a game runs exclusive fullscreen, or an app you list is in front. External displays dim in software (no DDC/CI writes), the laptop panel through Windows (saving power). Its parts are verified on the test laptop; a real idle cycle hasn't been run. |
| Remember brightness | ● Ready | Restored after starting, waking and reconnecting, as Twinkle Tray does. Displays are told apart by their EDID serial number, so identical monitors keep separate levels. A level set with the laptop's keys or Windows' slider isn't undone. |
| Change all displays together | ◐ In testing | Linked sliders like Monitorian's "unison"; needs two displays to try. |
| Laptop brightness keys move external displays too | ◐ In testing | Optional. Follows the laptop panel's level rather than intercepting the keys; needs an external display to try. |
| Rename and hide displays | ● Ready | Keyed by the display's id, so names follow it between ports. Hidden displays are never changed. |
| Compatible displays list and compatibility report | ● Ready | Settings > Displays lists each display, its connection and how its brightness is controlled (DDC/CI, WMI, SDR level, software dimming or none, with why). The report can be copied or saved, or printed with `--report`. Sends nothing to the displays. |

### Night light and comfort

| Feature | Status | Notes |
|---|---|---|
| Night light with an adjustable color temperature | ● Ready | From 6500 K (neutral) down to as warm as Windows allows (warmer with a registry setting; see [docs/windows-notes.md](docs/windows-notes.md)). Keeps a monitor's calibration (even after a crash), works around Windows' limit on very warm colors, and skips HDR displays (Windows ignores gamma there). |
| Scheduling | ● Ready | Custom times, or sunset to sunrise at a city (searchable, with Detect) or coordinates. Gradual transitions, following twilight or a length you choose (none, or 5 to 120 minutes), and temporary Suspend/Activate. Right across daylight saving changes and time zone travel. |
| Sleep-friendly recommendation | ● Ready | Based on sleep research (dim, warm light around 2700 K for the two to three hours before bed). One click fills in a full schedule from your bedtime and wake-up time, or just the warmth. General guidance, not medical advice. |
| Pause night light for apps, fullscreen and media | ● Ready | Three separate switches: any fullscreen app; the apps you select (in front, fullscreen or not); and an automatic mode for video, calls, games and photo, video and design apps, judged from what Windows reports and a list of well-known color-critical apps. Pauses on that display only (the panel says which) and fades back when the app is minimized, closed or left. As f.lux's "disable for". |
| Night light back at once after unlock and sleep | ◐ In testing | Windows' unlock, wake and display-on messages reach the app (tested with messages sent to its window), and a ramp Windows resets is put back within 1.5 s (tested). A real lock and sleep cycle hasn't been timed yet. |
| Keep each display's color calibration | ● Ready | Optional. When Windows drops a calibration (the screen turning off and on, waking, or before Winlight started), it is put back, with night light on top. |
| Windows Night Light notice | ◐ In testing | Detects Windows Night Light from its registry state (undocumented; reading "off" is verified) and offers to open its setting. |

### The app

| Feature | Status | Notes |
|---|---|---|
| Tray icon, quick-access panel and settings window | ● Ready | Modelled on KDE Plasma's "Brightness and Color" panel and Night Light settings, drawn in the Windows 11 style. Light and dark mode follow Windows, or pick one, with Windows' accent color or one of six presets. Windows open without a white flash and fit small screens. |
| Command line (`winlight-cli`) | ● Ready | Tested installed, from PATH: list, get and set brightness per display (by number, name or part of an id) or all, relative changes, showing the panel after a change, night light on/off/toggle/auto, refresh, diagnostics, JSON output, exit codes, output to files and pipes, many calls at once. |
| Crash-safe colors and clean exits | ● Ready | A journal records each display's colors before Winlight changes them, so a crash or a killed process can't leave a tint behind: the next start puts them back. Quitting, signing out, Ctrl+C and the installer all restore normal colors. |
| Multi-display detection, hotplug and sleep | ◐ In testing | Sleep, waking and displays turning off are handled from Windows' own notifications, with re-detection over two minutes after waking, each pass logged with its timing. Plugging monitors in and out, and a real sleep cycle, need testing with external monitors. |
| One-click diagnostics, crash safe mode | ● Ready (safe mode: ◐) | "Report a Problem…" saves a zip and copies a summary; the zip is deleted after 7 days and the summary cleared from the clipboard after 10 minutes. A display whose DDC/CI crashed or hung the app is skipped until retried: tested with a real crash of a child process, not yet with a real DDC/CI monitor. |
| Private logs and reports | ● Ready | User name, computer name, home folder and emails are scrubbed, display serials shortened, and no location, time zone, custom names or listed app names are written. |
| Never steals focus from fullscreen games | ● Ready | Windows' "don't interrupt" state and fullscreen detection keep Winlight from opening windows on its own over a game, video or presentation. |
| Start at sign-in, start minimized | ● Ready | On by default: the installer adds the entry (the current user's Run key, no administrator rights), and the setting switches it on and off as Task Manager does. |
| Installer, portable zip, uninstaller | ● Ready | Per-user, no administrator rights, built by GitHub Actions. |

## Goals

- **Every display, one place.** Control the brightness and color of every screen on a Windows PC, laptop panel, external monitors, HDR displays and TVs alike, from one tray panel, the way KDE Plasma does on Linux.
- **Healthier screens, without thinking about it.** Warm evenings, gentle transitions and sleep-friendly schedules that run on their own, grounded in what sleep research recommends.
- **Never in the way.** True colors for games, video, calls and color work; no windows popping up over a fullscreen game; no fighting other software over the screen.
- **Safe for your hardware.** No DDC/CI to sleeping monitors, conservative probing, time limits on every monitor call, never a black screen, and colors always put back.
- **Dependable.** Sleep, waking, hotplugging, docks, driver hiccups and crashes are handled, logged clearly and recovered from, without a restart.
- **Respectful of your privacy.** Nothing personal in logs or reports, nothing sent anywhere.
- **Open and scriptable.** Free software under the GPL, with a command line for automation, and an honest status for every feature.

## The Problem on Windows

- **Brightness:** Windows' brightness slider mainly covers laptop panels. Most external monitors connected over HDMI, DisplayPort or USB-C still need their own buttons and on-screen menu, one monitor at a time. TVs and displays without DDC/CI have no brightness control at all, and there's no way to schedule brightness by time of day.
- **Night light:** Windows' built-in Night Light offers one schedule and one strength for every display, and can't step aside for a game, a video or a photo editor. It's unreliable in some setups, such as certain docking stations (e.g. DisplayLink) and some Arm-based laptops.
- **Together:** brightness, color and schedules live in different places, and nothing ties them to how you actually use your screens through the day.

Winlight fills those gaps with DDC/CI for hardware brightness, Windows' SDR brightness for HDR displays, and the gamma ramp for night light and for dimming displays that have no brightness control, all from one app. See [Why Winlight?](docs/why-winlight.md) for how it compares with Windows' own controls and with similar apps.

## Built With

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
| Packaging | PyInstaller, Inno Setup |

On a KDE Plasma desktop the app drives Plasma's own brightness and Night Light services (via `jeepney`) so it can be developed and tried without Windows. See [Development](#development).

## Known Challenges

Most of these were learned from [Monitorian](https://github.com/emoacht/Monitorian) and [Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray); [docs/windows-notes.md](docs/windows-notes.md) has the details and what this app does about each.

- DDC/CI support varies by monitor, cable, and dock. Some monitors have it disabled in their on-screen menu, and DisplayLink-based USB docks can't pass it at all.
- DDC/CI calls are slow and sometimes fail for a moment, so they run off the UI thread in one queue, are retried on transient errors, and only the latest slider value is sent. Every call has a time limit: a monitor that hangs is left alone until its call returns, and the others carry on.
- Sleep and hibernation cause most trouble: monitors vanish, handles go stale, and DDC/CI to a sleeping monitor can wake it or hang it. Nothing is sent while the PC sleeps or the screens are off, and after waking, changes wait until the displays have been detected again (successfully: a detection that fails for a moment keeps the displays known before and tries again).
- Monitors often don't answer DDC/CI for several seconds after being plugged in or waking from sleep, so detection runs again a few times.
- Requesting a monitor's DDC/CI capabilities string is slow and can hang on some monitors, so the Windows backend never asks for it; names come from the EDID.
- Windows refuses gamma ramps that are too far from neutral, which limits how warm night light can go unless the `GdiIcmGammaRange` registry value is raised.
- Gamma ramps can be reset by sleep, resolution changes, or fullscreen apps, so they are re-applied on display events.
- Gamma ramps don't apply to displays in HDR mode, so night light skips them, and their brightness uses Windows' SDR level instead.
- Software dimming can't go as dark as a backlight (Windows rejects very dark ramps), and saves no power: it's for displays with no other control.
- Most TVs don't support DDC/CI.
- Gamma ramps belong to displays, not windows, so pausing night light for an app pauses it for that app's whole display.
- Windows reports that some app keeps the screen on, but not which one, so the automatic pause goes to the display of the app in front.
- Windows Night Light's state has no API; Winlight reads its undocumented registry value, and shows nothing if it can't tell.
- Laptop brightness keys are handled by the laptop's firmware and Windows, not passed to apps, so Winlight can't take them over. It follows the brightness they set instead, which is reliable and doesn't need a keyboard hook (which games' anti-cheat may flag), and a level set with them isn't undone at the next start.
- Running alongside Windows Night Light, f.lux or GPU color settings (such as AMD's "Custom Colors") may cause conflicts. When another program keeps changing a display's colors, Winlight backs off from that display for a while (and says so in the log) rather than fighting over it and making the screen flicker.

## Roadmap

- ● **Done:** night light and scheduling, the tray panel and settings, laptop panel brightness, brightness by time of day, compatible displays list and report, sleep-safe monitor handling, renaming and hiding displays, pausing for apps and media, sleep-friendly schedules, themes, the command line, the installer.
- ◐ **Now (0.2.0 Beta):** testing with external monitors, TVs and HDR displays ([installer test plan](docs/testing-installer.md), [full test plan](docs/windows-testing.md)).
- ○ **Next:** hotkeys with an on-screen indicator, per-display brightness schedules, code signing, then a 1.0 release.

## Related Projects

Winlight stands on the shoulders of these projects; each is excellent at what it sets out to do. [Why Winlight?](docs/why-winlight.md) compares them in more detail and explains how to switch.

- **[Monitorian](https://github.com/emoacht/Monitorian):** a Windows tray app for adjusting the brightness of multiple monitors over DDC/CI and WMI. Its handling of monitor detection, fallbacks and flyout placement shaped this project's Windows backend.
- **[Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray):** a Windows tray app for monitor brightness with a Windows-style flyout, scheduling and DDC/CI features. Its notes on DDC/CI reliability and gamma ramps shaped the night light and error handling here.
- **PowerToys Power Display:** Microsoft's PowerToys includes a monitor control utility that uses DDC/CI. Winlight focuses on night light, schedules and comfort alongside brightness.
- **f.lux / Windows Night Light:** color temperature tools that Winlight complements or replaces for per-display control.

## Why This Project?

Windows leaves display comfort scattered: brightness for some screens, a night light for all of them at once, and nothing that adapts to what you're doing. Winlight aims to be the one dependable place for it, on every display, through the whole day.

It is also built in the open as a learning project, to:

- Practice GUI programming with Python and Qt
- Work with real Windows APIs
- Handle cross-device compatibility (DDC/CI, multi-monitor)
- Use AI tools (like ChatGPT or Copilot) effectively in software development

## Command line

`winlight-cli.exe` sits next to `Winlight.exe` (the installer can add it to PATH). It talks to the running app, so Winlight must be running.

```powershell
winlight-cli --list                      # displays, their numbers, ids and brightness
winlight-cli --set 40                    # every display to 40% (hidden ones excepted)
winlight-cli --set +10 --display 2       # display 2 up by 10
winlight-cli --set 70 --display DELL     # by name (any part, custom names too) or id (or part of one)
winlight-cli --set +10 --panel           # and show the panel, for hotkey tools
winlight-cli --get --display 1           # prints just the number
winlight-cli --night-light toggle        # also on, off, auto (follow the schedule), status
winlight-cli --refresh                   # detect displays again
winlight-cli --list --json               # for scripts
```

Display numbers are the order in the panel and in `--list`. Changes fade rather than jump (a laptop panel eases itself). Raw DDC/CI codes, contrast and input switching (Twinkle Tray's `--VCP`, Monitorian's add-on) are deliberately left out for now: an arbitrary DDC/CI write can switch a monitor's input, turn it off or reset it, and they need testing on many monitors first. Exit codes: 0 done, 1 failed or a mistake in the command (the message says why), 2 Winlight isn't running. Run without options, `winlight-cli` prints its help.

`winlight-cli.exe` is a console program of its own, next to the windowed `Winlight.exe`, so the terminal waits for it and gets its output and exit code; it needs no shim or wrapper script.

## Reporting a problem

Right-click the tray icon and choose **Report a Problem…**. Winlight saves a `Winlight-diagnostics-….zip` to your Desktop (logs, settings, and what it knows about each display) and copies a summary to the clipboard. It deletes the zip after 7 days, and clears the summary from the clipboard after 10 minutes if it's still there.

Reports are meant to be posted publicly, so nothing personal goes in: your user name, computer name, home folder and email addresses are replaced with placeholders in every file, and your location, city, custom display names and the names of apps you listed aren't included at all. The same goes for the log file itself. Open an [issue](https://github.com/Arnie14/Night-Light-Windows/issues), paste the summary and attach the zip.

## Download details

Get the latest build from [Releases](https://github.com/Arnie14/Night-Light-Windows/releases) (Windows 10 and 11, 64-bit):

- **`Winlight-<version>-setup.exe`**: installs for your user only (no administrator rights), with a Start menu entry, starting at sign-in (optional, on by default) and an uninstaller.
- **`Winlight-<version>-portable.zip`**: unzip anywhere and run `Winlight.exe`.

Builds are beta and not yet code-signed, so Windows SmartScreen may show "Windows protected your PC". Choose **More info → Run anyway**. Microsoft Defender flagged 0.1.0 Beta 5 (`Behavior:Win32/Persistence.A!ml`) because it rewrote its start-up entry at every start; 0.2.0 doesn't. If Defender flags a build, please [submit it to Microsoft as a false positive](https://www.microsoft.com/wdsi/filesubmission) and open an issue. External monitors need DDC/CI switched on in their own on-screen menu.

## Getting Started (from source)

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

## Development

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
- `python main.py --report` prints the hardware compatibility report (also in Settings > Displays) and saves it as `compatibility.txt` in the log folder. Unlike `--probe`, it sends nothing to the displays.
- `--allow-multiple` lets a second copy start. Normally a second launch just opens the running copy's flyout.
- `--quit` asks the running copy to quit cleanly (restoring normal colors). The installer uses it before upgrading or uninstalling.
- `--diagnostics` saves the same zip as "Report a Problem…" and prints its path.
- The command line options above work from source too: `python main.py --list`.

### Building the Windows app

On Windows, with [Inno Setup 6](https://jrsoftware.org/isdl.php) installed for the installer:

```powershell
pip install -r requirements-dev.txt -r requirements-build.txt
python packaging/build.py --version 0.2.0-beta
```

This writes `dist\Winlight\` (the app), the portable zip and the installer. [`packaging/`](packaging/) has the PyInstaller spec (one folder, no UPX, unused Qt modules left out), the Inno Setup script and the icon generator. The README screenshots come from `python tools/screenshots.py` (demo displays, light and dark); run it on Windows for the native look.

Releases are built by GitHub Actions ([`.github/workflows/release.yml`](.github/workflows/release.yml)): pushing a tag such as `v0.2.0-beta` runs the tests, builds on Windows and publishes a release titled "Winlight 0.2.0 Beta". Tags with `-alpha`, `-rc` or `-dev` become pre-releases. Running the workflow by hand from the Actions tab builds the files without a release.

Logs go to the terminal and to `night-light.log` in the user's log folder (`%LOCALAPPDATA%\night-light\Logs` on Windows).

Testing on Windows follows [docs/windows-testing.md](docs/windows-testing.md) (from source, everything) or [docs/testing-installer.md](docs/testing-installer.md) (with the installer, for testers).

### Project layout

```
main.py                  entry point, command line and logging
nightlight/
  app.py                 wires services to the UI
  config.py              settings and JSON storage
  controller.py          applies the night light schedule to the displays (animation, overrides)
  monitors.py            the one queue of monitor calls, on a background thread, with each
                         display's desired, commanded and observed brightness
  brightness_scheduler.py  applies the brightness schedule, idempotently
  watcher.py             notices display changes, hotplug, sleep and waking
  single_instance.py     keeps one copy running, and carries command-line requests
  probe.py               the --probe diagnostic report
  compat.py              the list of compatible displays and the compatibility report
  core/                  pure logic, no Qt or hardware: colortemp.py, schedule.py,
                         brightness_schedule.py (and presets), clock.py (daylight saving),
                         edid.py, locations.py
  hardware/              backend interfaces (brightness.py, gamma.py), windows.py + win32.py
                         (Windows display APIs), winsystem.py (foreground app, sleep, display
                         and session notifications, Windows Night Light), calls.py (time
                         limits), kde.py (Linux dev backend)
  ui/                    tray, flyout, placement, settings_window (+ settings_parts,
                         settings_widgets), schedule_editor, widgets, night_light_text, icons,
                         theme (light, dark and accent colors), reveal (opening without a flash)
  autostart.py           starting at sign-in (the installer's Run entry, switched as Task Manager does)
  brightness_memory.py   each display's remembered level, put back after start, sleep and reconnecting
  exemptions.py          pausing night light for apps, fullscreen and media
  commands.py, cli.py    the command line (winlight-cli) and what the app does for it
  diagnostics.py         "Report a Problem…"
  safe_mode.py           skipping displays whose DDC/CI crashed or hung the app
  privacy.py             scrubbing personal details from logs and reports
  brightness_keys.py     external displays following the laptop's brightness keys
  wake_report.py         re-detecting displays after sleep, with timings in the log
  idle_dim.py            dimming while the PC is left alone
  gc_guard.py            garbage collection on the main thread only (Qt objects and threads)
  files.py               atomic file writes
  data/                  time zone table for picking a location (from tools/make_timezones.py)
packaging/               Windows build: PyInstaller spec, Inno Setup script, icon, build.py
tools/                   screenshots.py (README images), make_timezones.py
docs/                    why-winlight.md, Windows research notes, test plans; screenshots/
tests/                   pytest suite (Windows backends run against a fake of the Windows APIs)
```

## License

Winlight is free software under the **GNU General Public License v3.0**. See [LICENSE](LICENSE). Source files carry an `SPDX-License-Identifier: GPL-3.0-only` header, and the license is also declared in [pyproject.toml](pyproject.toml).

## Acknowledgements

Behavior, wording and some data are based on the open-source work of other projects, used under licenses compatible with the GPL-3.0:

- **[KDE Plasma](https://kde.org/plasma-desktop/)**: the design of the Brightness and Color panel and the Night Light settings page, KWin's Night Light transition behavior and timings, KWin's composition of brightness and color temperature into one transform per display, and the schedule rules from [knighttime](https://invent.kde.org/plasma/knighttime). KWin and PowerDevil are GPL-2.0-or-later; knighttime is LGPL.
- **[f.lux](https://justgetflux.com/)**: the idea of pausing night light for chosen apps and fullscreen.
- **[Monitorian](https://github.com/emoacht/Monitorian)** (MIT) and **[Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray)** (MIT): their users' requests for a command line and the shape of it, Twinkle Tray's brightness schedule, renaming and hiding displays and crash safe mode, Monitorian's power notifications; and how the Windows display, DDC/CI, WMI, SDR white level and gamma APIs are used, one class per kind of brightness control, the order of fallbacks, Twinkle Tray's driver-safe software dimming curve, flyout placement and tray behavior, retry and re-detection timings, and many Windows quirks. No code is copied; the approach is re-implemented in Python, as described in [docs/windows-notes.md](docs/windows-notes.md).
- **[Redshift](https://github.com/jonls/redshift)** (GPL-3.0) and KWin: the blackbody color table by Ingo Thies (2013) used in [nightlight/core/colortemp.py](nightlight/core/colortemp.py).
- Python libraries: [PySide6](https://doc.qt.io/qtforpython-6/) (LGPL-3.0), [monitorcontrol](https://github.com/newAM/monitorcontrol) (MIT), [WMI](https://github.com/tjguk/wmi) (MIT) with [pywin32](https://github.com/mhammond/pywin32) (PSF), [astral](https://github.com/sffjunkie/astral) (Apache-2.0), [platformdirs](https://github.com/tox-dev/platformdirs) (MIT), [jeepney](https://gitlab.com/takluyver/jeepney) (MIT).
