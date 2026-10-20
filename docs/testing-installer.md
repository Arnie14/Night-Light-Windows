# Testing Winlight with the installer

For testers using the installer from [Releases](https://github.com/Arnie14/Night-Light-Windows/releases). No Python or source code needed. It takes about 20 minutes; skip anything that doesn't apply to your hardware. The full developer checklist is [windows-testing.md](windows-testing.md).

**Found a problem?** Right-click the tray icon → **Report a Problem…**. Winlight saves a zip to your Desktop and copies a summary. [Open an issue](https://github.com/Arnie14/Night-Light-Windows/issues), paste the summary, attach the zip, and say which step below went wrong.

Tell us your setup in the issue: Windows version, laptop or desktop, and each display's model and how it's connected (HDMI, DisplayPort, USB-C, dock, adapter).

## 1. Install

- [ ] Run `Winlight-<version>-setup.exe`. If SmartScreen says "Windows protected your PC", choose **More info → Run anyway** (the beta isn't code-signed yet).
- [ ] No administrator prompt appears. "Start Winlight when I sign in" is ticked; optionally tick "Add winlight-cli to PATH".
- [ ] At the end, "Launch Winlight" starts it: a sun or moon icon appears in the tray (check the ^ overflow area), and no console window opens.

## 2. The panel

- [ ] Click the tray icon: the panel opens above the taskbar, fully on screen, with a slider for each display.
- [ ] Hover over the icon: the tooltip says "Winlight" and the night light status, and the brightness schedule's level while that schedule is on.
- [ ] Drag each slider: the display's brightness follows while you drag.
  - A display that says **Software dimming** gets darker by dimming its colors (normal for TVs).
  - One that says **HDR · SDR content brightness** changes Windows' SDR brightness.
  - One that says it **didn't answer over DDC/CI**: turn on DDC/CI in the monitor's own menu, then click ⟳ in the panel.
- [ ] If the panel shows "Windows Night Light is on too", click **Turn it off…** and switch Windows Night Light off. The notice goes away within about 10 seconds.

## 3. Night light

In the panel, click ⚙ (Settings):

- [ ] Switching times **Always on**, Apply: every display fades warm over about 2 seconds.
- [ ] Drag the night light temperature: the screen previews it while you drag.
- [ ] **Lock** the PC (Win+L) and unlock it: night light is back at once, without a visible flash of normal colors.
- [ ] **Sleep** the PC and wake it: same.
- [ ] Open Settings from the tray menu and from the panel, in dark mode: no white flash. Maximize the window, then click the same button again: it goes back to its smaller size.
- [ ] With **Sunset to sunrise**, change **Transition** (twilight, none, or 5 to 120 minutes), Apply: the times listed under the temperatures change to match.
- [ ] Under **Sleep**, set a bedtime and wake-up time and click **Use sleep schedule**: the schedule changes to custom times starting 3 hours before bedtime, with a 60-minute transition and 2700 K. Click **Reset** to undo it, then **Use warmth only**: only the night temperature changes.
- [ ] Settings → Night Light → under **Pause night light**, turn on **Fullscreen apps**, Apply. Play a video fullscreen or start a game: that display fades to normal colors, the others stay warm. Leave fullscreen: it fades back.
- [ ] Turn that off, turn on **Selected apps**, add an app (e.g. your photo editor), Apply, and switch to it: the same happens while it's in front, fullscreen or not; other fullscreen apps keep night light.
- [ ] Turn that off, turn on **Video, calls, games and color work**, Apply. Play a video in a window (not fullscreen), join a video call, or open Photoshop or Lightroom: the display in front fades to normal colors, and back when the video stops or you switch away. Note anything it misses.
- [ ] With a game or video fullscreen, start Winlight again from the Start menu: nothing pops up over it, and the game keeps focus.
- [ ] Switch back to your usual schedule (custom times or sunset/sunrise).

## 4. Brightness

- [ ] Settings → Brightness → **Remember each display's brightness** is on. Set distinctive levels (e.g. 23% and 61%), quit Winlight (tray → Quit) and start it from the Start menu: the levels come back.
- [ ] Turn a monitor off with its power button and on again, or unplug and replug it: its level comes back within a few seconds.
- [ ] Turn on **Change all displays together**, Apply: one slider moves them all.
- [ ] Settings → Brightness: turn on **Change brightness by time of day**, choose each preset in turn and check the times listed make sense, then **Add a time** a minute or two from now with a distinct level, Apply: at that time every display fades to it. Move a slider afterwards: your level stays until the next time. If you used a schedule in beta 3 or 4, check it was carried over as a Custom one.
- [ ] Settings → Displays → turn on **Keep each display's color calibration**, Apply (on a display with a calibration loaded). Let the screen turn off and wake it: the calibration is back at once (with night light on top), rather than flat colors until you sign in again.
- [ ] Settings → **Displays**: each display is listed with how its brightness is controlled; **Copy report** puts the compatibility report on the clipboard. Under **Names and visibility**, rename one, untick "Show in panel" for another, Apply. The panel uses the new name and leaves out the hidden one.
- [ ] Laptop with an external monitor: turn on **Follow the laptop's brightness keys**, Apply, and press the brightness keys: the external monitor follows within about 2 seconds.
- [ ] Under **When I'm away**, turn on **Dim displays when I'm away**, set **After** 1 min and **Dim to** 20 %, Apply, and leave the PC alone: after a minute the displays fade down; move the mouse and they come back. Play a video and leave it: nothing dims. Open a web page in fullscreen (F11) and leave it: the displays dim. Add an app under **Never dim while these apps are in front** and leave it in front: nothing dims.
- [ ] Sleep the PC for a few minutes with a monitor connected, wake it, and wait 2 minutes before **Report a Problem…**: the summary's "Last wake from sleep" section shows when each display answered again.

## 5. Command line (optional)

In a new terminal (or use the full path `%LOCALAPPDATA%\Programs\Winlight\winlight-cli.exe`):

- [ ] `winlight-cli --list` shows your displays with numbers and brightness.
- [ ] `winlight-cli --set 30 --display 1` fades display 1 to 30%; `winlight-cli --set +20 --display 1` brings it up.
- [ ] `winlight-cli --night-light toggle` turns night light on or off.
- [ ] Quit Winlight and run `winlight-cli --list`: it says Winlight isn't running.

## 6. Startup, upgrade, uninstall

- [ ] Settings → General → **Start Winlight when I sign in** is on. Sign out and in: Winlight starts in the tray only. Turn it off, Apply: Task Manager → Startup apps shows Winlight as Disabled. Microsoft Defender doesn't flag or quarantine Winlight (Windows Security → Protection history).
- [ ] Settings → General → **Theme**: Light and Dark change Winlight's windows at once; Match Windows goes back to Windows' choice. **Accent color**: each preset recolors switches, sliders and the Apply button in both themes, and the text on Apply stays readable.
- [ ] In light mode, the panel's percentages and captions are easy to read.
- [ ] With night light on, run the installer again: Winlight closes itself, colors go back to normal, and your settings are kept after it starts again.
- [ ] Uninstall (Settings → Apps → Winlight): Winlight closes first and colors return to normal. It's gone from the Start menu, and from Task Manager → Startup apps.

## 7. Send it back

Even if everything worked, a **Report a Problem…** zip is useful: it shows how each of your displays was detected. It doesn't contain your user name, computer name, location or custom display names; you can open it and check before sending.
