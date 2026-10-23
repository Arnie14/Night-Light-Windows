# SPDX-License-Identifier: GPL-3.0-only
from datetime import UTC, datetime, timedelta

from PySide6.QtGui import QColor, QImage

from nightlight.ui.day_ribbon import DayRibbon
from nightlight.ui.widgets import WarmthSlider, kelvin_color

NOON = datetime(2026, 6, 15, 12, tzinfo=UTC)


def night_from_20_to_7(moment):
    return 3000 if moment.hour >= 20 or moment.hour < 7 else 6500


def render(widget, width=480) -> QImage:
    widget.resize(width, widget.height())
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor("white"))
    widget.render(image)
    return image


def test_each_moment_is_painted_in_the_white_the_screen_shows_then():
    ribbon = DayRibbon(night_from_20_to_7, lambda: NOON)
    image = render(ribbon)
    at = lambda hour: image.pixelColor(round(480 * hour / 24), 8)  # noqa: E731
    warm, neutral = kelvin_color(3000), kelvin_color(6500)
    assert abs(at(2).blue() - warm.blue()) < 12 and abs(at(23).blue() - warm.blue()) < 12  # night: amber
    assert abs(at(10).blue() - neutral.blue()) < 12  # day: white
    assert at(12).lightness() < 120  # the needle, at noon


def test_the_needle_follows_a_marker_and_settings_are_read_again_on_refresh():
    temperatures = {"night": 3000}
    ribbon = DayRibbon(lambda moment: temperatures["night"] if moment.hour < 7 else 6500, lambda: NOON)
    render(ribbon)
    temperatures["night"] = 2000
    assert ribbon.kelvins(NOON.replace(hour=0), NOON.replace(hour=0) + timedelta(days=1))[0] == 3000  # cached until the settings change
    ribbon.refresh()
    assert ribbon.kelvins(NOON.replace(hour=0), NOON.replace(hour=0) + timedelta(days=1))[0] == 2000
    ribbon.set_marker(NOON.replace(hour=3))
    image = render(ribbon)
    assert image.pixelColor(60, 8).lightness() < 120  # the needle at 03:00


def test_a_setting_that_cant_be_worked_out_still_paints():
    def broken(_moment):
        raise ValueError("half-typed")

    render(DayRibbon(broken, lambda: NOON, slim=True))


def test_the_temperature_slider_track_shows_the_temperatures_it_sets():
    from PySide6.QtCore import Qt

    slider = WarmthSlider(Qt.Orientation.Horizontal)  # as TemperatureSlider sets it up, on its own
    slider.setRange(10, 65)
    slider.setInvertedAppearance(True)
    slider.setValue(10)  # the handle at the warm (right) end, out of the way
    image = render(slider, 300)
    middle = slider.height() // 2
    cool, warm = image.pixelColor(30, middle), image.pixelColor(200, middle)
    assert cool.blue() > 200 and warm.blue() < 140 and warm.red() > 200  # neutral on the left, amber towards the right
