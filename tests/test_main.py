# SPDX-License-Identifier: GPL-3.0-only
"""main.py: what happens to errors nothing else caught."""

import logging
import sys
import threading

from PySide6.QtCore import QEventLoop, QTimer

import main


def test_uncaught_exceptions_in_qt_slots_and_threads_reach_the_log(monkeypatch, caplog):
    # The installed app has no console: without this they'd vanish without a trace.
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)  # restored after the test
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    main.log_uncaught()

    def broken_slot():
        raise RuntimeError("a bug in a timer")

    def broken_thread():
        raise ValueError("a bug in a thread")

    with caplog.at_level(logging.CRITICAL, logger="nightlight"):
        loop = QEventLoop()  # as app.exec() runs them (QTest.qWait re-raises instead)
        QTimer.singleShot(0, broken_slot)
        QTimer.singleShot(50, loop.quit)
        loop.exec()
        thread = threading.Thread(target=broken_thread, name="monitor-service")
        thread.start()
        thread.join()
    messages = [(r.getMessage(), r.exc_info[1].args[0] if r.exc_info else None) for r in caplog.records]
    assert ("Uncaught exception", "a bug in a timer") in messages
    assert ("Uncaught exception in thread monitor-service", "a bug in a thread") in messages
