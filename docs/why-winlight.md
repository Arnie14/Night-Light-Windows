# Why Winlight?

Windows and several established apps already handle parts of what Winlight does. This page explains what Winlight adds, where another tool may suit you better, and how to switch if you decide to. It's written for anyone weighing Winlight against what they use today.

Winlight is a beta. Laptop panels, night light, schedules and the app itself are tested on Windows. External DDC/CI monitors, TVs and HDR displays are built and tested against the Windows APIs, and are being tested on real hardware now. The [README](../README.md#features) gives the status of each feature.

## What Winlight combines

Most display tools on Windows cover one side of the problem:

- **Brightness tools** (Twinkle Tray, Monitorian, PowerToys, monitor makers' utilities) set monitor brightness, typically over DDC/CI, and some add a brightness schedule or idle dimming. They don't change color temperature.
- **Color tools** (Windows Night Light, f.lux, Iris, LightBulb, CareUEyes) shift color temperature on a schedule, and some can be turned off for listed apps or fullscreen. They don't set monitors' hardware brightness.

Winlight manages both, for several displays, from one panel:

- **Night light** with its own schedule (custom times, or sunset to sunrise) and transitions.
- **Brightness** for every display, whatever its control, with **its own schedule**, separate from night light.
- **Dimming when idle**, with exceptions for apps that need the screen on.
- **Night light paused per display**, while the other displays keep it, in three independent ways: for any fullscreen app, for apps on a list, and automatically for video playback, calls, exclusive-fullscreen games and known photo, video and design apps.

We haven't found another Windows app that combines these. If you only need one of them, a dedicated tool may serve you well, and the sections below say when.

## Compared with Windows' own controls

| | Windows | Winlight |
|---|---|---|
| Brightness of the laptop panel | Yes | Yes, plus schedules and dimming when you're away |
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

- **Hardware brightness alongside color**, for every display, so evenings get dimmer as well as warmer, with one schedule for both.
- **Pausing per display:** a paused game or photo editor gets true colors on its own display while your other displays stay warm.
- **An automatic mode:** night light pauses for video playback, calls and exclusive-fullscreen games as Windows reports them, and for well-known photo, video and design apps, without a list to maintain.
- **A sleep schedule** from your bedtime and wake-up time, based on what sleep research recommends.
- **Recovery:** a crash or a killed process can't leave your screen tinted, and the calibration of a calibrated monitor is kept.

A color tool may still suit you better if you need macOS or Linux, its particular color presets or effects, or many years of track record.

## If you use a brightness tool: Twinkle Tray, Monitorian, PowerToys, a monitor maker's utility

Twinkle Tray and Monitorian set the standard for monitor brightness on Windows, and Winlight learned a great deal from them: how to find monitors, which fallbacks to use, how to survive sleep, and which Windows quirks to expect. The [acknowledgements](../README.md#acknowledgements) list what came from each, and [windows-notes.md](windows-notes.md) has the details.

What Winlight adds:

- **Night light in the same app**, on the same schedule as brightness, with per-display pausing.
- **Sleep-safe monitor handling:** nothing is sent to monitors while the PC sleeps or the screens are off, every monitor call has a time limit, a monitor that hangs is set aside without freezing the others, and one that crashed the app is skipped until you retry it.
- **A brightness schedule that respects you:** each point applies once, so a level you choose stands until the next one, and sleeping through a point neither repeats nor misses it.
- **Dimming when you're away** that never brightens a screen, never sends DDC/CI to external monitors for it, and holds off while a video, a call or a listed app is in front.
- **Private reports:** "Report a Problem…" leaves out your name, computer name, location and listed apps, and deletes its file after a week.
- **A native app**, built with Qt rather than a browser engine.

A brightness tool may suit you better if you rely on:

- hotkeys with an on-screen overlay (planned for Winlight, not there yet);
- contrast, input switching or raw VCP codes (deliberately left out of Winlight until they can be tested on many monitors, since a wrong DDC/CI write can switch a monitor's input or reset it);
- a Microsoft Store install or a code-signed build;
- per-monitor brightness ranges.

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
| "Disable when fullscreen" | Night Light > Pause night light > Fullscreen apps |
| Time-of-day brightness adjustments | Brightness > Change brightness by time of day: a preset or your own times |
| Idle dimming | Brightness > When I'm away |
| Renamed or hidden monitors | Displays > Names and visibility |
| Linked sliders ("unison") | Brightness > Change all displays together |

4. **Scripts and shortcuts.** If you used a command line, these are the equivalents:

| Twinkle Tray | Monitorian | Winlight |
|---|---|---|
| `--List` | `/get all` | `winlight-cli --list` (`--json` for scripts) |
| `--MonitorNum=1 --Set=40` | `/set <id> 40` | `winlight-cli --set 40 --display 1` |
| `--Offset=10` | `/set <id> +10` | `winlight-cli --set +10` |
| `--All` | `all` | the default, or `--display all` |
| `--Panel` | | `winlight-cli --set +10 --panel` |

Displays can also be chosen by name or by part of their id: `--display DELL`.

5. **If something doesn't work,** right-click the tray icon and choose **Report a Problem…**, then [open an issue](https://github.com/Arnie14/Night-Light-Windows/issues) with the summary and the file. External monitors need DDC/CI turned on in their own on-screen menu; most TVs don't support it, and Winlight dims them through their colors instead.

## Choosing

Every tool here is free to try, and most are open source. If one already does what you need, keep it. Winlight is for setups with several displays where night light and brightness should each follow a schedule, and night light should step aside per display for media, games and color work. Feedback from people coming from other tools is welcome, especially about settings that don't carry over.
