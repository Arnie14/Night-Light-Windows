# SPDX-License-Identifier: GPL-3.0-only
import uuid

from PySide6.QtTest import QTest

from nightlight.single_instance import SingleInstance


def test_second_launch_reaches_the_first():
    name = f"night-light-test-{uuid.uuid4().hex}"
    first = SingleInstance(name)
    assert first.notify_running() is False
    activated = []
    first.activated.connect(lambda: activated.append(True))
    assert SingleInstance(name).notify_running() is True
    for _ in range(100):
        if activated:
            break
        QTest.qWait(10)
    assert activated == [True]
