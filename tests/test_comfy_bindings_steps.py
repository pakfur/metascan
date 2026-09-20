"""MS_STEPS: the optional sampler-step-count binding (i2v quality presets)."""

import json
import unittest
from pathlib import Path

from metascan.core.comfy_bindings import (
    BindingError,
    GenerationParams,
    apply_overrides,
    resolve_bindings,
)

_BASE = {
    "1": {
        "class_type": "JWStringMultiline",
        "inputs": {"text": ""},
        "_meta": {"title": "MS_POSITIVE"},
    },
    "2": {
        "class_type": "RandomNoise",
        "inputs": {"noise_seed": 0},
        "_meta": {"title": "MS_SEED"},
    },
    "3": {"class_type": "SaveVideo", "inputs": {}, "_meta": {"title": "MS_SAVE"}},
}


def _with_steps(inputs=None):
    wf = json.loads(json.dumps(_BASE))
    wf["4"] = {
        "class_type": "BasicScheduler",
        "inputs": (
            {"scheduler": "simple", "steps": 20, "denoise": 1}
            if inputs is None
            else inputs
        ),
        "_meta": {"title": "MS_STEPS"},
    }
    return wf


def _params(**over):
    kwargs = dict(positive="p", seed=1, width=64, height=64, batch_size=1)
    kwargs.update(over)
    return GenerationParams(**kwargs)


class TestStepsBinding(unittest.TestCase):
    def test_resolves_ms_steps(self):
        self.assertEqual(resolve_bindings(_with_steps(), "ref2v").steps, "4")

    def test_absent_is_none(self):
        self.assertIsNone(resolve_bindings(_BASE, "ref2v").steps)

    def test_missing_steps_widget_rejected(self):
        with self.assertRaises(BindingError) as ctx:
            resolve_bindings(_with_steps({"scheduler": "simple"}), "ref2v")
        self.assertIn("steps", str(ctx.exception))

    def test_apply_writes_steps(self):
        wf = _with_steps()
        graph = apply_overrides(wf, resolve_bindings(wf, "ref2v"), _params(steps=35))
        self.assertEqual(graph["4"]["inputs"]["steps"], 35)
        # Untouched siblings, and the source workflow is not mutated.
        self.assertEqual(graph["4"]["inputs"]["scheduler"], "simple")
        self.assertEqual(wf["4"]["inputs"]["steps"], 20)

    def test_none_keeps_baked_in_steps(self):
        wf = _with_steps()
        graph = apply_overrides(wf, resolve_bindings(wf, "ref2v"), _params())
        self.assertEqual(graph["4"]["inputs"]["steps"], 20)

    def test_steps_without_binding_raises(self):
        with self.assertRaises(BindingError) as ctx:
            apply_overrides(_BASE, resolve_bindings(_BASE, "ref2v"), _params(steps=30))
        self.assertIn("MS_STEPS", str(ctx.exception))


class TestShippedWorkflows(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent / "data" / "workflows"

    def _load(self, name):
        return json.loads((self.ROOT / name).read_text())

    def test_quality_workflow_binds_steps_on_the_scheduler(self):
        wf = self._load("minimax_i2va_quality_api.json")
        b = resolve_bindings(wf, "ref2v")
        self.assertIsNotNone(b.steps)
        self.assertEqual(wf[b.steps]["class_type"], "BasicScheduler")

    def test_turbo_workflow_does_not_bind_steps(self):
        wf = self._load("minimax_i2va_turbo_api.json")
        self.assertIsNone(resolve_bindings(wf, "ref2v").steps)


if __name__ == "__main__":
    unittest.main()
