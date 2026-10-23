# SPDX-License-Identifier: GPL-3.0-only
"""Building blocks of the settings window, in the style of Windows 11's
Settings app: a navigation pane, page titles, cards of setting rows (title
and description on the left, the control on the right) and On/Off switches.

Everything is painted from the palette, so light and dark mode and the
system accent colour carry through without a stylesheet.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from nightlight.ui.widgets import HEADING_WEIGHT, SecondaryLabel, blend, set_font

RADIUS = 7.0  # corner radius of cards, as Windows 11 draws them


def accent(palette: QPalette) -> QColor:
    """The system accent colour (Qt 6.6+), else the selection colour."""
    role = getattr(QPalette.ColorRole, "Accent", QPalette.ColorRole.Highlight)
    return palette.color(role)


def luminance(color: QColor) -> float:
    """WCAG relative luminance."""
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b, _a = color.getRgbF()
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: QColor, b: QColor) -> float:
    """WCAG contrast ratio, 1 to 21."""
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def text_on(fill: QColor) -> QColor:
    """Black or white, whichever reads better on `fill`."""
    white, black = QColor("#ffffff"), QColor("#000000")
    return white if contrast(fill, white) >= contrast(fill, black) else black


def is_dark(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightnessF() < 0.5


class ToggleSwitch(QAbstractButton):
    """An On/Off switch, like Windows 11's, with its state written beside it.

    A checkable button underneath, so it keeps the check box interface
    (setChecked, isChecked, toggled) and works from the keyboard."""

    TRACK_W, TRACK_H = 40, 20

    def __init__(self, accessible_name: str = "") -> None:
        super().__init__()
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if accessible_name:
            self.setAccessibleName(accessible_name)
        self.toggled.connect(self.update)

    def sizeHint(self) -> QSize:
        text = max(self.fontMetrics().horizontalAdvance(t) for t in ("On", "Off"))
        return QSize(text + 12 + self.TRACK_W + 4, max(self.TRACK_H + 6, self.fontMetrics().height() + 6))

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        palette = self.palette()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        enabled = self.isEnabled()
        text_color = palette.color(QPalette.ColorRole.WindowText)
        if not enabled:
            text_color = blend(text_color, palette.color(QPalette.ColorRole.Window), 0.55)

        track = QRectF(self.width() - self.TRACK_W - 2, (self.height() - self.TRACK_H) / 2, self.TRACK_W, self.TRACK_H)
        text_rect = QRectF(0, 0, track.left() - 12, self.height())
        painter.setPen(text_color)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "On" if self.isChecked() else "Off")

        radius = self.TRACK_H / 2
        knob_r = self.TRACK_H / 2 - (4.5 if not self.isDown() else 3.5)
        if self.isChecked():
            fill = accent(palette) if enabled else blend(text_color, palette.color(QPalette.ColorRole.Window), 0.3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(track, radius, radius)
            knob_color = QColor("#000000") if is_dark(palette) else QColor("#ffffff")  # as Windows 11 draws it
            center = QPointF(track.right() - radius, track.center().y())
        else:
            painter.setPen(QPen(text_color, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
            knob_color = text_color
            center = QPointF(track.left() + radius, track.center().y())
        if self.underMouse() and enabled:
            knob_r += 0.8
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_color)
        painter.drawEllipse(center, knob_r, knob_r)
        if self.hasFocus():
            pen = QPen(text_color, 1.4)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(track.adjusted(-3, -3, 3, 3), radius + 3, radius + 3)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class Card(QFrame):
    """A rounded panel holding setting rows, divided by hairlines."""

    def __init__(self, title: str = "") -> None:
        super().__init__()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 2, 0, 2)
        self._layout.setSpacing(0)
        self._card_rows: list[QWidget] = []
        self.title = title

    def add(self, widget: QWidget) -> QWidget:
        if self._card_rows:
            divider = _Divider()
            self._layout.addWidget(divider)
            widget.setProperty("divider", divider)
        self._card_rows.append(widget)
        self._layout.addWidget(widget)
        return widget

    def set_row_visible(self, row: QWidget, visible: bool) -> None:
        row.setVisible(visible)
        self._sync_dividers()

    def _sync_dividers(self) -> None:
        # A divider shows only between two visible rows.
        seen_visible = False
        for row in self._card_rows:
            divider = row.property("divider")
            if divider is not None:
                divider.setVisible(seen_visible and not row.isHidden())
            seen_visible |= not row.isHidden()

    def paintEvent(self, _event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        base = palette.color(QPalette.ColorRole.Base)
        fill = blend(window, base, 0.75) if not is_dark(palette) else blend(window, QColor("#ffffff"), 0.045)
        border = blend(window, palette.color(QPalette.ColorRole.WindowText), 0.1 if not is_dark(palette) else 0.06)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(border, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)


class _Divider(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(2)

    def paintEvent(self, _event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        painter = QPainter(self)
        # One device pixel, unblended, in the middle of the widget: at display
        # scaling like 125% a 1-pixel rectangle lands between pixels, so some
        # lines came out twice as thick, and a line at the edge could fall
        # outside the widget and vanish.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(blend(window, palette.color(QPalette.ColorRole.WindowText), 0.12), 0))
        middle = self.height() / 2
        painter.drawLine(QPointF(16, middle), QPointF(self.width() - 17, middle))


class SettingRow(QWidget):
    """Title (and a quieter description) on the left, the control on the right;
    or, with `below`, a wide control under the text."""

    def __init__(self, title: str, description: str = "", control: QWidget | None = None, icon: QIcon | None = None,
                 below: QWidget | None = None) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 15, 16, 15)
        outer.setSpacing(12)
        line = QHBoxLayout()
        line.setSpacing(16)
        if icon is not None:
            glyph = QLabel()
            glyph.setPixmap(icon.pixmap(20, 20))
            glyph.setFixedWidth(24)
            line.addWidget(glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(3)
        self.title = QLabel(title)
        self.title.setWordWrap(True)
        text.addWidget(self.title)
        self.description = SecondaryLabel(description, scale=0.9)
        self.description.setWordWrap(True)
        text.addWidget(self.description)
        line.addLayout(text, 1)
        if control is not None:
            line.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addLayout(line)
        if below is not None:
            outer.addWidget(below)
        self.control = control
        # Only now, inside this row: showing a widget without a parent opens
        # it as a window of its own, and every row's label flashed on screen
        # while the settings window was first built.
        self.description.setVisible(bool(description))

    def set_description(self, text: str) -> None:
        self.description.setText(text)
        self.description.setVisible(bool(text))


class SectionLabel(QLabel):
    """A small heading above a card."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        set_font(self, 1.0, QFont.Weight.DemiBold)
        self.setContentsMargins(2, 18, 0, 6)


class PageTitle(QWidget):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 0, 0, 12)
        layout.setSpacing(4)
        heading = QLabel(title)
        set_font(heading, 1.75, HEADING_WEIGHT)
        layout.addWidget(heading)
        if subtitle:
            layout.addWidget(SecondaryLabel(subtitle))


class ActionButton(QAbstractButton):
    """A button as Windows 11 draws them: "accent" (filled with the accent
    colour, for the main action), "standard" (a quiet card with a hairline
    border) or "subtle" (text only until hovered)."""

    HEIGHT = 32
    RADIUS = 4.0

    def __init__(self, text: str, kind: str = "standard") -> None:
        super().__init__()
        self.setText(text)
        self.kind = kind
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(text)

    def sizeHint(self) -> QSize:
        width = self.fontMetrics().horizontalAdvance(self.text()) + 2 * (14 if self.kind == "subtle" else 20)
        return QSize(max(width, 0 if self.kind == "subtle" else 104), max(self.HEIGHT, self.fontMetrics().height() + 12))

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        dark = is_dark(palette)
        enabled, hover, down = self.isEnabled(), self.underMouse(), self.isDown()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        border = Qt.PenStyle.NoPen
        if self.kind == "accent" and enabled:
            fill = accent(palette)
            if down:
                fill = blend(fill, window, 0.25)
            elif hover:
                fill = blend(fill, QColor("#ffffff") if not dark else QColor("#000000"), 0.1)
            label = text_on(fill)
        elif self.kind == "subtle":
            fill = blend(window, text, 0.09 if down else 0.05) if (hover or down) and enabled else None
            label = text
        else:
            base = blend(window, QColor("#ffffff"), 0.7) if not dark else blend(window, QColor("#ffffff"), 0.06)
            fill = blend(base, text, 0.06 if down else 0.03) if (hover or down) and enabled else base
            if self.kind == "accent":  # disabled
                fill = blend(window, text, 0.07)
            border = QPen(blend(window, text, 0.16 if not dark else 0.12), 1)
            label = text
        if not enabled:
            label = blend(text, window, 0.6)
        if fill is not None:
            painter.setPen(border)
            painter.setBrush(fill)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)
        painter.setPen(label)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.text())
        if self.hasFocus() and enabled:
            painter.setPen(QPen(text, 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75), self.RADIUS + 1, self.RADIUS + 1)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class ActionBar(QWidget):
    """The bottom of the settings window: "Restore defaults" on the left,
    "Reset" and "Apply" on the right. Answers button(StandardButton) like the
    QDialogButtonBox it replaces."""

    def __init__(self) -> None:
        super().__init__()
        from PySide6.QtWidgets import QDialogButtonBox

        Button = QDialogButtonBox.StandardButton
        self.restore_defaults = ActionButton("Restore defaults", "subtle")
        self.reset = ActionButton("Reset", "standard")
        self.apply = ActionButton("Apply", "accent")
        self._buttons = {Button.RestoreDefaults: self.restore_defaults, Button.Reset: self.reset, Button.Apply: self.apply}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.restore_defaults)
        layout.addStretch(1)
        layout.addWidget(self.reset)
        layout.addWidget(self.apply)

    def button(self, which) -> ActionButton:
        return self._buttons[which]


class NavButton(QAbstractButton):
    """An entry of the navigation pane: a glyph and a label; the current
    page has a soft highlight and an accent bar, as in Windows 11."""

    def __init__(self, text: str, glyph: Callable[[QColor], QIcon]) -> None:
        super().__init__()
        self.setText(text)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._glyph = glyph
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(180, max(36, self.fontMetrics().height() + 18))

    def paintEvent(self, _event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        text = palette.color(QPalette.ColorRole.WindowText)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        hover = self.underMouse()
        if self.isChecked() or hover:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(blend(window, text, 0.09 if self.isChecked() else 0.05))
            painter.drawRoundedRect(rect, 5, 5)
        if self.isChecked():
            bar = QRectF(rect.left(), rect.center().y() - 8, 3, 16)
            path = QPainterPath()
            path.addRoundedRect(bar, 1.5, 1.5)
            painter.fillPath(path, accent(palette))
        icon = self._glyph(text)
        icon.paint(painter, int(rect.left() + 14), int(rect.center().y() - 9), 18, 18)
        painter.setPen(text)
        painter.drawText(rect.adjusted(44, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        if self.hasFocus():
            painter.setPen(QPen(text, 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 5, 5)

    def event(self, event) -> bool:
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            self.update()
        return super().event(event)


class StatusBadge(QWidget):
    """A small rounded label: green for working, quiet grey otherwise."""

    def __init__(self, text: str, good: bool) -> None:
        super().__init__()
        self._text = text
        self._good = good
        font = QFont(self.font())
        font.setPointSizeF(font.pointSizeF() * 0.88)
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self._text) + 20, metrics.height() + 8)

    def paintEvent(self, _event) -> None:
        palette = self.palette()
        window = palette.color(QPalette.ColorRole.Window)
        dark = is_dark(palette)
        if self._good:
            tone = QColor("#6ccb5f") if dark else QColor("#0f7b0f")
        else:
            tone = blend(palette.color(QPalette.ColorRole.WindowText), window, 0.35)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(blend(window, tone, 0.16 if dark else 0.12))
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.setPen(tone)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
