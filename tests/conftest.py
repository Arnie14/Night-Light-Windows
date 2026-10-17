# SPDX-License-Identifier: GPL-3.0-only
import os

import pytest

# No windows are shown, so tests also run without a display (CI, SSH).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def qapp():
    # One application for the whole run: Qt allows only one per process, and
    # some tests need widgets (the settings window) or screens.
    return QApplication.instance() or QApplication([])
