# Integration report: 0.1.0 beta 5

Branch `beta5-integration` merges `windows-reliability-and-ui` (the reliability, scheduling, identity and UI work, built on a version from before beta 3) into `main` at beta 4 (`863dbe6`). This report summarizes the changes, design decisions, test results, unverified areas and deferred work.

## Summary

- **Base architecture: the reliability branch.** One serialized monitor queue with time limits, stable display identity, each display's desired, commanded and observed state, an idempotent brightness scheduler, daylight-saving-safe times, the gamma crash journal, and the redesigned settings window.
- **Every beta 3 and 4 feature kept, ported onto that base.** Night light pausing for apps and fullscreen, the command line, diagnostics and privacy scrubbing, crash safe mode, the fullscreen focus guard, laptop brightness keys, the wake report, idle dimming, renaming and hiding displays, and the Windows Night Light notice.
- **No feature removed.** No conflicts required feature removal; deliberate behavior changes are listed below.
- **Tests:** 567, all passing on Windows 11 (beta 4 had 299, the reliability branch 332). Every test from both sides is kept or ported under the same or an equivalent assertion (see [Tests](#tests)).
- **Real Windows checks:** 113 of 113 passed. They exercised the real APIs: ctypes display configuration, DDC/CI error paths, WMI, gamma ramps, safe mode, the journal, and winsystem's queries. The app was also run end to end through the command line.
- **Bugs found and fixed while integrating:** 9, several of them present in beta 4 as well (see [Bugs found while integrating](#bugs-found-while-integrating)).
- **Release round:** HDR brightness-lock detection with SDR fallback and a reason for every display's control; the CLI compared with Twinkle Tray's and Monitorian's (`--panel` and partial ids added); per-display pausing clearer and fixed for minimized apps; rename/hide end-to-end tests; a watcher audit (3 stuck states fixed); installer notes, author and GitHub link; roomier settings and Windows 11 style buttons (see [Release round](#release-round-010-beta-5)).
- **Final round:** an option to keep display calibrations; the command line, installer and GUI checked again; 6 more bugs fixed, including a crash that garbage collection on a monitor thread could cause (see [Final round before release](#final-round-before-release)).
- **Hardening pass:** 13 more bugs fixed, in concurrency, sleep, clashes with other software, files, logging and the CLI (see [Hardening pass](#hardening-pass)).

## What changed, by area

| Area | Beta 4 | Reliability branch | Beta 5 (merged) |
|---|---|---|---|
| Monitor calls | One worker, index-addressed | One coalescing queue, key-addressed, time limits, per-display quarantine | Queue kept; beta 4's **fades**, **retry (⟳)**, **refresh started/finished signals**, additive `refresh_later`, `note_level`, `recently_written` and `restore_brightness(source=IDLE)` ported into it |
| Display identity | Hardware id (`DEL41A2`) | Hardware id + EDID serial (`DEL41A2:7XKJ9F3`) | Serial keys. Names, hidden displays, remembered levels and safe-mode markers saved by beta 4 under the old key are still found (`AppConfig.display/hidden`, memory migration, `SafeMode.is_skipped(key, gdi)`) |
| Brightness schedule | "Steps" inside `BrightnessMemory` | Points and presets, `BrightnessScheduler`, applied once per point | Scheduler and presets; beta 4's `steps` load as a Custom schedule and are saved as `points`; the scheduler holds while idle-dimmed and skips hidden displays |
| Remembered brightness | v2 file (levels + set_at), hidden displays, idle hold | Keys, observed-state based | Both: v2 file, key-based, predicate for hidden displays (finds old keys), idle hold |
| System events | `winsystem.SystemEvents`: unlock, resume, display on, display change, panel brightness | `winevents.SystemEvents`: suspend, resume, display off/on, lock, clock, end session | One `winsystem.SystemEvents` with all of them, deduplicated; `winevents.py` removed |
| Gamma | Per-display override (pause), `verify()`, idle layer | Journal crash recovery, calibration kept | All of them, composed in one ramp |
| Safe mode | One marker at a time, GDI names | none | Concurrent markers (parallel probes), keys or GDI names, reads beta 4's marker format |
| Settings window | Tabs | Navigation pane and cards | Navigation pane; beta 4's controls placed: pausing (Night Light), laptop keys and "When I'm away" (Brightness), names and visibility (Displays), focus and Report a Problem (General) |
| Command line, diagnostics | Index-based | none | Key-based; refuses an explicitly chosen display that can't be changed now, and says why; diagnostics include the compatibility report |
| Privacy | User/computer/home/email scrubbed | none | Plus display serials shortened to their last four characters, and no time zone names (compatibility report and probe in UTC; the app logs only whether a zone is known) |

## Design decisions

1. **Unlocking re-applies night light; it no longer re-detects displays.** The reliability branch treated unlock as "displays on". Displays turning off and on has its own event now, so unlocking only needs the ramps put back.
2. **Displays turning off suspend DDC/CI** (`winsystem.classify` now reports `DISPLAY_OFF` and `LOCKED`; beta 4 returned nothing for them). The two classification test rows were changed on purpose.
3. **The wake report owns the schedule after waking:** 2, 5, 10, 20, 30, 60 and 120 s, stopping early. This replaces the reliability branch's 3–120 s list. A second wake within a minute still resumes the queue (tested).
4. **Idle dimming of a display in standby is dropped, not retried.** There's no point dimming a sleeping display, and coming back restores with `RESTORE`, which is retried.
5. **The schedule wins over a remembered level only when a new point begins.** Within a point that was already applied, a manually selected level remains in effect across restarts and sleep. Beta 4's "a manual change pauses the schedule until its next step" behaves the same; it's now enforced by the scheduler's saved point id instead of `set_at` times.
6. **Sunrise/sunset fallback without a location: 06:00 and 18:00**, as in beta 4. The reliability branch used 07:00 and 19:00.
7. **Levels set by the schedule are remembered**, so a monitor reconnected in the evening comes back at the evening level.
8. **A reading that disagrees with a level accepted less than a second ago isn't taken as the truth** (see bug 3). The verification read 1.5 s later decides, so a display that really ignores changes is still reported (tested).
9. **The settings file stays at version 1.** New fields are additive and unknown fields are ignored. Downgrading to beta 4 keeps everything except the schedule (beta 4 doesn't read `points` and falls back to its default) and names saved under serial keys.

## Bugs found while integrating

Found by running the merged code against the real Windows APIs and app on the Windows test machine; each has a test that failed before the fix.

| # | Bug | In beta 4? | Fix |
|---|---|---|---|
| 1 | A gamma ramp Windows reset (lock screen, wake) was adopted as the new base whenever the display list happened to refresh during `verify()` (every 10 s), so night light stayed off until the temperature next changed | Yes | Refreshing the list re-applies tint, dimming and pauses to a display previously tinted by Winlight (`windows.py`, 3 tests) |
| 2 | A listed or fullscreen app already in front at start didn't pause night light: the first check ran before the controller was connected, and later checks only report changes | Yes | The app hands the watcher's current state to the controller after connecting (test) |
| 3 | On a WMI laptop panel, a reading right after a write returns the old level (Windows eases the backlight), so after waking the slider showed the stale level, memory and the laptop-key follower saw it, and a false "ignores changes" warning was logged | Not visible: beta 4 had no observed state | `SETTLE_S` (2 tests, plus the real-panel check) |
| 4 | The compatibility report and `probe.txt` printed the local time zone name ("Eastern Daylight Time"), which tells where the user is; `probe.txt` goes into the diagnostics zip | Yes (probe) | UTC in both |
| 5 | Diagnostics said "via None" for a display without a recorded channel | n/a | Falls back to the control type |
| 6 | An empty beta 4 schedule was migrated as preset "custom" while getting the default points | n/a | Keeps the default preset |
| 7 | Out-of-range idle minutes or level in the settings file weren't clamped | Yes | `GeneralConfig.normalized()` |
| 8 | The sunrise/sunset offset field cut off "+30 min" | n/a | Sized from its longest value |
| 9 | The compatibility report listed "Build: from source" and "build: 26200" | n/a | Renamed to "Running" |

The backend investigation also found that WMI calls from the monitor thread are about 100× slower while the main thread sits in `QTest.qWait`, which holds the GIL. Under `app.exec()` they take about 7 ms, so this only affects the test harness, which now waits in an event loop.

## Tests

- **567 tests, all passing** (`pytest`, Python 3.15, Windows 11 26200), run repeatedly. `ruff --select F` is clean.
- **Nothing was dropped to make the suite pass.** Every test name from both sides was checked. The ones without the same name were ported under an equivalent assertion:
  - beta 4's memory-schedule tests are now scheduler tests: current point once, user change until the next point, turning on applies at once, sleeping monitor, idle hold. Two new integration tests on the real `MonitorManager` cover "a new point wins over an old remembered level" and "the user's level stands within a point".
  - beta 4's step-schedule tests are the points tests, with the same cases (off/empty, latest point, overnight, sun offsets, fallback 06:00/18:00, polar).
  - `test_winevents.py` is ported into `test_winsystem.py`, including the real-window test. It now also sends display on/off and panel-brightness power settings with real payloads.
  - `test_brightness_schedule_steps` and `test_turned_off_neither_restores_nor_saves` keep their assertions, rewritten for points and keys.
- **Assertions changed on purpose:** the two winsystem classification rows (decision 2), the 06:00/18:00 fallback (decision 6), masked serials in the report, and the watcher's unlock row (decision 1).
- **Environment fix:** beta 4's `test_start_not_minimized_opens_settings` failed because it called the real fullscreen check while VS Code was fullscreen. The app fixture now stubs it; tests that need a busy fullscreen state still set one.
- **New tests for ported behavior** that had none: fades and cutting a fade short, retry, refresh signals, idle source, `note_level`, settling readings, idle hold ordering, legacy keys in the flyout and settings, concurrent safe-mode markers and beta 4's marker, the real-window event test, diagnostics with the compatibility report, and the CLI's "can't be changed now".

## Verified on real Windows

Windows 11 Pro 26200, one laptop panel (AUO 24ED, WMI, a calibrated gamma ramp), no external monitor, HDR off.

- **Real-API checks (113/113)**, from a script outside the repo that drives the real APIs:
  - ctypes structures and `QueryDisplayConfig`
  - DDC/CI error codes and "monitor gone" on invalid handles
  - WMI read/write
  - night light, dimming and calibration handling on the real ramp
  - handle and GDI leak checks
  - beta 4's gamma `verify()`, per-display override and idle layer
  - the gamma journal after a real process kill
  - safe mode around real detection, including beta 4's marker
  - `idle_seconds`, `display_required`, `user_busy`, `foreground_app`, `running_apps` and the Windows Night Light registry read
  - a change through the manager to the WMI panel (one write, which Windows eases; see the hardening pass)
  - suspend holding writes, then resume and detection releasing them
  - the compatibility report's privacy
- **`--probe`** runs cleanly and puts the original ramp back.
- **The app, end to end:**
  - started from a beta 4 style settings file, whose schedule migrated and applied
  - `--list`, `--list --json`, `--set` (absolute and relative), `--get`, wrong display or value (exit code 1), `--night-light off/status/auto`, `--refresh`, `--diagnostics`
  - night light paused by a listed app (Notepad) and by a fullscreen window, including one already in front at start
  - the diagnostics zip checked for the user name, computer name, time zone, the custom name and the listed app's name: none present
  - `--quit` restored the calibration and saved levels, the applied schedule point and a clean journal
- **Screenshots** regenerated from the merged UI (`tools/screenshots.py`) and checked.
- The panel was left at 100% with its calibration restored.

## Hardening pass

A second pass before release, about finding and fixing bugs rather than adding features: concurrency and idempotency, sleep and hibernation, DDC/CI and ctypes, clashes with other software, dependencies, and logging. It was checked against the original goals of the reliability work and against the untouched backup of that work (`c3ab673`; the Desktop copy is identical, all 81 files compared).

### The original goals, checked

| Goal | Where it stands | Evidence |
|---|---|---|
| Brightness schedule presets | ✅ | `core/brightness_schedule.py` presets; settings tests |
| Compatible displays list with the channel (DDC/CI, WMI, SDR, software) | ✅ | `compat.py`; real report names "WMI (laptop panel)" |
| One serialized queue, bounded time limits and retries | ✅ | `monitors.py`, `hardware/calls.py`; DDC/CI transient retries in `win32.py` |
| Races between sliders, sleep/wake and discovery tested | ✅ | `test_monitor_service.py` (interleaved step by step), `test_chaos.py` (everything at once) |
| Idempotent scheduler, not a sequence of commands | ✅ | Saved point id; "applied once", restart, sleep-through and autumn-hour tests |
| Daylight saving, sleeping through a point, starting after one | ✅ | Scheduler tests; time zone changes while running now followed too |
| Trust: HDR, DDC/CI hangs, two schedules at once, termination mid-operation, a different display on the same port, a display vanishing mid-write | ✅ | Backend, service, app and chaos tests; the journal tested with a real process kill |
| Recovery: no stale state, no call to the wrong display, no indefinite blocking | ✅ | Chaos invariants; 4 fixes below (all three were possible before) |
| Disabled sliders with a short reason | ✅ | Flyout tests; reasons for sleep, standby, not responding, safe mode |
| Hardware compatibility report | ✅ | Settings > Displays, `--report`, diagnostics zip |
| ctypes calls reviewed | ✅ | Every native function in `win32.py` and `winsystem.py` declares its argument and return types (audited); struct layouts self-checked at start |
| DDC/CI probed conservatively | ✅ | No capability strings; laptop panels never probed over DDC/CI; probes only at detection, side by side under one deadline |
| Per-display failures isolated | ✅ | Stuck calls quarantine one display; chaos and hang tests |
| API adapters, runtime capability detection, native errors logged | ✅ | `DisplayApi`, one `_Control` per kind; `win32.capabilities()`; Windows error codes with their text |
| Sleep and hibernation handled | ✅ | Real messages sent to the running app, a real screen-off; see below |
| Desired, commanded and observed states | ✅ | `DisplayState`; settling readings no longer mistaken for the truth |
| Stable display identity | ✅ | EDID serial keys; older keys still found |
| Lessons from Monitorian and Twinkle Tray | ✅ | `docs/windows-notes.md` |
| Logging that isolates a problem | ✅ | Every backend call traced at debug level (what, which display, how long, the result); thread names; nothing uncaught is lost (below) |

### Bugs found and fixed

Each has a test that fails without the fix (checked by undoing it).

| Area | Bug | Fix |
|---|---|---|
| Concurrency | A call returning at the moment it was given up on could stay listed as stuck forever. Detection then never ran again, monitor handles were never released, and quitting thought a call was hung | Listed and taken off the list under one lock (`calls.py`) |
| Concurrency | The same race for a DDC/CI probe: its display could stay "not responding" until restart | `_probe_all` and the probe's late clean-up agree under one lock |
| Sleep | A read of several displays went on after sleep began, and could reach stale handles after waking | Multi-call work checks between calls; reads stop, a fade's level is sent after waking, a write's power check is followed by a check too |
| Robustness | A failed detection (Windows' display APIs erroring for a moment, e.g. right after waking) counted as "no displays": every display left the panel and came back as if reconnected | Keeps the displays found before, retries with growing gaps (1 s up to about a minute), logs the traceback once |
| Robustness | An unexpected error outside the work itself would end the monitor thread for the session without a word | The worker logs it and carries on |
| Time | Changing the time zone while running left both schedules on the old zone until restart | Re-read on Windows' clock-change message and every minute |
| Clashes | Another program setting gamma ramps (f.lux, GPU colour tools) was undone within 1.5 s, forever. The screen flickered between the two, two log lines were written per round, and a mild tint of theirs was tinted again | After 3 changes within 30 s, that display is left alone for 2 minutes, doubling up to 30 minutes if it keeps happening; one warning says why |
| Usability | A level set with the laptop's brightness keys was undone at the next start by an older remembered one (Twinkle Tray's classic "startup overwrites my level") | A level changed outside the app is no longer put back over; the schedule treats it like the user's own change |
| Usability | A change on a WMI laptop panel was faded in six steps on top of Windows' own easing, so the backlight bounced (Windows reported 85, 80, 85, 75, 70, 75…) | One write; Windows eases it |
| Files | Saving state failed with "Access is denied" while antivirus or the indexer held the file, losing the save | `files.write_atomic` retries briefly; every state file uses it |
| Logging | Exceptions nothing caught (in Qt slots, timers, threads) went to stderr, which the installed app doesn't have | Logged with their traceback |
| CLI | During sleep `--set` said "No display with brightness control matched" | Says no display can be changed right now, and why |
| CLI | An unexpected error while handling a request left the CLI waiting 10 s for "didn't answer" | Answered at once, and logged |

### Logging, as it reads now

```
INFO [MainThread] nightlight.hardware.winsystem: Windows: suspending
INFO [MainThread] nightlight.monitors: Display control paused (sleep or displays off)
INFO [MainThread] nightlight.hardware.winsystem: Windows: resumed
INFO [MainThread] nightlight.wake_report: Woke up; detecting displays again at 2, 5, 10, 20, 30, 60, 120 s
DEBUG [MainThread] nightlight.hardware.winsystem: Windows: resumed again 0.0 s later; the same wake
DEBUG [monitor-service] nightlight.monitors: detecting displays: 1 item(s) in 11 ms
DEBUG [monitor-service] nightlight.monitors: reading the brightness of Built-in display [AUO24ED]: 70 in 0 ms
INFO [MainThread] nightlight.wake_report: After waking +2 s: 1 of 1 answering (Built-in display 70%), detection took 0.0 s
WARNING [monitor-service] nightlight.hardware.calls: setting the brightness of DELL U2720Q didn't return within 5 s; carrying on without it
INFO [monitor-service] nightlight.monitors: setting the brightness of DELL U2720Q [DEL41A2:…J9F3]: left alone until that call returns; the other displays carry on
```

Thread names tell the UI, the queue and a stuck call apart, every backend call is traced at debug level, and decisions (paused, resumed, deferred and why, backed off from another program) are logged at info level. The notification window's handle and its registrations are logged at debug level, so a report shows whether Windows' messages could arrive at all. Privacy rules still apply: the file is scrubbed, serials are shortened, and no time zone or app names are written.

### Verified on the Windows test machine in this pass

- **Sleep and session, in the running app (source and the PyInstaller build):** suspend, resume (both messages merged into one wake), lock, unlock, display change, clock change and sign-out were sent to its real notification window from another process. The screen was also really turned off and on (`SC_MONITORPOWER`).
  - Nothing was sent while asleep.
  - Changes waited for a fresh detection after waking.
  - The wake report stopped early once the display was back.
  - Sign-out shut down cleanly in 0.3 s, removing the tint and marking the journal clean.
- **Observed on this laptop:** turning the screen off and on resets its gamma ramp to a flat one, dropping the ICC calibration (Windows does this without Winlight too). Winlight re-applies night light within 0.5 s over what Windows now shows, and leaves the calibration to Windows' loader. Keeping it ourselves, as DisplayCAL's loader does, is a possible later improvement.
- **Clash with another program:** a second process rewriting the ramp every 0.7 s. Before the fix: replaced 7 times in 10 s with 14 log lines. After: 2 times, then one warning, with no more fighting.
- **Idle dimming on the real panel:** dimmed to 20% through WMI, held, back to 100%, nothing remembered.
- **Laptop keys:** a WMI change from another program reached the app at once.
- **PyInstaller build and installer:**
  - It built, and `tzdata` and pywin32 were bundled; the installer compiled with Inno Setup.
  - `Winlight.exe` and `winlight-cli.exe` worked: list, set, get, exit codes 0/1/2, `--report` and `--diagnostics`.
  - Its log had no errors, and its diagnostics zip no personal details.
- **Real-API checks:** 113 of 113 again after the changes.
- **Dependencies:** `pip check` is clean; all code parses as Python 3.12, and no newer standard-library APIs are used. The `wmi` package warns about invalid escape sequences on Python 3.15, which a future Python will reject; CI uses 3.13. The Inno Setup installer was built, installed, upgraded, uninstalled and reinstalled on the Windows test machine.
- **CI robustness:** the chaos test allows for a stalled thread on a loaded runner. The real-window test skips on CI, with a reason, only if Windows refuses the registration (no desktop session).

## Final round before release

### Keeping color calibrations (new option)

Settings > Displays > **Keep each display's color calibration** (off by default). On this laptop, turning the screen off and on makes Windows reset the ramp to a flat one, dropping the ICC calibration until Windows' loader runs again (usually the next sign-in). With the option on, Winlight puts it back, as DisplayCAL's profile loader does.

The backend stays small by reusing what was already there:

- Each display's own non-flat ramp is remembered by display identity in the existing `gamma.json`, even with the option off, so turning it on later can still restore it.
- With the option on, a reset to flat doesn't replace the remembered calibration as the base. The display counts as "ours", so the existing 1.5 s check notices the reset and re-applies it, with night light, dimming or a pause on top as usual.
- Only resets to flat are undone. A new non-flat ramp (a re-calibration, or Windows' loader) is taken up and remembered.
- Clash back-off, HDR skipping and crash recovery apply as for night light.

Tested on the real panel:

- the screen really turned off and on with night light on (tint back over the calibration)
- the same with night light off (the calibration alone back)
- a calibration lost while Winlight was closed (restored at start)
- quitting (the calibration stays)

There are also 8 backend tests, plus settings and app tests.

### Command line

- **Why Twinkle Tray needs a shim and Winlight doesn't.** Twinkle Tray's command line is its Electron GUI executable. A GUI program returns to the shell at once and can't print to it, so its Chocolatey package adds a shim, a small console exe on PATH, with a switch to make it wait for output. `winlight-cli.exe` is a console executable of its own (checked: PE subsystem "console", and "GUI" for `Winlight.exe`), and the installer's PATH option gives it a short name.
- **Fixed: non-Latin display names crashed the CLI** when output was redirected or piped (`UnicodeEncodeError` with names like "显示器"), and the change had already been made when it crashed. This happens on Python before 3.15, which CI builds with. Such output is now UTF-8.
- **Fixed: usage mistakes exited with 2**, the code that means "Winlight isn't running". They now exit with 1.
- **Fixed: `winlight-cli` with no options started the whole tray app**, tied to the console window. It now prints its help.
- **Tested installed, from PATH in a fresh `cmd`:**
  - list (text and JSON to a file), get, and set by number, id and name
  - relative changes (`-10`, `+20`), clamping, and the laptop panel's 1% floor
  - errors (exit 1), night light on/toggle/auto, refresh, piping, `--report`
  - exit code 2 once the app quit
  - 12 calls at once, all answered
  - about 0.4 s per call

### Installer

- Built with Inno Setup.
- Installed silently with the PATH option, then upgraded over a running copy: the installer quit it first, replaced the files and didn't duplicate the PATH entry.
- Uninstalled: program, Start menu entry, PATH entry and uninstall record removed, settings kept.
- Reinstalled. Beta 5 is now installed on this PC, and the older beta was already gone before this round.

### Other apps' names

The app's own windows and tooltips no longer name other software; the repository's code comments and docs still credit them. Two places still mentioned other software:

- **The compatibility report's last line**, shown in Settings, listed "KDE Plasma" and "Simulated" from the Linux development backends. It now lists only the Windows ways in.
- **Tooltips:** the previously installed beta displayed older text. The current tooltips and per-display reasons were checked by parsing every string in the code.

Log messages still mention f.lux where it helps a bug report.

### GUI review

Every settings page was rendered in full, in light and dark, along with the panel, and checked:

- **Width:** pages with and without a scrollbar now have the same width, lined up with the notices above them (they differed by the scrollbar's width).
- **Dividers:** the lines between rows were uneven at 125% display scaling. Some came out twice as thick, and a first fix made some vanish. They're now a single device pixel each, verified pixel by pixel across a page in both themes.
- **Text:** "20 %" now reads "20%", like the rest.

### A crash found by repeating the test suite

The full suite crashed the Python process (an access violation) in about half of the runs. Tracing every garbage collection to a file showed the cause: the last collection before each crash ran on the `monitor-service` thread. Python collects reference cycles in whichever thread happens to allocate. When the garbage holds Qt objects, they are destroyed from the wrong thread, and Qt crashes.

The app has the same background threads, so this could crash it at random. pyqtgraph hit the same problem and solved it the same way: automatic collection is off, and a main-thread timer collects instead (`nightlight/gc_guard.py`). The young generation is collected when Python would, and everything once a minute. A test shows a busy worker thread no longer collects (200 times without the guard, 0 with it). The suite also collects after each test, on the main thread. Five full runs in a row passed (543 tests each) where about half had crashed before.

## Release round (0.1.0 Beta 5)

### HDR monitors: is brightness locked in HDR mode?

Read from both projects:

- **Monitorian** claims every display in HDR mode for SDR content brightness before it looks at DDC/CI.
- **Twinkle Tray 1.17** keeps DDC/CI and adds an SDR slider (optionally replacing the main one). Since 1.17.2 it shows only SDR where that is the only method; users report the SDR level resetting after sleep.

Winlight now does both. DDC/CI stays while the monitor takes it in HDR mode, since it changes the backlight. A change the monitor ignores (seen in the read-back 1.5 s later), or two it refuses, marks its brightness as locked. The next detection then switches it to SDR content brightness and gives it the level asked for. The lock lasts while HDR is on.

Every display's info now says how its brightness is controlled and why. Settings > Displays shows it on hover, and the compatibility report has a "How:" line.

Tests:

- backend: an HDR monitor on DDC/CI says why; locked, it moves to SDR and its handle is released; HDR off and on tries DDC/CI again; only HDR DDC/CI displays can be marked; with no SDR available, it keeps DDC/CI
- monitor manager: a monitor that ignores changes and one that refuses them both move to SDR with the user's level; one that takes changes stays on DDC/CI
- the report: the explanation appears, wrapped under its label

### Command line vs. what users asked of Twinkle Tray and Monitorian

| Requested | Twinkle Tray | Monitorian | Winlight |
|---|---|---|---|
| List displays | `--List` | `/get all` | `--list`, `--list --json` |
| Get one / all | via `--List` | `/get`, `/get all` | `--get`, `--display` |
| Set, absolute and relative | `--Set`, `--Offset` | `/set 20`, `/set +10` | `--set 40`, `--set +10` |
| By number / id / all | `--MonitorNum`, `--MonitorID` (partial), `--All` | id, `all` | number, id or **part of one (new)**, name, `all` |
| Show the result | `--Panel`, `--Overlay` | none | **`--panel` (new)**; no overlay yet |
| Raw VCP codes, contrast, input | `--VCP` | contrast, color preset, input (paid add-on) | left out on purpose |
| Night light | none | none | `--night-light` |

Raw VCP codes, contrast and input switching stay out on purpose: an arbitrary DDC/CI write can switch a monitor's input, turn it off or reset it, and testing these features requires external monitors. Every command was run against the installed app.

### Pausing night light per display

The "Pause night light" settings now say exactly what happens: only on the display showing the app, and night light fades back when the user switches away, minimizes or closes it. The panel and tray name the display ("Paused on DELL U2720Q for photoshop.exe"), using custom names, instead of "Paused for photoshop.exe".

- **Fixed:** a minimized window can stay Windows' foreground window, which kept night light paused. It no longer counts.
- **Tested on the real machine with Notepad on the list:** in front, paused; minimized, back; restored, paused; closed, back.

### Renaming and hiding displays

A new end-to-end test file (`test_display_prefs.py`, 8 tests) runs the whole app with one display renamed and one hidden. It checks:

- the panel
- the command line: by custom name, "all" skipping the hidden display, and the hidden one still reachable by name
- remembered levels following a renamed display
- the brightness schedule, idle dimming and the laptop-key follower leaving the hidden display alone
- changes taking effect without a restart
- reports never containing custom names
- displays hidden under a beta 4 key

### watcher.py audit

Three ways the app could get stuck for the rest of a session, now handled and tested:

- **A sleep announced but never taken** (cancelled or failed): no resume comes. If the clock checks keep running normally 90 s later, the watcher carries on as if woken.
- **A missed "display on" notification:** input while the displays are reported off means they are on.
- **A clock change mistaken for a wake:** Windows' clock-change message now re-baselines the check.

### Installer, about, look

- **Installer:** an information page before installing covers the beta disclaimer (no warranty, SmartScreen), what clashes and should be turned off (Windows Night Light, other blue-light filters, GPU color settings, other DDC/CI brightness tools, calibration loaders, Windows' automatic brightness), and recommendations (DDC/CI on, direct connections, TVs, HDR, reporting problems). Publisher information, the GitHub link and copyright also appear in the program files' version details.
- **About:** Settings > General has a creator credit and a View on GitHub button, along with the exact version ("0.1.0 Beta 5"). `__version__` is now `0.1.0-beta.5`, so diagnostics and reports name the build.
- **Settings:**
  - Rows, section headings and page titles have more room, close to Windows 11 Settings' spacing.
  - The bottom bar is redrawn in the Windows 11 style: an accent Apply, a quiet bordered Reset, and a text-only Restore defaults on the left. It is painted from the palette in both themes.
  - The window opens a little larger (960×760).
- **README:** formal headings without emojis, and a monochrome status scale (● Ready, ◐ In testing, ○ Planned). Screenshots regenerated.

## Not verified (needs hardware or CI)

- **External DDC/CI monitors:** real writes, the VCP 0xD6 power state, time limits on a hanging monitor, safe mode skipping a real monitor after a crash, flicker.
- **Two or more displays:** linked sliders, hotplug, the laptop-key follower (needs an external display), per-display pausing on the other display.
- **TVs:** software dimming on a TV. **HDR:** setting the SDR level, night light skipping HDR, idle dimming through SDR.
- **A real sleep, hibernate and lock cycle.** Sleep, wake, lock, unlock, display change, clock change and sign-out were sent to the running app's real window (source and installed builds), and the screen was really turned off and on; a real sleep or hibernation, and how it looks, weren't.
- **A real idle cycle**, and video keeping the screen on. Dimming and coming back were run on the real panel with a scripted idle time.
- **Windows Night Light turned on:** only "off" was read.
- **CI:** the GitHub Actions run used Python 3.13 and produced a build. On the test machine (Python 3.15), the PyInstaller build and Inno Setup installer were built and run, including installation with the PATH option, upgrade over a running copy, uninstall and reinstall.
- **A time zone change while running:** handled from Windows' clock-change message and a check each minute (tested with a stand-in zone); not tried by changing this PC's zone.
- **The GUI by eye:** layout at other scalings, keyboard navigation, the settings pages with real displays.

## Deferred

- Per-display brightness schedules, hotkeys with an on-screen indicator, reordering displays.
- Twinkle Tray's "normalize brightness" (a per-monitor range), contrast.
- Falling back to VCP 0x13/0x6B, and an update-rate setting, if testing finds monitors that need them.
- Preferring SDR over DDC/CI on HDR monitors that lock brightness in HDR mode (needs testing).
- Adding the real-API check script to `tools/`: it assumes this laptop's single panel and would need generalizing first.

## Release checklist

| Check | Status |
|---|---|
| Valuable features from both versions accounted for | ✅ All kept; none removed |
| Beta 4's Night Light, CLI, privacy, fullscreen focus, laptop keys, sleep recovery, idle dimming intact | ✅ Ported and tested, and all run on real Windows: sleep through injected messages and a real screen-off, laptop keys through a real WMI change, idle dimming with a scripted idle time |
| Scheduling, identity, compatibility, reliability, UI integrated | ✅ |
| Strongest safety behavior kept | ✅ Time limits and quarantine, safe mode (now concurrent), journal, suspend gating, never-black panel, never-brighten idle; plus the safety bugs fixed while integrating and in the hardening pass |
| Configuration and workflows compatible | ✅ Beta 4 settings, levels, names and markers load (tested); downgrading loses the schedule only |
| Tests not weakened | ✅ See [Tests](#tests) |
| Docs and screenshots match | ✅ README, windows-notes, windows-testing, testing-installer, screenshots |
| Unverified hardware and CI identified | ✅ See above |

## Release steps

At the time of this report, `main` is fast-forwarded to this work and tagged `v0.1.0-beta.5` locally. To publish, push both:

```
git push origin main
git push origin v0.1.0-beta.5
```

Pushing the tag starts the release workflow: tests on Python 3.13, the build, and a GitHub release titled "Winlight 0.1.0 Beta 5", marked Latest, with the installer and portable zip. Push `beta5-integration` separately to preserve the integration branch.
