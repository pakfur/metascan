"""REST tests for /api/i2v with a fake runner installed."""

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.i2v import get_i2v_config, set_i2v_runner
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.i2v_runner import I2vRequestError, I2vUnavailableError
from metascan.core.media import Media
from metascan.core.vlm_client import VlmError
from metascan.core.vlm_select import VlmSelectError


def _seed_media(db: DatabaseManager, paths) -> None:
    for p in paths:
        db.save_media(
            Media(
                file_path=Path(p),
                file_size=1,
                width=1,
                height=1,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )


class FakeRunner:
    def __init__(self):
        self.prompt_result = ("For the target video, ...", ["w1"])
        self.prompt_error = None
        self.generate_result = 42
        self.generate_error = None
        self.calls = []

    async def generate_prompt(self, source_path, idea, duration_s):
        self.calls.append(("prompt", source_path, idea, duration_s))
        if self.prompt_error:
            raise self.prompt_error
        return self.prompt_result

    async def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        if self.generate_error:
            raise self.generate_error
        return self.generate_result


class _I2vApiBase(unittest.TestCase):
    """App + temp DB + fake runner. Holds no tests, so subclasses don't
    re-run one another's."""

    tmp: Optional[tempfile.TemporaryDirectory] = None

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("METASCAN_API_KEY", "")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db = DatabaseManager(self.data_dir)
        _seed_media(self.db, ["/lib/a.png", "/lib/b.png"])
        self._patches = [
            patch(
                "backend.dependencies.get_data_dir",
                return_value=self.data_dir,
            ),
            patch("backend.dependencies._db_singleton", None, create=False),
        ]
        for p in self._patches:
            p.start()
        import backend.dependencies as deps

        deps._db_singleton = self.db  # type: ignore[attr-defined]

        self.runner = FakeRunner()
        set_i2v_runner(self.runner)

        self._config_patch = patch(
            "backend.api.i2v.load_app_config",
            return_value={"i2v": {"fast_preset_id": 5, "quality_preset_id": None}},
        )
        self._config_patch.start()

        from backend.api import i2v as i2v_api
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(i2v_api.router)
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        set_i2v_runner(None)
        self._config_patch.stop()
        for p in self._patches:
            p.stop()
        import backend.dependencies as deps

        deps._db_singleton = None  # type: ignore[attr-defined]
        assert self.tmp is not None
        self.tmp.cleanup()


class TestI2vApi(_I2vApiBase):
    # ---- /prompt ----------------------------------------------------

    def test_prompt_happy_path(self):
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 6},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["prompt"], self.runner.prompt_result[0])
        self.assertEqual(body["warnings"], ["w1"])

    def test_prompt_503_when_runner_missing(self):
        set_i2v_runner(None)
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 6},
        )
        self.assertEqual(resp.status_code, 503)

    def test_prompt_503_on_unavailable(self):
        self.runner.prompt_error = I2vUnavailableError("no vlm")
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 6},
        )
        self.assertEqual(resp.status_code, 503)

    def test_prompt_400_on_request_error(self):
        self.runner.prompt_error = I2vRequestError("not an image")
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 6},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "not an image")

    def test_prompt_502_on_vlm_error(self):
        self.runner.prompt_error = VlmError("boom")
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 6},
        )
        self.assertEqual(resp.status_code, 502)

    def test_prompt_400_nonpositive_duration(self):
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 0},
        )
        self.assertEqual(resp.status_code, 400)

    def test_prompt_503_on_vlm_select_error(self):
        self.runner.prompt_error = VlmSelectError("no model")
        resp = self.client.post(
            "/api/i2v/prompt",
            json={"source_path": "/lib/a.png", "idea": "x", "duration_s": 6},
        )
        self.assertEqual(resp.status_code, 503)

    # ---- /generate ----------------------------------------------------

    def test_generate_happy_path(self):
        resp = self.client.post(
            "/api/i2v/generate",
            json={
                "source_path": "/lib/a.png",
                "prompt": "For the target video, the shot begins mid-air.",
                "duration_s": 6,
                "quality": "fast",
                "seed": 1,
                "megapixels": 0.75,
                "loras": [],
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["job_id"], 42)
        self.assertIsInstance(body["warnings"], list)
        kind, kwargs = self.runner.calls[0]
        self.assertEqual(kind, "generate")
        self.assertEqual(kwargs["preset_id"], 5)

    def test_generate_400_unconfigured_slot(self):
        resp = self.client.post(
            "/api/i2v/generate",
            json={
                "source_path": "/lib/a.png",
                "prompt": "some prompt text",
                "duration_s": 6,
                "quality": "quality",
                "seed": 1,
                "megapixels": 0.75,
                "loras": [],
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Configuration", resp.json()["detail"])

    def test_generate_passes_megapixels_to_runner(self):
        resp = self.client.post(
            "/api/i2v/generate",
            json={
                "source_path": "/lib/a.png",
                "prompt": "For the target video, the shot begins mid-air.",
                "duration_s": 6,
                "quality": "fast",
                "seed": 1,
                "megapixels": 0.5,
                "loras": [],
            },
        )
        self.assertEqual(resp.status_code, 200)
        kwargs = self.runner.calls[0][1]
        self.assertEqual(kwargs["megapixels"], 0.5)
        self.assertNotIn("width", kwargs)
        self.assertNotIn("height", kwargs)

    def _generate_body(self, **over):
        body = {
            "source_path": "/lib/a.png",
            "prompt": "For the target video, the shot begins mid-air.",
            "duration_s": 6,
            "quality": "fast",
            "seed": 1,
            "loras": [],
        }
        body.update(over)
        return body

    # ---- /lint -------------------------------------------------------

    def test_lint_reports_warnings_fixes_and_the_fixed_prompt(self):
        resp = self.client.post(
            "/api/i2v/lint",
            json={
                "prompt": 'The camera slowly dollies in. She says, "Hello."',
                "duration_s": 6,
            },
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(
            [f["code"] for f in body["fixes"]], ["camera_phrase", "speech_format"]
        )
        self.assertEqual(body["fixes"][0]["original"], "The camera slowly dollies in.")
        self.assertEqual(
            body["fixed_prompt"],
            "The camera pushes in at slow speed. "
            "She (S1) says: <d>[English] Hello.</d>",
        )
        self.assertTrue(any("non-guide phrasing" in w for w in body["warnings"]))

    def test_lint_nothing_to_fix(self):
        resp = self.client.post(
            "/api/i2v/lint",
            json={"prompt": "The camera does a barrel roll.", "duration_s": 6},
        )
        body = resp.json()
        self.assertEqual(body["fixes"], [])
        self.assertIsNone(body["fixed_prompt"])
        self.assertTrue(body["warnings"])

    def test_lint_needs_no_runner(self):
        """Pure text analysis -- must work while the i2v runner is down."""
        set_i2v_runner(None)
        resp = self.client.post("/api/i2v/lint", json={"prompt": "x", "duration_s": 6})
        self.assertEqual(resp.status_code, 200)

    def test_lint_400_nonpositive_duration(self):
        resp = self.client.post("/api/i2v/lint", json={"prompt": "x", "duration_s": 0})
        self.assertEqual(resp.status_code, 400)

    def test_generate_passes_configured_output_placement(self):
        with patch(
            "backend.api.i2v.load_app_config",
            return_value={
                "i2v": {
                    "fast_preset_id": 5,
                    "output_root": "/mnt/d/Media/images",
                    "output_prefix": "/%Y-%m-%d/minimax_",
                }
            },
        ):
            resp = self.client.post("/api/i2v/generate", json=self._generate_body())
        self.assertEqual(resp.status_code, 200)
        kwargs = self.runner.calls[0][1]
        self.assertEqual(kwargs["output_root"], "/mnt/d/Media/images")
        self.assertEqual(kwargs["output_prefix"], "/%Y-%m-%d/minimax_")

    def test_generate_output_root_defaults_to_none(self):
        resp = self.client.post("/api/i2v/generate", json=self._generate_body())
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self.runner.calls[0][1]["output_root"])

    def test_output_preview(self):
        resp = self.client.get(
            "/api/i2v/output-preview",
            params={"root": str(self.data_dir), "prefix": "/%Y-%M-%d/minimax_"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["path"].startswith(str(self.data_dir)))
        self.assertTrue(body["path"].endswith(".mp4"))
        self.assertIn("minimax_", body["path"])
        self.assertEqual(len(body["warnings"]), 1)  # %M is minutes
        self.assertIsNone(body["error"])

    def test_output_preview_reports_problems_without_failing(self):
        resp = self.client.get(
            "/api/i2v/output-preview",
            params={"root": str(self.data_dir), "prefix": "../x_"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["path"])
        self.assertTrue(resp.json()["error"])

        resp = self.client.get(
            "/api/i2v/output-preview",
            params={"root": str(self.data_dir / "missing"), "prefix": "x_"},
        )
        self.assertIn("does not exist", resp.json()["error"])

    def test_output_preview_blank_root_describes_the_default(self):
        resp = self.client.get("/api/i2v/output-preview", params={"root": ""})
        body = resp.json()
        self.assertIsNone(body["path"])
        self.assertIsNone(body["error"])

    def test_generate_passes_steps_to_runner(self):
        resp = self.client.post("/api/i2v/generate", json=self._generate_body(steps=35))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.runner.calls[0][1]["steps"], 35)

    def test_generate_steps_default_to_none(self):
        resp = self.client.post("/api/i2v/generate", json=self._generate_body())
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self.runner.calls[0][1]["steps"])

    def test_generate_400_out_of_range_steps(self):
        for bad in (0, -5, 201):
            resp = self.client.post(
                "/api/i2v/generate", json=self._generate_body(steps=bad)
            )
            self.assertEqual(resp.status_code, 400, bad)
            self.assertIn("steps", resp.json()["detail"])
        self.assertEqual(self.runner.calls, [])

    def test_generate_defaults_megapixels_when_omitted(self):
        resp = self.client.post(
            "/api/i2v/generate",
            json={
                "source_path": "/lib/a.png",
                "prompt": "For the target video, the shot begins mid-air.",
                "duration_s": 6,
                "quality": "fast",
                "seed": 1,
                "loras": [],
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.runner.calls[0][1]["megapixels"], 0.75)

    def test_generate_400_non_positive_megapixels(self):
        resp = self.client.post(
            "/api/i2v/generate",
            json={
                "source_path": "/lib/a.png",
                "prompt": "For the target video, the shot begins mid-air.",
                "duration_s": 6,
                "quality": "fast",
                "seed": 1,
                "megapixels": 0,
                "loras": [],
            },
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("megapixels", resp.json()["detail"].lower())

    def test_generate_400_bad_quality(self):
        resp = self.client.post(
            "/api/i2v/generate",
            json={
                "source_path": "/lib/a.png",
                "prompt": "some prompt text",
                "duration_s": 6,
                "quality": "ultra",
                "seed": 1,
                "megapixels": 0.75,
                "loras": [],
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_generate_400_nonpositive_duration(self):
        resp = self.client.post(
            "/api/i2v/generate",
            json={
                "source_path": "/lib/a.png",
                "prompt": "some prompt text",
                "duration_s": 0,
                "quality": "fast",
                "seed": 1,
                "megapixels": 0.75,
                "loras": [],
            },
        )
        self.assertEqual(resp.status_code, 400)

    # ---- /videos ----------------------------------------------------

    def test_videos_list_and_delete(self):
        video_id = self.db.create_i2v_video(
            source_path="/lib/a.png",
            file_path="/lib/a.png",
            prompt_used="some prompt",
            idea="idea",
            seed=1,
            duration_s=6.0,
            quality="fast",
            preset_id=5,
            comfy_prompt_id="pid",
        )
        resp = self.client.get("/api/i2v/videos", params={"source_path": "/lib/a.png"})
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], video_id)

        with patch("metascan.utils.trash.send2trash"):
            resp = self.client.delete(f"/api/i2v/videos/{video_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "deleted"})

        resp = self.client.delete(f"/api/i2v/videos/{video_id}")
        self.assertEqual(resp.status_code, 404)

    # ---- /config ----------------------------------------------------

    def test_config_endpoint_defaults(self):
        with patch("backend.api.i2v.load_app_config", return_value={}):
            resp = self.client.get("/api/i2v/config")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["durations"], [6, 10, 15, 20])
        self.assertEqual(body["default_quality"], "fast")
        self.assertIsNone(body["fast_preset_id"])
        self.assertIsNone(body["quality_preset_id"])

    def test_config_endpoint_reports_steps(self):
        with patch("backend.api.i2v.load_app_config", return_value={}):
            body = self.client.get("/api/i2v/config").json()
        self.assertEqual(body["steps"], [20, 25, 30, 35, 40])
        self.assertEqual(body["default_steps"], 25)
        # No quality preset configured -> nothing to drive.
        self.assertFalse(body["quality_steps_supported"])

    def _preset(self, with_steps: bool) -> int:
        import json

        wf = {
            "1": {"inputs": {"text": ""}, "_meta": {"title": "MS_POSITIVE"}},
            "2": {"inputs": {"noise_seed": 0}, "_meta": {"title": "MS_SEED"}},
            "3": {"inputs": {}, "_meta": {"title": "MS_SAVE"}},
        }
        if with_steps:
            wf["4"] = {"inputs": {"steps": 20}, "_meta": {"title": "MS_STEPS"}}
        return self.db.create_workflow_preset(
            f"q-{with_steps}", "ref2v", json.dumps(wf), "{}"
        )

    def test_config_quality_steps_supported_follows_the_preset(self):
        for with_steps in (True, False):
            pid = self._preset(with_steps)
            with patch(
                "backend.api.i2v.load_app_config",
                return_value={"i2v": {"quality_preset_id": pid}},
            ):
                body = self.client.get("/api/i2v/config").json()
            self.assertEqual(body["quality_steps_supported"], with_steps)

    def test_config_quality_steps_supported_false_for_missing_preset(self):
        with patch(
            "backend.api.i2v.load_app_config",
            return_value={"i2v": {"quality_preset_id": 9999}},
        ):
            body = self.client.get("/api/i2v/config").json()
        self.assertFalse(body["quality_steps_supported"])


class TestI2vFormStateApi(_I2vApiBase):
    """GET /videos hands the dialog a complete form_state per clip, and
    PATCH /videos/{id} autosaves into it without touching the rendered
    facts."""

    FORM = {
        "idea": "a cat",
        "prompt": "[Shot 1] as submitted",
        "duration_s": 10.0,
        "quality": "quality",
        "megapixels": 0.5,
        "steps": 30,
        "seed": 42,
        "loras": [{"name": "a.safetensors", "strength": 0.8}],
    }

    def _clip(self, path="/lib/out.mp4", **kw):
        _seed_media(self.db, [path])
        return self.db.create_i2v_video(
            source_path="/lib/a.png",
            file_path=path,
            prompt_used="[Shot 1] as submitted",
            seed=42,
            duration_s=10.0,
            quality="quality",
            steps=30,
            width=640,
            height=1184,
            **kw,
        )

    def _videos(self):
        resp = self.client.get("/api/i2v/videos", params={"source_path": "/lib/a.png"})
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_list_returns_the_stored_form_state(self):
        self._clip(form_state=self.FORM, megapixels=0.5, loras=self.FORM["loras"])
        self.assertEqual(self._videos()[0]["form_state"], self.FORM)

    def test_list_builds_a_form_state_for_a_clip_that_predates_the_column(self):
        self._clip()  # no form_state, no megapixels, no loras
        state = self._videos()[0]["form_state"]
        self.assertEqual(state["prompt"], "[Shot 1] as submitted")
        self.assertEqual(state["seed"], 42)
        self.assertEqual(state["megapixels"], 0.75)  # 640x1184 snapped
        self.assertEqual(state["loras"], [])

    def test_patch_merges_into_the_form_state(self):
        vid = self._clip(form_state=self.FORM)

        resp = self.client.patch(
            f"/api/i2v/videos/{vid}", json={"prompt": "edited", "seed": 7}
        )

        self.assertEqual(resp.status_code, 200)
        expected = {**self.FORM, "prompt": "edited", "seed": 7}
        self.assertEqual(resp.json()["form_state"], expected)
        self.assertEqual(self._videos()[0]["form_state"], expected)

    def test_patch_never_touches_the_rendered_facts(self):
        vid = self._clip(form_state=self.FORM)

        self.client.patch(
            f"/api/i2v/videos/{vid}",
            json={"prompt": "edited", "seed": 7, "quality": "fast", "steps": None},
        )

        row = self._videos()[0]
        self.assertEqual(row["prompt_used"], "[Shot 1] as submitted")
        self.assertEqual(row["seed"], 42)
        self.assertEqual(row["quality"], "quality")
        self.assertEqual(row["steps"], 30)

    def test_patch_on_a_legacy_clip_starts_from_its_built_form_state(self):
        vid = self._clip()

        resp = self.client.patch(f"/api/i2v/videos/{vid}", json={"idea": "new idea"})

        state = resp.json()["form_state"]
        self.assertEqual(state["idea"], "new idea")
        self.assertEqual(state["prompt"], "[Shot 1] as submitted")  # not lost
        self.assertEqual(state["seed"], 42)

    def test_patch_unknown_video_is_404(self):
        resp = self.client.patch("/api/i2v/videos/99999", json={"idea": "x"})
        self.assertEqual(resp.status_code, 404)

    def test_patch_bad_value_is_400_naming_the_field(self):
        vid = self._clip(form_state=self.FORM)
        resp = self.client.patch(f"/api/i2v/videos/{vid}", json={"quality": "ultra"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("quality", resp.json()["detail"])

    def test_patch_unknown_field_is_400(self):
        vid = self._clip(form_state=self.FORM)
        resp = self.client.patch(f"/api/i2v/videos/{vid}", json={"prompt_used": "x"})
        self.assertEqual(resp.status_code, 400)

    def test_patch_empty_body_is_400(self):
        vid = self._clip(form_state=self.FORM)
        resp = self.client.patch(f"/api/i2v/videos/{vid}", json={})
        self.assertEqual(resp.status_code, 400)


class TestGetI2vConfig(unittest.TestCase):
    def test_empty_dict_defaults(self):
        cfg = get_i2v_config({})
        self.assertEqual(cfg["durations"], [6.0, 10.0, 15.0, 20.0])
        self.assertEqual(cfg["default_duration"], 6.0)
        self.assertEqual(cfg["default_quality"], "fast")
        self.assertIsNone(cfg["fast_preset_id"])
        self.assertIsNone(cfg["quality_preset_id"])

    def test_junk_types_fall_back_to_defaults(self):
        cfg = get_i2v_config(
            {
                "i2v": {
                    "durations": "x",
                    "fast_preset_id": "abc",
                    "quality_preset_id": "def",
                    "default_duration": "y",
                    "default_quality": 5,
                }
            }
        )
        self.assertEqual(cfg["durations"], [6.0, 10.0, 15.0, 20.0])
        self.assertIsNone(cfg["fast_preset_id"])
        self.assertIsNone(cfg["quality_preset_id"])
        self.assertEqual(cfg["default_duration"], 6.0)
        self.assertEqual(cfg["default_quality"], "fast")

    def test_valid_values_pass_through(self):
        cfg = get_i2v_config(
            {
                "i2v": {
                    "fast_preset_id": 3,
                    "quality_preset_id": 7,
                    "durations": [4, 8],
                    "default_duration": 8,
                    "default_quality": "quality",
                }
            }
        )
        self.assertEqual(cfg["fast_preset_id"], 3)
        self.assertEqual(cfg["quality_preset_id"], 7)
        self.assertEqual(cfg["durations"], [4.0, 8.0])
        self.assertEqual(cfg["default_duration"], 8.0)
        self.assertEqual(cfg["default_quality"], "quality")


if __name__ == "__main__":
    unittest.main()


class TestI2vConfigMegapixels(unittest.TestCase):
    def test_defaults(self):
        cfg = get_i2v_config({})
        self.assertEqual(cfg["megapixels"], [0.25, 0.5, 0.75, 1.0])
        self.assertEqual(cfg["default_megapixels"], 0.75)

    def test_junk_falls_back_to_defaults(self):
        cfg = get_i2v_config({"i2v": {"megapixels": "x", "default_megapixels": "y"}})
        self.assertEqual(cfg["megapixels"], [0.25, 0.5, 0.75, 1.0])
        self.assertEqual(cfg["default_megapixels"], 0.75)

    def test_valid_values_pass_through(self):
        cfg = get_i2v_config({"i2v": {"megapixels": [0.5, 2], "default_megapixels": 2}})
        self.assertEqual(cfg["megapixels"], [0.5, 2.0])
        self.assertEqual(cfg["default_megapixels"], 2.0)

    def test_default_outside_ladder_falls_back_to_first_entry(self):
        cfg = get_i2v_config({"i2v": {"megapixels": [0.5, 1.0]}})
        self.assertEqual(cfg["default_megapixels"], 0.5)

    def test_non_positive_entries_are_dropped(self):
        cfg = get_i2v_config({"i2v": {"megapixels": [0, -1, 0.5]}})
        self.assertEqual(cfg["megapixels"], [0.5])


class TestI2vConfigSteps(unittest.TestCase):
    def test_defaults(self):
        cfg = get_i2v_config({})
        self.assertEqual(cfg["steps"], [20, 25, 30, 35, 40])
        self.assertEqual(cfg["default_steps"], 25)

    def test_junk_falls_back_to_defaults(self):
        cfg = get_i2v_config({"i2v": {"steps": "x", "default_steps": "y"}})
        self.assertEqual(cfg["steps"], [20, 25, 30, 35, 40])
        self.assertEqual(cfg["default_steps"], 25)

    def test_valid_values_pass_through(self):
        cfg = get_i2v_config({"i2v": {"steps": [30, 50], "default_steps": 50}})
        self.assertEqual(cfg["steps"], [30, 50])
        self.assertEqual(cfg["default_steps"], 50)

    def test_default_outside_ladder_falls_back_to_first_entry(self):
        cfg = get_i2v_config({"i2v": {"steps": [30, 50]}})
        self.assertEqual(cfg["default_steps"], 30)

    def test_non_positive_entries_are_dropped(self):
        cfg = get_i2v_config({"i2v": {"steps": [0, -4, 30]}})
        self.assertEqual(cfg["steps"], [30])


class TestI2vConfigOutput(unittest.TestCase):
    def test_defaults(self):
        cfg = get_i2v_config({})
        self.assertEqual(cfg["output_root"], "")
        self.assertEqual(cfg["output_prefix"], "/%Y-%m-%d/i2v_")

    def test_values_pass_through_trimmed(self):
        cfg = get_i2v_config(
            {"i2v": {"output_root": "  /mnt/d/Media  ", "output_prefix": "/a/b_"}}
        )
        self.assertEqual(cfg["output_root"], "/mnt/d/Media")
        self.assertEqual(cfg["output_prefix"], "/a/b_")

    def test_explicit_empty_prefix_is_kept(self):
        self.assertEqual(
            get_i2v_config({"i2v": {"output_prefix": ""}})["output_prefix"], ""
        )

    def test_junk_types_fall_back(self):
        cfg = get_i2v_config({"i2v": {"output_root": 5, "output_prefix": ["x"]}})
        self.assertEqual(cfg["output_root"], "")
        self.assertEqual(cfg["output_prefix"], "/%Y-%m-%d/i2v_")
