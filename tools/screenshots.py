# SPDX-License-Identifier: GPL-3.0-only
"""Renders the README screenshots: the flyout and the settings window, in
light and dark mode, with demo displays (one of each kind of brightness
control) and night light on. Nothing touches real displays or settings.

    python tools/screenshots.py [output folder, default docs/screenshots]

Run it on Windows for the native look the app has there.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from nightlight.brightness.monitors import MonitorManager  # noqa: E402
from nightlight.color.controller import NightLightController  # noqa: E402
from nightlight.config import Anchor, AppConfig, BrightnessScheduleConfig, GeneralConfig, Mode, NightLightConfig, SchedulePoint  # noqa: E402
from nightlight.hardware.brightness import FakeBrightnessBackend  # noqa: E402
from nightlight.hardware.gamma import FakeGammaBackend  # noqa: E402
from nightlight.ui import icons  # noqa: E402
from nightlight.ui.flyout import Flyout  # noqa: E402
from nightlight.ui.settings_window import SettingsWindow  # noqa: E402

EVENING = datetime(2026, 10, 17, 21, 30).astimezone()  # night light fully on
# Custom times, so the schedule text reads the same in every time zone.
NIGHT_LIGHT = NightLightConfig(
    enabled=True, mode=Mode.TIMES, night_start="20:00", day_start="07:00", night_temperature=3800,
    exempt_fullscreen=True, exempt_apps=("photoshop.exe",),
)


def settle(app: QApplication, seconds: float = 0.3) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def use_scheme(app: QApplication, dark: bool) -> None:
    """The system's light or dark look. Where the platform can't switch
    (offscreen rendering on Linux), a plain palette stands in."""
    hints = app.styleHints()
    hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
    settle(app, 0.2)
    if (hints.colorScheme() == Qt.ColorScheme.Dark) == dark:
        return
    palette = QPalette()
    colors = (
        {"Window": "#202020", "WindowText": "#f0f0f0", "Base": "#2b2b2b", "Text": "#f0f0f0", "Button": "#2d2d2d", "ButtonText": "#f0f0f0"}
        if dark
        else {"Window": "#f3f3f3", "WindowText": "#1a1a1a", "Base": "#ffffff", "Text": "#1a1a1a", "Button": "#fbfbfb", "ButtonText": "#1a1a1a"}
    )
    for role, color in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in ("WindowText", "Text", "ButtonText"):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor("#7a7a7a" if dark else "#a0a0a0"))
    app.setPalette(palette)


def main(out: Path) -> None:
    app = QApplication(sys.argv)
    app.setWindowIcon(icons.night_icon())
    out.mkdir(parents=True, exist_ok=True)
    monitors = MonitorManager(FakeBrightnessBackend(latency=0))
    controller = NightLightController(FakeGammaBackend(), NIGHT_LIGHT, clock=lambda: EVENING)
    monitors.refresh()
    settle(app, 1.0)
    config = AppConfig(
        night_light=NIGHT_LIGHT,
        general=GeneralConfig(start_with_windows=True),
        brightness_schedule=BrightnessScheduleConfig(
            True, "custom", (SchedulePoint(80, anchor=Anchor.SUNRISE, offset_minutes=30), SchedulePoint(40, "21:00"))
        ),
    )
    try:
        for dark in (False, True):
            use_scheme(app, dark)
            theme = "dark" if dark else "light"
            # Rendered with grab() without being shown, so nothing pops up on screen.
            flyout = Flyout(monitors, controller, as_window=True)
            flyout.adjustSize()
            settle(app)
            flyout.grab().save(str(out / f"flyout-{theme}.png"))
            flyout.deleteLater()
            settings = SettingsWindow(controller, config, monitors=monitors)
            settle(app)
            settings.grab().save(str(out / f"settings-{theme}.png"))
            settings.show_page(1)  # Brightness
            settle(app)
            settings.grab().save(str(out / f"settings-brightness-{theme}.png"))
            settings.deleteLater()
            print(f"Saved {theme} screenshots to {out}")
    finally:
        controller.shutdown()
        monitors.shutdown()


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshots")
