# SPDX-License-Identifier: GPL-3.0-only
"""Small reusable widgets shared by the flyout and the settings window.

Styling stays within what the native style draws: fonts, spacing and colours
derived from the palette, so light/dark mode and the Windows accent colour
keep working without a stylesheet.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QIcon, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from nightlight.core.colortemp import MAX_KELVIN, MIN_KELVIN, whitepoint
from nightlight.hardware.brightness import MonitorInfo

# While the slider moves, send at most one brightness change per this long,
# so the monitor follows the drag (as in Monitorian and Twinkle Tray) without
# flooding a slow DDC/CI link. The final position is always sent.
BRIGHTNESS_THROTTLE_MS = 100
# How far quieter text is faded from the text colour towards the background.
SECONDARY_FADE_LIGHT = 0.28
SECONDARY_FADE_DARK = 0.38
NOT_SUPPORTED = "No brightness control"
# Said under the name when brightness isn't the display's own backlight.
CONTROL_CAPTIONS = {
    "sdr": ("HDR · SDR content brightness", "HDR is on, so this sets Windows' brightness for SDR content, like Settings > Display > HDR."),
    "software": (
        "Software dimming",
        (
            "This display has no brightness control Windows can reach, so its colors are dimmed instead. "
            "Blacks stay black but the backlight stays on, so it saves no power."
        ),
    ),
}


def blend(a: QColor, b: QColor, amount: float) -> QColor:
    """`amount` of the way from a to b."""
    return QColor.fromRgbF(*(x + (y - x) * amount for x, y in zip(a.getRgbF()[:3], b.getRgbF()[:3])))


# Large headings are Bold, not DemiBold: on Windows, Qt can draw DemiBold
# Segoe UI Variable with its much thinner Semilight face, which looks washed
# out as dark text on a light background.
HEADING_WEIGHT = QFont.Weight.Bold


# Windows 11's titles use the display cut of Segoe UI Variable; asked for by
# its own family name, its semibold is drawn as itself (see HEADING_WEIGHT).
DISPLAY_FAMILY = "Segoe UI Variable Display Semibold"


@cache
def _has_display_family() -> bool:
    return DISPLAY_FAMILY in QFontDatabase.families()


def set_heading_font(widget: QWidget, scale: float) -> None:
    """A title: Windows 11's display face where installed, else bold."""
    font = QFont(widget.font())
    font.setPointSizeF(font.pointSizeF() * scale)
    if _has_display_family():
        font.setFamilies([DISPLAY_FAMILY])
        font.setWeight(QFont.Weight.Normal)
    else:
        font.setWeight(HEADING_WEIGHT)
    widget.setFont(font)


def kelvin_color(kelvin: float) -> QColor:
    """White as the screen shows it under night light at `kelvin`: the same
    blackbody table as the gamma ramps (colortemp.whitepoint)."""
    return QColor.fromRgbF(*whitepoint(kelvin))


def set_font(widget: QWidget, scale: float = 1.0, weight: QFont.Weight | None = None) -> None:
    font = QFont(widget.font())
    font.setPointSizeF(font.pointSizeF() * scale)
    if weight is not None:
        font.setWeight(weight)
    widget.setFont(font)


class SecondaryLabel(QLabel):
    """Quieter text for status lines and hints: the text colour faded towards
    the background, rather than the greyed-out look of a disabled label.
    Follows the system switching between light and dark mode."""

    def __init__(self, text: str = "", scale: float = 1.0) -> None:
        super().__init__(text)
        if scale != 1.0:
            set_font(self, scale)
        self._recolor()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.StyleChange):
            self._recolor()

    def _recolor(self) -> None:
        app = QApplication.palette()
        palette = self.palette()
        window = app.color(QPalette.ColorRole.Window)
        # Light mode fades less: near Windows 11's secondary text (#5f5f5f on
        # #f3f3f3), so captions and percentages stay easy to read.
        amount = SECONDARY_FADE_LIGHT if window.lightnessF() >= 0.5 else SECONDARY_FADE_DARK
        faded = blend(app.color(QPalette.ColorRole.WindowText), window, amount)
        palette.setColor(QPalette.ColorRole.WindowText, faded)
        self.setPalette(palette)


class Separator(QWidget):
    """A hairline that is visible in light and dark mode without being loud."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # How far from the background towards the text colour. A faint line
    # reads well on a dark background but disappears on a light one.
    STRENGTH_LIGHT = 0.3
    STRENGTH_DARK = 0.24

    def paintEvent(self, event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        strength = self.STRENGTH_DARK if window.lightnessF() < 0.5 else self.STRENGTH_LIGHT
        painter = QPainter(self)
        painter.fillRect(self.rect(), blend(window, palette.color(QPalette.ColorRole.WindowText), strength))


class GlyphButton(QToolButton):
    """A flat icon button whose glyph is drawn in the current text colour."""

    def __init__(self, draw: Callable[[QColor], QIcon], tooltip: str, size: int = 16) -> None:
        super().__init__(toolTip=tooltip, autoRaise=True)
        self._draw = draw
        self.setIconSize(QSize(size, size))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._redraw()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self._redraw()

    def _redraw(self) -> None:
        palette = self.palette()
        self.setIcon(self._draw(blend(palette.color(QPalette.ColorRole.ButtonText), palette.color(QPalette.ColorRole.Button), 0.25)))


class WindowsNightLightNotice(QFrame):
    """Shown while Windows' own Night Light is on: the two tints stack, so
    colors end up far too warm. Winlight is meant to replace it."""

    SETTINGS_URL = "ms-settings:nightlight"

    def __init__(self, compact: bool = False) -> None:
        super().__init__(frameShape=QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        text = (
            "Windows Night Light is on too, so colors are tinted twice."
            if compact
            else "Windows Night Light is on too. Both tint the screen, so colors end up too warm. "
            "Winlight replaces it: turn Windows Night Light off."
        )
        label = QLabel(text)
        label.setWordWrap(True)
        self.button = QPushButton("Turn it off…" if compact else "Open Windows Settings")
        self.button.setToolTip("Opens Settings > System > Display > Night light")
        self.button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.SETTINGS_URL)))
        layout.addWidget(label, 1)
        layout.addWidget(self.button)
        self.hide()


class MonitorBrightness(QWidget):
    """Monitor name and level on one line, a full-width slider below.

    The slider only moves when the display can really be changed right now
    (its state is OK). Otherwise it is disabled, with a few words under it
    saying why and the longer advice in its tooltip, rather than a slider
    that moves and does nothing."""

    brightnessRequested = Signal(str, int)  # display key, value

    def __init__(self, info: MonitorInfo, state=None, name: str = "") -> None:
        super().__init__()
        self.info = info
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        top = QHBoxLayout()
        title = QLabel(name or info.name)  # a custom name (Settings > Displays), else its own
        set_font(title, weight=QFont.Weight.DemiBold)
        top.addWidget(title, 1)
        layout.addLayout(top)
        if caption := CONTROL_CAPTIONS.get(info.control):
            text, tooltip = caption
            label = SecondaryLabel(text, scale=0.88)
            label.setToolTip(tooltip)
            layout.addWidget(label)

        self.value_label = SecondaryLabel("…")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value_label.setMinimumWidth(self.value_label.fontMetrics().horizontalAdvance("100%"))
        top.addWidget(self.value_label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(info.minimum, 100)  # e.g. above 0 for laptop panels, see MonitorInfo
        self.slider.setPageStep(10)
        self.slider.setEnabled(False)  # until the display's state says it can be changed
        layout.addWidget(self.slider)
        self.explanation = SecondaryLabel("", scale=0.9)
        self.explanation.setWordWrap(True)
        layout.addWidget(self.explanation)

        self._unsent = False
        self._throttle = QTimer(self, singleShot=True, interval=BRIGHTNESS_THROTTLE_MS)
        self._throttle.timeout.connect(self._on_throttle)
        self.slider.valueChanged.connect(self._on_slider_changed)
        if state is not None:
            self.update_state(state)
        elif not info.supported:
            self._explain(info.reason or NOT_SUPPORTED, info.detail)
            self.value_label.setText("–")
        else:
            self._explain("", "")

    @property
    def key(self) -> str:
        return self.info.key

    def clamp(self, value: int) -> int:
        """`value` within what this monitor's slider allows."""
        return max(self.slider.minimum(), min(self.slider.maximum(), value))

    @property
    def controllable(self) -> bool:
        return self.slider.isEnabled()

    def update_state(self, state) -> None:
        """Follow a monitors.DisplayState: value, enabled, explanation."""
        controllable = state.controllable
        if not self.slider.isSliderDown():  # a fresh reading mustn't yank the slider while it's held
            value = state.value
            if value is not None:
                self.slider.blockSignals(True)
                self.slider.setValue(value)
                self.slider.blockSignals(False)
            self.value_label.setText(f"{value}%" if value is not None and state.info.supported else "–" if not state.info.supported else "…")
        self.slider.setEnabled(controllable)
        self._explain(state.message or ("" if controllable else NOT_SUPPORTED), state.detail)

    def _explain(self, text: str, detail: str) -> None:
        self.explanation.setText(text)
        self.explanation.setVisible(bool(text))
        self.explanation.setToolTip(detail)
        self.slider.setToolTip(detail if not self.slider.isEnabled() else "")

    def _on_slider_changed(self, value: int) -> None:
        self.value_label.setText(f"{value}%")
        if self._throttle.isActive():
            self._unsent = True
        else:
            self._send()
            self._throttle.start()

    def _on_throttle(self) -> None:
        if self._unsent:
            self._unsent = False
            self._send()
            self._throttle.start()

    def _send(self) -> None:
        if self.slider.isEnabled():
            self.brightnessRequested.emit(self.info.key, self.slider.value())


class WarmthSlider(QSlider):
    """A slider whose track is the temperatures it sets: neutral white at the
    cool end, through amber, to the warmest. The handle is the style's own."""

    TRACK = 4.0  # px, as Windows 11's slider track

    def paintEvent(self, _event) -> None:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        groove = style.subControlRect(QStyle.ComplexControl.CC_Slider, option, QStyle.SubControl.SC_SliderGroove, self)
        handle = style.pixelMetric(QStyle.PixelMetric.PM_SliderLength, option, self)
        # The track spans the handle's travel, so each point sits under the value it stands for.
        left, right = groove.left() + handle / 2, groove.right() - handle / 2
        track = QRectF(left, groove.center().y() - self.TRACK / 2 + 0.5, max(1.0, right - left), self.TRACK)
        gradient = QLinearGradient(QPointF(track.left(), 0), QPointF(track.right(), 0))
        steps = 12
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        for i in range(steps + 1):
            share = i / steps
            fraction = 1 - share if self.invertedAppearance() else share  # inverted: the high (cool) end on the left
            kelvin = (self.minimum() + (self.maximum() - self.minimum()) * fraction) * 100
            color = kelvin_color(kelvin)
            gradient.setColorAt(share, color if self.isEnabled() else blend(window, color, 0.35))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # A hairline, so the neutral end stays visible on a light card.
        painter.setPen(QPen(blend(window, palette.color(QPalette.ColorRole.WindowText), 0.22), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(track, self.TRACK / 2, self.TRACK / 2)
        option.subControls = QStyle.SubControl.SC_SliderHandle
        style.drawComplexControl(QStyle.ComplexControl.CC_Slider, option, painter, self)


class TemperatureSlider(QWidget):
    """Kelvin slider like KDE's, cool (no filter) on the left and warm on the
    right, with quiet captions at its ends and the value after it."""

    valueChanged = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.slider = WarmthSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(MIN_KELVIN // 100, MAX_KELVIN // 100)  # steps of 100 K
        self.slider.setPageStep(5)
        self.slider.setInvertedAppearance(True)  # high (cool) values on the left
        self.slider.setInvertedControls(True)
        self.slider.setMinimumWidth(160)
        self.slider.setToolTip("Left: cool, no filter. Right: warm.")
        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._label.setMinimumWidth(self._label.fontMetrics().horizontalAdvance("10000 K"))

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(SecondaryLabel("Cool", scale=0.88))
        row.addWidget(self.slider, 1)
        row.addWidget(SecondaryLabel("Warm", scale=0.88))
        row.addSpacing(6)
        row.addWidget(self._label)

        self.slider.valueChanged.connect(self._on_changed)
        self._on_changed(self.slider.value())

    def value(self) -> int:
        return self.slider.value() * 100

    def setValue(self, kelvin: int) -> None:
        self.slider.setValue(round(kelvin / 100))

    def _on_changed(self, steps: int) -> None:
        self._label.setText(f"{steps * 100} K")
        self.valueChanged.emit(steps * 100)
