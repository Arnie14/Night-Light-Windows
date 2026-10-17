# SPDX-License-Identifier: GPL-3.0-only
from collections import Counter

from nightlight.core.locations import all_locations, find


def test_labels_are_unique():
    duplicates = [label for label, n in Counter(loc.label for loc in all_locations()).items() if n > 1]
    assert duplicates == []


def test_coordinates_are_in_range():
    assert all(-90 <= loc.latitude <= 90 and -180 <= loc.longitude <= 180 for loc in all_locations())


def test_find_by_zone():
    kyiv = find("Europe/Kyiv")
    assert kyiv is not None
    assert kyiv.label == "Kyiv, Ukraine"
    assert kyiv.latitude == 50.4333 and kyiv.longitude == 30.5167


def test_find_by_old_name():
    assert find("Asia/Calcutta") == find("Asia/Kolkata")
    assert find("Europe/Kiev") == find("Europe/Kyiv")


def test_southern_and_western_hemispheres_are_negative():
    sydney, la = find("Australia/Sydney"), find("America/Los_Angeles")
    assert sydney.latitude < 0 < sydney.longitude
    assert la.longitude < 0 < la.latitude


def test_unknown_zone():
    assert find("Mars/Olympus_Mons") is None
    assert find("") is None
