# SPDX-License-Identifier: GPL-3.0-only
import argparse
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

import platformdirs
from PySide6.QtWidgets import QApplication

from nightlight import config
from nightlight.app import NightLightApp
from nightlight.hardware import BACKENDS, create_backends
from nightlight.single_instance import SingleInstance
from nightlight.ui import icons

log = logging.getLogger("nightlight")


def main() -> int:
    parser = argparse.ArgumentParser(description="Night Light for Windows")
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default="auto",
        help="hardware backend (default auto: real on Windows, kde on a Plasma desktop, otherwise fake)",
    )
    parser.add_argument("--config", type=Path, help="settings file to use instead of the default")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="print a report of what Windows says about each display, then exit. Read-only, "
        "except that it briefly tints each display to find the warmest night light Windows allows",
    )
    parser.add_argument("--allow-multiple", action="store_true", help="don't stop a second copy from starting")
    args = parser.parse_args()

    log_file = setup_logging(args.debug)

    # Let Ctrl+C in the terminal quit; Qt's event loop otherwise swallows it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setApplicationName("Night Light")
    app.setWindowIcon(icons.night_icon())

    if args.probe:
        from nightlight import probe

        return probe.run(args.backend, log_file.parent if log_file else None)

    instance = SingleInstance()
    if not args.allow_multiple and instance.notify_running():
        log.info("Night Light is already running; asked it to show its panel")
        return 0

    log.info("Log file: %s", log_file or "none")
    brightness, gamma = create_backends(args.backend)
    night_light = NightLightApp(app, brightness, gamma, args.config or config.default_path())
    instance.activated.connect(night_light.show_panel)
    exit_code = app.exec()
    del night_light  # kept alive until here: Qt objects it owns must outlive the event loop
    return exit_code


def setup_logging(debug: bool) -> Path | None:
    """Log to the terminal, if there is one, and to a file in the user's log folder.

    The file makes reports possible from a packaged app, and an app started
    with pythonw (or a windowed PyInstaller build) has no terminal at all:
    sys.stderr is None there, so a terminal handler is only added when it exists.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    try:
        log_dir = Path(platformdirs.user_log_dir("night-light", appauthor=False))
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "night-light.log"
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        log.warning("Could not open a log file", exc_info=True)
        return None
    handler.setFormatter(formatter)
    root.addHandler(handler)
    return path


if __name__ == "__main__":
    sys.exit(main())
