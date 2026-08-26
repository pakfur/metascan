"""workflow_validation: per-(target, mode) validators + title fixes."""

from typing import Any, Dict, Optional

from metascan.core.workflow_validation import (
    apply_fixes,
    validate_workflow,
)


def _node(title: Optional[str], inputs: Optional[Dict[str, Any]] = None) -> dict:
    node: Dict[str, Any] = {"inputs": inputs or {}}
    if title is not None:
        node["_meta"] = {"title": title}
    return node


def _ref2v_workflow(**extra: dict) -> Dict[str, Any]:
    wf: Dict[str, Any] = {
        "1": _node("MS_POSITIVE", {"text": ""}),
        "2": _node("MS_SEED", {"noise_seed": 0}),
        "3": _node("MS_SAVE"),
        "9": _node(None),  # untitled bystander node
    }
    wf.update(extra)
    return wf


def _codes(report, level=None):
    return [f.code for f in report.findings if level is None or f.level == level]


def test_minimax_ref2va_happy_path_is_clean():
    wf = _ref2v_workflow(
        ref=_node("MS_REF_IMAGE", {"image": ""}),
        audio=_node("MS_AUDIO", {"audio": ""}),
        dur=_node("MS_DURATION", {"value": 6.0}),
    )
    report = validate_workflow(wf, "ref2v", "minimax", "ref2va")
    assert report.ok
    assert report.findings == []
    assert report.fixes == []


def test_minimax_ref2va_capability_warnings():
    report = validate_workflow(_ref2v_workflow(), "ref2v", "minimax", "ref2va")
    assert report.ok  # warnings only
    codes = _codes(report)
    assert "no_ref_image_slot" in codes
    assert "no_audio_slot" in codes
    assert "no_duration" in codes

    wf = _ref2v_workflow(ff=_node("MS_FIRST_FRAME", {"image": ""}))
    report = validate_workflow(wf, "ref2v", "minimax", "ref2va")
    assert "keyframe_slot_unused" in _codes(report)


def test_unregistered_target_mode_pair_warns_not_errors():
    report = validate_workflow(_ref2v_workflow(), "ref2v", "minimax", "i2va")
    assert report.ok
    assert _codes(report) == ["no_validator"]
    # No target/mode at all -> generic checks only, no no_validator noise.
    report = validate_workflow(_ref2v_workflow(), "ref2v")
    assert report.ok and report.findings == []


def test_misspelled_title_gets_a_rename_fix_that_repairs_the_graph():
    wf = _ref2v_workflow(ref=_node("MS_REF_IMGE", {"image": ""}))
    report = validate_workflow(wf, "ref2v", "minimax", "ref2va")
    assert report.ok
    assert "unknown_title" in _codes(report, "warning")
    assert len(report.fixes) == 1
    fix = report.fixes[0]
    assert fix.node_id == "ref" and fix.title == "MS_REF_IMAGE"

    fixed = apply_fixes(wf, report.fixes)
    assert fixed["ref"]["_meta"]["title"] == "MS_REF_IMAGE"
    report2 = validate_workflow(fixed, "ref2v", "minimax", "ref2va")
    codes2 = _codes(report2)
    assert "unknown_title" not in codes2
    assert "no_ref_image_slot" not in codes2
    # apply_fixes never mutates the input graph.
    assert wf["ref"]["_meta"]["title"] == "MS_REF_IMGE"


def test_missing_required_title_is_an_error_and_mentions_pending_fix():
    wf = _ref2v_workflow()
    del wf["2"]
    wf["seed"] = _node("MS_SEDE", {"seed": 0})  # misspelled MS_SEED
    report = validate_workflow(wf, "ref2v", "minimax", "ref2va")
    assert not report.ok
    missing = [f for f in report.findings if f.code == "missing_required"]
    assert len(missing) == 1 and "MS_SEED" in missing[0].message
    assert "fix" in missing[0].message.lower()
    assert any(f.title == "MS_SEED" for f in report.fixes)
    assert validate_workflow(apply_fixes(wf, report.fixes), "ref2v").ok


def test_duplicate_and_widget_errors():
    wf = _ref2v_workflow(dup=_node("MS_POSITIVE", {"text": ""}))
    report = validate_workflow(wf, "ref2v")
    assert not report.ok
    assert "duplicate_title" in _codes(report, "error")

    wf = _ref2v_workflow()
    wf["1"] = _node("MS_POSITIVE", {})  # no text widget
    wf["2"] = _node("MS_SEED", {"steps": 20})  # neither seed nor noise_seed
    report = validate_workflow(wf, "ref2v")
    assert _codes(report, "error").count("missing_widget") == 2


def test_unknown_title_without_a_close_match_has_no_fix():
    wf = _ref2v_workflow(x=_node("MS_TOTALLY_NOVEL_THING", {"v": 1}))
    report = validate_workflow(wf, "ref2v")
    assert report.ok
    assert "unknown_title" in _codes(report, "warning")
    assert report.fixes == []
