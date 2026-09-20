"""workflow_validation: per-(target, mode) validators + title fixes."""

import unittest
from typing import Any, Dict, List, Optional

from metascan.core.comfy_bindings import _REQUIRED_WIDGETS
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


_WIDGET_DEFAULTS: Dict[str, Any] = {
    "text": "",
    "seed": 0,
    "noise_seed": 0,
    "image": "",
    "audio": "",
    "value": 6.0,
}


def _widgets_for(title: str) -> Dict[str, Any]:
    if title == "MS_SEED":
        return {"noise_seed": 0}
    widgets = _REQUIRED_WIDGETS.get(title, ())
    return {w: _WIDGET_DEFAULTS.get(w, "") for w in widgets}


class TestMinimaxI2vaValidator(unittest.TestCase):
    """("minimax","i2va") -- the i2v flow's dialect."""

    def _wf(self, titles: List[str]) -> Dict[str, Any]:
        return {str(i): _node(t, _widgets_for(t)) for i, t in enumerate(titles)}

    def test_missing_first_frame_is_error(self):
        wf = self._wf(["MS_POSITIVE", "MS_SEED", "MS_SAVE"])
        report = validate_workflow(wf, "ref2v", "minimax", "i2va")
        codes = [f.code for f in report.findings if f.level == "error"]
        self.assertIn("no_first_frame", codes)
        self.assertFalse(report.ok)

    def test_complete_workflow_is_ok(self):
        wf = self._wf(
            [
                "MS_POSITIVE",
                "MS_SEED",
                "MS_SAVE",
                "MS_FIRST_FRAME",
                "MS_DURATION",
                "MS_LORA_STACK",
            ]
        )
        report = validate_workflow(wf, "ref2v", "minimax", "i2va")
        self.assertTrue(report.ok)
        self.assertEqual([f for f in report.findings if f.code == "no_validator"], [])

    def test_missing_duration_and_lora_stack_warn(self):
        wf = self._wf(["MS_POSITIVE", "MS_SEED", "MS_SAVE", "MS_FIRST_FRAME"])
        report = validate_workflow(wf, "ref2v", "minimax", "i2va")
        self.assertTrue(report.ok)
        codes = [f.code for f in report.findings]
        self.assertIn("no_duration", codes)
        self.assertIn("no_lora_stack", codes)

    def test_missing_resolution_warns(self):
        wf = self._wf(["MS_POSITIVE", "MS_SEED", "MS_SAVE", "MS_FIRST_FRAME"])
        report = validate_workflow(wf, "ref2v", "minimax", "i2va")
        self.assertTrue(report.ok)
        warnings = [f for f in report.findings if f.code == "no_resolution"]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0].level, "warning")

    def test_present_resolution_does_not_warn(self):
        wf = self._wf(
            [
                "MS_POSITIVE",
                "MS_SEED",
                "MS_SAVE",
                "MS_FIRST_FRAME",
                "MS_RESOLUTION",
            ]
        )
        report = validate_workflow(wf, "ref2v", "minimax", "i2va")
        self.assertTrue(report.ok)
        self.assertNotIn("no_resolution", [f.code for f in report.findings])

    def test_resolution_is_a_known_title(self):
        wf = self._wf(
            ["MS_POSITIVE", "MS_SEED", "MS_SAVE", "MS_FIRST_FRAME", "MS_RESOLUTION"]
        )
        report = validate_workflow(wf, "ref2v", "minimax", "i2va")
        self.assertNotIn("unknown_title", [f.code for f in report.findings])

    def test_unused_slots_warn(self):
        wf = self._wf(
            [
                "MS_POSITIVE",
                "MS_SEED",
                "MS_SAVE",
                "MS_FIRST_FRAME",
                "MS_LAST_FRAME",
                "MS_AUDIO",
            ]
        )
        report = validate_workflow(wf, "ref2v", "minimax", "i2va")
        unused = [f for f in report.findings if f.code == "slot_unused"]
        self.assertEqual(len(unused), 2)


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
    report = validate_workflow(_ref2v_workflow(), "ref2v", "minimax", "fl2va")
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
