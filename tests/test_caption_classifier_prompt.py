"""Tests for the classifier grammar, system prompt and prompt version."""

from __future__ import annotations

import json
import re

from metascan.core.t2i_captions import CaptionRow
from scripts.caption_classifier import prompt
from scripts.caption_classifier.grammar import GRAMMAR
from scripts.caption_classifier.rubric import (
    ACT_BY_LETTER,
    ACTS,
    EMOTION,
    ISSUE_TYPES,
    KISS,
    PARTNER,
    allowed_acts,
)

_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"')
_CLASS = re.compile(r"\[[^\]]*\]")


def _rules():
    rules = {}
    for line in GRAMMAR.strip().splitlines():
        name, body = line.split(" ::= ", 1)
        rules[name] = body
    return rules


def test_grammar_has_no_escaped_hyphen():
    assert "\\-" not in GRAMMAR


def test_grammar_defines_every_rule_it_references():
    rules = _rules()
    assert next(iter(rules)) == "root"
    for body in rules.values():
        bare = _CLASS.sub(" ", _LITERAL.sub(" ", body))
        for ref in re.findall(r"[a-z][a-z_]*", bare):
            assert ref in rules, ref


def test_grammar_lists_every_letter_and_issue_type():
    rules = _rules()
    assert rules["act"] == "[ABCDEFGHIJKLMNOPQRS]"
    assert rules["partner"] == "[ABCD]"
    assert rules["kiss"] == "[YN]"
    assert rules["emotion"] == "[ABC]"
    for name in ISSUE_TYPES:
        assert f'"{name}"' in rules["itype"]


def test_system_prompt_names_every_act_and_issue_and_placeholders():
    for act in ACTS:
        assert f"{act.letter} {act.name}:" in prompt.SYSTEM_PROMPT
    for name in ISSUE_TYPES:
        assert name in prompt.SYSTEM_PROMPT
    assert "__ALICE__" in prompt.SYSTEM_PROMPT


def test_worked_examples_are_valid_answers():
    for ex in prompt.EXAMPLES:
        a = ex.answer
        assert a["partner"] in PARTNER and a["kiss"] in KISS
        assert a["emotion"] in EMOTION and a["act"] in ACT_BY_LETTER
        assert a["act"] in allowed_acts(ex.males, ex.females, a["partner"])
        for issue in a["issues"]:
            assert issue["type"] in ISSUE_TYPES
            assert issue["quote_a"] in ex.caption and issue["quote_b"] in ex.caption
        compact = json.dumps(a, separators=(",", ":"))
        assert compact in prompt.SYSTEM_PROMPT


def test_user_message_marks_blank_cells_with_question_marks():
    row = CaptionRow(
        id=0,
        caption="__ALICE__ sits.",
        aspect_ratio="1:1",
        nudity=None,
        artistic_quality=None,
        erotic_score=None,
        pornographic_score=0.25,
        males=0,
        females=None,
        clothing=(),
    )
    assert prompt.user_message(row) == (
        "Counts: M=0 F=? · Nudity: ? · Erotic ? · Porn 0.25\nCaption: __ALICE__ sits."
    )


def test_prompt_version_is_a_short_stable_hash():
    assert re.fullmatch(r"[0-9a-f]{12}", prompt.PROMPT_VERSION)
    assert prompt.PROMPT_VERSION == prompt._compute_version()
