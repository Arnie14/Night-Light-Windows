# SPDX-License-Identifier: GPL-3.0-only
import time

from PySide6.QtWidgets import QApplication, QWidget

from nightlight.ui import reveal


def wait_until(condition, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    return condition()


def test_a_window_opens_transparent_then_fades_in():
    window = QWidget()
    reveal.show_window(window)
    assert window.isVisible() and window.windowOpacity() < 0.5  # nothing to flash before the first paint
    assert wait_until(lambda: window.windowOpacity() == 1.0)
    window.close()


def test_showing_an_open_window_only_brings_it_forward():
    window = QWidget()
    window.show()
    reveal.show_window(window)
    assert window.windowOpacity() == 1.0
    window.showMinimized()
    reveal.show_window(window)
    assert wait_until(lambda: not window.isMinimized())
    window.close()


def test_fading_in_without_a_paint_still_ends_opaque():
    window = QWidget()
    reveal.fade_in(window)
    assert window.windowOpacity() == 0.0
    window.show()
    assert wait_until(lambda: window.windowOpacity() == 1.0, reveal.LONGEST_WAIT_MS / 1000 + 1.0)
    window.close()
