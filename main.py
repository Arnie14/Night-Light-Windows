# SPDX-License-Identifier: GPL-3.0-only
import argparse
import os
import logging
import logging.handlers
import signal
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from nightlight import APP_NAME, cli, config
from nightlight.app import NightLightApp
from nightlight.gc_guard import MainThreadGarbageCollector
from nightlight.hardware import BACKENDS, create_backends
from nightlight.privacy import ScrubbingFormatter
from nightlight.single_instance import SingleInstance
from nightlight.ui import icons

log = logging.getLogger("nightlight")


class _Parser(argparse.ArgumentParser):
    """Usage mistakes exit with 1, like any failed command: 2 means Winlight
    isn't running (cli.py), which scripts may check for."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def is_cli_exe() -> bool:
    """Running as winlight-cli.exe, the console twin of Winlight.exe."""
    return bool(getattr(sys, "frozen", False)) and Path(sys.executable).stem.lower() == "winlight-cli"


def main() -> int:
    parser = _Parser(
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
    parser.add_argument(
        "--report",
        action="store_true",
        help="print the hardware compatibility report (each display and how its brightness is controlled), then exit. "
        "Changes nothing",
    )
    parser.add_argument("--quit", action="store_true", help="ask a running copy to quit (restoring normal colors), then exit")
    parser.add_argument("--allow-multiple", action="store_true", help="don't stop a second copy from starting")
    parser.add_argument("--diagnostics", action="store_true", help="save a diagnostics file for a bug report (Winlight must be running), then exit")
    cli.add_arguments(parser)
    args = parser.parse_args()

    if is_cli_exe() and not (cli.requested(args) or args.diagnostics or args.probe or args.report or args.quit):
        # The console twin given nothing to do: show how to use it, rather than
        # start the tray app tied to this console window.
        parser.print_help()
        return 0
    if cli.requested(args) or args.diagnostics:
        # The command line: quick, no windows, and no log lines mixed into its output.
        from PySide6.QtCore import QCoreApplication

        _app = QCoreApplication(sys.argv)  # noqa: F841 (sockets need one)
        if args.diagnostics:
            args.list, args.diagnostics_request = False, True
        return cli.run(args)

    setup_logging(args.debug)
    log_uncaught()

    # Ctrl+C before the app runs just stops; once it runs, see quit_on_signals.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    set_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(icons.night_icon())
    # Before any monitor thread starts: garbage holding Qt objects must never
    # be collected on one of them (nightlight/gc_guard.py).
    MainThreadGarbageCollector(app)

    if args.probe:
        from nightlight import probe

        return probe.run(args.backend, log_folder())
    if args.report:
        return print_report(args.backend, log_folder())

    instance = SingleInstance()
    if args.quit:
        # Used by the installer and uninstaller: a clean exit resets the gamma ramps.
        running = instance.send(b"quit")
        log.info("Asked the running copy to quit" if running else "No running copy to quit")
        return 0
    if not args.allow_multiple and instance.notify_running():
        log.info("Night Light is already running; asked it to show its panel")
        return 0

    # Only the copy that keeps running writes the log file: on Windows, two
    # processes appending to one file overwrite each other's lines, and
    # rotating it fails while another process has it open. Second launches
    # and --quit are logged by the running copy when they reach it.
    log_file = add_log_file()
    log.info("Log file: %s", log_file or "none")
    brightness, gamma = create_backends(args.backend)
    night_light = NightLightApp(app, brightness, gamma, args.config or config.default_path())
    instance.activated.connect(night_light.show_panel)
    instance.requestReceived.connect(night_light.handle_request)
    instance.quitRequested.connect(app.quit)
    app.aboutToQuit.connect(instance.close)
    quit_on_signals(app)
    exit_code = app.exec()
    if night_light.monitors.hung:
        # A display's DDC/CI call never returned and can't be interrupted;
        # don't wait for it (night light was already reset, settings saved).
        logging.shutdown()
        os._exit(exit_code)
    del night_light  # kept alive until here: Qt objects it owns must outlive the event loop
    return exit_code


def quit_on_signals(app: QApplication) -> None:
    """Quit cleanly on Ctrl+C, Ctrl+Break, closing the console or SIGTERM,
    so night light and dimming are undone rather than left on the screen.

    Python runs signal handlers only between bytecodes, and Qt's event loop
    is native code, so a timer gives Python a moment regularly."""

    def request_quit(signum, _frame) -> None:
        log.info("Received signal %d; quitting cleanly", signum)
        app.quit()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if (number := getattr(signal, name, None)) is not None:
            try:
                signal.signal(number, request_quit)
            except (OSError, ValueError):
                pass
    timer = QTimer(app)
    timer.timeout.connect(lambda: None)
    timer.start(250)


def print_report(backend: str, out_dir: Path | None) -> int:
    """`--report`: detect the displays once, print the compatibility report and save it next to the log."""
    from nightlight import compat
    from nightlight.monitors import MonitorService

    brightness, _gamma = create_backends(backend)
    service = MonitorService(brightness, lambda _event: None)
    service.request_detect(force=True)
    service.run_pending()  # one detection, with the usual time limits
    monitors = service.monitors
    report = compat.render(compat.entries(monitors), compat.system_summary())
    service.close()
    service.step()
    print(report)
    if out_dir is not None:
        path = out_dir / "compatibility.txt"
        try:
            path.write_text(report, encoding="utf-8")
            print(f"Saved to {path}")
        except OSError as exc:
            print(f"Could not save the report to {path}: {exc}")
    return 0


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


# Scrubbed: logs end up in public bug reports (see nightlight/privacy.py).
# The thread names (MainThread, monitor-service, monitor-calls-N) tell a stuck call from a busy UI.
LOG_FORMAT = ScrubbingFormatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s")


def log_uncaught() -> None:
    """Exceptions nothing caught, in Qt slots and timers or in a thread, go to
    the log with their traceback: the installed app has no console to print
    them to, so they would otherwise vanish without a trace."""

    def hook(kind, value, traceback) -> None:
        if issubclass(kind, KeyboardInterrupt):
            sys.__excepthook__(kind, value, traceback)
            return
        log.critical("Uncaught exception", exc_info=(kind, value, traceback))

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        log.critical("Uncaught exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = hook
    threading.excepthook = thread_hook


def setup_logging(debug: bool) -> None:
    """Log to the terminal, if there is one.

    An app started with pythonw (or a windowed PyInstaller build) has no
    terminal at all: sys.stderr is None there, so the handler is only added
    when it exists. The running copy also logs to a file (add_log_file).
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(LOG_FORMAT)
        root.addHandler(console)


def log_folder() -> Path | None:
    """The user's log folder (also where --probe saves its report), created if needed."""
    try:
        folder = config.log_dir()
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.warning("Could not create the log folder", exc_info=True)
        return None
    return folder


def add_log_file() -> Path | None:
    """Also log to a file in the user's log folder, which makes reports
    possible from a packaged app that has no terminal."""
    folder = log_folder()
    if folder is None:
        return None
    path = folder / "night-light.log"
    try:
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        log.warning("Could not open a log file", exc_info=True)
        return None
    handler.setFormatter(LOG_FORMAT)
    logging.getLogger().addHandler(handler)
    return path


if __name__ == "__main__":
    sys.exit(main())
