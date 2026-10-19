# Integration report: 0.1.0 beta 5

Branch `beta5-integration` merges `windows-reliability-and-ui` (the reliability, scheduling, identity and UI work, built on a version from before beta 3) into `main` at beta 4 (`863dbe6`). This report records what changed, the design decisions, how it was tested, what is known not to be verified, and what is deferred.

## Summary

- **Base architecture: the reliability branch.** One serialized monitor queue with time limits, stable display identity, each display's desired, commanded and observed state, an idempotent brightness scheduler, daylight-saving-safe times, the gamma crash journal, and the redesigned settings window.
- **Every beta 3 and 4 feature kept, ported onto that base.** Night light pausing for apps and fullscreen, the command line, diagnostics and privacy scrubbing, crash safe mode, the fullscreen focus guard, laptop brightness keys, the wake report, idle dimming, renaming and hiding displays, and the Windows Night Light notice.
- **No feature removed.** Nothing conflicted badly enough to need a decision from you; the few deliberate behavior changes are listed below.
- **Tests:** 504, all passing on Windows 11 (beta 4 had 299, the reliability branch 332). Every test from both sides is kept or ported under the same or an equivalent assertion (see [Tests](#tests)).
- **Real Windows checks on this machine:** 113 of 113 passed. They drove the real APIs: ctypes display configuration, DDC/CI error paths, WMI, gamma ramps, safe mode, the journal, and winsystem's queries. The app itself was also run end to end through the command line.
- **Bugs found and fixed while integrating:** 9, several of them present in beta 4 as well (see [Bugs found while integrating](#bugs-found-while-integrating)).

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
5. **The schedule wins over a remembered level only when a new point begins.** Within a point that was already applied, the level you chose stands across restarts and sleep. Beta 4's "a manual change pauses the schedule until its next step" behaves the same; it's now enforced by the scheduler's saved point id instead of `set_at` times.
6. **Sunrise/sunset fallback without a location: 06:00 and 18:00**, as in beta 4. The reliability branch used 07:00 and 19:00.
7. **Levels set by the schedule are remembered**, so a monitor reconnected in the evening comes back at the evening level.
8. **A reading that disagrees with a level accepted less than a second ago isn't taken as the truth** (see bug 3). The verification read 1.5 s later decides, so a display that really ignores changes is still reported (tested).
9. **The settings file stays at version 1.** New fields are additive and unknown fields are ignored. Downgrading to beta 4 keeps everything except the schedule (beta 4 doesn't read `points` and falls back to its default) and names saved under serial keys.

## Bugs found while integrating

Found by running the merged code against the real Windows APIs and the real app on this machine; each has a test that failed before the fix.

| # | Bug | In beta 4? | Fix |
|---|---|---|---|
| 1 | A gamma ramp Windows reset (lock screen, wake) was adopted as the new base whenever the display list happened to refresh during `verify()` (every 10 s), so night light stayed off until the temperature next changed | Yes | Refreshing the list re-applies tint, dimming and pauses to a display we had set (`windows.py`, 3 tests) |
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

- **504 tests, all passing** (`pytest`, Python 3.15, Windows 11 26200), run repeatedly. `ruff --select F` is clean.
- **Nothing was dropped to make the suite pass.** Every test name from both sides was checked. The ones without the same name were ported under an equivalent assertion:
  - beta 4's memory-schedule tests are now scheduler tests: current point once, user change until the next point, turning on applies at once, sleeping monitor, idle hold. Two new integration tests on the real `MonitorManager` cover "a new point wins over an old remembered level" and "the user's level stands within a point".
  - beta 4's step-schedule tests are the points tests, with the same cases (off/empty, latest point, overnight, sun offsets, fallback 06:00/18:00, polar).
  - `test_winevents.py` is ported into `test_winsystem.py`, including the real-window test. It now also sends display on/off and panel-brightness power settings with real payloads.
  - `test_brightness_schedule_steps` and `test_turned_off_neither_restores_nor_saves` keep their assertions, rewritten for points and keys.
- **Assertions changed on purpose:** the two winsystem classification rows (decision 2), the 06:00/18:00 fallback (decision 6), masked serials in the report, and the watcher's unlock row (decision 1).
- **Environment fix:** beta 4's `test_start_not_minimized_opens_settings` failed on this machine because it called the real fullscreen check while VS Code was fullscreen. The app fixture now stubs it; the tests that need it busy still set it.
- **New tests for ported behavior** that had none: fades and cutting a fade short, retry, refresh signals, idle source, `note_level`, settling readings, idle hold ordering, legacy keys in the flyout and settings, concurrent safe-mode markers and beta 4's marker, the real-window event test, diagnostics with the compatibility report, and the CLI's "can't be changed now".

## Verified on real Windows (this machine)

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
  - a WMI fade through the manager (5 steps in 0.5 s)
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

## Not verified (needs hardware or CI)

- **External DDC/CI monitors:** real writes, the VCP 0xD6 power state, time limits on a hanging monitor, safe mode skipping a real monitor after a crash, flicker.
- **Two or more displays:** linked sliders, hotplug, the laptop-key follower (needs an external display), per-display pausing on the other display.
- **TVs:** software dimming on a TV. **HDR:** setting the SDR level, night light skipping HDR, idle dimming through SDR.
- **A real sleep, hibernate, lock and screen-off cycle.** The messages and their handling were tested by sending them to the app's real window; the visible timing wasn't.
- **A real idle cycle**, and video keeping the screen on.
- **Windows Night Light turned on:** only "off" was read.
- **CI and packaging:** the GitHub Actions run on Python 3.13, the PyInstaller build (including bundling `tzdata`), the installer and its PATH option. PyInstaller isn't installed here and this machine has Python 3.15.
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
| Beta 4's Night Light, CLI, privacy, fullscreen focus, laptop keys, sleep recovery, idle dimming intact | ✅ Ported, tested, and the first four run on real Windows; laptop keys, sleep and idle are unit-tested and their parts verified (see above) |
| Scheduling, identity, compatibility, reliability, UI integrated | ✅ |
| Strongest safety behavior kept | ✅ Time limits and quarantine, safe mode (now concurrent), journal, suspend gating, never-black panel, never-brighten idle, plus 3 safety bugs fixed |
| Configuration and workflows compatible | ✅ Beta 4 settings, levels, names and markers load (tested); downgrading loses the schedule only |
| Tests not weakened | ✅ See [Tests](#tests) |
| Docs and screenshots match | ✅ README, windows-notes, windows-testing, testing-installer, screenshots |
| Unverified hardware and CI identified | ✅ See above |

## Next steps (for you)

1. Review and push the branch: `git push -u origin beta5-integration` (pushing isn't possible from this session's shell).
2. Run your visual checks of the settings pages and flyout, and if possible one external monitor and a real sleep/lock.
3. Merge into `main` and tag `v0.1.0-beta.5` to let CI build the release.
