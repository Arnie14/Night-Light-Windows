# SPDX-License-Identifier: GPL-3.0-only
"""The release scheme and the installer's identity."""

import importlib.util
import pathlib
import re

import pytest

from nightlight import __version__

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The installer's AppId: upgrades, the uninstaller and Windows' Apps list find
# the installed copy by it. Changing it would install a second copy beside
# the first. Never change it.
APP_ID = "{{F658C16C-1589-4566-8E3C-295499D77F23}"


def build_module():
    spec = importlib.util.spec_from_file_location("build", ROOT / "packaging" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("version", "numbers"), [
    ("0.3.0-alpha.2", (0, 3, 0, 1002)),
    ("0.3.0-beta.1", (0, 3, 0, 2001)),
    ("0.3.0-beta.12", (0, 3, 0, 2012)),
    ("0.3.0-rc.1", (0, 3, 0, 3001)),
    ("0.3.0", (0, 3, 0, 9000)),
    ("0.3.0-dev", (0, 3, 0, 0)),
    ("0.2.0-beta", (0, 2, 0, 2000)),
])
def test_windows_file_versions(version, numbers):
    assert build_module().numeric(version) == numbers


def test_file_versions_increase_from_beta_to_release():
    numeric = build_module().numeric
    order = ["0.2.0-beta", "0.3.0-alpha.1", "0.3.0-beta.1", "0.3.0-beta.2", "0.3.0-rc.1", "0.3.0", "0.3.1-beta.1"]
    assert [numeric(v) for v in order] == sorted(numeric(v) for v in order)


@pytest.mark.parametrize("version", ["0.3", "v0.3.0", "0.3.0-beta.0", "0.3.0-gamma.1", "0.3.0-dev.2", "0.3.0-beta.1000"])
def test_bad_versions_are_refused(version):
    with pytest.raises(ValueError):
        build_module().numeric(version)


def test_the_app_version_follows_the_scheme():
    build_module().numeric(__version__)


def test_the_installer_app_id_never_changes():
    script = (ROOT / "packaging" / "winlight.iss").read_text(encoding="utf-8")
    assert re.search(r"^AppId=(.+)$", script, re.M).group(1) == APP_ID
