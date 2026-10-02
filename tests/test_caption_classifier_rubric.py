"""Tests for the classifier rubric and its act participant gate."""

from __future__ import annotations

from scripts.caption_classifier.rubric import (
    ACT_BY_LETTER,
    ACTS,
    UNCLEAR,
    allowed_acts,
)


def _names(letters):
    return {ACT_BY_LETTER[letter].name for letter in letters}


def test_act_letters_run_a_to_s_in_order():
    assert "".join(a.letter for a in ACTS) == "ABCDEFGHIJKLMNOPQRS"
    assert ACT_BY_LETTER[UNCLEAR].name == "unclear"


def test_solo_woman_without_partner_gets_only_solo_acts():
    assert _names(allowed_acts(0, 1, "A")) == {
        "none-artistic",
        "breast-fondling",
        "female-masturbation",
        "female-toy-masturbation",
        "object-insertion",
        "unclear",
    }


def test_uncounted_male_partner_unlocks_pov_acts():
    got = _names(allowed_acts(0, 1, "B"))
    assert {"fellatio", "handjob", "doggy", "cowgirl", "cunnilingus"} <= got
    assert "male-masturbation" not in got  # needs a counted man
    assert "ff-tribbing" not in got


def test_unknown_partner_counts_toward_total_but_not_gender():
    got = _names(allowed_acts(0, 1, "D"))
    assert {"partner-manual-female", "cunnilingus"} <= got
    assert "fellatio" not in got
    assert "doggy" not in got


def test_tribbing_needs_two_counted_women():
    assert "ff-tribbing" not in _names(allowed_acts(0, 1, "C"))
    assert "ff-tribbing" in _names(allowed_acts(0, 2, "A"))


def test_blank_counts_disable_the_gate():
    assert allowed_acts(None, 1, "A") == frozenset(ACT_BY_LETTER)
    assert allowed_acts(1, None, "D") == frozenset(ACT_BY_LETTER)


def test_none_and_unclear_are_always_allowed():
    for males, females in [(0, 0), (1, 0), (0, 1), (2, 2)]:
        for partner in "ABCD":
            assert {"A", UNCLEAR} <= allowed_acts(males, females, partner)
