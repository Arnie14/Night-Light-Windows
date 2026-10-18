# SPDX-License-Identifier: GPL-3.0-only
"""The list of compatible displays and the compatibility report."""

from nightlight import compat
from nightlight.hardware.brightness import DisplayDetails, FakeBrightnessBackend, MonitorInfo
from nightlight.monitors import DisplayState, Status

DELL = MonitorInfo(0, "DELL U2720Q", True, key="DEL41A2:7XKJ9F3", channel="ddc",
                   details=DisplayDetails("DEL", "DELL U2720Q", "7XKJ9F3", "DisplayPort", False, 2021, 27.0, r"\\.\DISPLAY1"))
HDR = MonitorInfo(1, "LG HDR", True, key="GSM5BBF", control="sdr", channel="sdr",
                  details=DisplayDetails("GSM", "LG HDR", "", "HDMI", True, 2022, 27.0, r"\\.\DISPLAY2"))
TV = MonitorInfo(2, "TV", False, key="SAM7136", reason="DDC/CI didn't answer (most TVs don't support it)",
                 detail="Turn DDC/CI on in the display's own menu", status="unsupported",
                 details=DisplayDetails("SAM", "SAMSUNG", "", "HDMI", False, 2019, 54.6, r"\\.\DISPLAY3"))


def test_each_display_says_how_it_is_controlled():
    dell, hdr, tv = compat.entries([DELL, HDR, TV])
    assert (dell.works, dell.brightness, dell.connector, dell.serial) == (True, "DDC/CI", "DisplayPort", "…J9F3")
    assert hdr.brightness == "Windows SDR brightness (HDR)" and hdr.night_light.startswith("No: HDR is on")
    assert not tv.works and tv.brightness == "None" and "most TVs" in tv.status


def test_live_state_overrides_detection():
    states = {DELL.key: DisplayState(DELL, Status.NOT_RESPONDING, message="Not responding", detail="A call hung")}
    [dell] = compat.entries([DELL], states)
    assert not dell.works and dell.status == "Not responding" and dell.detail == "A call hung"


def test_night_light_limits_are_reported():
    [dell] = compat.entries([DELL], warmest={r"\\.\DISPLAY1": 3300})
    assert "3300 K" in dell.night_light


def test_serials_are_partly_hidden():
    assert compat.mask_serial("") == "" and compat.mask_serial("AB12") == "AB12" and compat.mask_serial("7XKJ9F3") == "…J9F3"


def test_the_report_has_what_a_bug_report_needs():
    text = compat.render(compat.entries([DELL, HDR, TV]), {"Winlight": "0.1.0", "build": "26200"})
    assert "Displays: 3, 2 with brightness control" in text
    assert "1. DELL U2720Q" in text and "Brightness:   DDC/CI" in text and "Identity:     DEL41A2:7XKJ9F3" in text
    assert "3. TV" in text and "Why:" in text and "Try:" in text and "Turn DDC/CI on" in text
    assert "7XKJ9F3" not in text.replace("DEL41A2:7XKJ9F3", "")  # only the identity carries it in full


def test_advice_matches_the_problem():
    assert "hung" in compat.advice_for("Not responding")
    assert "waking" in compat.advice_for("Not answering yet, retrying")
    assert "DDC/CI on" in compat.advice_for("DDC/CI didn't answer")


def test_the_simulated_displays_cover_every_kind():
    monitors = FakeBrightnessBackend(latency=0).list_monitors()
    kinds = {e.brightness for e in compat.entries(monitors)}
    assert kinds == {"DDC/CI", "Windows SDR brightness (HDR)", "Software dimming (gamma ramp)", "None"}


def test_system_summary_includes_windows_capabilities():
    summary = compat.system_summary()
    assert "Winlight" in summary and "Python" in summary
