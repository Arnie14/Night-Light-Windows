# SPDX-License-Identifier: GPL-3.0-only
import logging

from nightlight.support.privacy import Scrubber, ScrubbingFormatter

SCRUB = Scrubber(user="jsmith", computer="JSMITH-PC", home=r"C:\Users\Jsmith")


def test_home_paths_in_both_slash_styles_and_any_case():
    assert SCRUB(r"C:\Users\Jsmith\AppData\Local\x.log") == r"<home>\AppData\Local\x.log"
    assert SCRUB("c:/users/jsmith/Desktop/Winlight.zip") == "<home>/Desktop/Winlight.zip"


def test_user_computer_and_email():
    assert SCRUB("pipe night-light-jsmith on JSMITH-PC") == "pipe night-light-<user> on <computer>"
    assert SCRUB("from a.b+c@mail.example.org") == "from <email>"


def test_words_merely_containing_the_name_are_left_alone():
    scrub = Scrubber(user="ann", computer="", home="")
    assert scrub("ann is here; channel and annotation stay") == "<user> is here; channel and annotation stay"


def test_display_and_windows_details_stay():
    text = "Monitor 0: DELL U2720Q [DEL41A2] (vcp); Windows 10.0.26100; error 0xC0262582"
    assert SCRUB(text) == text


def test_tracebacks_are_scrubbed_too():
    formatter = ScrubbingFormatter("%(message)s")
    formatter_scrub = SCRUB
    import nightlight.support.privacy as privacy

    original = privacy.scrub
    privacy.scrub = formatter_scrub
    try:
        try:
            raise OSError(r"cannot open C:\Users\Jsmith\AppData\settings.json")
        except OSError:
            record = logging.getLogger("t").makeRecord("t", logging.ERROR, __file__, 1, "Saving failed for jsmith", (), None)
            import sys

            record.exc_info = sys.exc_info()
        output = formatter.format(record)
    finally:
        privacy.scrub = original
    assert "Jsmith" not in output and "jsmith" not in output
    assert "<home>" in output and "<user>" in output
