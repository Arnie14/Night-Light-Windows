# Windows notes: what Monitorian and Twinkle Tray taught us

[Monitorian](https://github.com/emoacht/Monitorian) (C#/WPF) and [Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray) (Electron with native C++ modules) are two mature, MIT-licensed Windows apps that control monitor brightness from the tray. Both have years of bug reports behind them. This page records how they use the Windows APIs, their fallbacks and their tray and flyout behavior, and the quirks they had to work around. Each point is marked with what this project does about it.

- ✅ done in this project
- 📋 planned or worth doing later
- ➖ deliberately not done

Code references are to the two projects' repositories as of October 2026.

## 1. Windows APIs

### Finding displays and naming them

Both projects combine several APIs, because no single one gives everything:

| API | Gives | Used by |
|---|---|---|
| `QueryDisplayConfig` + `DisplayConfigGetDeviceInfo` | Each active path: source GDI name (`\\.\DISPLAY1`), the EDID friendly name ("DELL U2720Q"), device path, connector type, HDR state | Both (`DisplayConfig.cs`; `win32-displayconfig`) |
| `EnumDisplayMonitors` + `GetMonitorInfoW` | `HMONITOR` handles and their GDI names | Both |
| `GetPhysicalMonitorsFromHMONITOR` (dxva2) | DDC/CI handles, one per physical monitor (more than one when displays are cloned) | Both |
| `EnumDisplayDevices` | Older device list; description is usually "Generic PnP Monitor" | Both, as a fallback |
| WMI `root\wmi` `WmiMonitorBrightness` / `WmiMonitorBrightnessMethods` | Built-in laptop panel brightness and its supported levels | Both |
| WinRT `DisplayMonitor` | Better names, connection kind | Monitorian, on Windows 10 1803+ |

- ✅ `nightlight/hardware/win32.py` binds `QueryDisplayConfig`, `EnumDisplayMonitors`, dxva2 and GDI with ctypes. Names come from the EDID via `QueryDisplayConfig`, not from the DDC/CI capabilities string (see §8).
- ✅ The device path converts to a PnP instance id (`DISPLAY\BOE0812\4&...&UID265988`), which is how both projects match a WMI instance to a display.
- ✅ The connector type marks built-in panels (LVDS, embedded DisplayPort or UDI, "internal") and "indirect" displays (DisplayLink/USB, Miracast, virtual), which have no DDC/CI channel.
- ➖ WinRT `DisplayMonitor`: `QueryDisplayConfig` names have been good enough.

### DDC/CI (external monitors)

- **Low level vs high level.** `GetVCPFeatureAndVCPFeatureReply(0x10)` reads brightness directly. `GetMonitorBrightness` is the "high-level" API: it parses the monitor's capabilities string first, which is slow and unreliable. Monitorian uses whichever the capabilities say works. Twinkle Tray's default "fast" check reads VCP codes `0x02`, `0xDF` and `0x10` and never requests capabilities.
  - ✅ VCP `0x10` first, `GetMonitorBrightness` only as a fallback.
- **Ranges aren't 0–100.** Both scale between the minimum and maximum the monitor reports (Monitorian's `DdcMonitorItem`: "not always 0", "not always 100").
  - ✅ `to_percent`/`to_raw` in `brightness.py`.
- **Transient errors get retried.** I2C transmit/receive errors and invalid-message errors (`0xC0262582/83/86/89/8A/8B`) are glitches. Twinkle Tray retries up to 3 times, 50 ms apart; Monitorian retries once.
  - ✅ 3 attempts, 50 ms apart.
- **Some errors mean the monitor is gone.** `ERROR_GRAPHICS_MONITOR_NO_LONGER_EXISTS` and `INVALID_PHYSICAL_MONITOR_HANDLE` mean the monitor was unplugged or the layout changed, so both re-detect.
  - ✅ `MonitorGoneError` triggers re-detection, rate-limited to once per 30 s.
- **Handles stay open.** Both keep physical monitor handles between calls and destroy them on re-detection. `monitorcontrol` opens them at listing time and frees them on garbage collection.
  - ✅ Our own backend keeps them, and `close()` frees them.
- **Other brightness codes.** Some monitors only respond to `0x13` (brightness), `0x6B` (backlight level) or even only `0x12` (contrast). Twinkle Tray tries these in that order and allows a per-monitor VCP override.
  - 📋 Add the fallback codes if testing turns up such a monitor.

### Laptop panels (WMI)

- Monitorian prefers the **power plan's brightness setting** (`PowerWriteACValueIndex`/`PowerWriteDCValueIndex` + `PowerSetActiveScheme`) over WMI for the built-in panel, so the value survives power-plan switches and works with adaptive brightness. WMI is its fallback, and `/powerignore` turns the power-plan path off.
  - 📋 Plain WMI for now; the power-plan path is a good follow-up if WMI changes get undone.
- WMI reports the panel's supported `Level`s, and Monitorian rounds to the nearest one.
  - ✅ `nearest_level()`.
- Twinkle Tray: WMIC (the command line tool) no longer ships with current Windows 11, so never shell out to it.
  - ✅ The `wmi` package (COM) is used instead.
- WMI calls fail transiently at sign-in and straight after resume. Monitorian catches `ManagementException` from the enumerator; Twinkle Tray only gives up on its WMI bridge after repeated failures.
  - ✅ A failed query is logged, and the next scan (after resume there are several) tries again.
- COM must be initialised on the thread that uses it.
  - ✅ `pythoncom.CoInitialize()` runs lazily on the monitor worker thread.
- Some laptops (OLED panels, OEM-controlled backlights) expose no WMI brightness at all.
  - ✅ They're listed as unsupported, with that reason.

### Gamma ramps (night light)

Twinkle Tray uses gamma ramps for its "software brightness" fallback (`tt-windows-utils/windows_display_brightness.cc`), and the same lessons apply to night light:

- **Windows rejects ramps far from identity.** Twinkle Tray never dims the peak below about 70% and changes the curve below that. For night light this means very warm temperatures are likely to be refused: if the same limit applies to a single channel, blue crosses it between 4100 and 4000 K (blue is 0.667 and 0.648 there). The real limit is measured during Windows testing with `--probe`. Raising the `GdiIcmGammaRange` registry value (DWORD `256` under `HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM`, the same value f.lux's "expanded range" sets) lifts the limit.
  - ✅ When a ramp is refused, the backend finds the warmest temperature Windows accepts for that display, stays there and logs one warning. `--probe` reports the limit and the registry value.
  - Measured on the first Windows machine (Intel UHD 630, Windows 11 25H2): the limit is **3280 K**, blue peaking at 0.50, well past the 70% guess. The 70% figure holds for dimming every channel at once (software dimming at 20% is accepted), not for one channel alone. With 20% dimming on top, the limit moves to 4400 K, so the limit is found again whenever the dimming changes.
- **"Success" isn't always success.** Twinkle Tray reads the ramp back after `SetDeviceGammaRamp` and compares it with a tolerance of 256, because some drivers return TRUE without applying the ramp or keep fewer bits.
  - ✅ The ramp is read back and checked, with one warning if the driver ignored it.
- **Ramps outlive the app**, and Windows loads a calibrated monitor's ICC calibration into the ramp. Twinkle Tray reads the existing ramp as the base and recognises its own leftover dimming.
  - ✅ The existing ramp is kept as the base, tinted, and restored on reset, so calibration survives.
  - ✅ A ramp that already looks tinted (any channel peaking below 90%: a crash leftover, or f.lux) is not kept; a neutral ramp is used instead.
  - ✅ So that a crash doesn't lose the calibration, the base ramp is journaled (`gamma.json`, per display identity) while the app has a display tinted. At the next start, a tint the journal recognises as the app's own leftover gets its calibration back; a ramp Windows has reset meanwhile makes the entry stale, and it is dropped. Verified on Windows with a real calibration (`CalibratedDisplayProfile-3.icc`) and `taskkill /F`.
- **HDR.** Twinkle Tray skips gamma for displays in HDR. Monitorian adjusts the SDR white level for HDR displays instead.
  - ✅ HDR displays are detected (`DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO_2` on Windows 11 24H2+, where auto color management makes the older query report "advanced color" for SDR displays) and skipped, with a one-time warning.
- Gamma is per GDI source: cloned displays share one ramp.

## 2. Fallbacks

Monitorian's order (`MonitorManager.EnumerateMonitorsAsync`) is:

1. HDR displays get SDR-white-level control.
2. DDC/CI for external monitors that answer.
3. WMI for built-in panels.
4. Everything else is listed as "unreachable", so the user sees that the display exists.

Twinkle Tray adds gamma-based software dimming for monitors with no hardware control, and separate support for Apple Studio Displays.

- ✅ Every active display is listed exactly once: external through DDC/CI, built-in through WMI, and anything else as unsupported. The reason is shown when it's known, such as "connected through DisplayLink" or "Windows offers no brightness control for this display".
- ✅ If `QueryDisplayConfig` fails, physical monitors are still listed under their dxva2 descriptions, and gamma falls back to `EnumDisplayDevices`.
- 📋 Software dimming through the gamma ramp, for monitors without DDC/CI. It would share the ramp with night light, so both would have to be combined into one ramp.

## 3. Flyout UI

- **Placement.** Monitorian's `StickWindowMover` finds the taskbar's edge and rectangle (`SHAppBarMessage(ABM_GETTASKBARPOS)`), then lines the flyout's right edge up with the tray icon (`Shell_NotifyIconGetRect`). It falls back to the taskbar's corner because the icon rectangle is wrong on some Windows 11 builds (22621+) and for icons in the overflow area. Twinkle Tray reads the taskbar edge from the work area or the registry, and puts the panel in the corner above the clock.
  - ✅ `ui/placement.py` gets the taskbar edge from the screen's geometry versus its available geometry. It only trusts the icon rectangle when it lies inside the taskbar strip, uses the corner otherwise, and keeps a 12 px gap as Windows 11 does.
- **Hiding.** Both hide the flyout when it loses focus. Monitorian ignores a show request within 0.2 s of hiding, so the tray click that closed the flyout doesn't reopen it.
  - ✅ The existing 0.3 s guard in `Flyout.toggle` does the same.
- **Refresh on open.** Brightness may have been changed with the monitor's own buttons. Monitorian re-reads values if they're older than a threshold when the flyout opens, and Twinkle Tray does an "eager update".
  - ✅ The flyout re-reads brightness on open, at most every 5 s.
- **Sliders.** Both throttle while dragging (send now, then at most every N ms) instead of debouncing, so the monitor follows the drag.
  - ✅ The slider throttles at 100 ms.
  - ✅ The worker only ever sends a monitor's latest value, so slow DDC/CI writes can't pile up.
- **Focus.** On every tray click Monitorian calls `SetForegroundWindow` on the notify icon's hidden window, then makes the flyout topmost while showing it, because Windows may refuse to give a tray app's window focus. If the flyout doesn't close when you click elsewhere, this is why.
  - 📋 Check during Windows testing (see the [test plan](windows-testing.md)).

## 4. Tray behavior

- **Single instance.** Monitorian uses named pipes and Twinkle Tray uses Electron's `requestSingleInstanceLock`. A second launch shows the running copy instead.
  - ✅ `single_instance.py` does this with a per-user `QLocalServer`.
- **Left click opens the flyout and right click opens a menu**, in both.
  - ✅ Same here.
- **Mouse wheel over the icon changes brightness** in both.
  - ➖ Qt's `QSystemTrayIcon` doesn't expose wheel events. It would need a native event filter for the icon's messages.
- **Explorer restarts.** Twinkle Tray has a "recreate tray" option for icons that vanish after Explorer restarts or display changes.
  - ✅ Qt re-adds the icon on Explorer's `TaskbarCreated` message, so this is a test-plan item, not code.
- **Icons.** Both pick tray icon variants for light and dark taskbars.
  - ✅ The sun and moon are coloured, so they work on both.
  - ✅ They're now drawn at each tray size (16–64 px) instead of being scaled down from 64 px.
- **Start with Windows.** Monitorian uses the `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` registry key, or the MSIX `StartupTask` when installed from the Store. Twinkle Tray uses Electron's login items.
  - ✅ `autostart.py` writes the `Run` value (with `pythonw.exe`, so no console opens), and re-writes or removes it at every start to match the setting, so a moved folder doesn't leave a broken entry. An MSIX `StartupTask` is for when there is a Store package.

## 5. Brightness features users like

From Twinkle Tray's settings (its `en.json` strings) and Monitorian's README:

- **Restore brightness.** Twinkle Tray's "Apply brightness at startup" restores each display's last known level, and its "Auto-apply brightness" re-applies it after a monitor is turned off and on or reconnected. Its own warning: "If your monitor responds strangely after turning it off/on or disconnecting/connecting hardware, disabling this may help", and it lets you skip specific monitors.
  - ✅ `brightness_memory.py`, on by default and switchable in Settings > Brightness. Restores at start, after waking and when a display reappears; never while a display stays connected, so the monitor's own buttons and Windows' adaptive and battery brightness aren't fought. Only levels set in the app are remembered: a monitor that wakes at the wrong level can't overwrite the remembered one. A restore waits until the monitor answers, and is retried at the later detections.
  - 📋 Skipping specific monitors, if a monitor turns out to misbehave.
- **All monitors together.** Monitorian's "unison" and Twinkle Tray's "linked levels" move every slider at once.
  - ✅ "Change all displays together" in Settings > Brightness.
  - 📋 Twinkle Tray's "normalize brightness" (a min/max per monitor, so linked levels look alike on different panels).
- **Never black out a laptop panel.** On some laptops 0 turns the backlight off, so the screen looks dead. KDE keeps the built-in display above zero.
  - ✅ The built-in display's slider, linked changes and restores stop at 1%.
- **Update rate.** Twinkle Tray offers rates from "Ludicrous" to 2 s, because some monitors flicker when written to quickly.
  - ✅ A fixed 100 ms throttle with only the latest value sent. 📋 Make it a setting if testing finds a monitor that flickers.
- **Brightness by time of day** (Twinkle Tray's "time adjustments": the latest adjustment at or before now, applied when a newer one comes due, at startup and after waking; sun-position times through SunCalc).
  - ✅ `core/brightness_schedule.py` and `brightness_scheduler.py`, with presets. Each point is applied once per display, identified by the instant it began, and that identity is saved, so unlike Twinkle Tray's "apply at startup", a restart within a point leaves the levels the user picked alone. Sleeping through several points applies only the latest; a display that wasn't reachable gets the level when it answers. Sunrise and sunset come from astral and the Night Light location.
- 📋 **Later:** hide and rename displays, reorder them (both); hotkeys and an on-screen overlay (Twinkle Tray, and Monitorian as a paid add-on); dimming when idle (Twinkle Tray); contrast and min/max range per monitor (Monitorian); scrolling over the tray icon (needs a native event filter, see §4).

## 6. External monitors and TVs: what to expect and how it's handled

From Twinkle Tray's [display detection page](https://github.com/xanderfrangos/twinkle-tray/wiki/Display-Detection-&-Support-Issues) and [release notes](https://github.com/xanderfrangos/twinkle-tray/releases), Monitorian's [README](https://github.com/emoacht/Monitorian) and issues (e.g. [#50](https://github.com/emoacht/monitorian/issues/50), monitors uncontrollable after hibernation), and reports from other DDC/CI tools:

| Problem | What the other projects say | Here |
|---|---|---|
| DDC/CI off in the monitor's menu, or blocked by a preset/mode | The most common cause (both FAQs) | ✅ The "not supported" text says to turn DDC/CI on, then to try another cable or port |
| Hubs, converters, KVMs, MST and docks strip DDC/CI | Test without them; switching HDMI ↔ DisplayPort can help | ✅ Same advice in the "not supported" text; DisplayLink/USB displays get their own reason. 📋 Results by dock and adapter go in the test report |
| TVs | "Most consumer TVs don't support it"; HDR TVs only have Windows' SDR brightness | ✅ HDR displays without DDC/CI get Windows' SDR brightness (as Monitorian and Twinkle Tray 1.17); other displays without control get software dimming (as Twinkle Tray 1.18). HDMI displays with neither say "most TVs don't support it" |
| NVIDIA Surround / AMD Eyefinity | Incompatible: several monitors merged into one virtual display | ➖ Documented in the test plan |
| Old AMD drivers, hub firmware | Known DDC/CI problems; update drivers and dock firmware | ➖ Advice for reports |
| Monitors that hang during detection | Twinkle Tray 1.18 added a "safety system" and retries; capability strings are the worst offender | ✅ No capability strings. Every call has a time limit (§9): a probe that hangs past 4 s, or a read or write past 5 s, is abandoned, its display shows "Not responding" and is left alone until the call returns, the other displays carry on, and quitting doesn't wait for it |
| DDC/CI to a sleeping monitor | Can wake it or, on some models, hang the firmware until it's unplugged; Twinkle Tray added "monitor power state detection" | ✅ Before restoring brightness (the only time the app writes unprompted), VCP 0xD6 is read; a monitor in standby or off is left alone and restored at a later reading. Monitors that don't report it are assumed awake |
| Monitor not ready after sleep or plugging in | Both re-detect several times over 30–60 s (Monitorian over about two minutes after its issue #50) | ✅ Re-detection at 2 and 8 s after changes, and 3, 8, 15, 30, 60 and 120 s after waking; restores wait for an answer. A monitor that answered earlier is shown as "not answering yet", not told to turn DDC/CI on (Twinkle Tray keeps "feature snapshots" for the same reason) |
| Brightness reverting after hardware events, startup overwriting the current level | Fixed in Twinkle Tray 1.17/1.18 | ✅ Only levels set in the app are remembered; restores happen only on start, wake and reconnect |
| Writes that are acknowledged but ignored | Monitorian notes `SetMonitorBrightness` may report success | ✅ Each display keeps its desired, commanded and observed level; it is read back once, 1.5 s after the last change, and a mismatch is logged once and shown as it is |
| Monitors answering other codes than 0x10 | Twinkle Tray tries 0x13, 0x6B (and lets users pick a code) | ✅ `--probe` reports 0x12, 0x13 and 0x6B for each monitor. 📋 Fall back to them if testing finds such a monitor |
| Flicker when written to quickly | Twinkle Tray's update rate setting goes up to 2 s | ✅ 100 ms throttle, latest value only. 📋 A setting if testing finds flicker |
| A monitor turned off with its button and back on | DisplayPort monitors disappear and come back (a display change); HDMI ones often stay listed | ✅ Display changes re-detect, and so do the displays turning back on and unlocking (as Monitorian), at 3, 8 and 20 s |

## 7. Design choices taken from their source code

Reading [Monitorian](https://github.com/emoacht/Monitorian), [Twinkle Tray](https://github.com/xanderfrangos/twinkle-tray) (both MIT) and [KWin](https://invent.kde.org/plasma/kwin) (GPL) for structure, not just behavior. No code is copied; the ideas are re-implemented.

- **One class per way of controlling brightness** (Monitorian's `DdcMonitorItem`, `WmiMonitorItem`, `HdrMonitorItem`, `UnreachableMonitorItem` behind `IMonitor`).
  - ✅ `hardware/windows.py`: `_VcpControl`, `_HighLevelControl`, `_WmiControl`, `_SdrControl`, `_SoftwareControl` behind `_Control` (read, write, power state, close). The backend picks one per display and never branches on the kind again; DDC/CI's "monitor gone" errors are translated in one place.
- **Order of controls** (Monitorian's `MonitorManager`, plus Twinkle Tray's gamma fallback): hardware first, then SDR level on HDR displays, then software dimming, then "unreachable" with a reason.
  - ✅ The same order. On an HDR monitor that answers DDC/CI, DDC/CI is kept (Twinkle Tray's default); 📋 testing will show whether HDR monitors ignore it, as some lock brightness in HDR mode, in which case SDR should win as in Monitorian.
- **SDR white level** (Monitorian `DisplayConfig.cs`/`HdrMonitorItem.cs`, Twinkle Tray `windows-hdr.cc`): documented get (type 11), undocumented set (type `0xFFFFFFEE`, a flag byte of 1), thousandths of 80 nits, 80–480 nits mapped linearly to 0–100%, Twinkle Tray rounds to 4 nits.
  - ✅ `win32.get_sdr_white_level`/`set_sdr_white_level`, same constants; the struct sizes are tested against the C layout.
- **Software dimming** (Twinkle Tray `windows_display_brightness.cc`): a 20–100% range mapped to a 60–100% scale; the ramp's peak never below 70% because Windows rejects darker ramps; below that the exponent bends the curve so mid-tones still darken; always rebuilt from the stored base ramp so it never compounds; read back with a tolerance of 256.
  - ✅ `colortemp.dimming_curve`/`dimmed_ramp`, the same numbers. If Windows still refuses, the backend settles at the darkest level it accepts and logs it once.
- **Brightness and temperature in one transform** (KWin `ColorDevice::recalculateFactors`: brightness factors × temperature factors; setters only store a value and schedule one update).
  - ✅ `WindowsGammaBackend` keeps each display's base ramp and dimming; night light and dimming both recompose base → dimmed → tinted, under a lock (night light comes from the UI thread, dimming from the monitor thread). Night light turning off keeps dimming; quitting (`release()`) undims and restores every ramp.
- **Monitorian's 24H2 HDR check** (`ADVANCED_COLOR_INFO_2`, `activeColorMode == HDR`).
  - ✅ Already used.
- **Display power notifications** (Monitorian `PowerManagement.cs` and its watchers: `RegisterPowerSettingNotification` for the console display state, `SystemEvents.PowerModeChanged`, session switches).
  - ✅ `hardware/winevents.py`: a hidden window and a native event filter receive suspend and resume (the two resume messages merged), displays off/on, lock/unlock, `WM_DISPLAYCHANGE`, `WM_TIMECHANGE` and `WM_ENDSESSION`. Nothing is sent to displays while suspended or off; the wall-clock jump stays as a fallback.

## 8. Quirks and issues they ran into

- **Monitors aren't ready straight after a change.** After a display change, resume or sign-in, monitors often don't answer DDC/CI for several seconds.
  - Monitorian's timers tick at gaps of 1, 4, 5 and 10 s after a display settings change, so it re-scans 1, 5, 10 and 20 s afterwards. After resume it re-scans at 5, 10, 20, 30 and 60 s; after the display turns on at 5 and 10 s; and after sign-in at 30 and 60 s. It ignores display changes while the display is off.
  - Twinkle Tray waits 5 s ("hardwareRestoreSeconds") after hardware changes and 7 s after metric changes, merges events and skips the first event at startup.
  - ✅ `watcher.py` watches Qt's screen added/removed/geometry signals and `WM_DISPLAYCHANGE`, waits for them to settle, then re-applies night light and re-scans at 2 and 8 s. After resume it re-scans at 3, 8, 15, 30, 60 and 120 s; after the displays turn back on, at 3, 8 and 20 s. Display changes while suspending are ignored, as Monitorian ignores them while the display is off.
  - ✅ Resume comes from Windows' notification, with a jump in the wall clock as a fallback; the two are merged into one wake.
- **Capabilities strings are dangerous.** Twinkle Tray found that requesting them can hang or crash the whole process, or the machine, on some monitors and drivers. It writes a "sentinel" file before each risky DDC call and switches to the fast method on the next start if one survives. Monitorian notes that some monitors return malformed capabilities.
  - ✅ The Windows backend never requests capabilities; names come from the EDID instead.
  - ➖ The Linux `monitorcontrol` backend still reads them for the model name.
- **Null or invalid physical monitor handles** sometimes come back from `GetPhysicalMonitorsFromHMONITOR`, so Twinkle Tray retries after 200 ms. Monitorian notes that a handle value of 0 may be valid.
  - ✅ 0 isn't treated as invalid. A failing monitor is retried by the next scheduled re-scan.
- **There's no documented order between a source's physical monitors and its targets** (Twinkle Tray), so clone mode can mix up names.
  - ✅ Handles that answer DDC/CI go to external targets in order, and the laptop panel never gets a DDC handle.
- **Identifiers change with the port.** Monitorian's README: the same monitor on a different connector, or over USB-C, gets a new device instance id.
  - ✅ Remembered brightness is keyed by the EDID manufacturer and product code from the device path (`DEL41A2`), which doesn't change with the port; identical monitors are told apart by order (`DEL41A2#2`).
  - ✅ Better than order: the EDID Windows keeps in the registry (`HKLM\SYSTEM\CurrentControlSet\Enum\<instance>\Device Parameters\EDID`, readable without talking to the monitor) has its serial number, so the key is `DEL41A2:7XKJ9F3` and identical monitors keep their own levels whichever port each is on. Panels and monitors without a serial keep the old key, and levels saved under the old key carry over the first time a display is seen. Neither Monitorian (device instance id, which changes with the port) nor Twinkle Tray (path-based ids) does this.
- **Docks and adapters.**
  - Twinkle Tray's README warns that USB, Thunderbolt and Surface docks with HDMI or DisplayPort may not be compatible.
  - Monitorian's README says converters aren't compatible, Thunderbolt/USB-C cables generally are, and Surface Dock 1 and 2 are known to work.
  - DisplayLink docks are a separate case: their screens are "indirect" displays drawn by a driver, so there is no DDC/CI channel at all. The app tells the user that instead of the generic message.
  - Monitors often have DDC/CI switched off in their on-screen menu.
- **The AMD Radeon control panel's "Custom Colors"** interferes (Twinkle Tray's README). It probably also fights over gamma, so test with it off.
- **Some monitors behave badly with DDC/CI writes** (Twinkle Tray's README warns about power-state and brightness commands on certain models).
  - ✅ Only VCP `0x10` is written: when the user moves a slider, and for restores and the brightness schedule only after VCP `0xD6` says the monitor is on. Messages to one monitor are at least 50 ms apart, as DDC/CI asks.
- **Windows Night Light and f.lux** change colors too. f.lux writes the same GDI ramp, so the two fight. Windows Night Light may not show up in the GDI ramp at all, depending on the driver, in which case the two tints stack.
  - ✅ Ramps changed by another program are noticed and logged. For a test, turn Windows Night Light off.
- **`SetMonitorBrightness` can report success when the monitor ignored it** (Monitorian).
  - ✅ One read-back per burst of changes (1.5 s after the last), not per change, so a drag costs one extra round trip; see §6.
- **Mixed-DPI screens.** Placement uses Qt's device-independent coordinates. Moving windows between screens with different scaling has had bugs in Qt on Windows.
  - 📋 This is in the test plan.

## 9. Reliability design

Sleep and hibernation cause most failures in this kind of app, DDC/CI the next most. These rules shape `monitors.py`, `hardware/windows.py`, `hardware/calls.py` and `hardware/winevents.py`; each is covered by tests that drive the code step by step (`tests/test_monitor_service.py` for the races).

- **One serialized queue.** The UI thread only asks; one worker thread makes every backend call in turn. A display's newest level replaces an older one not yet sent (Twinkle Tray's update throttle), detection requests close together are merged, and a level the user just picked is never replaced by an unprompted one (restore, schedule).
- **Addressed by identity, never by position.** Commands carry the display's key and are resolved against the current detection when they run (Twinkle Tray's refresh generations do the same job). A command for a display that disappeared, or whose port now has a different monitor, reaches nothing.
- **Bounded waits.** Every call has a time limit (detection 20 s, reads and writes 5 s, power state 3 s). A call that doesn't return is abandoned; its display is left alone until that call returns (at most one stuck thread per display), its handle is destroyed only once the call is done with it, and the other displays carry on. Detection probes monitors side by side under one 4 s deadline, as Monitorian's enumeration does under its timeout. Quitting waits at most 3 s.
- **Failures stay per display.** Each display is detected, read and written in its own try block; one display's gamma ramp failing doesn't stop the others.
- **Desired, commanded, observed.** Each display's state says what the app wants, what the display last accepted, and what it last reported, plus why it can't be changed now (detecting, reading, standby, paused while asleep, not responding, retrying, disconnected, failing). The slider is enabled only when a change can really be made; otherwise it says why in a few words.
- **Sleep.** On suspend and displays-off nothing is sent. After waking, commands wait for a fresh detection (old handles are stale), observed values are treated as stale, memory restores everything once read, and the brightness schedule re-evaluates (applying only the latest point slept through).
- **Conservative DDC/CI.** One read of `0x10` per monitor per detection; never the capabilities string; never to laptop panels; the high-level API isn't tried when nothing gets through at all; `0xD6` only before unprompted writes; a read-back once per burst; 50 ms between messages.
- **Native calls behind one adapter.** `win32.py` is the only module that touches ctypes; the backends call it through `DisplayApi`, and tests replace it. At startup it checks every structure's size against the SDK's and turns off a feature whose layout is wrong rather than corrupting memory; it detects optional APIs (the 24H2 HDR query is tried once and remembered); it never lets an exception escape a ctypes callback; and errors are logged with the code, Windows' text and what it usually means (for example `0xC0262589 … — the answer was garbled or missing; DDC/CI may be off`).
- **Clean exits.** Ctrl+C, Ctrl+Break, closing the console, `--quit` and signing out (`WM_ENDSESSION`) all restore the colors; a crash is recovered at the next start through the gamma journal (§1).
- **Time.** Schedules lay their points out in the local zone (from Qt's mapping of Windows' zone to an IANA name) and compare absolute instants, so daylight saving changes neither skip nor repeat a point. The previous fixed-offset clock put the next morning's times an hour off across a change.

## 10. First Windows run (October 2026)

HP ZBook Studio G5, Windows 11 25H2 (build 26200), Intel UHD 630 + NVIDIA Quadro P1000, one built-in AUO panel (`AUO24ED`, eDP, no DDC/CI, WMI with 101 levels), Python 3.15, PySide6 6.12.

- All structure layouts match the SDK; `QueryDisplayConfig`, both HDR queries, the SDR level query, dxva2 and GDI gamma calls all work, without leaking handles or GDI objects over hundreds of calls.
- The panel's DDC/CI handle (value 0, which is valid) answers every read with `0xC0262589`; invalid handles give `0xC026258C` on all four dxva2 calls, which the backend turns into "monitor gone". Detection no longer probes the panel over DDC/CI, which took it from about 670 ms to about 25 ms.
- Night light: the warmest ramp accepted is 3280 K (see §1). Software dimming is accepted down to 20%.
- The panel carries a Windows display calibration; it is kept under night light, given back on quitting, and since the gamma journal, after a crash too.
- Remember brightness, the brightness schedule (applied at start and at the next point to the second), suspend/resume handling (driven by real `WM_POWERBROADCAST` messages), `--quit` and Ctrl+Break were run against the real app.
- Not yet tried on Windows: an external DDC/CI monitor, a TV, an HDR display, hotplug, linked sliders.
