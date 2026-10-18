# SPDX-License-Identifier: GPL-3.0-only
import argparse
import os
import logging
import logging.handlers
import signal
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from nightlight import APP_NAME, cli, config
from nightlight.privacy import ScrubbingFormatter
from nightlight.app import NightLightApp
from nightlight.hardware import BACKENDS, create_backends
from nightlight.single_instance import SingleInstance
from nightlight.ui import icons

log = logging.getLogger("nightlight")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Winlight: brightness and night light for Windows",
        epilog=cli.EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
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
    parser.add_argument("--quit", action="store_true", help="ask a running copy to quit (restoring normal colors), then exit")
    parser.add_argument("--allow-multiple", action="store_true", help="don't stop a second copy from starting")
    parser.add_argument("--diagnostics", action="store_true", help="save a diagnostics file for a bug report (Winlight must be running), then exit")
    cli.add_arguments(parser)
    args = parser.parse_args()

    if cli.requested(args) or args.diagnostics:
        # The command line: quick, no windows, and no log lines mixed into its output.
        from PySide6.QtCore import QCoreApplication

        _app = QCoreApplication(sys.argv)  # noqa: F841 (sockets need one)
        if args.diagnostics:
            args.list, args.diagnostics_request = False, True
        return cli.run(args)

    log_file = setup_logging(args.debug)

    # Let Ctrl+C in the terminal quit; Qt's event loop otherwise swallows it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    set_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(icons.night_icon())

    if args.probe:
        from nightlight import probe

        return probe.run(args.backend, log_file.parent if log_file else None)

    instance = SingleInstance()
    if args.quit:
        # Used by the installer and uninstaller: a clean exit resets the gamma ramps.
        running = instance.send(b"quit")
        log.info("Asked the running copy to quit" if running else "No running copy to quit")
        return 0
    if not args.allow_multiple and instance.notify_running():
        log.info("Night Light is already running; asked it to show its panel")
        return 0

    log.info("Log file: %s", log_file or "none")
    brightness, gamma = create_backends(args.backend)
    night_light = NightLightApp(app, brightness, gamma, args.config or config.default_path())
    instance.activated.connect(night_light.show_panel)
    instance.requestReceived.connect(night_light.handle_request)
    instance.quitRequested.connect(app.quit)
    app.aboutToQuit.connect(instance.close)
    exit_code = app.exec()
    if night_light.monitors.hung:
        # A display's DDC/CI call never returned and can't be interrupted;
        # don't wait for it (night light was already reset, settings saved).
        logging.shutdown()
        os._exit(exit_code)
    del night_light  # kept alive until here: Qt objects it owns must outlive the event loop
    return exit_code


APP_USER_MODEL_ID = "Winlight.Winlight"


def set_app_id() -> None:
    """Give the process its own identity on the Windows taskbar.

    Run from source, the process is python.exe/pythonw.exe, and Windows groups
    its windows under Python's taskbar button and icon. An explicit
    AppUserModelID makes them a separate app that shows the window icon set
    below. It must be set before any window exists.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        log.warning("Could not set the taskbar identity; windows may show Python's icon", exc_info=True)


def setup_logging(debug: bool) -> Path | None:
    """Log to the terminal, if there is one, and to a file in the user's log folder.

    The file makes reports possible from a packaged app, and an app started
    with pythonw (or a windowed PyInstaller build) has no terminal at all:
    sys.stderr is None there, so a terminal handler is only added when it exists.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    # Scrubbed: logs end up in public bug reports (see nightlight/privacy.py).
    formatter = ScrubbingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
    try:
        log_dir = config.log_dir()
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
