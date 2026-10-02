"""Tests for reading answer-letter distributions out of llama-server logprobs."""

from __future__ import annotations

import math

import pytest

from scripts.caption_classifier.parse import ParseError, field_distributions
from tests._caption_classifier_helpers import answer


def test_each_field_gets_a_renormalised_distribution():
    content, tokens = answer(
        alts={
            "partner": {"A": 0.6, "B": 0.3, "{": 0.1},
            "emotion": {"A": 0.5, "B": 0.25, "C": 0.25},
        }
    )
    d = field_distributions(content, tokens)
    assert d["partner"] == {"A": 0.6667, "B": 0.3333, "C": 0.0, "D": 0.0}
    assert d["kiss"] == {"Y": 0.0, "N": 1.0}
    assert d["emotion"] == {"A": 0.5, "B": 0.25, "C": 0.25}
    assert d["act"]["A"] == 1.0 and sum(d["act"].values()) == 1.0
    assert len(d["act"]) == 19


def test_letter_merged_with_the_preceding_quote():
    content, tokens = answer()
    # Re-split '{"partner":"' + 'A' into '{"partner":' + '"A'.
    tokens[0] = dict(tokens[0], token='{"partner":')
    tokens[1] = {
        "token": '"A',
        "logprob": math.log(0.7),
        "top_logprobs": [
            {"token": '"A', "logprob": math.log(0.7)},
            {"token": '"D', "logprob": math.log(0.2)},
            {"token": '"', "logprob": math.log(0.1)},
        ],
    }
    d = field_distributions(content, tokens)
    assert d["partner"] == {"A": 0.7778, "B": 0.0, "C": 0.0, "D": 0.2222}


def test_chosen_token_missing_from_top_list_still_counts():
    content, tokens = answer()
    tokens[7] = {
        "token": "A",
        "logprob": math.log(0.25),
        "top_logprobs": [{"token": "B", "logprob": math.log(0.5)}],
    }
    assert field_distributions(content, tokens)["act"]["A"] == 0.3333


def test_token_stream_that_disagrees_with_the_text_is_rejected():
    content, tokens = answer()
    tokens[1] = dict(tokens[1], token="B")
    with pytest.raises(ParseError, match="does not match"):
        field_distributions(content, tokens)


def test_missing_field_is_rejected():
    with pytest.raises(ParseError, match="kiss"):
        field_distributions(
            '{"partner":"A"}', [{"token": '{"partner":"A"}', "logprob": 0.0}]
        )


def test_broken_utf8_pieces_in_the_issue_quotes_do_not_matter():
    issue = {"type": "extra_limb", "quote_a": "café table", "quote_b": "“right hand”"}
    content, tokens = answer(issues=[issue])
    tokens[-1] = dict(tokens[-1], token=tokens[-1]["token"].replace("é", "�"))
    assert field_distributions(content, tokens)["partner"]["A"] == 1.0
