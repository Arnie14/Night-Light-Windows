# SPDX-License-Identifier: GPL-3.0-only
"""Checks Winlight's Windows backends against the real Windows APIs and
hardware of this PC, beyond what the unit tests (which use fakes of the
Windows APIs) can show.

    python tools/hardware_checks.py

Quit Winlight first (Winlight.exe --quit): it re-applies its own colors and
would make the gamma checks fail. The script changes the screen's colors
and, on a laptop, the panel's brightness for a few seconds each, and puts
both back at the end, even if a check fails.

It tests one display: the built-in panel if there is one, otherwise the
first display. Checks that only apply to a built-in panel (WMI brightness,
a panel refusing DDC/CI) are skipped elsewhere, and gamma checks are skipped
on a display in HDR mode, where Windows ignores gamma ramps. DDC/CI is only
read, never written, so external monitors keep their settings.

Each line is PASS, FAIL, SKIP or INFO; the exit code is 1 if anything failed.
"""

from __future__ import annotations

import ctypes
import getpass
import json
import logging
import re
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nightlight.core import colortemp  # noqa: E402
from nightlight.core.colortemp import gamma_ramp, identity_ramp, ramps_match, whitepoint  # noqa: E402
from nightlight.hardware import win32, windows  # noqa: E402

RESULTS: list[tuple[str, str, str]] = []


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def messages(self, level=logging.WARNING):
        return [r.getMessage() for r in self.records if r.levelno >= level]

    def clear(self):
        self.records.clear()


CAP = Capture()
logging.getLogger().addHandler(CAP)
logging.getLogger().setLevel(logging.DEBUG)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(("PASS" if ok else "FAIL", name, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{f' -- {detail}' if detail else ''}", flush=True)


def note(name: str, detail: str) -> None:
    RESULTS.append(("INFO", name, detail))
    print(f"[INFO] {name} -- {detail}", flush=True)


def skip(name: str, why: str) -> None:
    RESULTS.append(("SKIP", name, why))
    print(f"[SKIP] {name} -- {why}", flush=True)


def section(title):
    def wrap(fn):
        def run():
            print(f"\n== {title} ==", flush=True)
            try:
                fn()
            except Exception:
                check(f"{title}: ran without an exception", False, traceback.format_exc())
        return run
    return wrap


_k32 = ctypes.WinDLL("kernel32")
_u32 = ctypes.WinDLL("user32")
_k32.GetCurrentProcess.restype = wintypes.HANDLE
_k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
_u32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def handle_count() -> int:
    n = wintypes.DWORD()
    _k32.GetProcessHandleCount(_k32.GetCurrentProcess(), ctypes.byref(n))
    return n.value


def gdi_count() -> int:
    return _u32.GetGuiResources(_k32.GetCurrentProcess(), 0)


TARGET: win32.DisplayTarget | None = None  # the display under test
DISPLAY: str | None = None  # its GDI name
PANEL = False  # it is a built-in panel
ORIGINAL_RAMP = None


def winlight_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Winlight.exe"], capture_output=True, text=True, check=False).stdout
    return "Winlight.exe" in out


# --- 1. Display configuration ---------------------------------------------


@section("1. QueryDisplayConfig / DisplayConfigGetDeviceInfo")
def display_config():
    global TARGET, DISPLAY, PANEL
    targets = win32.display_targets()
    check("display_targets() finds the displays", len(targets) >= 1, repr([t.gdi_name for t in targets]))
    TARGET = next((t for t in targets if t.internal), targets[0])
    DISPLAY, PANEL = TARGET.gdi_name, TARGET.internal
    note("display under test", f"{DISPLAY} {TARGET.friendly_name or '(no EDID name)'} via {TARGET.connector}, "
                               f"{'built-in panel' if PANEL else 'external display'}, HDR {'on' if TARGET.hdr else 'off'}")
    for t in targets:
        check(f"{t.gdi_name}: GDI name is \\\\.\\DISPLAYn", t.gdi_name.startswith("\\\\.\\DISPLAY"), t.gdi_name)
        check(f"{t.gdi_name}: device path parses to a PnP instance id", t.instance_id.startswith("DISPLAY\\"), t.instance_id)
        check(f"{t.gdi_name}: hardware id is an EDID manufacturer and product code", bool(re.fullmatch(r"[A-Z]{3}[0-9A-F]{4}", t.hardware_id)),
              t.hardware_id)
        check(f"{t.gdi_name}: adapter LUID and target id came back", t.adapter != (0, 0), f"adapter {t.adapter}, target {t.target_id}")

    # Raw HDR queries, to confirm both structure layouts are accepted by Windows.
    luid = win32.LUID(*TARGET.adapter)
    info2 = win32.DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO_2()
    win32._fill_header(info2.header, win32.DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO_2, info2, luid, TARGET.target_id)
    r2 = win32._device_info(info2.header)
    if r2 in (win32.ERROR_NOT_SUPPORTED, win32.ERROR_INVALID_PARAMETER):
        skip("ADVANCED_COLOR_INFO_2 query", "this Windows predates 24H2")
    else:
        check("ADVANCED_COLOR_INFO_2 (24H2+) query accepted", r2 == 0, f"result {r2}, activeColorMode {info2.activeColorMode}")
    info = win32.DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO()
    win32._fill_header(info.header, win32.DISPLAYCONFIG_DEVICE_INFO_GET_ADVANCED_COLOR_INFO, info, luid, TARGET.target_id)
    r1 = win32._device_info(info.header)
    check("ADVANCED_COLOR_INFO (older) query accepted", r1 == 0, f"result {r1}, value 0x{info.value:X} (bit0 supported, bit1 enabled)")

    names = win32.attached_display_names()
    check("EnumDisplayDevices fallback lists the same displays", set(names) == {t.gdi_name for t in targets}, repr(names))

    nits = win32.get_sdr_white_level(TARGET)
    check("SDR white level query works", nits is not None, f"{nits} nits")

    before = handle_count(), gdi_count()
    for _ in range(300):
        win32.display_targets()
    after = handle_count(), gdi_count()
    check("300 display queries leak no handles or GDI objects", after[0] - before[0] < 10 and after[1] == before[1],
          f"handles {before[0]}->{after[0]}, GDI {before[1]}->{after[1]}")


# --- 2. DDC/CI ---------------------------------------------------------------


@section("2. dxva2 / DDC/CI bindings and error handling (read-only)")
def ddc():
    check("dxva2.dll loaded", win32.ddc_available())
    monitors = win32.physical_monitors()
    note("physical monitors", repr([(m.gdi_name, m.description) for m in monitors]))
    mine = next((m for m in monitors if m.gdi_name == DISPLAY), None)
    if mine is None:
        skip("DDC/CI on the display under test", "Windows gives no physical monitor for it (virtual, remote or some USB displays)")
    else:
        CAP.clear()
        try:
            current, maximum = win32.get_vcp(mine.handle, win32.VCP_LUMINANCE)
            if PANEL:
                check("the built-in panel refuses DDC/CI (expected for laptop panels)", False, f"it answered {current}/{maximum}")
            else:
                check("the external monitor answers DDC/CI brightness", maximum > 0, f"{current}/{maximum}")
        except win32.DdcError as exc:
            check("DdcError carries the Windows code and text", exc.code != 0 and str(exc), f"0x{exc.code:08X} {exc}")
            check("not classified as 'monitor gone'", not exc.monitor_gone)
            if PANEL:
                check("the built-in panel refuses DDC/CI (expected for laptop panels)", True, f"0x{exc.code:08X}")
            else:
                note("the external monitor doesn't answer DDC/CI", "turn DDC/CI on in its menu, or it's connected through a dock that drops it")

    bogus = 0x7FFF1234
    for what, call in (
        ("GetVCPFeatureAndVCPFeatureReply", lambda: win32.get_vcp(bogus, win32.VCP_LUMINANCE)),
        ("GetMonitorBrightness", lambda: win32.get_monitor_brightness(bogus)),
    ):
        try:
            call()
            check(f"{what} on an invalid handle fails", False, "succeeded?!")
        except win32.DdcError as exc:
            check(f"{what} on an invalid handle -> monitor_gone", exc.monitor_gone, f"0x{exc.code:08X} {exc}")
    win32.destroy_physical_monitor(bogus)
    check("DestroyPhysicalMonitor on an invalid handle doesn't crash", True)
    for m in monitors:
        win32.destroy_physical_monitor(m.handle)

    before = handle_count()
    for _ in range(100):
        for pm in win32.physical_monitors():
            win32.destroy_physical_monitor(pm.handle)
    check("100 open/close cycles of physical monitors leak no handles", handle_count() - before < 10, f"handles {before}->{handle_count()}")


# --- 3. WMI and the real brightness backend ---------------------------------


@section("3. Brightness through WindowsBrightnessBackend")
def brightness():
    backend = windows.WindowsBrightnessBackend(windows.WindowsGammaBackend())
    started = time.perf_counter()
    infos = backend.list_monitors()
    note("detection", f"{len(infos)} display(s) in {(time.perf_counter() - started) * 1000:.0f} ms: "
                      + "; ".join(f"{i.name} [{i.key}] {i.control}/{i.channel}" for i in infos))
    index = next((n for n, i in enumerate(infos) if i.display == DISPLAY), None)
    check("the display under test is listed", index is not None)
    if index is None:
        return
    info = infos[index]
    if not (PANEL and info.channel == "wmi"):
        skip("WMI panel brightness", "the display under test isn't a built-in panel with WMI brightness")
        backend.close()
        return
    check("listed as the built-in display via WMI", info.supported and info.internal, f"{info.name} [{info.key}]")
    check("slider minimum is 1% (never black)", info.minimum == 1)
    control = backend._monitors[index].control
    decoy = ("DISPLAY\\ABC0000\\1&deadbeef&0&UID1_0", 50, [0, 100])
    matched = backend._panel("x", TARGET, [decoy, (control.instance, 0, control.levels)])
    check("WMI instance matched by PnP id (with a decoy panel present)", matched.control.instance == control.instance, control.instance)
    backend.get_brightness(index)  # the first call hands out the detection-time value
    original = backend.get_brightness(index)
    note("original panel brightness", f"{original}%")
    try:
        for value in (37, 1, 64):
            backend.set_brightness(index, value)
            time.sleep(0.3)
            got = backend.get_brightness(index)
            check(f"WMI set {value}% reads back", got == value, f"read {got}%")
    finally:
        backend.set_brightness(index, original)
        time.sleep(0.3)
        check("panel brightness restored", backend.get_brightness(index) == original, f"{backend.get_brightness(index)}%")
    before = handle_count(), gdi_count()
    for _ in range(10):
        backend.list_monitors()
    after = handle_count(), gdi_count()
    check("10 re-detections leak no handles or GDI objects", after[0] - before[0] < 20 and after[1] - before[1] < 3,
          f"handles {before[0]}->{after[0]}, GDI {before[1]}->{after[1]}")
    backend.close()


# --- 4. Gamma: night light ---------------------------------------------------


def current_ramp():
    return win32.get_gamma_ramp(DISPLAY)


def expect_tint(kelvin, base=None):
    return gamma_ramp(whitepoint(kelvin), 256, base)


def gamma_usable(what: str) -> bool:
    if TARGET is not None and TARGET.hdr:
        skip(what, "HDR is on for this display: Windows ignores gamma ramps")
        return False
    return True


@section("4. Gamma ramps: night light")
def gamma():
    global ORIGINAL_RAMP
    ORIGINAL_RAMP = current_ramp()
    if not gamma_usable("night light on the gamma ramp"):
        return
    base = ORIGINAL_RAMP
    note("starting ramp", "identity" if ramps_match(base, identity_ramp()) else "a calibration")
    windows.WindowsGammaBackend.DISPLAYS_TTL = 0.0  # re-list every call, as after a long run

    g = windows.WindowsGammaBackend()
    CAP.clear()
    g.apply(4500)
    check("4500 K applied and read back", ramps_match(current_ramp(), expect_tint(4500, base)))
    g.apply(5600)
    check("5600 K applied", ramps_match(current_ramp(), expect_tint(5600, base)))
    check("no warnings at normal temperatures", not CAP.messages(), repr(CAP.messages()))

    CAP.clear()
    g.apply(2000)
    d = g._displays[DISPLAY]
    shown = colortemp.achievable_kelvin(2000, d.warmest)
    check("2000 K: the warmest Windows accepts, or 2000 K itself", ramps_match(current_ramp(), expect_tint(shown, base)),
          f"settled at {shown} K" + (" (Windows' limit)" if d.warmest else ""))
    g.apply(4000)
    check("a cooler value after the limit is applied as asked", ramps_match(current_ramp(), expect_tint(4000, base)))

    g.reset()
    check("reset() puts the original ramp back", ramps_match(current_ramp(), base))
    g.apply(4500)
    g.release()
    check("release() (quit) puts the original ramp back", ramps_match(current_ramp(), base))

    # A leftover tint found at start (a crash, another tool): not compounded.
    win32.set_gamma_ramp(DISPLAY, expect_tint(4500))
    CAP.clear()
    g = windows.WindowsGammaBackend()
    g.apply(5000)
    check("a leftover tint is detected at start", any("already had a tinted gamma ramp" in w for w in CAP.messages()))
    check("night light goes over a neutral base, not over the leftover", ramps_match(current_ramp(), expect_tint(5000)))
    g.release()
    check("quitting after a leftover puts neutral back", ramps_match(current_ramp(), identity_ramp()))

    # A calibration, including one to a warm white point (blue below 90%), is kept.
    for label, curve in (("a calibration", ((1.08, 1.0), (1.0, 1.0), (0.94, 1.0))),
                         ("a warm-white calibration", ((1.05, 1.0), (1.0, 0.95), (0.95, 0.82)))):
        calibration = [[min(65535, round(65535 * (i / 255) ** g_ * peak)) for i in range(256)] for g_, peak in curve]
        if not win32.set_gamma_ramp(DISPLAY, calibration):
            skip(f"{label} kept as the base", "Windows refused to load it as a test ramp")
            continue
        calibration = current_ramp()  # as the driver stores it
        g = windows.WindowsGammaBackend()
        g.apply(4500)
        check(f"{label} is kept as the base and tinted", ramps_match(current_ramp(), expect_tint(4500, calibration)))
        g.release()
        check(f"quitting restores {label}, not a flat ramp", ramps_match(current_ramp(), calibration))
    win32.set_gamma_ramp(DISPLAY, base)

    before = handle_count(), gdi_count()
    for _ in range(300):
        win32.set_gamma_ramp(DISPLAY, base)
        win32.get_gamma_ramp(DISPLAY)
    after = handle_count(), gdi_count()
    check("600 gamma calls leak no handles or GDI objects", after[0] - before[0] < 10 and after[1] == before[1],
          f"handles {before[0]}->{after[0]}, GDI {before[1]}->{after[1]}")


# --- 5. Software dimming -----------------------------------------------------


@section("5. Software dimming through the gamma ramp")
def dimming():
    if not gamma_usable("software dimming"):
        return
    base = ORIGINAL_RAMP
    g = windows.WindowsGammaBackend()
    accepted = []
    CAP.clear()
    for level in range(100, 15, -10):
        got = g.set_dimming(DISPLAY, level)
        accepted.append((level, got, ramps_match(current_ramp(), colortemp.dimmed_ramp(max(level, 20), 256, base))))
    check("every level 100..20% accepted and read back", all(ok and got == max(lv, 20) for lv, got, ok in accepted), repr(accepted))
    g.set_dimming(DISPLAY, 20)
    g.apply(4500)
    want = gamma_ramp(whitepoint(4500), 256, colortemp.dimmed_ramp(20, 256, base))
    check("20% dimming + 4500 K composed into one ramp", ramps_match(current_ramp(), want))
    g.reset()
    check("night light off keeps the dimming", ramps_match(current_ramp(), colortemp.dimmed_ramp(20, 256, base)))
    win32.set_gamma_ramp(DISPLAY, base)
    g.displays_changed()
    check("displays_changed() re-applies the dimming", ramps_match(current_ramp(), colortemp.dimmed_ramp(20, 256, base)))
    g.release()
    check("release() (quit) undims", ramps_match(current_ramp(), base))


# --- 6. The --probe report ----------------------------------------------------


@section("6. The --probe report")
def reports():
    from nightlight import config
    from nightlight.support import probe

    note("log folder", str(config.log_dir()))
    with tempfile.TemporaryDirectory() as folder:
        probe.run("real", Path(folder))
        text = (Path(folder) / "probe.txt").read_text(encoding="utf-8")
    for heading in ("== System ==", "== Qt screens ==", "== Displays (QueryDisplayConfig) ==", "== DDC/CI monitors (dxva2, read-only) ==",
                    "== Built-in panels (WMI) ==", "== Gamma ramps (night light) ==", "== What the app sees"):
        check(f"probe.txt has {heading}", heading in text)
    check("probe.txt has no FAILED section", "FAILED" not in text)
    zone_names = {n for n in time.tzname if n}
    check("probe.txt names no local time zone (privacy)", not any(n in text for n in zone_names))
    check("probe.txt has no user name", getpass.getuser().lower() not in text.lower())
    check("the probe put the original ramp back", ORIGINAL_RAMP is None or ramps_match(current_ramp(), ORIGINAL_RAMP))


# --- 7. Pausing, idle dimming, crash recovery, safe mode, winsystem --------------


@section("7. Pausing, idle layer, crash recovery, safe mode and system queries")
def features():
    from nightlight.hardware import winsystem
    from nightlight.system.safe_mode import SafeMode

    if gamma_usable("pausing and the idle layer on the gamma ramp"):
        base = current_ramp()
        g = windows.WindowsGammaBackend()
        g.apply(4500)
        tinted = current_ramp()
        check("verify() with nothing changed does nothing", g.verify() is False)
        win32.set_gamma_ramp(DISPLAY, base)  # as the lock screen does
        check("verify() puts back a ramp Windows reset", g.verify() is True and ramps_match(current_ramp(), tinted))
        g.set_override(DISPLAY, 6500)  # paused for an app
        check("a paused display shows its own colors", ramps_match(current_ramp(), base))
        g.set_override(DISPLAY, None)
        g.set_idle_dim({DISPLAY: 30})
        check("the idle layer darkens the tinted ramp", current_ramp()[0][200] < tinted[0][200] * 0.85)
        g.set_idle_dim({})
        check("idle layer removed: exactly the tint again", ramps_match(current_ramp(), tinted))
        g.release()
        check("release() puts the original ramp back", ramps_match(current_ramp(), base))

        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / "gamma.json"
            code = (
                "import os; from pathlib import Path; from nightlight.hardware import windows\n"
                f"g = windows.WindowsGammaBackend(); g.set_journal(Path({str(journal)!r}))\n"
                "g.apply(3800)\n"
                "os._exit(9)\n"
            )
            died = subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=False).returncode
            check("a process killed mid-tint leaves the tint on screen", died == 9 and not ramps_match(current_ramp(), base))
            g = windows.WindowsGammaBackend()
            g.set_journal(journal)
            g.reset()
            check("the next start's journal puts the original ramp back", ramps_match(current_ramp(), base))
            g.release()

    with tempfile.TemporaryDirectory() as folder:
        safe = SafeMode(Path(folder))
        backend = windows.WindowsBrightnessBackend(windows.WindowsGammaBackend())
        backend.safe_mode = safe
        infos = backend.list_monitors()
        check("detection leaves no safe-mode marker behind", not (Path(folder) / "ddc-in-progress.json").exists() and not safe.skipped)
        external = next((i for i in infos if i.channel == "ddc"), None)
        if external is None:
            skip("safe mode skipping a DDC/CI monitor", "no DDC/CI monitor connected")
        else:
            safe.skipped.add(external.key)
            skipped = next(i for i in backend.list_monitors() if i.key == external.key)
            check("a skipped DDC/CI monitor is left alone until retried", not skipped.supported, skipped.reason)
            backend.retry_skipped()
            again = next(i for i in backend.list_monitors() if i.key == external.key)
            check("retrying brings it back", again.supported, repr(again))
        (Path(folder) / "ddc-in-progress.json").write_text(json.dumps({"display": DISPLAY, "what": "detecting"}))
        check("a marker left by a crash is read at start", SafeMode(Path(folder)).is_skipped("", DISPLAY))
        backend.close()

    idle = winsystem.idle_seconds()
    check("idle_seconds() reads GetLastInputInfo", 0 <= idle < 24 * 3600, f"{idle:.1f} s")
    check("display_required() answers", isinstance(winsystem.display_required(), bool))
    check("user_busy() answers", isinstance(winsystem.user_busy(), bool))
    app = winsystem.foreground_app()
    check("foreground_app() names an exe and its display", app is not None and app.exe.endswith(".exe") and app.display.startswith("\\\\.\\"),
          repr(app))
    running = winsystem.running_apps()
    check("running_apps() lists lowercase exe names", running and all(a == a.lower() and a.endswith(".exe") for a in running), f"{len(running)} apps")
    note("Windows Night Light now", repr(winsystem.windows_night_light_on()))


def main() -> int:
    if sys.platform != "win32":
        print("These checks drive the Windows APIs: run them on Windows.", file=sys.stderr)
        return 1
    if winlight_running():
        print("Winlight is running and would re-apply its own colors: quit it first (Winlight.exe --quit).", file=sys.stderr)
        return 1
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841 (Qt objects in the backends)
    try:
        for step in (display_config, ddc, brightness, gamma, dimming, reports, features):
            step()
    finally:
        if DISPLAY and ORIGINAL_RAMP is not None:
            win32.set_gamma_ramp(DISPLAY, ORIGINAL_RAMP)
            print("\nGamma ramp restored:", ramps_match(win32.get_gamma_ramp(DISPLAY), ORIGINAL_RAMP))
    counts = {kind: sum(r[0] == kind for r in RESULTS) for kind in ("PASS", "FAIL", "SKIP")}
    print(f"\n{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
