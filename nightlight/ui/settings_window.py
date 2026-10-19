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
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPalette
from PySide6.QtWidgets import (
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
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from nightlight import APP_NAME, __version__
from nightlight.config import MAX_IDLE_MINUTES, MAX_TRANSITION_MINUTES, AppConfig, GeneralConfig, Mode, NightLightConfig
from nightlight.controller import NightLightController
from nightlight.core import locations
from nightlight.core.brightness_schedule import describe as describe_schedule
from nightlight.core.schedule import OverrideKind
from nightlight.ui import icons
from nightlight.ui.night_light_text import is_night_light_on, night_light_status, schedule_description
from nightlight.ui.schedule_editor import ScheduleEditor
from nightlight.ui.settings_parts import Card, NavButton, PageTitle, SectionLabel, SettingRow, StatusBadge, ToggleSwitch
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
# Controls on the right of the cards share widths, so they end on one line.
WIDE_FIELD = 300
NARROW_FIELD = 120
PAGES = ("Night Light", "Brightness", "Displays", "General")


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
        self.buttons = QDialogButtonBox(Button.RestoreDefaults | Button.Reset | Button.Apply)
        # Display names are the user's own, not settings with defaults: keep them.
        self.buttons.button(Button.RestoreDefaults).clicked.connect(lambda: self._load(replace(AppConfig(), displays=self.displays.prefs())))
        self.buttons.button(Button.Reset).clicked.connect(lambda: self._load(self._saved))
        self.buttons.button(Button.Apply).clicked.connect(self._apply)
        self.buttons.button(Button.Apply).setDefault(True)

        content = QVBoxLayout()
        content.setContentsMargins(0, 20, 0, 0)
        content.setSpacing(0)
        # Shown while Windows' own Night Light is on too: the tints stack.
        self.windows_night_light = WindowsNightLightNotice()
        notice_holder = QWidget()
        notice_layout = QVBoxLayout(notice_holder)
        notice_layout.setContentsMargins(28, 0, 32, 12)
        notice_layout.addWidget(self.windows_night_light)
        self._notice_holder = notice_holder
        notice_holder.setVisible(False)
        content.addWidget(notice_holder)
        banner_holder = QWidget()
        banner_holder_layout = QVBoxLayout(banner_holder)
        banner_holder_layout.setContentsMargins(28, 0, 32, 12)
        banner_holder_layout.addWidget(self.banner)
        self._banner_holder = banner_holder
        content.addWidget(banner_holder)
        content.addWidget(self._stack, 1)
        content.addWidget(Separator())
        footer = QVBoxLayout()
        footer.setContentsMargins(28, 12, 32, 16)
        footer.addWidget(self.buttons)
        content.addLayout(footer)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._navigation())
        layout.addLayout(content, 1)
        self.resize(940, 720)
        self.setMinimumSize(780, 540)

        for signal in (
            self.mode.currentIndexChanged,
            self.day_temperature.valueChanged,
            self.night_temperature.valueChanged,
            self.latitude.valueChanged,
            self.longitude.valueChanged,
            self.night_start.timeChanged,
            self.day_start.timeChanged,
            self.transition.valueChanged,
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
            self.exempt_apps.changed,
            self.displays.changed,
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
        pane.setFixedWidth(232)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(14, 22, 10, 16)
        layout.setSpacing(3)
        identity = QHBoxLayout()
        identity.setContentsMargins(8, 0, 0, 18)
        identity.setSpacing(10)
        self._header_icon = QLabel()
        name = QVBoxLayout()
        name.setSpacing(0)
        title = QLabel(APP_NAME)
        set_font(title, 1.2, HEADING_WEIGHT)
        name.addWidget(title)
        name.addWidget(SecondaryLabel("Brightness and night light", scale=0.88))
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
        self.day_temperature = TemperatureSlider()
        self.night_temperature = TemperatureSlider()
        for field in (self.mode, self._location_row):
            field.setFixedWidth(WIDE_FIELD)
        for slider in (self.day_temperature, self.night_temperature):
            slider.setFixedWidth(WIDE_FIELD + 40)
        for field in (self.latitude, self.longitude, self.night_start, self.day_start, self.transition):
            field.setFixedWidth(NARROW_FIELD)
        self.schedule_info = SecondaryLabel(scale=0.92)
        self.schedule_info.setWordWrap(True)
        self.exempt_fullscreen = ToggleSwitch("Pause night light while an app is fullscreen")
        self.exempt_apps = AppList(self._running_apps)

        # Brightness
        self.schedule = ScheduleEditor()
        self.remember_brightness = ToggleSwitch("Remember each display's brightness")
        self.link_brightness = ToggleSwitch("Change all displays together")
        self.follow_laptop = ToggleSwitch("External displays follow the laptop's brightness keys")
        self.software_dimming = ToggleSwitch("Dim displays without brightness control")
        self.idle_dim = ToggleSwitch("Dim displays when I'm away")
        self.idle_minutes = QSpinBox(minimum=1, maximum=MAX_IDLE_MINUTES, suffix=" min")
        self.idle_level = QSpinBox(minimum=0, maximum=100, singleStep=5, suffix=" %")
        for field in (self.idle_minutes, self.idle_level):
            field.setFixedWidth(NARROW_FIELD)

        # General
        self.start_with_windows = ToggleSwitch(f"Start {APP_NAME} when I sign in")
        self.start_minimized = ToggleSwitch("Start minimized in the system tray")
        self.respect_fullscreen = ToggleSwitch("Never open over fullscreen games, videos or presentations")
        if not can_autostart:
            self.start_with_windows.setEnabled(False)
            self.start_with_windows.setToolTip("Not available on this system")

        # Displays
        self.displays = DisplayList()
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
        layout.addWidget(SectionLabel("Pause night light"))
        pause = Card()
        pause.add(SettingRow("While an app is fullscreen", "Games and videos keep their true colors on the display showing them; "
                             "other displays keep night light", self.exempt_fullscreen))
        pause.add(SettingRow("For these apps", "On the display showing one of them, while it's in front", below=self.exempt_apps))
        layout.addWidget(pause)
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
        away.add(SettingRow("Dim displays when I'm away", "Not while a video, game or presentation keeps the screen on. "
                            "Any input brings them back", self.idle_dim))
        away.add(SettingRow("After", "Without keyboard or mouse input", self.idle_minutes))
        away.add(SettingRow("Dim to", "Never brighter than they were", self.idle_level))
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
        return page

    def _general_page(self) -> QWidget:
        page, layout = _page("General", "Starting with Windows, focus, and about this app.")
        startup = Card()
        startup.add(SettingRow(f"Start {APP_NAME} when I sign in", "Opens in the system tray", self.start_with_windows))
        startup.add(SettingRow("Start minimized", "Only the tray icon at start, without this window", self.start_minimized))
        layout.addWidget(startup)
        layout.addWidget(SectionLabel("Focus"))
        focus = Card()
        focus.add(SettingRow("Never open over fullscreen apps", f"{APP_NAME} won't open its windows on its own while a game, "
                             "video or presentation is fullscreen", self.respect_fullscreen))
        layout.addWidget(focus)
        layout.addWidget(SectionLabel("About"))
        about = Card()
        logs = QPushButton("Open log folder")
        logs.setToolTip("Where the log, probe.txt and compatibility.txt are saved")
        logs.clicked.connect(_open_log_folder)
        about.add(SettingRow(f"{APP_NAME} {__version__}", "Brightness and night light for every display. Free software, GPL-3.0.", logs,
                             icon=icons.night_icon()))
        if self._report_problem is not None:
            report = QPushButton("Report a Problem…")
            report.setToolTip("Saves logs and settings, without personal details, to a zip on the Desktop")
            report.clicked.connect(self._report_problem)
            about.add(SettingRow("Report a problem", "Collects what a bug report needs into one zip", report))
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
        )
        return AppConfig(night_light=night_light, general=general, brightness_schedule=self.schedule.config(),
                         displays=self.displays.prefs())

    def set_windows_night_light(self, on: bool | None) -> None:
        """Show the notice while Windows' own Night Light is on too."""
        self.windows_night_light.setVisible(bool(on))
        self._notice_holder.setVisible(bool(on))

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
        self.exempt_fullscreen.setChecked(night_light.exempt_fullscreen)
        self.exempt_apps.set_apps(night_light.exempt_apps)
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
            # Solar transitions last from sunset to dusk (and dawn to sunrise),
            # so only custom times have a configurable duration.
            self.transition: mode is Mode.TIMES,
        }
        for widget, show in visible.items():
            self._night_card.set_row_visible(self._field_rows[widget], show)
        self._temperature_card.set_row_visible(self._field_rows[self.day_temperature], mode in (Mode.LOCATION, Mode.TIMES))
        self.night_temperature.setEnabled(night_light.enabled)
        for field in (self.idle_minutes, self.idle_level):
            field.setEnabled(self.idle_dim.isChecked())
        self._update_status()
        self._update_schedule_status()

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
        self._header_icon.setPixmap(icon.pixmap(36, 36))

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
    layout.setContentsMargins(28, 0, 32, 24)
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
    return area


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
