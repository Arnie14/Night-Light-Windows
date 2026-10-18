# Testing Winlight with the installer

For testers using the installer from [Releases](https://github.com/Arnie14/Night-Light-Windows/releases). No Python or source code needed. It takes about 20 minutes; skip anything that doesn't apply to your hardware. The full developer checklist is [windows-testing.md](windows-testing.md).

**Found a problem?** Right-click the tray icon → **Report a Problem…**. Winlight saves a zip to your Desktop and copies a summary. [Open an issue](https://github.com/Arnie14/Night-Light-Windows/issues), paste the summary, attach the zip, and say which step below went wrong.

Tell us your setup in the issue: Windows version, laptop or desktop, and each display's model and how it's connected (HDMI, DisplayPort, USB-C, dock, adapter).

## 1. Install

- [ ] Run `Winlight-<version>-setup.exe`. If SmartScreen says "Windows protected your PC", choose **More info → Run anyway** (the beta isn't code-signed yet).
- [ ] No administrator prompt appears. Optionally tick "Add winlight-cli to PATH".
- [ ] At the end, "Launch Winlight" starts it: a sun or moon icon appears in the tray (check the ^ overflow area), and no console window opens.

## 2. The panel

- [ ] Click the tray icon: the panel opens above the taskbar, fully on screen, with a slider for each display.
- [ ] Hover over the icon: the tooltip says "Winlight" and the night light status.
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
- [ ] Settings → Night Light → tick **While an app is fullscreen**, Apply. Play a video fullscreen or start a game: that display fades to normal colors, the others stay warm. Leave fullscreen: it fades back.
- [ ] Add an app under **For these apps** (e.g. your photo editor), Apply, and switch to it: the same happens while it's in front.
- [ ] With a game or video fullscreen, start Winlight again from the Start menu: nothing pops up over it, and the game keeps focus.
- [ ] Switch back to your usual schedule (custom times or sunset/sunrise).

## 4. Brightness

- [ ] Settings → Brightness → **Remember each display's brightness** is on. Set distinctive levels (e.g. 23% and 61%), quit Winlight (tray → Quit) and start it from the Start menu: the levels come back.
- [ ] Turn a monitor off with its power button and on again, or unplug and replug it: its level comes back within a few seconds.
- [ ] Tick **Change all displays together**, Apply: one slider moves them all.
- [ ] Turn on the **Brightness schedule** with a step a minute or two from now, Apply: at that time every display fades to the step's level. Move a slider afterwards: your level stays until the next step.
- [ ] Settings → **Displays**: rename one, untick "Show in panel" for another, Apply. The panel uses the new name and leaves out the hidden one.
- [ ] Laptop with an external monitor: tick **External displays follow the laptop's brightness keys**, Apply, and press the brightness keys: the external monitor follows within about 2 seconds.
- [ ] Sleep the PC for a few minutes with a monitor connected, wake it, and wait 2 minutes before **Report a Problem…**: the summary's "Last wake from sleep" section shows when each display answered again.

## 5. Command line (optional)

In a new terminal (or use the full path `%LOCALAPPDATA%\Programs\Winlight\winlight-cli.exe`):

- [ ] `winlight-cli --list` shows your displays with numbers and brightness.
- [ ] `winlight-cli --set 30 --display 1` fades display 1 to 30%; `winlight-cli --set +20 --display 1` brings it up.
- [ ] `winlight-cli --night-light toggle` turns night light on or off.
- [ ] Quit Winlight and run `winlight-cli --list`: it says Winlight isn't running.

## 6. Startup, upgrade, uninstall

- [ ] Settings → General → **Start Winlight when I sign in**, Apply. Sign out and in: Winlight starts in the tray only.
- [ ] With night light on, run the installer again: Winlight closes itself, colors go back to normal, and your settings are kept after it starts again.
- [ ] Uninstall (Settings → Apps → Winlight): Winlight closes first and colors return to normal. It's gone from the Start menu, and from Task Manager → Startup apps.

## 7. Send it back

Even if everything worked, a **Report a Problem…** zip is useful: it shows how each of your displays was detected. It doesn't contain your user name, computer name, location or custom display names; you can open it and check before sending.
