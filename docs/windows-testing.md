# Windows test plan

A checklist for running Winlight on real Windows hardware. The Windows backends have unit tests against a fake of the Windows APIs; this plan covers what only real hardware and a person at the screen can check. The background for many checks is in [windows-notes.md](windows-notes.md).

Note the hardware for each run: Windows version, GPU and driver, each monitor's model, and how it's connected (HDMI, DisplayPort, USB-C, dock).

**First run so far** (October 2026, a laptop with only its built-in panel; details in [windows-notes.md §10](windows-notes.md#10-first-windows-run-october-2026)): §1 and §2 pass; the backend parts of §4 (laptop panel, software dimming on its gamma ramp), §5 (night light, the warmth limit, crash recovery), §5b (the brightness schedule), §6 (remember brightness) and the sleep handling of §7 were verified by driving the real app and APIs without the GUI. Still to do: the GUI and visual checks (§3, §9), anything needing external monitors, a TV or HDR (§4, §7 hotplug), and the installed app (§10).

## 0. Setup

```powershell
git clone https://github.com/Arnie14/Night-Light-Windows.git
cd Night-Light-Windows
py -3.12 -m venv .venv          # 3.12 or newer, from python.org
.venv\Scripts\activate
pip install -r requirements-dev.txt   # includes wmi and pywin32 on Windows
```

Always create `.venv` on the machine that uses it. Git ignores it, but if the project folder is copied from Linux (USB stick, shared drive), delete the copied `.venv` first: a Linux virtual environment (`bin/`, Linux binaries) doesn't work on Windows (`Scripts\`, `.exe`), and the reverse is true too.

Before testing night light:

- Turn off **Windows Night Light** (Settings > System > Display), f.lux, and the AMD "Custom Colors" or NVIDIA color settings, if you use them. They are tested together with this app in §8.
- Turn on **DDC/CI** in each external monitor's on-screen menu. It is sometimes off by default.

## 1. Automated tests

```powershell
pytest
```

Expected: everything passes, and **nothing is skipped**. The Windows-only tests check the ctypes structure sizes, `QueryDisplayConfig`, each display's EDID identity, reading gamma ramps, opening DDC/CI handles, the error for an invalid handle, and (in a separate process with a real window) Windows' power notifications. A failure in `test_win32.py` means a binding is wrong; send its output.

## 2. Probe report

```powershell
python main.py --probe
```

This prints, and saves to the log folder as `probe.txt`, what each API says about every display. It changes nothing, except that each display is **briefly tinted** to find the warmest night light Windows allows, and then restored.

Check that:

- [ ] "Windows capabilities and ctypes layouts" says `layout problems: none`.
- [ ] Each monitor appears under "Displays" with its real model name, the right connector, and the right HDR state, and an "EDID:" line with its identity. Two identical monitors should have different identities if their EDID has a serial ("serial present").
- [ ] Each external monitor that has DDC/CI on reads a brightness under "DDC/CI monitors" ("VCP 0x10 brightness: N of M").
- [ ] A laptop panel appears under "Built-in panels (WMI)".
- [ ] "Warmest night light Windows accepts" shows a temperature, which is the limit described in windows-notes.md.
- [ ] "What the app sees" lists each display once, with a sensible name and brightness.

**Send the whole report back**, whatever the results; it is the starting point for any fix.

## 2b. Compatibility report

```powershell
python main.py --report
```

The user-facing summary (also in Settings > Displays, with Copy and Save). It sends nothing to the displays.

- [ ] Each display is listed once, with the right connection and "Brightness:" matching how it really works (DDC/CI, WMI for the laptop panel, Windows SDR brightness for HDR without DDC/CI, software dimming, or "not controllable" with a reason and something to try).
- [ ] Settings > Displays shows the same, and hovering over a row shows the details. Copy report puts the same text on the clipboard.

## 3. Start, tray and flyout

```powershell
python main.py --debug
```

The log is also written to `%LOCALAPPDATA%\night-light\Logs\night-light.log`, and its path is printed at startup.

- [ ] A sun or moon icon appears in the tray. Check it's sharp at your display scaling, and readable on a light and a dark taskbar.
- [ ] If the icon is hidden in the overflow (^) area, drag it onto the taskbar for the next checks, then repeat them from the overflow area.
- [ ] Hover over the icon: the tooltip is two lines, "Winlight" and then "Night Light: …" with the status. Nothing about "Brightness and Color".
- [ ] Left-click: the flyout opens just above the taskbar, its right edge in line with the icon (or in the corner by the clock), with a gap of about 12 px.
- [ ] Click the icon again: the flyout closes and doesn't reopen.
- [ ] Open it, then click on the desktop or another window: the flyout closes. **If it stays open, note it**; this is the known Windows foreground-window quirk.
- [ ] Open it and press Esc: it closes.
- [ ] Right-click: the menu shows Suspend/Activate, Settings… and Quit.
- [ ] Move the taskbar to the top, left or right (Windows 10), or set it to auto-hide, and check the flyout opens next to it each time.
- [ ] Restart Explorer (Task Manager > Windows Explorer > Restart): the tray icon comes back.

## 4. Brightness

Before testing external displays, run `Winlight.exe --probe` (or `python main.py --probe`) once with everything connected, and keep `probe.txt` from the log folder. For each display it shows how it is connected, which DDC/CI codes it answers, how long each answer takes, and whether it reports its power state.

For each external display, note: brand and model, the connection (HDMI, DisplayPort, USB-C, Thunderbolt; direct, adapter, hub or dock), and the GPU it is plugged into.

Per external monitor:

- [ ] The flyout lists it by model name, with its current brightness.
- [ ] Dragging the slider changes the monitor's brightness **while dragging**, not only on release, and the UI never freezes.
- [ ] After a fast drag, the monitor ends at the slider's final value.
- [ ] 0% and 100% match the monitor's own menu minimum and maximum.
- [ ] Change brightness with the monitor's buttons, then reopen the flyout after 5 s or more: the slider shows the new value.
- [ ] A monitor with DDC/CI switched off in its menu shows a **disabled** slider with "DDC/CI didn't answer" under it, and advice to turn DDC/CI on in its tooltip (switch it off, then click ⟳).
- [ ] No slider ever moves without changing anything: whenever a display can't be changed (checking, reading, in standby, not responding, disconnected), its slider is disabled with a few words saying why.
- [ ] Turn the monitor off with its power button and back on, with Winlight running: it is listed again with working brightness within about 10 s (DisplayPort monitors usually disappear while off; HDMI ones often stay listed).
- [ ] Sleep the PC with the monitor connected, wake it: the monitor wakes normally and isn't woken or turned on by Winlight on its own. **Note any monitor that stays black or needs unplugging** (some firmware hangs if sent DDC/CI while asleep). Right after waking it may briefly say "Not answering yet, retrying" (not "DDC/CI didn't answer"), then work.
- [ ] Note any **flicker** while dragging the slider, or a monitor that changes in visible steps instead of smoothly.
- [ ] If the flyout ever shows "A display isn't responding" or a slider says "Not responding", note which display and what you were doing, and send the log. The other displays should keep working meanwhile.
- [ ] A monitor that accepts changes but doesn't apply them: the log says "accepted N% but reports M%" and the slider shows what the monitor reports.

TVs and displays without DDC/CI (software dimming):

- [ ] The TV is listed with a slider and "Software dimming" under its name (Settings > Brightness > "Dim displays without brightness control" is on by default). If it says "DDC/CI didn't answer" instead, send `probe.txt`.
- [ ] Dragging its slider dims the picture smoothly down to 20%. Note how dark 20% looks, and any banding in dark gradients.
- [ ] With night light on, the TV is both dimmed and warm; turning night light off keeps it dimmed.
- [ ] Quit Winlight: the TV goes back to full brightness. Start it again: the dimming comes back (with "Remember each display's brightness" on).
- [ ] Turn "Dim displays without brightness control" off and Apply: the TV goes back to full brightness at once and shows "not supported".
- [ ] If a TV has a **real** brightness slider (DDC/CI works), note the brand and model.
- [ ] Night light tints the TV like the other displays (unless HDR is on for it, where it is skipped and the log says so).
HDR displays (Settings > Display > Use HDR):

- [ ] An HDR display without DDC/CI shows "HDR · SDR content brightness". Its slider moves Windows' own "SDR content brightness" slider (Settings > Display > HDR) and the two agree after reopening the flyout.
- [ ] An HDR monitor **with** DDC/CI starts on DDC/CI (Settings > Displays, hover: "How: HDR is on, and changes go over DDC/CI while the monitor takes them"). Move its slider:
  - If the brightness changes, it stays on DDC/CI.
  - If it doesn't (the monitor locks brightness in HDR mode), within about 2 s the log says "its brightness looks locked; switching to SDR content brightness", the slider becomes "SDR content brightness" at the level you chose, and the hover text and the compatibility report say why.
  - Note the model and which happened either way.
- [ ] Turn HDR off on that monitor: it goes back to DDC/CI.
- [ ] Turn HDR off and on with Winlight running: the display switches between the two kinds of slider within a few seconds.
- [ ] Note the TV's input mode (PC/Game mode or not) and whether that changes anything.

Laptop panel:

- [ ] The panel (named "Built-in display" unless its EDID gives a name) has a slider that changes its brightness.
- [ ] Windows' own brightness slider (in quick settings) and the app agree after you reopen the flyout.
- [ ] Unplug and replug the charger, or switch power mode: does the brightness jump back? If so, note it; Monitorian uses the power plan instead of WMI for this reason.

Docks, adapters and ports (whatever you have):

- [ ] A monitor on a DisplayLink/USB dock is listed as not supported, with the DisplayLink reason.
- [ ] A monitor through a USB-C or Thunderbolt dock: does DDC/CI work?
- [ ] The same monitor on HDMI and on DisplayPort (or USB-C): does DDC/CI work on both? The remembered brightness should follow it between ports.
- [ ] A converter (DisplayPort-to-HDMI, USB-C-to-HDMI) or KVM switch: does DDC/CI pass through?
- [ ] Two monitors daisy-chained over DisplayPort (MST), if available: are both controllable?
- [ ] NVIDIA Surround or AMD Eyefinity (several monitors combined into one): expected not to work, as in the other apps.

## 5. Night light

In Configure Night Light, choose "Always on":

- [ ] Applying fades the screens warm over about 2 s, on **every** display.
- [ ] Dragging a temperature slider previews it live. Releasing the slider returns to the schedule.
- [ ] Try 2000 K. If Windows refuses that, the screen stops at the limit `--probe` found, and the log has **one** warning about `GdiIcmGammaRange`.
- [ ] Optional: set `GdiIcmGammaRange` (DWORD `256` under `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM`), sign out and in, and check that 2000 K now works.
- [ ] Quit from the tray menu: the screens return to normal.
- [ ] With a calibrated monitor (ICC profile with calibration), quitting restores the calibrated look, not a flat one.
- [ ] At 4500 K or warmer, end the app from Task Manager, so it has no chance to clean up, then start it again. The log says the display "still had the tint Winlight left when it last stopped unexpectedly", and quitting returns the screen to its normal (calibrated, if it is) look. *(Verified on the first run's laptop, with a calibration.)*
- [ ] Run `python main.py` in a terminal with night light on and press Ctrl+C: the log says "quitting cleanly" and the colors return to normal. Same with closing the terminal window.
- [ ] Turn HDR on for a display (Settings > Display > Use HDR): the log warns once and that display isn't tinted, while the others still are.

Schedule:

- [ ] Custom times a few minutes ahead: the transition starts and ends on time, and the tray icon and status follow.
- [ ] Sunrise and sunset at location, picked by city: in Location, type part of your city (e.g. "lond"). A list of matches drops down; pick yours. No latitude/longitude fields are shown, and after Apply the times shown match a sunrise/sunset table for that city.
- [ ] Click **Detect**: the city of your Windows time zone is picked (Settings > Time & language > Date & time). If it says the time zone isn't in the list, note the zone it names.
- [ ] Type nonsense in Location and press Tab: the box goes back to the previous city.
- [ ] Choose **Custom location** (top of the list): Latitude and Longitude appear, filled with the last city's coordinates. Change them, Apply: the times follow your coordinates.
- [ ] Close and reopen the settings (and restart the app): the city or custom location you applied is still selected.
- [ ] Suspend for 1 hour, then Resume, from both the tray menu and the flyout.

## 5b. Brightness schedule

In Settings > Brightness, tick **Change brightness by time of day**:

- [ ] Each preset fills in its times; editing one switches the preset to "Custom". The line under the schedule says the current level and the next change.
- [ ] Custom times with a point a couple of minutes ahead, Apply: every display changes at that minute (the log says "Brightness schedule: N% from …"). Displays without control are left alone.
- [ ] Drag a slider after a point has applied: your level stays until the next point, which then applies to every display.
- [ ] Quit and start again within the same point: the levels you set come back (remembered), the schedule doesn't reset them.
- [ ] Sleep through a point (or two), wake: only the latest point applies, once.
- [ ] A sunrise or sunset point with a Night Light location: the time shown matches a sunrise/sunset table. Without a location, the line says 06:00 and 18:00 are used.
- [ ] "Change over" (gradual): the displays step towards the new level over that time.
- [ ] A monitor asleep or not answering at a point gets the level once it answers.

## 6. Startup

In Settings > General:

- [ ] Tick **Start Winlight when I sign in** and Apply. Task Manager > Startup apps lists Winlight. In `regedit`, `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run` has a `Winlight` value whose command uses `pythonw.exe` and the full path to `main.py`.
- [ ] Sign out and in (or restart): Winlight starts, with **no console window**, and only its tray icon shows.
- [ ] Untick it and Apply: the `Winlight` value is gone from the Run key.
- [ ] Untick **Start minimized in the system tray** and Apply, quit and start again: the settings window opens at start. Tick it again: only the tray icon appears.
- [ ] With both ticked, sign out and in: still only the tray icon.

Brightness options, in Settings > Brightness:

- [ ] **Remember each display's brightness** (on by default): set each display to a distinctive level in the flyout (e.g. 23% and 61%), quit and start again: they come back to those levels within a few seconds. The log says "Restoring … brightness".
- [ ] Change a monitor with **its own buttons**, then wait a minute with Winlight running: Winlight doesn't change it back.
- [ ] Sleep the PC, wake it: each display returns to its Winlight level within about 30 s, even if it woke at another one.
- [ ] Unplug a monitor, change its brightness with its buttons, plug it back in: it returns to the Winlight level. Then move it to **another port** (or HDMI ↔ DisplayPort): it is still recognised and restored.
- [ ] Laptop: drag the built-in display's slider fully left: it stops at 1%, the screen doesn't go black.
- [ ] Turn the option off and Apply, change levels, restart: nothing is restored.
- [ ] `%LOCALAPPDATA%\night-light\brightness.json` lists each display by a short code such as `DEL41A2`, followed by its serial number when the EDID has one (`DEL41A2:7XKJ9F3`). Note any that look odd (two monitors sharing one, or an empty one). Two identical monitors should have different codes, and keep them when swapped between ports.
- [ ] **Change all displays together**: dragging one slider moves every display's slider and brightness, smoothly.

## 7. Changes, sleep and hotplug

- [ ] Unplug an external monitor: within a few seconds the flyout no longer lists it.
- [ ] Plug it back in: it reappears within about 10 s, with working brightness.
- [ ] Change resolution, or the scaling of one display: night light stays applied (it is re-applied within about 1 s).
- [ ] Sleep the PC for over a minute, then wake it: night light is still applied, and within about 30 s brightness works on every monitor. The log shows "Windows: suspending", then "Windows: resumed" once, "Woke up; detecting displays again at 2, 5, 10, 20, 30, 60, 120 s", a line per pass ("After waking +5 s: 2 of 2 answering …") and a "Wake summary". *(The messages and their order were verified with messages sent to the app's window; a real sleep hasn't been run on the merged build.)*
- [ ] Hibernate and resume: the same.
- [ ] Lock and unlock (Win+L) with night light on: night light is back **at once** after unlocking, with no visible flash of normal colors (before: 1–2 s). The log shows "Windows: unlocked". Note if a flash remains, and how long.
- [ ] Let Windows turn the screens off (Settings > System > Power > Screen timeout), then wake them: the log shows "Windows: display off" and "Windows: display on", nothing is sent to the displays while off, and they are detected again after.
- [ ] Turn a monitor off and on with its power button: note anything that stops working.
- [ ] With **Keep each display's color calibration** on and a calibration (ICC profile) loaded: let the screens turn off and wake them, and sleep and wake. The log says "Windows dropped the color calibration of …; putting it back" and the colors are calibrated again at once. *(Verified on a laptop panel.)*

## 8. Running alongside other tools

- [ ] Start `python main.py` a second time: no second tray icon; the first copy's flyout opens instead.
- [ ] Turn Windows Night Light on (Settings > System > Display > Night light > Turn on now): within about 10 s the panel and Settings show "Windows Night Light is on too", and the button opens Windows' Night light page. Turn it off there: the notice goes away. If the notice never appears, export this registry key with regedit once with Windows Night Light on and once off, and send both: `HKCU\Software\Microsoft\Windows\CurrentVersion\CloudStore\Store\DefaultAccount\Current\default$windows.data.bluelightreduction.bluelightreductionstate`.
- [ ] With f.lux running: the log notes the ramp being changed by something else, and within a few seconds warns that "another program keeps changing the colors" of that display; from then on the screen shows f.lux's colors without flickering between the two. Quit f.lux: within about 2 minutes Winlight's night light is back. *(Verified with a program rewriting the ramp; not with f.lux itself.)*
- [ ] Twinkle Tray or Monitorian running at the same time: both send DDC/CI to the same monitors. Note whether either misbehaves.

## 9. Display scaling and layout

- [ ] Two monitors with different scaling (for example 100% and 150%): the flyout opens at the right place and size on the one with the taskbar.
- [ ] The settings window and flyout look right in the Windows dark and light themes: all text readable (including the grey status and hint text), nothing cut off or overlapping, the wide fields in the settings ending on one vertical line.
- [ ] Switching between Always off, Always on, location and custom times shows and hides rows in the Night Light card without leaving gaps or stray dividers; the window keeps its size, and a page scrolls only when it is taller than the window.
- [ ] The navigation pane (Night Light, Brightness, Displays, General) shows which page is open; Tab reaches every control (Space opens a page or flips an On/Off switch). The Brightness page with several schedule times, and the Displays page with all displays, keep their rows aligned; the schedule's details fold away while it is off.
- [ ] Take screenshots of the flyout and the settings window in both themes and send them back.

## 10. This round's features

Pausing night light (Settings > Night Light > Pause night light):

- [ ] Tick "While an app is fullscreen": a fullscreen video (browser F11, or a player) and a game, borderless and exclusive fullscreen, each fade their display to normal colors within about half a second; the other displays stay warm. Leaving fullscreen fades it back. Note any game that isn't detected.
- [ ] Add an app with "Add an app" (it lists running apps) and with "Browse for an app…": switching to it pauses night light on its display; switching away resumes it.
- [ ] The panel and the tray tooltip say "Paused for …" meanwhile.
- [ ] Nothing flashes or jumps: starting the app at night fades in, Quit fades out quickly, pausing and resuming fade.

Brightness schedule (Settings > Brightness):

- [ ] Steps at a clock time, and at sunrise/sunset with an offset (needs a location under Night Light): at each step every visible display fades to the level; a sleeping display gets it when it wakes.
- [ ] Moving a slider after a step keeps your level, also after sleep and restarting the app, until the next step, which takes over again.
- [ ] Turning the schedule on applies the current step at once.

Displays (Settings > Displays):

- [ ] Rename a display: the panel, `winlight-cli --list` and the diagnostics use the new name; clearing the name brings back its own.
- [ ] Hide a display: it's gone from the panel and from "Change all displays together", and the schedule and restores leave it alone.

Command line (`winlight-cli.exe` next to `Winlight.exe`, or `python main.py …` from source):

- [ ] `--list`, `--list --json`, `--get --display 1`, `--set 40`, `--set +10 --display 2`, `--set 50 --display <part of a name>`, `--night-light on|off|toggle|auto|status`, `--refresh`: each does what it says, fades, and prints the result.
- [ ] Errors say why and exit with 1 (try `--display 9` and `--set bright`); with Winlight not running it says so and exits with 2 (`echo %ERRORLEVEL%`).
- [ ] From AutoHotkey or a `.bat` file: works, and waits for the result.

Focus, keys, sleep and privacy:

- [ ] Start a game in exclusive fullscreen (and one in borderless fullscreen, and a fullscreen video). Then start `Winlight.exe` again (or use a hotkey tool that launches it): the panel does **not** appear and the game keeps focus; the log says "Not opening the panel over a fullscreen app". The same with "Start minimized" off at sign-in. Untick Settings > General > "Never open over fullscreen…": then it does open.
- [ ] Laptop: tick Settings > Brightness > "External displays follow the laptop's brightness keys". Press the brightness keys: external displays follow within about 2 s. The log says whether Windows reported it ("noticed by Windows") or the check found it ("noticed by a check of the panel"); please note which. Dragging the laptop's own slider in the panel does **not** move the others (unless "Change all displays together" is on).
- [ ] Sleep for a few minutes and wake: the log has "After waking +N s: …" lines for each pass and a "Wake summary". Note how long each external monitor took to answer. Please send these lines even when everything works; they show how long monitors take on your hardware.
- [ ] Open `night-light.log` and a Report a Problem zip: your Windows user name, computer name and email don't appear anywhere (look for `<user>`, `<computer>`, `<home>` instead), nor your city, coordinates, custom display names or the apps you listed.

Dimming when idle (Settings > Brightness > When idle, e.g. 1 minute to 20%):

- [ ] Leave the PC alone: after the time, every display fades to 20% (displays already darker stay as they are). Move the mouse: they fade back within about a second.
- [ ] Play a video in a browser or player (not fullscreen) and leave it: nothing dims. The same with a fullscreen game or a PowerPoint slideshow.
- [ ] With the brightness schedule on, stay away across a step's time: coming back sets the step's level, not the old one.
- [ ] Quit Winlight while dimmed: the displays come back first.
- [ ] Hidden displays stay as they are. External monitors dim through their colors (night light still on top), not their backlight: their own on-screen menu still shows the same brightness while dimmed. The laptop panel really dims (Windows' brightness slider moves too). An HDR display dims only if its slider says "SDR content brightness"; otherwise it stays as it is.

Diagnostics and safe mode:

- [ ] Tray > Report a Problem…: a zip appears on the Desktop with summary.txt, settings.json (location rounded) and the logs; the summary is on the clipboard; the buttons show the file and open GitHub. `winlight-cli --diagnostics` does the same and prints the path.
- [ ] If a monitor ever freezes Winlight: end it in Task Manager and start it again. That display shows "Skipped: Winlight stopped while talking to this display last time"; ⟳ in the panel tries it again.

## 11. The installed app

With a build from [Releases](https://github.com/Arnie14/Night-Light-Windows/releases), or the Actions tab's `winlight-windows` artifact. Quit any copy run from source first.

- [ ] Run `Winlight-<version>-setup.exe`. SmartScreen may warn ("More info → Run anyway"); note whether it does. There is **no administrator prompt**, and it installs to `%LOCALAPPDATA%\Programs\Winlight`.
- [ ] The installer, the Start menu entry and `Winlight.exe` in Explorer all show the Winlight moon icon. Right-click `Winlight.exe` > Properties > Details: product name Winlight and the version.
- [ ] "Launch Winlight" at the end starts it: tray icon only, **no console window**. The taskbar button (open Settings) shows the Winlight icon, not Python's.
- [ ] Settings > Location: the city list is filled (the time zone table was bundled).
- [ ] The app looks like the source version (native Windows 11 style, not a plainer fallback look).
- [ ] Start at sign-in from the installed app: the Run key's command is `"…\Programs\Winlight\Winlight.exe"` (no `pythonw.exe`), and it starts after signing out and in.
- [ ] With night light on, run the installer again (an upgrade): the running copy quits by itself, the screen goes back to normal colors before the files are replaced, and the settings are kept.
- [ ] Uninstall from Settings > Apps: Winlight quits first (colors back to normal), its files and Start menu entry are gone, and the `Winlight` value is gone from the Run key. `%LOCALAPPDATA%\night-light` (settings) stays.
- [ ] Portable zip: unzip to another folder, run `Winlight.exe`: it works the same.
- [ ] Note the installer's size, and how long the app takes to start.
- [ ] Tick "Add winlight-cli to PATH" when installing: a new terminal finds `winlight-cli`. Uninstall removes it from PATH again, leaving the rest of PATH as it was.
- [ ] The tester checklist [testing-installer.md](testing-installer.md) works start to finish with the installer alone.

## What to send back

- The zip from **Report a Problem…** (tray menu, or `--diagnostics`), which holds the log, settings, `probe.txt` if you ran `--probe`, the compatibility report and a summary of every display.
- The screenshots.
- The failing checklist items, with what happened instead.
- The output of `pytest`, if anything failed.
