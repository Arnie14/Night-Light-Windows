# SPDX-License-Identifier: GPL-3.0-only
# PyInstaller spec for Winlight. Run through packaging/build.py, which makes
# the icon and version resource first:  python packaging/build.py
#
# One-folder, not one-file: a one-file exe unpacks itself to a new temp
# folder on every start, which is slower, trips antivirus heuristics more
# often, and would give the sign-in (Run key) entry a moving target.
# No UPX for the same antivirus reason.

import os
from pathlib import Path

root = Path(SPECPATH).parent  # noqa: F821 (defined by PyInstaller)
build = Path(os.environ.get("WINLIGHT_BUILD_DIR", root / "build"))

a = Analysis(  # noqa: F821
    [str(root / "main.py")],
    pathex=[str(root)],
    # The time zone table for picking a location (nightlight/core/locations.py).
    datas=[(str(root / "nightlight" / "data"), "nightlight/data")],
    # Imported inside functions, only on Windows: listed so they're never missed.
    hiddenimports=["wmi", "pythoncom", "pywintypes", "win32com.client"],
    # Linux-only backends and development tools.
    excludes=["tkinter", "monitorcontrol", "jeepney", "pytest", "_pytest"],
    noarchive=False,
)

# Qt pieces a widgets app never loads, which PySide6's hooks bundle anyway:
# the virtual keyboard and PDF plugins drag in the whole QML/Quick engine,
# and the software OpenGL renderer and shader compiler are for Qt Quick.
# Leaving them out takes the app from well over 100 MB to a fraction of it.
UNNEEDED = (
    "Qt6Qml", "Qt6Quick", "Qt6VirtualKeyboard", "Qt6Pdf",
    "qtvirtualkeyboardplugin", "qpdf.", "opengl32sw", "d3dcompiler",
    "/qml/", "/translations/",
)


def needed(entry) -> bool:
    name = entry[0].replace("\\", "/").lower()
    return not any(part.lower() in name for part in UNNEEDED)


a.binaries = [b for b in a.binaries if needed(b)]
a.datas = [d for d in a.datas if needed(d)]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Winlight",
    console=False,  # a tray app: no console window
    icon=str(build / "winlight.ico"),
    version=str(build / "version_info.txt"),
    upx=False,
)
coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    name="Winlight",
    upx=False,
)
