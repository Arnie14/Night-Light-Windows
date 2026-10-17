import argparse
import logging
import sys

from PySide6.QtWidgets import QApplication

from nightlight.backends import DdcBackend, FakeBackend
from nightlight.ui.main_window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="Night Light for Windows")
    parser.add_argument("--fake", action="store_true", help="use fake monitors (no hardware needed)")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Night Light")
    backend = FakeBackend() if args.fake else DdcBackend()
    window = MainWindow(backend)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
