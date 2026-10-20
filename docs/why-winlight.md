# Why Winlight?

Windows and several established apps already handle parts of what Winlight does. This page explains what Winlight adds, where another tool may suit you better, and how to switch if you decide to. It's written for anyone weighing Winlight against what they use today.

Winlight is a beta. Laptop panels, night light, the schedules and the app itself are tested on Windows. External DDC/CI monitors, TVs and HDR displays are built and tested against the Windows APIs, and are being tested on real hardware now. [Features and status](features.md) gives the status of each feature.

## What Winlight combines

Most display tools on Windows cover one side of the problem:

- **Brightness tools** (Twinkle Tray, Monitorian, PowerToys Power Display, monitor makers' utilities) set monitor brightness over DDC/CI. Some add a brightness schedule (Twinkle Tray's time-of-day adjustments, Monitorian's time commands in its Store add-on) or idle dimming (Twinkle Tray). Some can switch a monitor's own color temperature preset over DDC/CI (PowerToys Power Display, Monitorian's add-on), which changes the monitor's setting rather than running a night light.
- **Color tools** (Windows Night Light, f.lux, Iris, LightBulb, CareUEyes) shift color temperature on a schedule. Some can be turned off for fullscreen apps or listed apps (f.lux, LightBulb), and some also dim the screen.

Winlight manages both, for several displays, from one panel:

- **Night light** with its own schedule (custom times, or sunset to sunrise) and transitions.
- **Brightness** for every display, whatever its control, with **its own schedule**, separate from night light's.
- **Dimming when idle**, with exceptions for apps that need the screen on.
- **Night light paused per display**, while the other displays keep it, in three independent ways: for any fullscreen app, for apps on a list, and automatically for video playback, calls, exclusive-fullscreen games and known photo, video and design apps.

We haven't found another Windows app that combines these. If you only need one of them, a dedicated tool may serve you well, and the sections below say when.

## Compared with Windows' own controls

| | Windows | Winlight |
|---|---|---|
| Brightness of the laptop panel | Yes | Yes, plus a schedule and dimming when you're away |
| Brightness of external monitors | Mostly through each monitor's own buttons and menu | A slider for each, over DDC/CI |
| Brightness of HDR displays | The SDR content brightness slider in Settings | The same control, on the same slider as the others |
| Displays with no brightness control (most TVs) | None | Software dimming, which can be turned off |
| Brightness by time of day | No | Six gradual presets, or your own times by clock, sunrise or sunset |
| Night light schedule | Sunset to sunrise, or set hours | The same, plus transitions that follow twilight or last as long as you choose, and a sleep schedule built from your bedtime and wake-up time |
| Night light for games, video, calls and color work | Applies everywhere, all the time | Pauses on the display showing a fullscreen app, an app you choose, or automatically for video, calls, games and photo, video and design apps |
| Dimming when idle | Power settings can dim the laptop panel before turning the screen off | Dims every display to a level you choose after a set time, except while an app keeps the screen on, a game runs exclusive fullscreen, or an app you list is in front |
| Several displays | One setting for all | Each display is named, controlled and paused on its own |
| Scripting | No | `winlight-cli` for AutoHotkey, Task Scheduler, a Stream Deck or any script |

Windows' own Night Light is built in, needs no installation and is well integrated with the system. If one schedule for every display is all you need, it serves well. Winlight is for when you want more control, or more displays under it.

Run only one night light at a time: Winlight shows a notice when Windows Night Light is on too, since the two tints would stack.

## If you use a color tool: f.lux, Iris, LightBulb, CareUEyes

These tools pioneered much of what night light means on a PC. f.lux in particular showed how much a warm evening screen and a "disable for this app" list matter, and Winlight's pausing follows its example.

What Winlight adds:

- **Hardware brightness alongside color**, for every display (DDC/CI, WMI or SDR brightness, not only dimming the picture), each with its own schedule, so evenings can get dimmer as well as warmer.
- **Pausing per display:** a game or photo editor gets true colors on its own display while your other displays stay warm.
- **An automatic mode:** night light pauses for video playback, calls and exclusive-fullscreen games as Windows reports them, and for well-known photo, video and design apps, without a list to maintain.
- **A sleep schedule** from your bedtime and wake-up time, based on what sleep research recommends.
- **Recovery:** a crash or a killed process can't leave your screen tinted, and the calibration of a calibrated monitor is kept.

A color tool may still suit you better if you need macOS or Linux, its particular color presets or effects, or many years of track record.

## If you use a brightness tool: Twinkle Tray, Monitorian, PowerToys, a monitor maker's utility

Twinkle Tray and Monitorian set the standard for monitor brightness on Windows, and Winlight learned a great deal from them: how to find monitors, which fallbacks to use, how to survive sleep, and which Windows quirks to expect. The [acknowledgements](../README.md#acknowledgements) list what came from each, and [windows-notes.md](windows-notes.md) has the details.

What Winlight adds:

- **Night light in the same app**, on its own schedule beside brightness's, with per-display pausing.
- **Sleep-safe monitor handling:** nothing is sent to monitors while the PC sleeps or the screens are off, every monitor call has a time limit, a monitor that hangs is set aside without freezing the others, and one that crashed the app is skipped until you retry it.
- **A brightness schedule that leaves your choices alone:** each point applies once, so a level you choose stands until the next point, and sleeping through a point neither repeats nor misses it.
- **Dimming when you're away** that never brightens a screen, never sends DDC/CI to external monitors for it, and holds off while a video, a call or a listed app is in front.
- **Private reports:** Report a Problem leaves out your name, computer name, location and listed apps, and deletes its file after a week.
- **A native app**, built with Qt rather than a browser engine.

A brightness tool may suit you better if you rely on:

- brightness hotkeys (Twinkle Tray; Monitorian's add-on) or Twinkle Tray's on-screen overlay: planned for Winlight, not there yet;
- contrast, input switching, volume or raw VCP codes (Twinkle Tray, Monitorian's add-on, PowerToys Power Display): deliberately left out of Winlight until they can be tested on many monitors, since a wrong DDC/CI write can switch a monitor's input or reset it;
- saved profiles of monitor settings (PowerToys Power Display, which can also switch them with Windows' light and dark theme);
- per-monitor brightness ranges (Twinkle Tray's "normalize", Monitorian's adjustable range);
- a Microsoft Store install (Twinkle Tray, Monitorian) or a code-signed build.

## Switching to Winlight

1. **Install Winlight.** The installer needs no administrator rights and starts Winlight at sign-in, which you can turn off.
2. **Turn off what would clash.** Run only one night light, and only one app that sends DDC/CI to your monitors:
   - Windows Night Light: Settings > System > Display > Night light.
   - Another color tool: turn off its night light, or quit it.
   - Another brightness tool: quit it, or stop it from starting with Windows. Two apps talking DDC/CI to one monitor can make it ignore both.
   - GPU color settings (NVIDIA, AMD, Intel): leave them at their defaults.

   The installer's first page lists these too.
3. **Carry over your settings.** They map closely:

| You had | In Winlight |
|---|---|
| A color tool's sunset/sunrise schedule at your location | Night Light > Schedule: Sunset to sunrise, with your city (Detect picks it from your time zone) |
| Fixed night hours | Night Light > Schedule: Custom times |
| "Disable for this app" | Night Light > Pause night light > Selected apps |
| "Disable for full screen apps" | Night Light > Pause night light > Fullscreen apps |
| Time-of-day brightness adjustments | Brightness > Change brightness by time of day: a preset or your own times |
| Idle dimming | Brightness > When I'm away |
| Renamed or hidden monitors | Displays > Names and visibility |
| Linked sliders ("unison") | Brightness > Change all displays together |

4. **Scripts and shortcuts.** [command-line.md](command-line.md) lists the `winlight-cli` equivalents of Twinkle Tray's, Monitorian's and PowerToys Power Display's commands.
5. **If something doesn't work,** right-click the tray icon and choose **Report a Problem…**, then [open an issue](https://github.com/Arnie14/WinLight/issues) with the summary and the file. External monitors need DDC/CI turned on in their own on-screen menu; most TVs don't support it, and Winlight dims them through their colors instead.

## The problem on Windows

- **Brightness.** Windows' brightness slider mainly covers laptop panels. Most external monitors connected over HDMI, DisplayPort or USB-C still need their own buttons and on-screen menu, one monitor at a time. TVs and displays without DDC/CI have no brightness control at all, and there's no way to schedule brightness by time of day.
- **Night light.** Windows' Night Light has one schedule and one strength for every display, and can't step aside for a game, a video or a photo editor. It's unreliable in some setups, such as certain docking stations (DisplayLink) and some Arm-based laptops.
- **Together.** Brightness, night light and their schedules live in different places, and nothing pauses night light per display.

## Goals

- Manage night light and brightness for every display on a Windows PC (laptop panel, external monitors, HDR displays and TVs) from one tray panel, as KDE Plasma does on Linux.
- Run night light, brightness and idle dimming on schedules of their own, with transitions, and evening settings based on sleep research.
- Keep true colors where they're needed: pause night light per display for fullscreen apps, listed apps, or automatically for media, games and color work; never open windows over a fullscreen game.
- Protect the hardware: no DDC/CI to sleeping monitors, conservative probing, time limits on every monitor call, no fully black screen, and colors always put back.
- Recover from sleep, waking, hotplugging, docks, driver errors and crashes without a restart, and log enough to diagnose a problem.
- Keep personal details out of logs and reports, and send nothing anywhere.
- Stay open and scriptable: free software under the GPL, a command line for automation, and a stated test status for every feature.

## About the project

On Windows, brightness, night light and their schedules are spread across the system settings, monitor menus and separate apps, and none of them adjust per display to what is on the screen. Winlight brings them into one app for setups with several displays.

It is also built in the open as a learning project, to:

- Practice GUI programming with Python and Qt
- Work with real Windows APIs
- Handle cross-device compatibility (DDC/CI, multi-monitor)
- Use AI tools (like ChatGPT or Copilot) effectively in software development

## Choosing

Every tool here is free to try, and several are open source. If one already does what you need, keep it. Winlight is for setups with several displays where night light and brightness should each follow a schedule, and night light should step aside per display for media, games and color work. Feedback from people coming from other tools is welcome, especially about settings that don't carry over.

Descriptions of other tools come from their own documentation as of October 2026: [Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray), [Monitorian](https://github.com/emoacht/Monitorian) and its [commands](https://github.com/emoacht/Monitorian/blob/master/docs/COMMANDS.md), [PowerToys Power Display](https://learn.microsoft.com/windows/powertoys/power-display), [f.lux](https://justgetflux.com/) and [LightBulb](https://github.com/Tyrrrz/LightBulb). Features change; corrections are welcome.
