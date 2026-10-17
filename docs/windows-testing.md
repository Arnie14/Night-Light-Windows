# Windows test plan

A checklist for the first full run on real Windows hardware. Most of the app has only run on Linux so far, and the Windows backends have unit tests against a fake of the Windows APIs, but have never touched real hardware. The background for many checks is in [windows-notes.md](windows-notes.md).

Note the hardware for each run: Windows version, GPU and driver, each monitor's model, and how it's connected (HDMI, DisplayPort, USB-C, dock).

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

- Turn off **Windows Night Light** (Settings > System > Display), f.lux, and the AMD "Custom Colors" or NVIDIA color settings, if you use them. They are tested together with this app in §7.
- Turn on **DDC/CI** in each external monitor's on-screen menu. It is sometimes off by default.

## 1. Automated tests

```powershell
pytest
```

Expected: everything passes, and **nothing is skipped**. The Windows-only tests check the ctypes structure sizes, `QueryDisplayConfig`, reading gamma ramps and opening DDC/CI handles. A failure in `test_win32.py` means a binding is wrong; send its output.

## 2. Probe report

```powershell
python main.py --probe
```

This prints, and saves to the log folder as `probe.txt`, what each API says about every display. It changes nothing, except that each display is **briefly tinted** to find the warmest night light Windows allows, and then restored.

Check that:

- [ ] Each monitor appears under "Displays" with its real model name, the right connector, and the right HDR state.
- [ ] Each external monitor that has DDC/CI on reads a brightness under "DDC/CI monitors" ("VCP 0x10 brightness: N of M").
- [ ] A laptop panel appears under "Built-in panels (WMI)".
- [ ] "Warmest night light Windows accepts" shows a temperature, which is the limit described in windows-notes.md.
- [ ] "What the app sees" lists each display once, with a sensible name and brightness.

**Send the whole report back**, whatever the results; it is the starting point for any fix.

## 3. Start, tray and flyout

```powershell
python main.py --debug
```

The log is also written to `%LOCALAPPDATA%\night-light\Logs\night-light.log`, and its path is printed at startup.

- [ ] A sun or moon icon appears in the tray. Check it's sharp at your display scaling, and readable on a light and a dark taskbar.
- [ ] If the icon is hidden in the overflow (^) area, drag it onto the taskbar for the next checks, then repeat them from the overflow area.
- [ ] Hover over the icon: the tooltip shows the Night Light status.
- [ ] Left-click: the flyout opens just above the taskbar, its right edge in line with the icon (or in the corner by the clock), with a gap of about 12 px.
- [ ] Click the icon again: the flyout closes and doesn't reopen.
- [ ] Open it, then click on the desktop or another window: the flyout closes. **If it stays open, note it**; this is the known Windows foreground-window quirk.
- [ ] Open it and press Esc: it closes.
- [ ] Right-click: the menu shows Suspend/Activate, Configure Night Light… and Quit.
- [ ] Move the taskbar to the top, left or right (Windows 10), or set it to auto-hide, and check the flyout opens next to it each time.
- [ ] Restart Explorer (Task Manager > Windows Explorer > Restart): the tray icon comes back.

## 4. Brightness

Per external monitor:

- [ ] The flyout lists it by model name, with its current brightness.
- [ ] Dragging the slider changes the monitor's brightness **while dragging**, not only on release, and the UI never freezes.
- [ ] After a fast drag, the monitor ends at the slider's final value.
- [ ] 0% and 100% match the monitor's own menu minimum and maximum.
- [ ] Change brightness with the monitor's buttons, then reopen the flyout after 5 s or more: the slider shows the new value.
- [ ] A monitor with DDC/CI switched off in its menu shows "not supported" (switch it off, then click ⟳).

Laptop panel:

- [ ] The panel (named "Built-in display" unless its EDID gives a name) has a slider that changes its brightness.
- [ ] Windows' own brightness slider (in quick settings) and the app agree after you reopen the flyout.
- [ ] Unplug and replug the charger, or switch power mode: does the brightness jump back? If so, note it; Monitorian uses the power plan instead of WMI for this reason.

Docks and adapters (if you have one):

- [ ] A monitor on a DisplayLink/USB dock is listed as not supported, with the DisplayLink reason.
- [ ] A monitor through a USB-C or Thunderbolt dock: does DDC/CI work?

## 5. Night light

In Configure Night Light, choose "Always on":

- [ ] Applying fades the screens warm over about 2 s, on **every** display.
- [ ] Dragging a temperature slider previews it live. Releasing the slider returns to the schedule.
- [ ] Try 2000 K. If Windows refuses that, the screen stops at the limit `--probe` found, and the log has **one** warning about `GdiIcmGammaRange`.
- [ ] Optional: set `GdiIcmGammaRange` (DWORD `256` under `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM`), sign out and in, and check that 2000 K now works.
- [ ] Quit from the tray menu: the screens return to normal.
- [ ] With a calibrated monitor (ICC profile with calibration), quitting restores the calibrated look, not a flat one.
- [ ] At 4500 K or warmer, end the app from Task Manager, so it has no chance to clean up, then start it again. The log says the ramp "already had a tinted gamma ramp", and quitting returns the screen to neutral.
- [ ] Turn HDR on for a display (Settings > Display > Use HDR): the log warns once and that display isn't tinted, while the others still are.

Schedule:

- [ ] Custom times a few minutes ahead: the transition starts and ends on time, and the tray icon and status follow.
- [ ] Sunrise/sunset with your coordinates: the times shown match a sunrise/sunset table.
- [ ] Suspend for 1 hour, then Resume, from both the tray menu and the flyout.

## 6. Changes, sleep and hotplug

- [ ] Unplug an external monitor: within a few seconds the flyout no longer lists it.
- [ ] Plug it back in: it reappears within about 10 s, with working brightness.
- [ ] Change resolution, or the scaling of one display: night light stays applied (it is re-applied within about 1 s).
- [ ] Sleep the PC for over a minute, then wake it: night light is still applied, and within about 30 s brightness works on every monitor. The log shows "Woke up after…".
- [ ] Lock and unlock (Win+L), and turn a monitor off and on with its power button: note anything that stops working.

## 7. Running alongside other tools

- [ ] Start `python main.py` a second time: no second tray icon; the first copy's flyout opens instead.
- [ ] With Windows Night Light on as well as this app: do the tints stack (extra warm) or does one win? Note what you see.
- [ ] With f.lux running: the log notes the ramp being changed by something else. Expect the two to fight; just note how bad it looks.

## 8. Display scaling and layout

- [ ] Two monitors with different scaling (for example 100% and 150%): the flyout opens at the right place and size on the one with the taskbar.
- [ ] The settings window and flyout look right in the Windows dark and light themes.

## What to send back

- `probe.txt` and `night-light.log`, both from `%LOCALAPPDATA%\night-light\Logs\`.
- The failing checklist items, with what happened instead.
- The output of `pytest`, if anything failed.
