# SPDX-License-Identifier: GPL-3.0-only
"""Builds Winlight for Windows: the app folder, a portable zip and, when Inno
Setup is installed, the installer. Run on Windows (PyInstaller doesn't
cross-compile):

    pip install -e ".[build]"
    python packaging/build.py                      # version from nightlight/__init__.py
    python packaging/build.py --version 0.3.0-beta.1

Output goes to dist/: Winlight/ (the app), Winlight-<version>-portable.zip
and Winlight-<version>-setup.exe.

Versions are MAJOR.MINOR.PATCH, optionally followed by -alpha.N, -beta.N,
-rc.N or -dev. Windows' version resources take four numbers; the fourth
orders pre-releases before the release they lead to, so the file version
always increases: alpha.N is 1000 + N, beta.N 2000 + N, rc.N 3000 + N, the
release itself 9000 and a dev build 0. 0.3.0-beta.1 is file version
0.3.0.2001; the product version keeps the readable form.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
INNO_SETUP = [
    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    ROOT / "Inno Setup 6" / "ISCC.exe",  # a copy next to the project (ignored by git)
]

VERSION_INFO = """\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Anurag Jha'),
      StringStruct('FileDescription', 'Winlight: brightness and night light for every display'),
      StringStruct('FileVersion', '{file_version}'),
      StringStruct('InternalName', 'Winlight'),
      StringStruct('LegalCopyright', 'Copyright (C) 2026 Anurag Jha. GPL-3.0-only'),
      StringStruct('OriginalFilename', 'Winlight.exe'),
      StringStruct('ProductName', 'Winlight'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def app_version() -> str:
    text = (ROOT / "nightlight" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-(alpha|beta|rc|dev)(?:\.(\d+))?)?")
STAGE_BASE = {"alpha": 1000, "beta": 2000, "rc": 3000, "dev": 0, None: 9000}


def numeric(version: str) -> tuple[int, int, int, int]:
    """"0.3.0-beta.1" -> (0, 3, 0, 2001): see the module docstring."""
    match = VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"not a Winlight version: {version!r} (expected e.g. 0.3.0 or 0.3.0-beta.1)")
    major, minor, patch, stage, number = match.groups()
    if stage == "dev" and number:
        raise ValueError("dev builds take no number")
    if number is not None and not 1 <= int(number) < 1000:
        raise ValueError(f"pre-release numbers run from 1 to 999: {version!r}")
    return int(major), int(minor), int(patch), STAGE_BASE[stage] + int(number or 0)


def run(*command: str, **kwargs) -> None:
    print(">", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=ROOT, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="version to stamp, e.g. 0.1.0-beta.1 (default: nightlight.__version__)")
    parser.add_argument("--no-installer", action="store_true", help="skip the Inno Setup installer")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("Build on Windows: PyInstaller makes executables for the system it runs on.", file=sys.stderr)
        return 1

    base = app_version()
    version = (args.version or base).removeprefix("v")
    if not version.startswith(base):
        print(f"Version {version} doesn't match nightlight.__version__ ({base}); update one of them.", file=sys.stderr)
        return 1
    try:
        numbers = numeric(version)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1

    shutil.rmtree(BUILD, ignore_errors=True)
    shutil.rmtree(DIST, ignore_errors=True)
    BUILD.mkdir()
    run(sys.executable, str(ROOT / "packaging" / "make_icon.py"), str(BUILD / "winlight.ico"))
    (BUILD / "version_info.txt").write_text(VERSION_INFO.format(numbers=numbers, version=version, file_version=".".join(map(str, numbers))), encoding="utf-8")

    env = {**os.environ, "WINLIGHT_BUILD_DIR": str(BUILD)}
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(DIST), "--workpath", str(BUILD / "pyinstaller"),
        str(ROOT / "packaging" / "winlight.spec"), env=env)
    app = DIST / "Winlight"
    # Licenses travel with the app, in the installer and the portable zip alike.
    shutil.copyfile(ROOT / "LICENSE", app / "LICENSE.txt")
    run(sys.executable, str(ROOT / "tools" / "third_party_notices.py"), str(app / "THIRD_PARTY_NOTICES.txt"))
    check(app)

    # Smoke test: the frozen app starts, finds its modules and Qt, and exits.
    run(str(app / "Winlight.exe"), "--quit", timeout=60)
    # The command line answers (exit code 2 and a message: the app isn't running).
    result = subprocess.run([str(app / "winlight-cli.exe"), "--list"], capture_output=True, text=True, timeout=60)
    if result.returncode != 2 or "isn't running" not in result.stderr:
        raise SystemExit(f"winlight-cli.exe didn't answer as expected: {result.returncode} {result.stdout!r} {result.stderr!r}")

    portable = shutil.make_archive(str(DIST / f"Winlight-{version}-portable"), "zip", DIST, "Winlight")
    print("Portable:", portable)

    if not args.no_installer:
        iscc = shutil.which("iscc") or next((str(p) for p in INNO_SETUP if p.exists()), None)
        if iscc is None:
            print("Inno Setup 6 not found; skipping the installer (https://jrsoftware.org/isdl.php).", file=sys.stderr)
        else:
            run(iscc, f"/DAppVersion={version}", f"/DVersionNumbers={'.'.join(map(str, numbers))}",
                f"/DSourceDir={app}", f"/DOutputDir={DIST}", f"/DIconFile={BUILD / 'winlight.ico'}",
                str(ROOT / "packaging" / "winlight.iss"))
    return 0


def check(app: Path) -> None:
    """Fail the build if something the app needs at run time is missing."""
    internal = app / "_internal"
    needed = {
        "command line": app / "winlight-cli.exe",
        "license": app / "LICENSE.txt",
        "third-party notices": app / "THIRD_PARTY_NOTICES.txt",
        "time zone table": internal / "nightlight" / "data" / "timezones.csv",
        "Qt's Windows platform plugin": next(internal.rglob("qwindows.dll"), None),
    }
    missing = [name for name, path in needed.items() if path is None or not Path(path).exists()]
    if missing:
        raise SystemExit(f"The build is missing: {', '.join(missing)}")
    # The native Windows 11 look; without it Qt falls back to another style.
    if next(internal.rglob("qmodernwindowsstyle.dll"), None) is None:
        print("Warning: Qt's Windows 11 style plugin wasn't bundled; the app will look less native.", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
