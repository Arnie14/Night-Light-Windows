# SPDX-License-Identifier: GPL-3.0-only
from PySide6.QtCore import QPoint, QRect, QSize

from nightlight.ui.placement import MARGIN, Edge, flyout_position, taskbar_edge

SCREEN = QRect(0, 0, 1920, 1080)
SIZE = QSize(360, 300)


def test_taskbar_edge_from_work_area():
    assert taskbar_edge(SCREEN, QRect(0, 0, 1920, 1032)) is Edge.BOTTOM
    assert taskbar_edge(SCREEN, QRect(0, 48, 1920, 1032)) is Edge.TOP
    assert taskbar_edge(SCREEN, QRect(62, 0, 1858, 1080)) is Edge.LEFT
    assert taskbar_edge(SCREEN, QRect(0, 0, 1858, 1080)) is Edge.RIGHT


def test_auto_hidden_taskbar_uses_the_icons_edge():
    assert taskbar_edge(SCREEN, SCREEN, QRect(1700, 2, 24, 24)) is Edge.TOP
    assert taskbar_edge(SCREEN, SCREEN) is Edge.BOTTOM


def test_bottom_taskbar_lines_up_with_the_icon():
    available = QRect(0, 0, 1920, 1032)
    icon = QRect(1700, 1044, 24, 24)
    pos = flyout_position(SIZE, SCREEN, available, icon)
    assert pos == QPoint(icon.right() + 1 - SIZE.width(), 1032 - MARGIN - SIZE.height())


def test_without_an_icon_it_opens_in_the_corner():
    available = QRect(0, 0, 1920, 1032)
    pos = flyout_position(SIZE, SCREEN, available, QRect())
    assert pos == QPoint(1920 - MARGIN - SIZE.width(), 1032 - MARGIN - SIZE.height())


def test_icon_reported_inside_the_work_area_is_not_trusted():
    # Windows 11 can report a wrong rectangle for the tray icon.
    available = QRect(0, 0, 1920, 1032)
    pos = flyout_position(SIZE, SCREEN, available, QRect(200, 500, 24, 24))
    assert pos == flyout_position(SIZE, SCREEN, available)


def test_left_taskbar_opens_beside_it():
    available = QRect(62, 0, 1858, 1080)
    icon = QRect(19, 1000, 24, 24)
    pos = flyout_position(SIZE, SCREEN, available, icon)
    assert pos == QPoint(62 + MARGIN, icon.bottom() + 1 - SIZE.height())


def test_top_taskbar_opens_below_it():
    available = QRect(0, 48, 1920, 1032)
    pos = flyout_position(SIZE, SCREEN, available, QRect(1800, 12, 24, 24))
    assert pos.y() == 48 + MARGIN


def test_flyout_stays_on_a_second_screen():
    screen = QRect(1920, 0, 2560, 1440)
    available = QRect(1920, 0, 2560, 1392)
    pos = flyout_position(SIZE, screen, available, QRect(4300, 1400, 24, 24))
    assert available.contains(QRect(pos, SIZE))
