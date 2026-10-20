# SPDX-License-Identifier: GPL-3.0-only
"""The notices shipped with the Windows app."""

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def notices_module():
    spec = importlib.util.spec_from_file_location("third_party_notices", ROOT / "tools" / "third_party_notices.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(sys.platform != "win32", reason="the notices cover the Windows build's packages")
def test_every_bundled_component_has_its_license_text():
    module = notices_module()
    text = module.render()
    for component in module.COMPONENTS:
        assert component.name in text and component.license in text
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in text  # Qt and PySide6
    assert "Apache License" in text  # astral, tzdata
    assert "PYTHON SOFTWARE FOUNDATION LICENSE" in text.upper()
    assert "Bootloader Exception" in text or "bootloader exception" in text.lower()


def test_the_licenses_kept_in_the_repository_exist():
    for component in notices_module().COMPONENTS:
        for path in component.files:
            if ROOT in path.parents:
                assert path.is_file(), path
