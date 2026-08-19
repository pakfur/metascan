"""expand_name_template: strftime expansion + filename sanitizing."""

from __future__ import annotations

from datetime import datetime

from metascan.core.storyboard_runner import expand_name_template


def test_blank_and_none_return_none():
    assert expand_name_template(None) is None
    assert expand_name_template("") is None
    assert expand_name_template("   ") is None


def test_plain_prefix_passes_through():
    assert expand_name_template("take_") == "take_"


def test_strftime_tokens_expand():
    now = datetime.now()
    out = expand_name_template("%m-%d-%y_")
    assert out == now.strftime("%m-%d-%y_")


def test_path_separators_and_illegal_chars_sanitized():
    # Both separators and Windows-illegal characters collapse to '-',
    # so the result is always a single safe filename fragment.
    assert expand_name_template("a/b\\c") == "a-b-c"
    assert expand_name_template('x:y*z?"<>|') == "x-y-z-----"


def test_surrounding_whitespace_trimmed_before_expansion():
    assert expand_name_template("  take_  ") == "take_"
