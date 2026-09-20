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


class TestI2vApi(unittest.TestCase):
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
