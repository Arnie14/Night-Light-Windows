# SPDX-License-Identifier: GPL-3.0-only
import os

import pytest

# No windows are shown, so tests also run without a display (CI, SSH).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qapp():
    # One application for the whole run: Qt allows only one per process, and
    # some tests need widgets (the settings window) or screens.
    app = QApplication.instance() or QApplication([])
    # As in the app (nightlight/gc_guard.py): garbage is collected on the main
    # thread only, here after each test, never on a monitor or call thread.
    import gc

    gc.disable()
    yield app
    gc.enable()


@pytest.fixture(autouse=True)
def collect_qt_garbage():
    """After each test, delete its leftover Qt objects here, on the main thread
    with no event loop running. Left to Python's garbage collector, they were
    destroyed at a random later moment (a test's widgets and timers sit in
    reference cycles), sometimes mid-event-loop in another test, which crashed
    Qt with an access violation about one full run in two."""
    yield
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent

    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
