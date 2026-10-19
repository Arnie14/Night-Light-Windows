# SPDX-License-Identifier: GPL-3.0-only
"""Opening windows without a white flash.

Windows puts a new window on screen before the app has painted it, so for a
frame or two it is plain white, glaring in dark mode (measured: a frame of
pure white where the settings window opens). A window here starts fully
transparent, and fades in once Qt has painted its first frame.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, QTimer
from PySide6.QtWidgets import QWidget

FADE_MS = 150
# After the first paint, a moment for it to reach the screen; and the
# longest wait for that paint before fading in anyway.
AFTER_PAINT_MS = 16
LONGEST_WAIT_MS = 400


class _Fade(QObject):
    """Fades its parent window in after the window's first paint, then
    deletes itself. It keeps no Python reference to the window (the parent
    is asked for), so no reference cycle is left for the garbage collector:
    breaking one between a window and its children aborted Qt."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._animation = QPropertyAnimation(window, b"windowOpacity", self)
        self._animation.setDuration(FADE_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.finished.connect(self._done)
        self._started = False
        window.installEventFilter(self)
        QTimer.singleShot(LONGEST_WAIT_MS, self, self._start)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.parent() and event.type() == QEvent.Type.Paint:
            obj.removeEventFilter(self)
            QTimer.singleShot(AFTER_PAINT_MS, self, self._start)
        return False

    def _start(self) -> None:
        if not self._started:  # once: after the first paint, or after the longest wait
            self._started = True
            self._animation.start()

    def _done(self) -> None:
        self.parent().setWindowOpacity(1.0)  # opaque again: Windows drops the layered style
        self.deleteLater()


def fade_in(window: QWidget) -> None:
    """Make `window` transparent until its first paint, then fade it in.
    Call just before show() or a dialog's exec()."""
    if window.isVisible() or window.findChild(_Fade) is not None:
        return
    window.setWindowOpacity(0.0)
    _Fade(window)


def show_window(window: QWidget) -> None:
    """Show a top-level window without a flash, or bring it to the front."""
    if not window.isVisible():
        fade_in(window)
        window.show()
    elif window.isMinimized():
        window.showNormal()
    window.raise_()
    window.activateWindow()
