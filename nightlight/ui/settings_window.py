# SPDX-License-Identifier: GPL-3.0-only
"""The settings window, laid out like Windows 11's Settings app: pages in a
navigation pane, each a title and cards of settings.

- Night Light: modelled on KDE System Settings > Display & Monitor > Night
  Light, plus pausing it for apps and fullscreen games (f.lux's "disable for").
- Brightness: the brightness schedule, brightness options, dimming when idle.
- Displays: each display and how it is controlled, the compatibility report,
  and display names and visibility.
- General: startup and focus, and about.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTime, QTimeZone, Qt, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from nightlight import APP_NAME, AUTHOR, REPO_URL, display_version
from nightlight.config import (
    MAX_IDLE_MINUTES,
    MAX_SOLAR_TRANSITION_MINUTES,
    MAX_TRANSITION_MINUTES,
    AppConfig,
    GeneralConfig,
    Mode,
    NightLightConfig,
)
from nightlight.controller import NightLightController
from nightlight.core import locations
from nightlight.core.brightness_schedule import describe as describe_schedule
from nightlight.core.schedule import OverrideKind
from nightlight.ui import icons
from nightlight.ui.night_light_text import is_night_light_on, night_light_status, schedule_description
from nightlight.ui.schedule_editor import ScheduleEditor
from nightlight.ui.settings_parts import ActionBar, Card, NavButton, PageTitle, SectionLabel, SettingRow, StatusBadge, ToggleSwitch
from nightlight.ui.settings_widgets import AppList, DisplayList
from nightlight.ui.widgets import HEADING_WEIGHT, SecondaryLabel, Separator, TemperatureSlider, WindowsNightLightNotice, set_font

log = logging.getLogger(__name__)

# Like KDE, "Always off" is a choice in the list even though it is stored as
# enabled=False rather than as a mode.
ALWAYS_OFF = "off"
SWITCHING_TIMES = {
    ALWAYS_OFF: "Always off",
    Mode.ALWAYS: "Always on",
    Mode.LOCATION: "Sunrise and sunset at location",
    Mode.TIMES: "Custom times",
}
# The location choice for typing in latitude and longitude.
CUSTOM_LOCATION = ""
CUSTOM_LOCATION_LABEL = "Custom location"
# Pages keep this much space on the right besides the scrollbar's width,
# taken up when a page needs no scrollbar: card edges stay put between pages
# and line up with the notices above them.
PAGE_RIGHT = 32
# Controls on the right of the cards share widths, so they end on one line.
WIDE_FIELD = 300
NARROW_FIELD = 120
HEADER_ICON = 48
PREFERRED_SIZE = (960, 760)
# Kept free around the window on small screens; the title bar's height,
# which Qt's sizes leave out.
SCREEN_MARGIN = 16
FRAME_HEIGHT = 40
PAGES = ("Night Light", "Brightness", "Displays", "General")
THEME_CHOICES = {"system": "Follow Windows", "light": "Light", "dark": "Dark"}
# Sleep-friendly night light, from what sleep researchers advise: dim, warm
# light for the 2-3 hours before bed (Harvard Health, "Blue light has a dark
# side"; Chang et al., PNAS 2015, on screens delaying melatonin). 2700 K is
# an incandescent bulb's color, f.lux's default at night.
SLEEP_KELVIN = 2700
SLEEP_LEAD_HOURS = 3
SLEEP_ADVICE = (
    "Bright, blue-rich light in the evening delays melatonin, the hormone that makes you sleepy. Sleep researchers "
    f"suggest dim, warm light, about {SLEEP_KELVIN} K (an incandescent bulb) or warmer, for the 2 to 3 hours before bed, "
    "and less screen time near bedtime. General guidance, not medical advice"
)


class SettingsWindow(QWidget):
    applied = Signal(object)  # AppConfig

    def __init__(
        self,
        controller: NightLightController,
        config: AppConfig,
        can_autostart: bool = True,
        monitors=None,
        scheduler=None,
        compatibility: Callable[[], list] | None = None,
        report: Callable[[], str] | None = None,
        running_apps: Callable[[], list[str]] = lambda: [],
        report_problem: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._controller = controller
        self._monitors = monitors
        self._scheduler = scheduler
        self._compatibility = compatibility
        self._report = report
        self._running_apps = running_apps
        self._report_problem = report_problem
        self._display_prefs = config.displays
        self._saved = replace(config, night_light=controller.config)
        self._mode_while_off = self._saved.night_light.mode
        self.setWindowTitle(f"{APP_NAME} Settings")
        self.setWindowIcon(icons.night_icon())

        self._build_controls(can_autostart)
        self._stack = QStackedWidget()
        for page in (self._night_light_page(), self._brightness_page(), self._displays_page(), self._general_page()):
            self._stack.addWidget(_scrolling(page))

        # Shown while a temporary override from the tray/flyout is in effect.
        self.banner = Card()
        banner_row = QWidget()
        banner_layout = QHBoxLayout(banner_row)
        banner_layout.setContentsMargins(16, 10, 12, 10)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_button = QPushButton()
        self.banner_button.clicked.connect(controller.clear_override)
        banner_layout.addWidget(self.banner_text, 1)
        banner_layout.addWidget(self.banner_button)
        self.banner.add(banner_row)

        Button = QDialogButtonBox.StandardButton
        self.buttons = ActionBar()
        # Display names are the user's own, not settings with defaults: keep them.
        self.buttons.button(Button.RestoreDefaults).clicked.connect(lambda: self._load(replace(AppConfig(), displays=self.displays.prefs())))
        self.buttons.button(Button.Reset).clicked.connect(lambda: self._load(self._saved))
        self.buttons.button(Button.Apply).clicked.connect(self._apply)

        content = QVBoxLayout()
        content.setContentsMargins(0, 20, 0, 0)
        content.setSpacing(0)
        # Shown while Windows' own Night Light is on too: the tints stack.
        self.windows_night_light = WindowsNightLightNotice()
        notice_holder = QWidget()
        notice_layout = QVBoxLayout(notice_holder)
        notice_layout.setContentsMargins(28, 0, PAGE_RIGHT + _scrollbar_width(), 12)
        notice_layout.addWidget(self.windows_night_light)
        self._notice_holder = notice_holder
        notice_holder.setVisible(False)
        content.addWidget(notice_holder)
        banner_holder = QWidget()
        banner_holder_layout = QVBoxLayout(banner_holder)
        banner_holder_layout.setContentsMargins(28, 0, PAGE_RIGHT + _scrollbar_width(), 12)
        banner_holder_layout.addWidget(self.banner)
        self._banner_holder = banner_holder
        content.addWidget(banner_holder)
        content.addWidget(self._stack, 1)
        content.addWidget(Separator())
        footer = QVBoxLayout()
        footer.setContentsMargins(28, 14, PAGE_RIGHT + _scrollbar_width(), 16)
        footer.addWidget(self.buttons)
        content.addLayout(footer)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._navigation())
        layout.addLayout(content, 1)
        self.resize(*PREFERRED_SIZE)
        self.setMinimumSize(720, 480)
        self._placed = False

        for signal in (
            self.mode.currentIndexChanged,
            self.day_temperature.valueChanged,
            self.night_temperature.valueChanged,
            self.latitude.valueChanged,
            self.longitude.valueChanged,
            self.night_start.timeChanged,
            self.day_start.timeChanged,
            self.transition.valueChanged,
            self.solar_transition.currentIndexChanged,
            self.theme.currentIndexChanged,
            self.start_with_windows.toggled,
            self.start_minimized.toggled,
            self.remember_brightness.toggled,
            self.link_brightness.toggled,
            self.software_dimming.toggled,
            self.schedule.changed,
            self.respect_fullscreen.toggled,
            self.follow_laptop.toggled,
            self.idle_dim.toggled,
            self.idle_minutes.valueChanged,
            self.idle_level.valueChanged,
            self.exempt_fullscreen.toggled,
            self.pause_for_apps.toggled,
            self.exempt_apps.changed,
            self.idle_exempt_apps.changed,
            self.displays.changed,
            self.keep_calibration.toggled,
        ):
            signal.connect(self._on_changed)
        self.location.currentIndexChanged.connect(self._on_location_picked)
        for slider in (self.day_temperature, self.night_temperature):
            self._connect_preview(slider)
        controller.changed.connect(self._update_status)
        controller.changed.connect(self._update_header_icon)
        if scheduler is not None:
            scheduler.changed.connect(self._update_schedule_status)
        if monitors is not None:
            monitors.monitorsChanged.connect(lambda _monitors: self._update_displays())
            monitors.monitorsChanged.connect(self._refresh_display_names)
            if hasattr(monitors, "displayChanged"):
                monitors.displayChanged.connect(lambda _key: self._update_displays())
        for button in (self.copy_report, self.save_report):
            button.setEnabled(report is not None)
        self.refresh_displays.setEnabled(monitors is not None)

        self._update_header_icon()
        self._load(self._saved)
        self._update_displays()

    # Pages ------------------------------------------------------------------

    def page_titles(self) -> list[str]:
        return list(PAGES)

    def page_count(self) -> int:
        return self._stack.count()

    def show_page(self, index: int) -> None:
        self._nav[index].setChecked(True)
        self._stack.setCurrentIndex(index)

    def current_page(self) -> int:
        return self._stack.currentIndex()

    def _navigation(self) -> QWidget:
        pane = QWidget()
        pane.setFixedWidth(252)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(14, 26, 10, 16)
        layout.setSpacing(3)
        identity = QHBoxLayout()
        identity.setContentsMargins(8, 0, 4, 28)
        identity.setSpacing(12)
        self._header_icon = QLabel()
        self._header_icon.setFixedSize(HEADER_ICON, HEADER_ICON)
        name = QVBoxLayout()
        name.setSpacing(2)
        title = QLabel(APP_NAME)
        set_font(title, 1.55, HEADING_WEIGHT)
        name.addWidget(title)
        tagline = SecondaryLabel("Brightness and night light for every display", scale=0.95)
        tagline.setWordWrap(True)
        name.addWidget(tagline)
        identity.addWidget(self._header_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        identity.addLayout(name, 1)
        layout.addLayout(identity)
        group = QButtonGroup(self)
        self._nav: list[NavButton] = []
        for index, (text, glyph) in enumerate(zip(PAGES, (icons.moon_glyph, icons.sun_glyph, icons.display_glyph, icons.gear_glyph))):
            button = NavButton(text, glyph)
            button.clicked.connect(lambda _checked=False, i=index: self._stack.setCurrentIndex(i))
            group.addButton(button)
            layout.addWidget(button)
            self._nav.append(button)
        self._nav[0].setChecked(True)
        layout.addStretch(1)
        return pane

    def _build_controls(self, can_autostart: bool) -> None:
        # Night Light
        self.mode = QComboBox()
        for key, label in SWITCHING_TIMES.items():
            self.mode.addItem(label, key)
        self.location = _location_combo()
        self.use_system_zone = QPushButton("Detect", toolTip="Use the place of this PC's time zone")
        self.use_system_zone.clicked.connect(self._pick_system_zone)
        self._location_row = QWidget()
        location_layout = QHBoxLayout(self._location_row)
        location_layout.setContentsMargins(0, 0, 0, 0)
        location_layout.setSpacing(8)
        location_layout.addWidget(self.location, 1)
        location_layout.addWidget(self.use_system_zone)
        self.latitude = QDoubleSpinBox(minimum=-90, maximum=90, decimals=4, suffix="°")
        self.longitude = QDoubleSpinBox(minimum=-180, maximum=180, decimals=4, suffix="°")
        self.night_start = QTimeEdit(displayFormat="HH:mm")
        self.day_start = QTimeEdit(displayFormat="HH:mm")
        self.transition = QSpinBox(minimum=1, maximum=MAX_TRANSITION_MINUTES, suffix=" min")
        # Sunrise and sunset: twilight's own length, none, or a chosen length.
        self.solar_transition = QComboBox()
        self.solar_transition.addItem("Follow twilight (about 30 min)", -1)
        self.solar_transition.addItem("None (switch at once)", 0)
        for minutes in range(5, MAX_SOLAR_TRANSITION_MINUTES + 1, 5):
            self.solar_transition.addItem(f"{minutes} min", minutes)
        self.solar_transition.setMaxVisibleItems(12)
        self.bedtime = QTimeEdit(displayFormat="HH:mm")
        self.bedtime.setTime(QTime(23, 0))
        self.use_sleep_settings = QPushButton("Use these")
        self.use_sleep_settings.setToolTip(f"Night light at {SLEEP_KELVIN} K or warmer; with custom times, "
                                           f"starting {SLEEP_LEAD_HOURS} hours before your bedtime. Apply to keep it")
        self.use_sleep_settings.clicked.connect(self._use_sleep_settings)
        self.day_temperature = TemperatureSlider()
        self.night_temperature = TemperatureSlider()
        for field in (self.mode, self._location_row, self.solar_transition):
            field.setFixedWidth(WIDE_FIELD)
        for slider in (self.day_temperature, self.night_temperature):
            slider.setFixedWidth(WIDE_FIELD + 40)
        for field in (self.latitude, self.longitude, self.night_start, self.day_start, self.transition, self.bedtime):
            field.setFixedWidth(NARROW_FIELD)
        self.schedule_info = SecondaryLabel(scale=0.92)
        self.schedule_info.setWordWrap(True)
        self.exempt_fullscreen = ToggleSwitch("Pause night light for any fullscreen app")
        self.pause_for_apps = ToggleSwitch("Pause night light for chosen apps")
        self.exempt_apps = AppList(self._running_apps)

        # Brightness
        self.schedule = ScheduleEditor()
        self.remember_brightness = ToggleSwitch("Remember each display's brightness")
        self.link_brightness = ToggleSwitch("Change all displays together")
        self.follow_laptop = ToggleSwitch("External displays follow the laptop's brightness keys")
        self.software_dimming = ToggleSwitch("Dim displays without brightness control")
        self.idle_dim = ToggleSwitch("Dim displays when I'm away")
        self.idle_minutes = QSpinBox(minimum=1, maximum=MAX_IDLE_MINUTES, suffix=" min")
        self.idle_level = QSpinBox(minimum=0, maximum=100, singleStep=5, suffix="%")
        for field in (self.idle_minutes, self.idle_level):
            field.setFixedWidth(NARROW_FIELD)
        self.idle_exempt_apps = AppList(self._running_apps, "Never dim while this app is in front")

        # General
        self.start_with_windows = ToggleSwitch(f"Start {APP_NAME} when I sign in")
        self.start_minimized = ToggleSwitch("Start minimized in the system tray")
        self.respect_fullscreen = ToggleSwitch("Never open over fullscreen games, videos or presentations")
        self.theme = QComboBox()
        for key, label in THEME_CHOICES.items():
            self.theme.addItem(label, key)
        self.theme.setFixedWidth(NARROW_FIELD + 60)
        if not can_autostart:
            self.start_with_windows.setEnabled(False)
            self.start_with_windows.setToolTip("Not available on this system")

        # Displays
        self.displays = DisplayList()
        self.keep_calibration = ToggleSwitch("Keep each display's color calibration")
        self.refresh_displays = QPushButton("Detect again")
        self.copy_report = QPushButton("Copy report")
        self.save_report = QPushButton("Save report…")
        self.refresh_displays.clicked.connect(self._detect_displays)
        self.copy_report.clicked.connect(self._copy_report)
        self.save_report.clicked.connect(self._save_report)

    def _night_light_page(self) -> QWidget:
        page, layout = _page("Night Light", "The blue light filter makes the colors on the screen warmer.")
        self._night_card = Card()
        self._field_rows: dict[QWidget, QWidget] = {}
        for field, title, description in (
            (self.mode, "Switching times", "When night light comes on"),
            (self._location_row, "Location", "Where sunrise and sunset are taken from"),
            (self.latitude, "Latitude", ""),
            (self.longitude, "Longitude", ""),
            (self.night_start, "Begin night light at", ""),
            (self.day_start, "Begin day light at", ""),
            (self.transition, "Transition duration", "How long the change from day to night takes"),
            (self.solar_transition, "Transition duration", "Night light starts warming at sunset, and is back to day light "
             "by sunrise. Twilight's length depends on the place and the season"),
        ):
            self._field_rows[field] = self._night_card.add(SettingRow(title, description, field))
        layout.addWidget(self._night_card)
        layout.addWidget(SectionLabel("Color temperature"))
        self._temperature_card = Card()
        self._field_rows[self.day_temperature] = self._temperature_card.add(SettingRow("Day light", "Neutral is 6500 K", self.day_temperature))
        self._temperature_card.add(SettingRow("Night light", "Drag to preview it", self.night_temperature))
        layout.addWidget(self._temperature_card)
        self.schedule_info.setContentsMargins(4, 10, 4, 0)
        layout.addWidget(self.schedule_info)
        layout.addWidget(SectionLabel("For better sleep"))
        self._sleep_card = Card()
        self._sleep_card.add(SettingRow("Sleep-friendly night light", SLEEP_ADVICE, self.use_sleep_settings))
        self._bedtime_row = self._sleep_card.add(SettingRow("My bedtime", f"With custom times, night light starts "
                                                            f"{SLEEP_LEAD_HOURS} hours before it", self.bedtime))
        layout.addWidget(self._sleep_card)
        layout.addWidget(SectionLabel("Pause night light"))
        pause = Card()
        pause.add(SettingRow("Pause for any fullscreen app", "Whenever any app fills a display (a game, a video, a browser in "
                             "fullscreen), night light pauses on that display until the app leaves fullscreen", self.exempt_fullscreen))
        pause.add(SettingRow("Pause for chosen apps", "Only for the apps in this list: night light pauses on the display showing "
                             "one while it's in front, fullscreen or not, and fades back when you switch away, minimize or close it. "
                             "Other apps keep night light", self.pause_for_apps, below=self.exempt_apps))
        layout.addWidget(pause)
        pause_hint = SecondaryLabel("The two are separate: turn on either or both. Other displays always keep night light.", scale=0.9)
        pause_hint.setWordWrap(True)
        pause_hint.setContentsMargins(4, 8, 4, 0)
        layout.addWidget(pause_hint)
        return page

    def _brightness_page(self) -> QWidget:
        page, layout = _page("Brightness", "Brightness by time of day, and how it is kept.")
        layout.addWidget(self.schedule)
        layout.addWidget(SectionLabel("Options"))
        options = Card()
        options.add(SettingRow("Remember each display's brightness", "Put back after starting, sleep and reconnecting a display",
                               self.remember_brightness))
        options.add(SettingRow("Change all displays together", "Moving one slider moves every display", self.link_brightness))
        options.add(SettingRow("Follow the laptop's brightness keys",
                               "When the built-in display's brightness changes, external displays are set to the same level",
                               self.follow_laptop))
        options.add(SettingRow("Dim displays without brightness control",
                               "TVs and monitors without DDC/CI are dimmed through their colors; saves no power", self.software_dimming))
        layout.addWidget(options)
        layout.addWidget(SectionLabel("When I'm away"))
        away = Card()
        away.add(SettingRow("Dim displays when I'm away", "Any input brings them back. Not while an app keeps the screen on "
                            "(videos, calls, presentations) or a game runs exclusive fullscreen", self.idle_dim))
        away.add(SettingRow("After", "Without keyboard or mouse input", self.idle_minutes))
        away.add(SettingRow("Dim to", "Never brighter than they were", self.idle_level))
        away.add(SettingRow("Never dim while these apps are in front", "For apps that don't keep the screen on by themselves, "
                            "such as some players, call or music apps", below=self.idle_exempt_apps))
        layout.addWidget(away)
        return page

    def _displays_page(self) -> QWidget:
        page, layout = _page("Displays", "Each display, and how Winlight controls its brightness.")
        self._displays_card = Card()
        self._displays_layout = layout
        self._shown_entries: list | None = None
        layout.addWidget(self._displays_card)
        self.display_rows: list[SettingRow] = []
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 10, 0, 0)
        actions.addWidget(self.refresh_displays)
        actions.addStretch(1)
        actions.addWidget(self.copy_report)
        actions.addWidget(self.save_report)
        layout.addLayout(actions)
        self.displays_hint = SecondaryLabel("Hover over a display for details. The report lists everything known about each display, "
                                            "to attach to a bug report.", scale=0.9)
        self.displays_hint.setWordWrap(True)
        self.displays_hint.setContentsMargins(2, 8, 2, 0)
        layout.addWidget(self.displays_hint)
        layout.addWidget(SectionLabel("Names and visibility"))
        names = Card()
        names.add(SettingRow("Rename or hide displays", "A name replaces the display's own in the panel. "
                             "Hidden displays are left out of the panel and never changed", below=self.displays))
        layout.addWidget(names)
        layout.addWidget(SectionLabel("Color calibration"))
        calibration = Card()
        calibration.add(SettingRow("Keep each display's color calibration",
                                   "Put a calibration back when Windows drops it, for example after the screen turns off",
                                   self.keep_calibration))
        layout.addWidget(calibration)
        return page

    def _general_page(self) -> QWidget:
        page, layout = _page("General", "Starting with Windows, focus, and about this app.")
        startup = Card()
        startup.add(SettingRow(f"Start {APP_NAME} when I sign in", "Opens in the system tray", self.start_with_windows))
        startup.add(SettingRow("Start minimized", "Only the tray icon at start, without this window", self.start_minimized))
        layout.addWidget(startup)
        layout.addWidget(SectionLabel("Appearance"))
        appearance = Card()
        appearance.add(SettingRow("Theme", f"Light or dark for {APP_NAME}'s windows, or the same as Windows", self.theme))
        layout.addWidget(appearance)
        layout.addWidget(SectionLabel("Focus"))
        focus = Card()
        focus.add(SettingRow("Never open over fullscreen apps", f"{APP_NAME} won't open its windows on its own while a game, "
                             "video or presentation is fullscreen", self.respect_fullscreen))
        layout.addWidget(focus)
        layout.addWidget(SectionLabel("About"))
        about = Card()
        about.add(SettingRow(f"{APP_NAME} {display_version()}", "Brightness and night light for every display. "
                             "Free software under the GPL-3.0 license", icon=icons.night_icon()))
        self.github = QPushButton("View on GitHub")
        self.github.setToolTip(REPO_URL)
        self.github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        about.add(SettingRow(f"Created by {AUTHOR}", "Source code, releases and the issue tracker", self.github))
        if self._report_problem is not None:
            report = QPushButton("Report a Problem…")
            report.setToolTip("Saves logs and settings, without personal details, to a zip on the Desktop")
            report.clicked.connect(self._report_problem)
            about.add(SettingRow("Report a problem", "Collects what a bug report needs into one zip", report))
        logs = QPushButton("Open log folder")
        logs.clicked.connect(_open_log_folder)
        about.add(SettingRow("Logs", "Where the log, probe.txt and compatibility.txt are saved", logs))
        layout.addWidget(about)
        return page

    # Settings ------------------------------------------------------------------

    def current_config(self) -> AppConfig:
        choice = self.mode.currentData()
        enabled = choice != ALWAYS_OFF
        night_light = NightLightConfig(
            enabled=enabled,
            # Qt hands enum data back as a plain str; keep the last mode while off.
            mode=Mode(choice) if enabled else self._mode_while_off,
            day_temperature=self.day_temperature.value(),
            night_temperature=self.night_temperature.value(),
            transition_minutes=self.transition.value(),
            night_start=self.night_start.time().toString("HH:mm"),
            day_start=self.day_start.time().toString("HH:mm"),
            latitude=self.latitude.value(),
            longitude=self.longitude.value(),
            timezone=self.location.currentData() or CUSTOM_LOCATION,
            exempt_apps=self.exempt_apps.apps(),
            exempt_fullscreen=self.exempt_fullscreen.isChecked(),
            pause_for_apps=self.pause_for_apps.isChecked(),
            solar_transition_minutes=self.solar_transition.currentData(),
        )
        general = GeneralConfig(
            start_with_windows=self.start_with_windows.isChecked(),
            start_minimized=self.start_minimized.isChecked(),
            remember_brightness=self.remember_brightness.isChecked(),
            link_brightness=self.link_brightness.isChecked(),
            software_dimming=self.software_dimming.isChecked(),
            respect_fullscreen=self.respect_fullscreen.isChecked(),
            follow_laptop_brightness=self.follow_laptop.isChecked(),
            idle_dim=self.idle_dim.isChecked(),
            idle_minutes=self.idle_minutes.value(),
            idle_level=self.idle_level.value(),
            keep_calibration=self.keep_calibration.isChecked(),
            idle_exempt_apps=self.idle_exempt_apps.apps(),
            theme=self.theme.currentData(),
        )
        return AppConfig(night_light=night_light, general=general, brightness_schedule=self.schedule.config(),
                         displays=self.displays.prefs())

    def set_windows_night_light(self, on: bool | None) -> None:
        """Show the notice while Windows' own Night Light is on too."""
        self.windows_night_light.setVisible(bool(on))
        self._notice_holder.setVisible(bool(on))

    def showEvent(self, event) -> None:
        if not self._placed:
            self._placed = True
            self._fit_to_screen()
        super().showEvent(event)

    def _fit_to_screen(self) -> None:
        """Open centered, and no larger than the screen's free area: with a
        normal size taller than the screen (960x760 at 150% on a 1080p
        laptop), restoring from maximized looked like nothing happened."""
        screen = QGuiApplication.screenAt(QCursor.pos()) or self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width = max(self.minimumWidth(), min(PREFERRED_SIZE[0], area.width() - 2 * SCREEN_MARGIN))
        height = max(self.minimumHeight(), min(PREFERRED_SIZE[1], area.height() - FRAME_HEIGHT - 2 * SCREEN_MARGIN))
        self.resize(width, height)
        self.move(area.x() + max(0, (area.width() - width) // 2),
                  area.y() + max(0, (area.height() - height - FRAME_HEIGHT) // 2))

    def closeEvent(self, event) -> None:
        self._controller.preview(None)
        super().closeEvent(event)

    def _load(self, config: AppConfig) -> None:
        night_light, general = config.night_light, config.general
        self._mode_while_off = night_light.mode
        self.mode.setCurrentIndex(self.mode.findData(night_light.mode if night_light.enabled else ALWAYS_OFF))
        # A zone this version doesn't know (a hand-edited file) shows as a custom location.
        self.location.setCurrentIndex(max(0, self.location.findData(night_light.timezone)))
        self.latitude.setValue(night_light.latitude)
        self.longitude.setValue(night_light.longitude)
        self.day_temperature.setValue(night_light.day_temperature)
        self.night_temperature.setValue(night_light.night_temperature)
        self.night_start.setTime(QTime.fromString(night_light.night_start, "HH:mm"))
        self.day_start.setTime(QTime.fromString(night_light.day_start, "HH:mm"))
        self.transition.setValue(night_light.transition_minutes)
        if self.solar_transition.findData(night_light.solar_transition_minutes) < 0:  # a hand-edited length
            self.solar_transition.addItem(f"{night_light.solar_transition_minutes} min", night_light.solar_transition_minutes)
        self.solar_transition.setCurrentIndex(self.solar_transition.findData(night_light.solar_transition_minutes))
        self.theme.setCurrentIndex(max(0, self.theme.findData(general.theme)))
        self.start_with_windows.setChecked(general.start_with_windows and self.start_with_windows.isEnabled())
        self.start_minimized.setChecked(general.start_minimized)
        self.remember_brightness.setChecked(general.remember_brightness)
        self.link_brightness.setChecked(general.link_brightness)
        self.software_dimming.setChecked(general.software_dimming)
        self.respect_fullscreen.setChecked(general.respect_fullscreen)
        self.follow_laptop.setChecked(general.follow_laptop_brightness)
        self.idle_dim.setChecked(general.idle_dim)
        self.idle_minutes.setValue(general.idle_minutes)
        self.idle_level.setValue(general.idle_level)
        self.keep_calibration.setChecked(general.keep_calibration)
        self.exempt_fullscreen.setChecked(night_light.exempt_fullscreen)
        self.pause_for_apps.setChecked(night_light.pause_for_apps)
        self.exempt_apps.set_apps(night_light.exempt_apps)
        self.idle_exempt_apps.set_apps(general.idle_exempt_apps)
        self.schedule.set_config(config.brightness_schedule)
        self._display_prefs = config.displays
        self._refresh_display_names(keep_edits=False)
        self._on_changed()

    def _refresh_display_names(self, *_args, keep_edits: bool = True) -> None:
        """Names and visibility list what is connected now, keeping unsaved edits."""
        prefs = self.displays.prefs() if keep_edits else self._display_prefs
        self.displays.set_displays(self._monitors.monitors if self._monitors is not None else [], prefs)

    def _apply(self) -> None:
        self._saved = self.current_config()
        self.applied.emit(self._saved)
        self._on_changed()

    def _on_changed(self) -> None:
        config = self.current_config()
        Button = QDialogButtonBox.StandardButton
        self.buttons.button(Button.Apply).setEnabled(config != self._saved)
        self.buttons.button(Button.Reset).setEnabled(config != self._saved)
        self.buttons.button(Button.RestoreDefaults).setEnabled(config != AppConfig())

        # Like KDE: only show what matters for the chosen switching times.
        night_light = config.night_light
        mode = night_light.mode if night_light.enabled else None
        custom = night_light.timezone == CUSTOM_LOCATION
        visible = {
            self._location_row: mode is Mode.LOCATION,
            self.latitude: mode is Mode.LOCATION and custom,
            self.longitude: mode is Mode.LOCATION and custom,
            self.night_start: mode is Mode.TIMES,
            self.day_start: mode is Mode.TIMES,
            self.transition: mode is Mode.TIMES,
            self.solar_transition: mode is Mode.LOCATION,
        }
        for widget, show in visible.items():
            self._night_card.set_row_visible(self._field_rows[widget], show)
        self._sleep_card.set_row_visible(self._bedtime_row, mode is Mode.TIMES)
        self.exempt_apps.setEnabled(self.pause_for_apps.isChecked())
        self._temperature_card.set_row_visible(self._field_rows[self.day_temperature], mode in (Mode.LOCATION, Mode.TIMES))
        self.night_temperature.setEnabled(night_light.enabled)
        for field in (self.idle_minutes, self.idle_level, self.idle_exempt_apps):
            field.setEnabled(self.idle_dim.isChecked())
        self._update_status()
        self._update_schedule_status()

    def _use_sleep_settings(self) -> None:
        """Fill in sleep-friendly values (not applied until Apply): night light
        at SLEEP_KELVIN or warmer, on if it was off, and with custom times
        starting SLEEP_LEAD_HOURS before bedtime."""
        if self.mode.currentData() == ALWAYS_OFF:
            self.mode.setCurrentIndex(self.mode.findData(self._mode_while_off))
        self.night_temperature.setValue(min(self.night_temperature.value(), SLEEP_KELVIN))
        if self.mode.currentData() == Mode.TIMES:
            self.night_start.setTime(self.bedtime.time().addSecs(-SLEEP_LEAD_HOURS * 3600))

    def _update_schedule_status(self) -> None:
        config = self.current_config().brightness_schedule
        if not config.enabled:
            self.schedule.set_status("")
            return
        if config != self._saved.brightness_schedule:
            self.schedule.set_status(f"{describe_schedule(config)}. Apply to start using it.")
            return
        state = self._scheduler.state if self._scheduler is not None else None
        if state is None:
            self.schedule.set_status(describe_schedule(config))
            return
        text = f"Now {state.level}%" + (" (changing gradually)" if state.transitioning else f" since {state.since.at:%H:%M}")
        if state.next is not None:
            text += f". Next: {state.next.level}% at {state.next.at:%H:%M}"
        if state.approximate_sun:
            text += ". Sunrise and sunset are taken as 06:00 and 18:00: set a location under Night Light for real times"
        self.schedule.set_status(text + ".")

    @property
    def schedule_status(self) -> QLabel:
        return self.schedule.status

    # Displays --------------------------------------------------------------

    def _update_displays(self) -> None:
        entries = self._compatibility() if self._compatibility is not None else []
        if entries == self._shown_entries:
            return  # every brightness reading lands here; rebuild only when something shown changed
        self._shown_entries = entries
        card = Card()
        self.display_rows = []
        for entry in entries:
            if entry.works:
                description = " · ".join(part for part in (entry.connector, entry.brightness) if part)
            else:
                description = " · ".join(part for part in (entry.connector, entry.status) if part)
            if entry.night_light != "Yes":
                description += f"\nNight light: {entry.night_light.split(':')[0].split(',')[0]}"
            row = SettingRow(entry.name + (" (built-in)" if entry.built_in else ""), description,
                             StatusBadge("Works" if entry.works else "Not controllable", entry.works), icon=icons.display_glyph(
                                 self.palette().color(QPalette.ColorRole.WindowText)))
            row.setToolTip("\n".join(line for line in (
                f"{entry.manufacturer} {entry.model}".strip(),
                f"Serial {entry.serial}" if entry.serial else "",
                f"Brightness: {entry.brightness if entry.works else entry.status}",
                entry.why,
                entry.detail,
                f"Night light: {entry.night_light}",
            ) if line))
            card.add(row)
            self.display_rows.append(row)
        if not entries:
            card.add(SettingRow("No displays found yet", "Detect again once Winlight has started up"))
        layout = self._displays_layout
        layout.replaceWidget(self._displays_card, card)
        self._displays_card.deleteLater()
        self._displays_card = card

    def _detect_displays(self) -> None:
        if self._monitors is not None:
            self._monitors.refresh(force=True)

    def _copy_report(self) -> None:
        if self._report is not None:
            QGuiApplication.clipboard().setText(self._report())
            self.displays_hint.setText("The report was copied to the clipboard.")

    def _save_report(self) -> None:
        if self._report is None:
            return
        folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        path, _filter = QFileDialog.getSaveFileName(self, "Save compatibility report", str(Path(folder) / "winlight-compatibility.txt"),
                                                   "Text files (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self._report(), encoding="utf-8")
            self.displays_hint.setText(f"Saved to {path}")
        except OSError as exc:
            self.displays_hint.setText(f"Could not save the report: {exc}")

    # Night light ------------------------------------------------------------

    def _on_location_picked(self) -> None:
        """Picking a place fills in its coordinates, which then stay as a
        starting point if "Custom location" is chosen afterwards."""
        if place := locations.find(self.location.currentData() or ""):
            for spin, value in ((self.latitude, place.latitude), (self.longitude, place.longitude)):
                spin.blockSignals(True)
                spin.setValue(value)
                spin.blockSignals(False)
        self._on_changed()

    def _pick_system_zone(self) -> None:
        try:
            zone = QTimeZone.systemTimeZoneId().data().decode("ascii", "replace")
        except Exception:
            log.warning("Could not read the system time zone", exc_info=True)
            zone = ""
        place = locations.find(zone)
        if place is None:
            log.info("System time zone %r is not in the list of places", zone)
            self.schedule_info.setText(f"This PC's time zone ({zone or 'unknown'}) isn't in the list. Pick the nearest city instead.")
            return
        self.location.setCurrentIndex(self.location.findData(place.zone))

    def _update_header_icon(self) -> None:
        icon = icons.night_icon() if is_night_light_on(self._controller) else icons.day_icon()
        self._header_icon.setPixmap(icon.pixmap(HEADER_ICON, HEADER_ICON))

    def _update_status(self) -> None:
        override = self._controller.override
        self._banner_holder.setVisible(override is not None)
        if override is not None:
            self.banner_text.setText(f"Night Light: {night_light_status(self._controller)}")
            self.banner_button.setText("Resume" if override.kind is OverrideKind.SUSPEND else "Turn Off")

        if self.current_config().night_light != self._saved.night_light:
            self.schedule_info.setText("Apply to see the new schedule.")
        else:
            self.schedule_info.setText(schedule_description(self._controller))

    def _connect_preview(self, slider: TemperatureSlider) -> None:
        """While a temperature slider is held, show that temperature on screen, as KDE does."""
        inner = slider.slider
        inner.sliderPressed.connect(lambda: self._controller.preview(slider.value()))
        inner.sliderMoved.connect(lambda _: self._controller.preview(slider.value()))
        inner.sliderReleased.connect(lambda: self._controller.preview(None))


def _page(title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(28, 0, PAGE_RIGHT, 28)
    layout.setSpacing(6)
    layout.addWidget(PageTitle(title, subtitle))
    return page, layout


def _scrolling(page: QWidget) -> QScrollArea:
    """A page that scrolls when the window is smaller than it, on the window's own background."""
    page.layout().addStretch(1)
    area = QScrollArea()
    area.setWidget(page)
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    for widget in (area, area.viewport(), page):
        widget.setBackgroundRole(QPalette.ColorRole.Window)
        widget.setAutoFillBackground(False)
    area.viewport().setAutoFillBackground(False)

    def make_room(_low: int, high: int) -> None:
        # Without a scrollbar, the page takes its width as extra margin.
        margins = page.layout().contentsMargins()
        right = PAGE_RIGHT + (0 if high > 0 else _scrollbar_width())
        if margins.right() != right:
            page.layout().setContentsMargins(margins.left(), margins.top(), right, margins.bottom())

    area.verticalScrollBar().rangeChanged.connect(make_room)
    make_room(0, 0)
    return area


def _scrollbar_width() -> int:
    return QApplication.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)


def _open_log_folder() -> None:
    import platformdirs

    folder = Path(platformdirs.user_log_dir("night-light", appauthor=False))
    folder.mkdir(parents=True, exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def _location_combo() -> QComboBox:
    """Every place in the time zone list, searchable by typing any part of
    its name, with "Custom location" first for typing in coordinates."""
    combo = QComboBox()
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    # Otherwise the longest of 400+ names sets the window's width.
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(14)
    combo.setMaxVisibleItems(14)
    combo.addItem(CUSTOM_LOCATION_LABEL, CUSTOM_LOCATION)
    for place in locations.all_locations():
        combo.addItem(place.label, place.zone)
    combo.lineEdit().setPlaceholderText("Type a city…")

    completer = combo.completer()
    completer.setFilterMode(Qt.MatchFlag.MatchContains)
    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
    completer.setCompletionMode(completer.CompletionMode.PopupCompletion)

    def settle() -> None:
        # Typed text that names a place selects it; anything else goes back
        # to the current choice rather than lingering in the box.
        index = combo.findText(combo.currentText().strip(), Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.setEditText(combo.itemText(combo.currentIndex()))

    combo.lineEdit().editingFinished.connect(settle)
    return combo
