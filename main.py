import argparse
import logging
import signal
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from nightlight import config
from nightlight.app import NightLightApp
from nightlight.hardware import create_backends
from nightlight.ui import icons


def main() -> int:
    parser = argparse.ArgumentParser(description="Night Light for Windows")
    parser.add_argument(
        "--backend",
        choices=["auto", "real", "fake"],
        default="auto",
        help="hardware backend: real monitors, fake ones, or auto (real on Windows, fake elsewhere)",
    )
    parser.add_argument("--config", type=Path, help="settings file to use instead of the default")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Let Ctrl+C in the terminal quit; Qt's event loop otherwise swallows it.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setApplicationName("Night Light")
    app.setWindowIcon(icons.night_icon())
    brightness, gamma = create_backends(args.backend)
    _night_light = NightLightApp(app, brightness, gamma, args.config or config.default_path())
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
