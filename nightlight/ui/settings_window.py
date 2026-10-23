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
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QTime, QTimer, QTimeZone, QUrl, Signal
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
from nightlight.color.controller import NightLightController
from nightlight.config import (
    MAX_IDLE_MINUTES,
    MAX_SOLAR_TRANSITION_MINUTES,
    MAX_TRANSITION_MINUTES,
    AppConfig,
    GeneralConfig,
    Mode,
    NightLightConfig,
)
from nightlight.core import locations
from nightlight.core.brightness_schedule import describe as describe_schedule
from nightlight.core.colortemp import limited_displays
from nightlight.core.schedule import OverrideKind, compute_state
from nightlight.hardware.gamma import GammaBusy
from nightlight.ui import icons
from nightlight.ui import theme as theme_module
from nightlight.ui.day_ribbon import DayRibbon
from nightlight.ui.night_light_text import is_night_light_on, night_light_status, schedule_description
from nightlight.ui.preview_notice import PreviewNotice
from nightlight.ui.schedule_editor import ScheduleEditor
from nightlight.ui.settings_parts import ActionBar, Card, NavButton, PageTitle, SectionLabel, SettingRow, StatusBadge, ToggleSwitch
from nightlight.ui.settings_widgets import AppList, DisplayList
from nightlight.ui.widgets import SecondaryLabel, Separator, TemperatureSlider, WindowsNightLightNotice, set_heading_font

log = logging.getLogger(__name__)

# Like KDE, "Always off" is a choice in the list even though it is stored as
# enabled=False rather than as a mode.
ALWAYS_OFF = "off"
SWITCHING_TIMES = {
    ALWAYS_OFF: "Always off",
    Mode.ALWAYS: "Always on",
    Mode.LOCATION: "Sunset to sunrise",
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
THEME_CHOICES = {"system": "Match Windows", "light": "Light", "dark": "Dark"}
# Sleep-friendly night light, from what sleep researchers advise: dim, warm
# light for the 2-3 hours before bed (Harvard Health, "Blue light has a dark
# side"; Chang et al., PNAS 2015, on screens delaying melatonin). 2700 K is
# an incandescent bulb's color, f.lux's default at night.
SLEEP_KELVIN = 2700
SLEEP_LEAD_HOURS = 3  # night light starts warming this long before bedtime
SLEEP_TRANSITION_MINUTES = 60  # so it is fully warm 2 hours before bed, and day light is back by the wake-up time
SLEEP_ADVICE = (
    "Bright, blue-rich light in the evening can delay melatonin, the hormone that helps you fall asleep. Sleep "
    f"researchers recommend dim, warm light (around {SLEEP_KELVIN} K, like an incandescent bulb) for the two to three "
        "hours before bed. Windows may limit how warm a display can go; Winlight then uses the warmest setting each display "
    "accepts. This is general guidance, not medical advice"
)
# The 24-hour preview, at LightBulb's pace (DashboardViewModel.UpdateInstant):
# 5 minutes of the day every 50 ms: a day in 15 to 20 seconds (Windows' timers run late).
DAY_PREVIEW_TICK_MS = 50
DAY_PREVIEW_STEP = timedelta(minutes=5)
# Unlocking warmer colors (win32.expand_gamma_range), worded after LightBulb's
# "Limited gamma range" message.
UNLOCK_DESCRIPTION = ("Windows limits how far colors can be changed, so night light stops early. Unlocking sets the "
                      "GdiIcmGammaRange setting, as f.lux and LightBulb do, after Windows asks for administrator approval")
UNLOCKED_DESCRIPTION = ("Unlocked. If night light still stops early, Windows applies it after you sign out and back in, "
                        "or restart")
UNLOCK_OUTCOMES = {
    "cancelled": "Nothing was changed: the administrator prompt was declined.",
    "failed": "It couldn't be unlocked; the log says why.",
}
GAMMA_BUSY = "A change to the display colors is stuck, probably in the display driver. Try again in a moment."
# When "never pause late at night" starts (config.NEVER_PAUSE_FROM).
NEVER_PAUSE_CHOICES = {
    "evening": "When night light starts warming",
    "night": "When night light is fully on",
    "time": "At a set time, such as bedtime",
}
NIGHT_DESCRIPTION = "Lower values are warmer. Drag to preview"


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
        running_apps: Callable[[], list[str]] = list,
        report_problem: Callable[[], None] | None = None,
        warmest: Callable[[], dict[str, int]] | None = None,
        display_name: Callable[[str], str] | None = None,
        reload_calibrations: Callable[[], dict[str, str]] | None = None,
        forget_calibrations: Callable[[], int] | None = None,
        unlock_warmer: Callable[[], str] | None = None,
        warmer_unlocked: Callable[[], bool] = lambda: False,
    ) -> None:
        super().__init__()
        self._controller = controller
        # GDI name -> the warmest Windows accepted there (gamma backend), and
        # a display's name from its GDI name, for the Windows limit note.
        self._warmest = warmest or dict
        self._display_name = display_name or (lambda gdi_name: gdi_name)
        self._monitors = monitors
        self._scheduler = scheduler
        self._compatibility = compatibility
        self._report = report
        self._running_apps = running_apps
        self._report_problem = report_problem
        self._reload_calibrations = reload_calibrations
        self._unlock_warmer = unlock_warmer  # sets GdiIcmGammaRange: "done", "cancelled" or "failed"
        self._warmer_unlocked = warmer_unlocked
        self._unlock_outcome = ""
        self._preview_notice: PreviewNotice | None = None  # made when a slider is first held
        # The 24-hour preview: the moment shown now, and when it ends.
        self._day_at: datetime | None = None
        self._day_end: datetime | None = None
        self._day_timer = QTimer(self, interval=DAY_PREVIEW_TICK_MS)
        self._day_timer.timeout.connect(self._day_preview_step)
        self._forget_calibrations = forget_calibrations
        self._display_prefs = config.displays
        self._saved = replace(config, night_light=controller.config)
        self._mode_while_off = self._saved.night_light.mode
        self._ribbon_config = self._saved.night_light  # what the day ribbon shows
        self.setWindowTitle(f"{APP_NAME} Settings")
        self.setWindowIcon(icons.night_icon())

        self._build_controls(can_autostart)
        self._stack = QStackedWidget()
        for page in (self._night_light_page(), self._brightness_page(), self._displays_page(), self._general_page()):
            self._stack.addWidget(_scrolling(page))
        # Each page opens at its top, rather than where it was last left.
        self._stack.currentChanged.connect(self._scroll_to_top)

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
            self.accent.currentIndexChanged,
            self.pause_automatically.toggled,
            self.start_with_windows.toggled,
            self.start_minimized.toggled,
            self.remember_brightness.toggled,
            self.link_brightness.toggled,
            self.software_dimming.toggled,
            self.schedule.changed,
            self.respect_fullscreen.toggled,
            self.follow_laptop.toggled,
            self.keep_on_power_change.toggled,
            self.idle_dim.toggled,
            self.idle_minutes.valueChanged,
            self.idle_level.valueChanged,
            self.exempt_fullscreen.toggled,
            self.pause_for_apps.toggled,
            self.exempt_apps.changed,
            self.never_pause.toggled,
            self.never_pause_from.currentIndexChanged,
            self.never_pause_time.timeChanged,
            self.idle_exempt_apps.changed,
            self.displays.changed,
            self.keep_calibration.toggled,
        ):
            signal.connect(self._on_changed)
        self.location.currentIndexChanged.connect(self._on_location_picked)
        for slider in (self.day_temperature, self.night_temperature):
            self._connect_preview(slider)
        controller.changed.connect(self._update_status)
        controller.changed.connect(self._update_windows_limit)  # a limit is learnt when Windows refuses a ramp
        self.night_temperature.valueChanged.connect(self._update_windows_limit)
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
        self._update_windows_limit()
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
        set_heading_font(title, 1.6)
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
        self.use_system_zone = QPushButton("Detect", toolTip="Use the city of your PC's time zone")
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
        self.solar_transition.addItem("Twilight (about 30 min)", -1)
        self.solar_transition.addItem("None", 0)
        for minutes in range(5, MAX_SOLAR_TRANSITION_MINUTES + 1, 5):
            self.solar_transition.addItem(f"{minutes} min", minutes)
        self.solar_transition.setMaxVisibleItems(12)
        self.bedtime = QTimeEdit(displayFormat="HH:mm")
        self.bedtime.setTime(QTime(23, 0))
        self.wake_time = QTimeEdit(displayFormat="HH:mm")
        self.wake_time.setTime(QTime(7, 0))
        self.use_sleep_schedule = QPushButton("Use sleep schedule")
        self.use_sleep_schedule.setToolTip(f"Custom times from your bedtime and wake-up time, {SLEEP_KELVIN} K or warmer, "
                                           f"and a {SLEEP_TRANSITION_MINUTES}-minute transition. Click Apply to keep them")
        self.use_sleep_schedule.clicked.connect(self._use_sleep_schedule)
        self.use_sleep_warmth = QPushButton("Use warmth only")
        self.use_sleep_warmth.setToolTip(f"Sets night light to {SLEEP_KELVIN} K or warmer and keeps your schedule. "
                                         "Click Apply to keep it")
        self.use_sleep_warmth.clicked.connect(self._use_sleep_warmth)
        self.day_temperature = TemperatureSlider()
        self.night_temperature = TemperatureSlider()
        for field in (self.mode, self._location_row, self.solar_transition):
            field.setFixedWidth(WIDE_FIELD)
        for slider in (self.day_temperature, self.night_temperature):
            slider.setFixedWidth(WIDE_FIELD + 40)
        for field in (self.latitude, self.longitude, self.night_start, self.day_start, self.transition, self.bedtime, self.wake_time):
            field.setFixedWidth(NARROW_FIELD)
        self.schedule_info = SecondaryLabel(scale=0.92)
        self.schedule_info.setWordWrap(True)
        self.exempt_fullscreen = ToggleSwitch("Pause night light for fullscreen apps")
        self.pause_for_apps = ToggleSwitch("Pause night light for selected apps")
        self.pause_automatically = ToggleSwitch("Pause night light automatically for video, calls, games and color work")
        self.exempt_apps = AppList(self._running_apps)
        self.never_pause = ToggleSwitch("Never pause night light late at night")
        self.never_pause_from = QComboBox()
        for key, label in NEVER_PAUSE_CHOICES.items():
            self.never_pause_from.addItem(label, key)
        self.never_pause_from.setFixedWidth(WIDE_FIELD)
        self.never_pause_time = QTimeEdit(displayFormat="HH:mm")
        self.never_pause_time.setFixedWidth(NARROW_FIELD)
        self._never_pause_when = QWidget()
        when = QHBoxLayout(self._never_pause_when)
        when.setContentsMargins(0, 0, 0, 0)
        when.setSpacing(8)
        when.addWidget(SecondaryLabel("Starting"))
        when.addWidget(self.never_pause_from)
        when.addWidget(self.never_pause_time)
        when.addStretch(1)

        # Brightness
        self.schedule = ScheduleEditor()
        self.remember_brightness = ToggleSwitch("Remember each display's brightness")
        self.link_brightness = ToggleSwitch("Change all displays together")
        self.follow_laptop = ToggleSwitch("External displays follow the laptop's brightness keys")
        self.keep_on_power_change = ToggleSwitch("Keep the built-in display's brightness when plugging in or unplugging")
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
        self.theme.setFixedWidth(NARROW_FIELD + 80)
        self.accent = QComboBox()
        for key, label in theme_module.ACCENT_NAMES.items():
            self.accent.addItem(label, key)
        self.accent.setFixedWidth(NARROW_FIELD + 80)
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
        page, layout = _page("Night Light", "Warmer colors in the evening reduce the blue light from your displays.")
        # Today, from the settings on this page (applied or not).
        self.day_ribbon = DayRibbon(
            lambda moment: compute_state(self._ribbon_config, moment, zone=self._controller.zone).temperature, self._controller.now,
            zone=lambda: self._controller.zone)
        layout.addWidget(self.day_ribbon)
        layout.addSpacing(16)
        self._night_card = Card()
        self._field_rows: dict[QWidget, QWidget] = {}
        for field, title, description in (
            (self.mode, "Schedule", "When night light is on"),
            (self._location_row, "Location", "Used to calculate sunset and sunrise"),
            (self.latitude, "Latitude", "Degrees north (negative for south)"),
            (self.longitude, "Longitude", "Degrees east (negative for west)"),
            (self.night_start, "Turn on at", "When the evening transition starts"),
            (self.day_start, "Turn off at", "When the morning transition starts"),
            (self.transition, "Transition", "How long the change between day and night takes"),
            (self.solar_transition, "Transition", ("Starts at sunset in the evening and ends at sunrise in the morning. "
                                                    "Twilight lasts longer in some places and seasons")),
        ):
            self._field_rows[field] = self._night_card.add(SettingRow(title, description, field))
        self.preview_day = QPushButton("Preview")
        self.preview_day.clicked.connect(self._toggle_day_preview)
        self._day_row = self._night_card.add(SettingRow("Preview 24 hours", "Plays the next day of these settings on screen in "
                                                        "about 20 seconds, before you apply them", self.preview_day))
        layout.addWidget(self._night_card)
        layout.addWidget(SectionLabel("Color temperature"))
        self._temperature_card = Card()
        self._field_rows[self.day_temperature] = self._temperature_card.add(SettingRow("Day", "6500 K is neutral, with no filter",
                                                                                         self.day_temperature))
        self._night_row = self._temperature_card.add(SettingRow("Night", NIGHT_DESCRIPTION, self.night_temperature))
        self.unlock_warmer = QPushButton("Unlock…")
        self.unlock_warmer.clicked.connect(self._unlock)
        self._unlock_row = self._temperature_card.add(SettingRow("Warmer night light", UNLOCK_DESCRIPTION, self.unlock_warmer))
        layout.addWidget(self._temperature_card)
        self.schedule_info.setContentsMargins(4, 10, 4, 0)
        layout.addWidget(self.schedule_info)
        layout.addWidget(SectionLabel("Sleep"))
        self._sleep_card = Card()
        self._sleep_card.add(SettingRow("Sleep-friendly night light", SLEEP_ADVICE))
        self._bedtime_row = self._sleep_card.add(SettingRow("Bedtime", f"Night light starts {SLEEP_LEAD_HOURS} hours before "
                                                            "and is fully warm 2 hours before", self.bedtime))
        self._wake_row = self._sleep_card.add(SettingRow("Wake-up time", "Day light is fully back by this time", self.wake_time))
        sleep_buttons = QWidget()
        sleep_layout = QHBoxLayout(sleep_buttons)
        sleep_layout.setContentsMargins(0, 0, 0, 0)
        sleep_layout.setSpacing(8)
        sleep_layout.addWidget(self.use_sleep_warmth)
        sleep_layout.addWidget(self.use_sleep_schedule)
        self._sleep_card.add(SettingRow("Apply the recommendation", "The full schedule uses custom times built from your bedtime "
                                        "and wake-up time. Warmth only keeps your current schedule", sleep_buttons))
        layout.addWidget(self._sleep_card)
        layout.addWidget(SectionLabel("Pause night light"))
        pause = Card()
        pause.add(SettingRow("Fullscreen apps", "Pauses while any app fills a display, such as a game, a video or a browser "
                             "in fullscreen", self.exempt_fullscreen))
        pause.add(SettingRow("Video, calls, games and color work", "Pauses automatically while an app keeps the screen on for "
                             "video playback, a call or a presentation, while a game runs in exclusive fullscreen, and for "
                             "photo, video and design apps such as Photoshop, Lightroom and DaVinci Resolve. Winlight "
                             "decides from the information Windows provides, which doesn't name the app or display "
                             "involved, so some cases may be missed or paused on the display in front",
                             self.pause_automatically))
        pause.add(SettingRow("Selected apps", "Pauses while a listed app is in front, fullscreen or not. Other apps are "
                             "not affected", self.pause_for_apps, below=self.exempt_apps))
        pause.add(SettingRow("Never pause late at night", "Keeps night light on for every app, whatever the options above, "
                             "from this point until the morning transition starts, so a late video or game doesn't undo it",
                             self.never_pause, below=self._never_pause_when))
        layout.addWidget(pause)
        pause_hint = SecondaryLabel("The first three options work independently, and the last one overrides them late at night. "
                                    "Night light pauses only on the display showing the app, and returns when you switch away, "
                                    "minimize or close it.", scale=0.9)
        pause_hint.setWordWrap(True)
        pause_hint.setContentsMargins(4, 8, 4, 0)
        layout.addWidget(pause_hint)
        return page

    def _brightness_page(self) -> QWidget:
        page, layout = _page("Brightness", "Schedule brightness by time of day, and choose how it is kept.")
        layout.addWidget(self.schedule)
        layout.addWidget(SectionLabel("Behavior"))
        options = Card()
        options.add(SettingRow("Remember each display's brightness", "Restores levels after startup, sleep or reconnecting "
                               "a display", self.remember_brightness))
        options.add(SettingRow("Change all displays together", "Moving one slider adjusts every display", self.link_brightness))
        options.add(SettingRow("Follow the laptop's brightness keys",
                               "External displays match the built-in display when its brightness changes",
                               self.follow_laptop))
        options.add(SettingRow("Keep brightness when plugging in or unplugging",
                               "Windows sets its own level for battery and for mains power; this puts the built-in "
                               "display's back. Your power plans aren't changed", self.keep_on_power_change))
        options.add(SettingRow("Dim displays without brightness control",
                               "Dims TVs and monitors without DDC/CI by adjusting their colors. This doesn't reduce power use",
                               self.software_dimming))
        layout.addWidget(options)
        layout.addWidget(SectionLabel("When I'm away"))
        away = Card()
        away.add(SettingRow("Dim displays when I'm away", "Any keyboard or mouse input restores brightness. Skipped while an "
                            "app keeps the screen on (video, calls, presentations) or a game runs in exclusive fullscreen",
                            self.idle_dim))
        away.add(SettingRow("Dim after", "Time without keyboard or mouse input", self.idle_minutes))
        away.add(SettingRow("Dim to", "Displays already below this level stay as they are", self.idle_level))
        away.add(SettingRow("Never dim while these apps are in front", "For apps that don't keep the screen on themselves, "
                            "such as some media players and call apps", below=self.idle_exempt_apps))
        layout.addWidget(away)
        return page

    def _displays_page(self) -> QWidget:
        page, layout = _page("Displays", "Your displays, and how Winlight controls their brightness.")
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
        self.displays_hint = SecondaryLabel("Hover over a display for details. The report lists everything Winlight knows about "
                                            "each display; attach it to bug reports.", scale=0.9)
        self.displays_hint.setWordWrap(True)
        self.displays_hint.setContentsMargins(2, 8, 2, 0)
        layout.addWidget(self.displays_hint)
        layout.addWidget(SectionLabel("Names and visibility"))
        names = Card()
        names.add(SettingRow("Rename or hide displays", "A custom name replaces the display's name in the panel. Hidden "
                             "displays aren't shown in the panel and are never changed", below=self.displays))
        layout.addWidget(names)
        layout.addWidget(SectionLabel("Color calibration"))
        calibration = Card()
        calibration.add(SettingRow("Keep each display's color calibration",
                                   "Restores a display's calibration when Windows resets it, for example after the screen "
                                   "turns off",
                                   self.keep_calibration))
        self.reload_calibration = QPushButton("Reload")
        self.reload_calibration.clicked.connect(self._reload)
        self.reload_calibration.setEnabled(self._reload_calibrations is not None)
        calibration.add(SettingRow("Reload calibrations", "Reads each display's calibration from its color profile in Windows "
                                   "again and puts it on screen, as Windows does when you sign in",
                                   self.reload_calibration))
        self.forget_calibration = QPushButton("Forget")
        self.forget_calibration.clicked.connect(self._forget)
        self.forget_calibration.setEnabled(self._forget_calibrations is not None)
        calibration.add(SettingRow("Forget remembered calibrations", "Stops putting back calibrations Winlight remembered, "
                                   "such as one you have since removed in Windows",
                                   self.forget_calibration))
        layout.addWidget(calibration)
        self.calibration_result = SecondaryLabel(scale=0.9)
        self.calibration_result.setWordWrap(True)
        self.calibration_result.setContentsMargins(2, 8, 2, 0)
        self.calibration_result.hide()
        layout.addWidget(self.calibration_result)
        return page

    def _general_page(self) -> QWidget:
        page, layout = _page("General", f"Startup, appearance, and information about {APP_NAME}.")
        startup = Card()
        startup.add(SettingRow(f"Start {APP_NAME} when I sign in", f"{APP_NAME} runs in the system tray", self.start_with_windows))
        startup.add(SettingRow("Start minimized", "Start in the system tray without opening this window", self.start_minimized))
        layout.addWidget(startup)
        layout.addWidget(SectionLabel("Appearance"))
        appearance = Card()
        appearance.add(SettingRow("Theme", "Light or dark, or the same as Windows", self.theme))
        appearance.add(SettingRow("Accent color", "Used for switches, sliders and buttons", self.accent))
        layout.addWidget(appearance)
        layout.addWidget(SectionLabel("Focus"))
        focus = Card()
        focus.add(SettingRow("Never open over fullscreen apps", f"{APP_NAME} won't open windows by itself while a game, "
                             "video or presentation is fullscreen", self.respect_fullscreen))
        layout.addWidget(focus)
        layout.addWidget(SectionLabel("About"))
        about = Card()
        about.add(SettingRow(f"{APP_NAME} {display_version()}", "Brightness and night light for every display. "
                             "Free software, licensed under the GPL-3.0", icon=icons.night_icon()))
        self.github = QPushButton("View on GitHub")
        self.github.setToolTip(REPO_URL)
        self.github.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))
        about.add(SettingRow(f"Created by {AUTHOR}", "Source code, releases and issue tracker", self.github))
        if self._report_problem is not None:
            report = QPushButton("Report a Problem…")
            report.setToolTip("Saves logs and settings to a zip file in Winlight's Reports folder, without personal details")
            report.clicked.connect(self._report_problem)
            about.add(SettingRow("Report a problem", "Saves the information a bug report needs to one zip file", report))
        notices = third_party_notices()
        if notices is not None:
            view = QPushButton("View notices")
            view.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(notices))))
            about.add(SettingRow("Third-party software", "Qt, Python and the other components Winlight includes, and their licenses",
                                 view))
        logs = QPushButton("Open folder")
        logs.clicked.connect(_open_log_folder)
        about.add(SettingRow("Log files", "The log and diagnostic files Winlight keeps", logs))
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
            pause_automatically=self.pause_automatically.isChecked(),
            solar_transition_minutes=self.solar_transition.currentData(),
            never_pause_at_night=self.never_pause.isChecked(),
            never_pause_from=self.never_pause_from.currentData(),
            never_pause_time=self.never_pause_time.time().toString("HH:mm"),
        )
        general = GeneralConfig(
            start_with_windows=self.start_with_windows.isChecked(),
            start_minimized=self.start_minimized.isChecked(),
            remember_brightness=self.remember_brightness.isChecked(),
            link_brightness=self.link_brightness.isChecked(),
            software_dimming=self.software_dimming.isChecked(),
            respect_fullscreen=self.respect_fullscreen.isChecked(),
            follow_laptop_brightness=self.follow_laptop.isChecked(),
            keep_brightness_on_power_change=self.keep_on_power_change.isChecked(),
            idle_dim=self.idle_dim.isChecked(),
            idle_minutes=self.idle_minutes.value(),
            idle_level=self.idle_level.value(),
            keep_calibration=self.keep_calibration.isChecked(),
            idle_exempt_apps=self.idle_exempt_apps.apps(),
            theme=self.theme.currentData(),
            accent=self.accent.currentData(),
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
        # Opening, Qt gives focus back to the entry used last; the current one takes it.
        self._nav[self.current_page()].setFocus(Qt.FocusReason.OtherFocusReason)

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
        self._stop_day_preview()
        self._preview(None)
        # Opened again, the window starts afresh: the first page, at its top,
        # without the last action's message.
        self.calibration_result.hide()
        self.show_page(0)
        self._scroll_to_top()
        if (focused := self.focusWidget()) is not None:
            focused.clearFocus()  # no outline left on the entry or field used last
        super().closeEvent(event)

    def _scroll_to_top(self, *_args) -> None:
        if isinstance(area := self._stack.currentWidget(), QScrollArea):
            area.verticalScrollBar().setValue(0)

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
        self.accent.setCurrentIndex(max(0, self.accent.findData(general.accent)))
        self.start_with_windows.setChecked(general.start_with_windows and self.start_with_windows.isEnabled())
        self.start_minimized.setChecked(general.start_minimized)
        self.remember_brightness.setChecked(general.remember_brightness)
        self.link_brightness.setChecked(general.link_brightness)
        self.software_dimming.setChecked(general.software_dimming)
        self.respect_fullscreen.setChecked(general.respect_fullscreen)
        self.follow_laptop.setChecked(general.follow_laptop_brightness)
        self.keep_on_power_change.setChecked(general.keep_brightness_on_power_change)
        self.idle_dim.setChecked(general.idle_dim)
        self.idle_minutes.setValue(general.idle_minutes)
        self.idle_level.setValue(general.idle_level)
        self.keep_calibration.setChecked(general.keep_calibration)
        self.exempt_fullscreen.setChecked(night_light.exempt_fullscreen)
        self.pause_for_apps.setChecked(night_light.pause_for_apps)
        self.pause_automatically.setChecked(night_light.pause_automatically)
        self.exempt_apps.set_apps(night_light.exempt_apps)
        self.never_pause.setChecked(night_light.never_pause_at_night)
        self.never_pause_from.setCurrentIndex(max(0, self.never_pause_from.findData(night_light.never_pause_from)))
        self.never_pause_time.setTime(QTime.fromString(night_light.never_pause_time, "HH:mm"))
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
        if config.night_light != self._ribbon_config:
            self._ribbon_config = config.night_light
            self.day_ribbon.refresh()
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
        self.exempt_apps.setEnabled(self.pause_for_apps.isChecked())
        self._never_pause_when.setEnabled(self.never_pause.isChecked())
        self.never_pause_time.setVisible(night_light.never_pause_from == "time")
        self._temperature_card.set_row_visible(self._field_rows[self.day_temperature], mode in (Mode.LOCATION, Mode.TIMES))
        self.night_temperature.setEnabled(night_light.enabled)
        self._night_card.set_row_visible(self._day_row, mode in (Mode.LOCATION, Mode.TIMES))  # a day that changes
        for field in (self.idle_minutes, self.idle_level, self.idle_exempt_apps):
            field.setEnabled(self.idle_dim.isChecked())
        self._update_status()
        self._update_schedule_status()

    def _update_windows_limit(self, *_args) -> None:
        """Under Night: the displays where Windows holds night light warmer
        than asked, e.g. "Built-in display: 3300 K (Windows limit)". Only
        while a limit is in the way and known; it changes when Windows
        refuses a ramp again (after dimming changes, say)."""
        limited = limited_displays(self.night_temperature.value(), self._warmest())
        notes = [f"{self._display_name(name)}: {kelvin} K (Windows limit)" for name, kelvin in sorted(limited.items())]
        self._night_row.set_description(NIGHT_DESCRIPTION + (". " + "; ".join(notes) if notes else ""))
        # Unlocking warmer colors, while Windows holds a display back.
        unlocked = bool(limited) and self._warmer_unlocked()
        self._temperature_card.set_row_visible(self._unlock_row, bool(limited) and self._unlock_warmer is not None)
        self.unlock_warmer.setVisible(not unlocked)
        row = self._unlock_row
        if unlocked:
            row.set_description(UNLOCKED_DESCRIPTION)
        else:
            row.set_description(" ".join(part for part in (UNLOCK_OUTCOMES.get(self._unlock_outcome, ""), UNLOCK_DESCRIPTION) if part))

    def _unlock(self) -> None:
        self._unlock_outcome = self._unlock_warmer() if self._unlock_warmer is not None else ""
        self._update_windows_limit()

    def _use_sleep_warmth(self) -> None:
        """Night light at SLEEP_KELVIN or warmer (a warmer choice stays), on if
        it was off; the schedule stays. Filled in, not applied until Apply."""
        if self.mode.currentData() == ALWAYS_OFF:
            self.mode.setCurrentIndex(self.mode.findData(self._mode_while_off))
        self.night_temperature.setValue(min(self.night_temperature.value(), SLEEP_KELVIN))

    def _use_sleep_schedule(self) -> None:
        """The whole recommendation as custom times: warming from
        SLEEP_LEAD_HOURS before bedtime, fully warm two hours before, and day
        light back by the wake-up time. Filled in, not applied until Apply."""
        self._use_sleep_warmth()
        self.mode.setCurrentIndex(self.mode.findData(Mode.TIMES))
        self.transition.setValue(SLEEP_TRANSITION_MINUTES)
        self.night_start.setTime(self.bedtime.time().addSecs(-SLEEP_LEAD_HOURS * 3600))
        self.day_start.setTime(self.wake_time.time().addSecs(-SLEEP_TRANSITION_MINUTES * 60))

    def _update_schedule_status(self) -> None:
        config = self.current_config().brightness_schedule
        if not config.enabled:
            self.schedule.set_status("")
            return
        if config != self._saved.brightness_schedule:
            self.schedule.set_status(f"{describe_schedule(config)}. Click Apply to use this schedule.")
            return
        state = self._scheduler.state if self._scheduler is not None else None
        if state is None:
            self.schedule.set_status(describe_schedule(config))
            return
        text = f"Now {state.level}%" + (f", changing to {state.since.level}%" if state.transitioning
                                        else f", since {state.since.at:%H:%M}")
        if state.next is not None:
            text += f". Next: {state.next.level}% at {state.next.at:%H:%M}"
        if state.approximate_sun:
            text += ". Sunrise and sunset are assumed to be 06:00 and 18:00 until you set a location on the Night Light page"
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
            if entry.calibration:
                description += f"\nCalibration: {entry.calibration}"
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

    def _reload(self) -> None:
        try:
            results = self._reload_calibrations() if self._reload_calibrations is not None else {}
        except GammaBusy:
            self._show_calibration_result(GAMMA_BUSY)
            return
        lines = [f"{self._display_name(name)}: {outcome}." for name, outcome in sorted(results.items())]
        self._show_calibration_result("\n".join(lines) or "No display to reload.")

    def _forget(self) -> None:
        try:
            count = self._forget_calibrations() if self._forget_calibrations is not None else 0
        except GammaBusy:
            self._show_calibration_result(GAMMA_BUSY)
            return
        self._show_calibration_result(
            f"Forgot {count} remembered calibration{'s' if count != 1 else ''}. Reload brings back the ones Windows still has."
            if count else "No calibrations were remembered.")

    def _show_calibration_result(self, text: str) -> None:
        self.calibration_result.setText(text)
        self.calibration_result.show()
        self._shown_entries = None  # the calibration lines changed
        self._update_displays()

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
            self.schedule_info.setText(f"Your PC's time zone ({zone or 'unknown'}) isn't in the list. Choose the nearest city instead.")
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
            self.schedule_info.setText("Click Apply to see the new times.")
        else:
            self.schedule_info.setText(schedule_description(self._controller))

    def _connect_preview(self, slider: TemperatureSlider) -> None:
        """While a temperature slider is held, show that temperature on screen,
        with a note in the middle of the screen saying so, as KDE does."""
        inner = slider.slider
        inner.sliderPressed.connect(self._stop_day_preview)
        inner.sliderPressed.connect(lambda: self._preview(slider.value()))
        inner.sliderMoved.connect(lambda _: self._preview(slider.value()))
        inner.sliderReleased.connect(lambda: self._preview(None))

    def _preview(self, kelvin: int | None, hint: str = "") -> None:
        self._controller.preview(kelvin)
        if kelvin is None:
            if self._preview_notice is not None:
                self._preview_notice.hide()
            return
        if self._preview_notice is None:
            self._preview_notice = PreviewNotice()
        self._preview_notice.show_kelvin(kelvin, near=self, hint=hint)

    def _toggle_day_preview(self) -> None:
        """LightBulb's 24-hour cycle preview: a day of the settings shown here,
        5 minutes of it every 50 ms, then back to now."""
        if self._day_at is not None:
            self._stop_day_preview()
            return
        self._day_at = self._controller.now()
        self._day_end = self._day_at + timedelta(days=1)
        self.preview_day.setText("Stop")
        self._day_timer.start()
        self._day_preview_step()

    def _day_preview_step(self) -> None:
        self._day_at += DAY_PREVIEW_STEP
        night_light = self.current_config().night_light
        if self._day_at >= self._day_end or not night_light.enabled:
            self._stop_day_preview()
            return
        # The schedule itself: a Suspend or Activate now isn't part of it.
        kelvin = compute_state(night_light, self._day_at, zone=self._controller.zone).temperature
        today = self._controller.now().date()
        day = "Today" if self._day_at.date() == today else "Tomorrow"
        self._preview(kelvin, hint=f"{day} {self._day_at:%H:%M} · Stop it in Settings")
        self.day_ribbon.set_marker(self._day_at)

    def _stop_day_preview(self) -> None:
        if self._day_at is None:  # the preview's own state, not its timer's: the note and needle must go too
            return
        self._day_timer.stop()
        self._day_at = self._day_end = None
        self.day_ribbon.set_marker(None)
        self.preview_day.setText("Preview")
        self._preview(None)


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


def third_party_notices() -> Path | None:
    """THIRD_PARTY_NOTICES.txt next to the installed app; None when running from source."""
    import sys

    if not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).with_name("THIRD_PARTY_NOTICES.txt")
    return path if path.is_file() else None


def _open_log_folder() -> None:
    from nightlight import config

    folder = config.log_dir()
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
    combo.lineEdit().setPlaceholderText("Search for a city")

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
