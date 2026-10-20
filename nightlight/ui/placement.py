# SPDX-License-Identifier: GPL-3.0-only
"""Where the tray flyout opens: against the taskbar, next to the tray icon.

Pure geometry, so it is testable without a screen. The approach follows
Monitorian: find which screen edge the taskbar is on from the difference
between the screen and its available (work) area, then open against that
edge. The tray icon's rectangle is used to line the flyout up when it is
believable, but Windows 11 sometimes reports a wrong one, and none at all
for icons in the overflow area, so the corner by the clock is the fallback.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QPoint, QRect, QSize

# Gap between the flyout and the taskbar or screen edge, as on Windows 11.
MARGIN = 12


class Edge(Enum):
    BOTTOM = "bottom"
    TOP = "top"
    LEFT = "left"
    RIGHT = "right"


def taskbar_edge(screen: QRect, available: QRect, anchor: QRect | None = None) -> Edge:
    """The edge the taskbar (or a panel) is on. An auto-hidden taskbar takes
    no space, so then the edge nearest the tray icon is used, or the bottom."""
    if available.bottom() < screen.bottom():
        return Edge.BOTTOM
    if available.top() > screen.top():
        return Edge.TOP
    if available.left() > screen.left():
        return Edge.LEFT
    if available.right() < screen.right():
        return Edge.RIGHT
    if anchor is not None and anchor.isValid() and screen.contains(anchor.center()):
        c = anchor.center()
        distances = {
            Edge.BOTTOM: screen.bottom() - c.y(),
            Edge.TOP: c.y() - screen.top(),
            Edge.LEFT: c.x() - screen.left(),
            Edge.RIGHT: screen.right() - c.x(),
        }
        return min(distances, key=distances.get)
    return Edge.BOTTOM


def flyout_position(size: QSize, screen: QRect, available: QRect, anchor: QRect | None = None, margin: int = MARGIN) -> QPoint:
    """Top-left corner for a flyout of `size`, kept inside the available area."""
    edge = taskbar_edge(screen, available, anchor)
    # Only trust the icon if it is on this screen and in the taskbar, i.e.
    # outside the work area (unless the taskbar auto-hides and takes none).
    icon = None
    if (anchor is not None and anchor.isValid() and screen.contains(anchor.center())
            and (available == screen or not available.contains(anchor.center()))):
        icon = anchor
    w, h = size.width(), size.height()

    if edge in (Edge.BOTTOM, Edge.TOP):
        # Right edge lined up with the icon's, else the screen's right corner.
        x = icon.right() + 1 - w if icon else available.right() + 1 - margin - w
        y = available.bottom() + 1 - margin - h if edge is Edge.BOTTOM else available.top() + margin
    else:
        x = available.left() + margin if edge is Edge.LEFT else available.right() + 1 - margin - w
        y = icon.bottom() + 1 - h if icon else available.bottom() + 1 - margin - h

    x = max(available.left() + margin, min(x, available.right() + 1 - margin - w))
    y = max(available.top() + margin, min(y, available.bottom() + 1 - margin - h))
    return QPoint(x, y)
