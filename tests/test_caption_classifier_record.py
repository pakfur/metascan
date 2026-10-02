"""Tests for the act gate, quote verification and record assembly."""

from __future__ import annotations

import pytest

from scripts.caption_classifier.parse import ParseError, build_record, verify_quote
from tests._caption_classifier_helpers import answer

CAPTION = (
    "__ALICE__ sits at the café table. Her right hand holds a cup; "
    "her right   hand also strokes her __HAIR__."
)


def _record(males, females, **kw):
    content, tokens = answer(**kw)
    return build_record(
        caption=CAPTION, males=males, females=females, content=content, tokens=tokens
    )


def test_gate_removes_acts_the_counts_rule_out_and_flags_the_conflict():
    rec = _record(0, 1, act="I", alts={"act": {"I": 0.7, "A": 0.3}})
    assert rec["act_raw"]["I"] == 0.7
    assert rec["act_gated"]["I"] == 0.0 and rec["act_gated"]["A"] == 1.0
    assert rec["act_gate_conflict"] is True


def test_uncounted_male_partner_keeps_pov_act():
    rec = _record(0, 1, partner="B", act="I", alts={"act": {"I": 0.7, "A": 0.3}})
    assert rec["act_gated"]["I"] == 0.7
    assert rec["act_gate_conflict"] is False


def test_all_mass_on_forbidden_acts_falls_back_to_unclear():
    rec = _record(0, 1, act="L", alts={"act": {"L": 0.8, "M": 0.2}})
    assert rec["act_gated"]["S"] == 1.0
    assert sum(rec["act_gated"].values()) == 1.0
    assert rec["act_gate_conflict"] is True


def test_blank_counts_leave_the_act_ungated():
    rec = _record(None, 1, act="L", alts={"act": {"L": 0.8, "M": 0.2}})
    assert rec["act_gated"] == rec["act_raw"]
    assert rec["act_gate_conflict"] is False


def test_issues_carry_a_quote_check():
    issues = [
        {
            "type": "extra_limb",
            "quote_a": "Her right hand holds a cup",
            "quote_b": "her right hand also strokes",
        },
        {
            "type": "gaze_conflict",
            "quote_a": "looks away",
            "quote_b": "Her right hand holds a cup",
        },
    ]
    rec = _record(0, 1, issues=issues)
    assert [i["quote_verified"] for i in rec["issues"]] == [True, False]
    assert rec["issues"][0]["type"] == "extra_limb"


def test_quote_check_ignores_case_spacing_and_edge_punctuation():
    assert verify_quote("AT THE CAFÉ TABLE.", CAPTION)
    assert verify_quote("“at the café table”", "she sat “at the café table” alone")
    assert not verify_quote("at the kitchen table", CAPTION)
    assert not verify_quote("  ", CAPTION)


def test_unknown_issue_type_is_rejected():
    issues = [{"type": "bad_type", "quote_a": "a", "quote_b": "b"}]
    with pytest.raises(ParseError, match="bad_type"):
        _record(0, 1, issues=issues)


def test_truncated_answer_is_rejected():
    content, tokens = answer()
    with pytest.raises(ParseError, match="not JSON"):
        build_record(
            caption=CAPTION, males=0, females=1, content=content[:-3], tokens=tokens
        )
