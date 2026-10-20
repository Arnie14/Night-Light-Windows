# SPDX-License-Identifier: GPL-3.0-only
"""Writes THIRD_PARTY_NOTICES.txt for the Windows app: each component the
build bundles, its version, license and source, followed by the full license
texts. packaging/build.py runs it, so the versions are the ones actually
built in. License texts come from each package's installed metadata, or from
packaging/licenses/ where a package ships none (the LGPL for Qt and PySide6,
which ship only Qt's commercial notice, and WMI's MIT notice).

    python tools/third_party_notices.py <output file>
"""

from __future__ import annotations

import importlib.metadata as metadata
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LICENSES = ROOT / "packaging" / "licenses"


@dataclass(frozen=True)
class Component:
    name: str
    distribution: str | None  # the installed package to read the version and license files from
    license: str
    source: str
    note: str = ""
    files: tuple[Path, ...] = ()  # license texts kept in the repository
    file_names: tuple[str, ...] = field(default=("LICENSE", "COPYING", "NOTICE"))  # to pick from the package's files
    within: tuple[str, ...] = ()  # only files whose path contains one of these (the parts the app bundles)


COMPONENTS = (
    Component(
        "Qt and Qt for Python (PySide6, shiboken6)", "PySide6", "LGPL-3.0-only",
        "https://code.qt.io and https://download.qt.io/official_releases/QtForPython/",
        "Used under the GNU Lesser General Public License v3.0. Qt and PySide6 are separate files in the app's folder "
        "(_internal\\PySide6 and _internal\\shiboken6): you may replace them with versions you build or modify yourself. "
        "Their source code is available from the addresses above; on request, the version used here is provided too.",
        files=(LICENSES / "LGPL-3.0.txt",), file_names=(),
    ),
    Component("Python", None, "PSF-2.0", "https://www.python.org",
              "The Python runtime, including the libraries its Windows build bundles (libffi, OpenSSL and others), "
              "under the terms in its license file below.",
              files=(Path(sys.base_prefix) / "LICENSE.txt",)),
    Component("PyInstaller bootloader", "pyinstaller", "GPL-2.0-or-later WITH Bootloader-exception",
              "https://pyinstaller.org",
              "The program that starts Winlight.exe and winlight-cli.exe. Its license allows distributing it with programs "
              "under any license (the bootloader exception, in the license text below)."),
    Component("astral", "astral", "Apache-2.0", "https://github.com/sffjunkie/astral"),
    Component("tzdata", "tzdata", "Apache-2.0", "https://github.com/python/tzdata",
              "The IANA time zone database (public domain), packaged for Python under the Apache License 2.0."),
    Component("platformdirs", "platformdirs", "MIT", "https://github.com/tox-dev/platformdirs"),
    Component("WMI", "WMI", "MIT", "https://github.com/tjguk/wmi", files=(LICENSES / "WMI-MIT.txt",), file_names=()),
    Component("pywin32", "pywin32", "PSF-2.0", "https://github.com/mhammond/pywin32",
              "Only its win32 and COM parts are bundled (pywintypes, pythoncom, win32com), for WMI.",
              file_names=("License.txt",), within=("dist-info/licenses/win32/", "dist-info/licenses/com/")),
)


def installed_license_texts(component: Component) -> list[tuple[str, str]]:
    """(file name, text) of the license files the installed package ships."""
    if component.distribution is None or not component.file_names:
        return []
    try:
        dist = metadata.distribution(component.distribution)
    except metadata.PackageNotFoundError:
        return []
    texts = []
    for file in dist.files or []:
        name = Path(str(file)).name
        if component.within and not any(part in str(file).replace("\\", "/") for part in component.within):
            continue
        if any(name.upper().startswith(prefix.upper()) for prefix in component.file_names):
            path = Path(dist.locate_file(file))
            if path.is_file():
                texts.append((str(file), path.read_text(encoding="utf-8", errors="replace")))
    return texts


def version_of(component: Component) -> str:
    if component.distribution is None:
        return sys.version.split()[0]
    try:
        return metadata.version(component.distribution)
    except metadata.PackageNotFoundError:
        return "not installed"


def render() -> str:
    rule = "=" * 78
    parts = [
        "WINLIGHT - THIRD-PARTY NOTICES",
        "",
        "Winlight is free software under the GNU General Public License v3.0 (LICENSE.txt).",
        "It includes the components below, each under its own license.",
        "",
    ]
    for component in COMPONENTS:
        parts += [f"- {component.name} {version_of(component)}: {component.license}", f"  {component.source}"]
    for component in COMPONENTS:
        parts += ["", rule, f"{component.name} {version_of(component)}", f"License: {component.license}", f"Source: {component.source}"]
        if component.note:
            parts += ["", component.note]
        texts = [(path.name, path.read_text(encoding="utf-8", errors="replace")) for path in component.files if path.is_file()]
        texts += installed_license_texts(component)
        if not texts:
            raise SystemExit(f"No license text found for {component.name}")
        seen = set()
        for name, text in texts:
            if text.strip() in seen:
                continue  # the same license in several of the package's parts
            seen.add(text.strip())
            parts += ["", f"--- {name} ---", "", text.strip()]
    return "\n".join(parts) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    Path(argv[1]).write_text(render(), encoding="utf-8")
    print("Third-party notices:", argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
