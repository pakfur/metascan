"""Reference-describe grammars, validators, model picker (spec V2 §4-§5)."""

import json

import pytest

from metascan.core import ref_describe as rd
from metascan.core.vlm_select import VlmSelectError, pick_vlm_model


def test_grammars_are_gbnf_safe():
    for g in (rd.SUBJECT_DESCRIBE_GRAMMAR, rd.SETTING_DESCRIBE_GRAMMAR):
        assert g.startswith("root ::=")
        assert r"\-" not in g
        assert g.count('"') % 2 == 0


def test_validate_subject_describe():
    out = rd.validate_subject_describe(
        json.dumps({"description": "late 20s, shaved head", "voice": "clear alto"})
    )
    assert out == {"description": "late 20s, shaved head", "voice": "clear alto"}
    out2 = rd.validate_subject_describe(json.dumps({"description": "x", "voice": None}))
    assert out2["voice"] is None
    with pytest.raises(rd.DescribeError):
        rd.validate_subject_describe("not json")
    with pytest.raises(rd.DescribeError):
        rd.validate_subject_describe(json.dumps({"description": "  ", "voice": None}))


def test_validate_setting_describe():
    out = rd.validate_setting_describe(
        json.dumps({"setting": "salvage yard", "lighting": None, "mood": "tense"})
    )
    assert out["setting"] == "salvage yard" and out["mood"] == "tense"
    with pytest.raises(rd.DescribeError):
        rd.validate_setting_describe(
            json.dumps({"setting": "", "lighting": None, "mood": None})
        )


def test_system_prompts_resolve_and_carry_contract():
    for key in ("REF_DESCRIBE_SUBJECT_SYSTEM", "REF_DESCRIBE_SETTING_SYSTEM"):
        text = getattr(rd, key)
        assert len(text) > 100
        assert text.rstrip().endswith("Output only the JSON.")


class _Vlm:
    model_id = "qwen3vl-8b"


def test_pick_vlm_model_prefers_loaded_model():
    assert pick_vlm_model(_Vlm()) == "qwen3vl-8b"


def test_pick_vlm_model_no_hardware(monkeypatch):
    from metascan.core import vlm_select

    class _Empty:
        model_id = None

    monkeypatch.setattr(vlm_select, "_recommended_qwen_gate", lambda: None)
    with pytest.raises(VlmSelectError):
        pick_vlm_model(_Empty())
