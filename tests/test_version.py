# SPDX-License-Identifier: GPL-3.0-only
import re

from nightlight import __version__, display_version


def test_release_names():
    assert display_version("0.1.0-beta.5") == "0.1.0 Beta 5"
    assert display_version("0.2.0-beta") == "0.2.0 Beta"
    assert display_version("1.0.0") == "1.0.0"


def test_the_version_builds_and_installs():
    """packaging/build.py stamps Windows' four numbers from the leading x.y.z;
    the installer upgrades only to a higher one."""
    assert re.match(r"\d+\.\d+\.\d+(-[a-z]+(\.\d+)?)?$", __version__)
