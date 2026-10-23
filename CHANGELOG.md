# Changelog

Changes in each version of Winlight, newest first. Version numbers follow the scheme in [docs/development.md](docs/development.md#versions-and-releases). Earlier, more detailed reports are kept in [docs/history/](docs/history/).

## 0.3.0 Beta 2 (2026-10-22)

### Added

- **Suspend until resumed**, as in KDE: the panel's Suspend button keeps night light off until it is clicked again, in the day too; its arrow has the timed choices. The tray menu has the same toggle, and the command line `--night-light suspend` and `resume`.
- **Never pause late at night**: a fourth pausing option. From night light starting to warm, being fully on, or a set time such as bedtime, until the morning transition, nothing pauses night light: no fullscreen app, listed app or automatic pause.
- **A note on screen while previewing**: holding a temperature slider shows "Night light preview" and the temperature in the middle of the screen, until the slider is let go.
- **Calibration status, Reload and Forget**: Settings > Displays shows each display's color calibration. Reload reads it again from the display's color profile and puts it on screen; Forget stops Winlight putting back a calibration you removed in Windows. On the command line: `--calibration status`, `reload` and `forget`. The first time a calibrated display is found, a notification explains keeping calibrations, once.

### Changed

- **High latitudes get their real sunset and sunrise.** In Reykjavik from May to July the sun sets but twilight never ends, and on some summer days in Stockholm and Helsinki dusk comes near midnight; the whole schedule fell back to 18:00 and 06:00 on those days. Night light now turns at solar midnight in a white night, and the brightness schedule's sunrise and sunset come from the same calculation.
- **A maximized window no longer counts as fullscreen**, also with the taskbar hidden automatically: Winlight measures the window's client area, without its title bar, and leaves out maximized windows that draw their own (VS Code, browsers).
- **While the displays are off**, the gamma checks and watching the app in front slow down rather than run as usual.
- The calibration status counts how often a calibration was put back today, and says "None detected" for a display without one.
- **Night light comes back more reliably after waking, unlocking and displays turning on**: the ramps are set again several times over 2 s even where Windows reports them unchanged, then checked for 12 s. Each ramp differs a little from the last (LightBulb's rotating offset), since some drivers ignore one they were sent before. Windows resetting the ramps meanwhile no longer counts as another program fighting over them.
- **More of Windows' own interface is told apart from fullscreen apps**: Start, search, Alt+Tab, Task View, Quick Settings, the lock screen and others (LightBulb's list, extended for Windows 11). Opening them over a paused video keeps the pause. Store apps are recognised by their own names for the app list.
- Settings opens on the first page, at its top, and each page opens at its top.

### Fixed

- **The settings window's first opening flickered**: about 40 small windows flashed over the screen while it was built, which also made it take about a second to open.
- Starting at sign-in compared program paths without case only on Windows; the test failed elsewhere.
- A gamma ramp change stuck in a display driver on the monitor thread could freeze the app at the next night light change. The app now waits at most 2 seconds, skips the change and says so once.

## 0.3.0 Beta 1 (2026-10-20)

### Changed

- **Data folder and log renamed.** Settings, state and logs move from `%LOCALAPPDATA%\night-light` to `%LOCALAPPDATA%\WinLight`, and the log from `night-light.log` to `WinLight.log`, its history included. The first start renames the folder, so nothing inside it changes; if an older copy still has it open, the old folder is used and the rename is tried again at the next start. Going back to 0.2.0 or earlier afterwards starts with default settings.
- **Report a Problem** saves its zip in `%LOCALAPPDATA%\WinLight\Reports` instead of on the Desktop, which OneDrive's folder backup often syncs to the cloud. Old zips on the Desktop are still deleted after a week.
- **Windows 11** is the supported system; Windows 10 hasn't been tested.
- The project's repository is now `github.com/Arnie14/WinLight`.
- Windows' file version is numeric (`0.3.0.2001` for 0.3.0 Beta 1), so it always increases from one beta to the next and on to the release.

### Added

- **Windows' limit on warm colors is shown.** Windows refuses gamma ramps far from neutral; when it holds a display warmer than asked, Settings shows the limit under the night temperature ("Built-in display: 3300 K (Windows limit)"). The sleep recommendation explains it, and asks each display for the warmest it accepts.
- **SHA-256 checksums.** Each release has a `SHA256SUMS.txt` with the checksum of every download, also listed on the release page, to check a download with `Get-FileHash`.
- **Third-party notices** (`THIRD_PARTY_NOTICES.txt`) ship with the app: Qt and PySide6 (LGPL-3.0), Python, the PyInstaller bootloader, astral, tzdata, platformdirs, WMI and pywin32, with their license texts. Settings > General > About opens them.

### Fixed

- **Calibrations to a warm white point were discarded.** A ramp with a channel below 90% was taken for a leftover tint and reset to neutral, so a calibration to D50, say, was lost until Windows loaded it again at the next sign-in. A ramp is now rejected only if it is also shaped like a tint (straight channels); Winlight's own tints are recognised by its crash journal first.
- **The command pipe** now checks that the program serving it runs as the same user before sending anything. Its access list already kept other users from connecting; the check covers another user's program taking the pipe's name first.

### For developers

- `nightlight/` is grouped into subpackages: `brightness/`, `color/`, `system/`, `support/` and `cli/`.
- Dependencies live in `pyproject.toml` only, with `dev` and `build` extras (`pip install -e ".[dev,build]"`).
- Ruff checks a wider rule set, configured in `pyproject.toml`, and CI runs it.
- New tests check every import, lint the documents, and pin the installer's AppId and the version scheme.
- CI reports each run's test counts in its summary; the documents no longer quote counts.
- `tools/hardware_checks.py` checks the backends against the real Windows APIs on any PC.
- The README is shorter; features and status, the comparison with other tools, the command line, development and the Windows notes each have their own page.

## 0.2.0 Beta (2026-10-19)

### Added

- **Pausing night light per display, three ways:** for any fullscreen app; for apps on a list (in front, fullscreen or not); and automatically for video playback, calls, exclusive-fullscreen games and well-known photo, video and design apps, as Windows reports them.
- **Sunrise and sunset transitions** follow twilight, or last a length you choose (none, or 5 to 120 minutes).
- **Sleep-friendly recommendation:** a full schedule from your bedtime and wake-up time, or just the warmth (2700 K).
- **Brightness presets** change gradually, with times and levels true to their names. A schedule saved from an older preset is shown as Custom.
- **The tray tooltip and panel** show the brightness schedule ("70% → 40% at 20:00").
- **Never dim while these apps are in front**, for dimming when idle.
- **Theme and accent color** choices.

### Changed

- **Starting at sign-in** is set up by the installer, on by default; the setting switches it on and off as Task Manager does. The app no longer rewrites its Run value at every start, which Microsoft Defender's behavior monitoring flagged in 0.1.0 Beta 5 (`Behavior:Win32/Persistence.A!ml`).
- **Dimming when idle** no longer stops for any window that fills the screen (a browser or an editor in fullscreen); only an app keeping the screen on, an exclusive-fullscreen game or a listed app hold it.
- **Report a Problem** deletes its zip after 7 days and clears its summary from the clipboard after 10 minutes. Its message is shorter.
- Settings text was rewritten throughout for clarity and consistency.

### Fixed

- The settings window was taller than some screens (960×760 at 150% scaling on 1080p), so restoring it from maximized looked like nothing happened. It now fits the screen.
- Windows showed a white frame before the settings window's first paint in dark mode. Windows now fade in after their first paint.
- With a location far from the PC's time zone, the morning twilight was paired with the next day's sunrise and "ended" before it began.
- After signing out began, a late display change could re-tint the screen, and the idle dimmer could dim it again.
- Input that turned the displays off was read as them being back on, so DDC/CI could reach monitors that were asleep.
- A display that kept changing (a loose cable) postponed re-detection indefinitely.
- Releasing the WMI connection collected garbage on the monitor thread, which can destroy Qt objects off the main thread; its COM cycles are now broken on that thread instead.
- Monitor handles leaked if opening them failed partway; the app list could stop early on one failing window.
- The command pipe failed on requests split across reads, and on answers to a command line that had already hung up.
- An unreadable settings file was overwritten with defaults; a copy is now kept as `settings.json.damaged`.

## 0.1.0 Beta 5 (2026-10-19)

The reliability and redesign release: one serialized monitor queue with time limits, stable display identity, an idempotent brightness scheduler with presets, the compatibility report, the gamma crash journal, and the redesigned settings window, merged with every beta 3 and 4 feature.

- **HDR:** a monitor that locks its brightness in HDR mode is noticed and moved to Windows' SDR content brightness.
- **Command line:** `--panel`, and displays chosen by part of their id.
- **Keep each display's color calibration** (optional).
- Per-display pausing names the display; a minimized window no longer keeps night light paused.
- **Notable fixes:**
  - A ramp Windows reset was sometimes adopted as the new base.
  - An app already in front at start didn't pause night light.
  - A WMI reading taken just after a write showed the old level.
  - The time zone name appeared in reports.
  - Several races between sleep, detection and stuck calls.
  - Another program's gamma ramps were fought over, flickering the screen.
  - Exceptions in Qt slots were lost.
  - A crash when garbage collection ran on a monitor thread.

## 0.1.0 Beta 4 (2026-10-17)

- Dimming when idle, the safe way for each kind of display: external displays in software, never over DDC/CI.

## 0.1.0 Beta 3 (2026-10-17)

- Pausing night light for apps and fullscreen.
- Night light back at once after unlocking.
- A brightness schedule.
- `winlight-cli`, with an optional PATH entry.
- Report a Problem.
- Crash safe mode for DDC/CI.
- Renaming and hiding displays.
- Personal details kept out of logs and diagnostics.
- Never opening windows over a fullscreen game.
- Following the laptop's brightness keys on external displays.
- Re-detection after waking, with timings in the log.
- Betas are published as full releases.

## 0.1.0 Beta 2 (2026-10-17)

- SDR content brightness for HDR displays, and software dimming for displays without brightness control.

## 0.1.0 Beta 1 (2026-10-17)

The first Windows build:

- night light with a schedule by custom times or a city's sunset and sunrise;
- brightness for laptop panels (WMI) and external monitors (DDC/CI);
- remembered brightness;
- starting at sign-in;
- the tray panel and settings;
- the installer and the portable zip.
