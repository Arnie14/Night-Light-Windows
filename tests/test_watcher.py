# SPDX-License-Identifier: GPL-3.0-only
from nightlight.watcher import RESUME_GAP_S, DisplayWatcher


def test_resume_is_noticed_from_a_clock_jump(qapp):
    now = [1000.0]
    watcher = DisplayWatcher(qapp, clock=lambda: now[0])
    resumed = []
    watcher.resumed.connect(lambda: resumed.append(True))
    now[0] += 5
    watcher.check_clock()
    assert resumed == []
    now[0] += RESUME_GAP_S + 60
    watcher.check_clock()
    assert resumed == [True]
