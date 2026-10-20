# Features and status

What Winlight does, and how far each part has been tested. Winlight is in beta: the laptop panel, night light, the schedules and the app are tested on Windows 11 on the test laptop ([details](windows-notes.md#12-test-laptop)); external monitors, TVs and HDR displays are built and tested against the Windows APIs, and waiting for a run on that hardware.

| Status | Meaning |
|---|---|
| Ready | Built, and tested on Windows |
| In testing | Built and tested against simulated hardware and the Windows APIs; waiting for a run on that hardware |
| Planned | Not started |

## Displays and brightness

| Feature | Status | Notes |
|---|---|---|
| Laptop panel brightness | Ready | Through WMI. Never goes fully dark: the slider stops at 1%. |
| External monitor brightness over DDC/CI | In testing | The Windows API bindings and their error handling are verified on Windows; a real DDC/CI monitor hasn't been driven yet. A monitor that hangs is given up on after a few seconds and left alone, without affecting the others. |
| HDR displays | In testing | DDC/CI is kept while the monitor obeys it in HDR mode; a monitor that locks its brightness in HDR mode is noticed and moved to Windows' SDR content brightness (80–480 nits). Reading the SDR level is verified; the rest needs an HDR display. |
| Software dimming for TVs and displays without brightness control | In testing | Verified on the test laptop's gamma ramp (20–100%, with night light on top); a TV hasn't been tried. Never darker than 20%. Can be turned off. |
| Brightness by time of day | Ready | Its own schedule, separate from night light. Six presets, each changing gradually (Day and night, Follow the sun, Office hours, Evening wind-down, Night owl, Dark room), or your own times by clock or sunrise/sunset ± minutes. Each point applies once: a level you pick stands until the next point, and restarts or sleeping through a point neither repeat nor miss it. The tray tooltip and panel show the level now and the next change. |
| Dimming when idle | In testing | Displays dim after a set time without input and come back on the first key or mouse move, never brighter than before. Held while an app keeps the screen on (video, calls, presentations), a game runs exclusive fullscreen, or an app you list is in front. External displays dim in software, never over DDC/CI. Its parts are verified on the test laptop; a real idle cycle hasn't been run. |
| Remember brightness | Ready | Each display's level is restored after starting, waking and reconnecting. Identical monitors are told apart by their EDID serial number. A level set with the laptop's keys or Windows' slider isn't undone. |
| Change all displays together | In testing | Needs two displays to try. |
| External displays follow the laptop's brightness keys | In testing | Optional. Follows the panel's level rather than intercepting the keys; needs an external display to try. |
| Rename and hide displays | Ready | Keyed by the display's identity, so names follow it between ports. Hidden displays are never changed. |
| Compatible displays list and report | Ready | Settings > Displays lists each display, its connection and how its brightness is controlled, with why; the report can be copied, saved, or printed with `--report`. It sends nothing to the displays. |

## Night light

| Feature | Status | Notes |
|---|---|---|
| Night light | Ready | From 6500 K (neutral) to as warm as Windows allows; the limit, when Windows enforces one, is shown in Settings ([why](windows-notes.md#3-gamma-ramps-night-light-dimming-and-calibrations)). Keeps a display's calibration, even after a crash. Skips HDR displays, which Windows doesn't tint. |
| Schedule | Ready | Custom times, or sunset to sunrise at a city (searchable, with Detect) or coordinates. Transitions follow twilight or last a length you choose (none, or 5 to 120 minutes). Temporary Suspend and Activate. Correct across daylight saving changes and time zone changes. |
| Pausing per display | Ready | Three independent switches: any fullscreen app; the apps you select (in front, fullscreen or not); and automatically for video playback, calls, exclusive-fullscreen games and well-known photo, video and design apps, as Windows reports them. Only the display showing the app pauses, and night light fades back when the app is minimized, closed or left. |
| Sleep-friendly recommendation | Ready | Dim, warm light (around 2700 K) for the two to three hours before bed, from sleep research. Fills in a full schedule from your bedtime and wake-up time, or just the warmth. General guidance, not medical advice. |
| Back at once after unlocking and waking | In testing | Windows' unlock, wake and display-on messages reach the app (tested with messages sent to its window), and a ramp Windows resets is put back within 1.5 s. A real lock and sleep cycle hasn't been timed yet. |
| Keep each display's color calibration | Ready | Optional. When Windows drops a calibration (the screen turning off and on, waking), it is put back, with night light on top. |
| Windows Night Light notice | In testing | Detects Windows' own Night Light (undocumented registry state; reading "off" is verified) and offers to open its setting, since the two tints would stack. |

## The app

| Feature | Status | Notes |
|---|---|---|
| Tray icon, panel and settings window | Ready | Modelled on KDE Plasma's Brightness and Color panel and Night Light settings, in the Windows 11 style. Light, dark or Windows' own theme, with Windows' accent color or one of six presets. |
| Command line (`winlight-cli`) | Ready | Tested installed, from PATH: list, get and set brightness per display (by number, name or part of an id) or all, relative changes, showing the panel, night light on/off/toggle/auto/status, refresh, diagnostics, JSON output, exit codes. See [command-line.md](command-line.md). |
| Crash-safe colors and clean exits | Ready | A journal of each display's colors means a crash or a killed process can't leave a tint behind. Quitting, signing out, Ctrl+C and the installer all restore normal colors. |
| Detection, hotplug and sleep | In testing | Sleep, waking and displays turning off are handled from Windows' own notifications, with re-detection over two minutes after waking. Plugging monitors in and out, and a real sleep cycle, need testing with external monitors. |
| Report a Problem, crash safe mode | Ready (safe mode: in testing) | Saves a zip to Winlight's Reports folder and copies a summary; the zip is deleted after 7 days and the summary cleared from the clipboard after 10 minutes. A display whose DDC/CI crashed or hung the app is skipped until retried: tested with a real crash of a child process, not yet with a real DDC/CI monitor. |
| Private logs and reports | Ready | User name, computer name, home folder and email addresses are scrubbed, display serials shortened, and no location, time zone, custom names or listed app names are written. |
| Never steals focus from fullscreen games | Ready | Winlight doesn't open its windows on its own over a game, video or presentation. |
| Start at sign-in, start minimized | Ready | On by default: the installer adds the entry (no administrator rights), and the setting switches it on and off as Task Manager does. |
| Installer, portable zip, uninstaller | Ready | Per-user, no administrator rights, built by GitHub Actions. |

## Roadmap

- **Done:** night light and its schedule, the tray panel and settings, laptop panel brightness, brightness by time of day, the compatible displays list and report, sleep-safe monitor handling, renaming and hiding displays, pausing per display, sleep-friendly schedules, themes, the command line, the installer.
- **Now:** testing with external monitors, TVs and HDR displays ([installer test plan](testing-installer.md), [full test plan](windows-testing.md)).
- **Next:** hotkeys with an on-screen indicator, per-display brightness schedules, code signing, then a 1.0 release.
