"""Tests for I2vRunner: prompt expansion, generate validation, ingest."""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.i2v_runner import (
    I2vRequestError,
    I2vRunner,
    I2vUnavailableError,
)
from metascan.core.media import Media

_WF = {
    "1": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": ""},
        "_meta": {"title": "MS_POSITIVE"},
    },
    "2": {
        "class_type": "KSampler",
        "inputs": {"seed": 0},
        "_meta": {"title": "MS_SEED"},
    },
    "3": {"class_type": "SaveVideo", "inputs": {}, "_meta": {"title": "MS_SAVE"}},
    "4": {
        "class_type": "LoadImage",
        "inputs": {"image": ""},
        "_meta": {"title": "MS_FIRST_FRAME"},
    },
    "5": {
        "class_type": "Float",
        "inputs": {"value": 6.0},
        "_meta": {"title": "MS_DURATION"},
    },
}


class FakeComfy:
    def __init__(self):
        self.uploads = []
        self.submits = []

    async def upload_file(self, path):
        self.uploads.append(Path(path))
        return f"uploaded_{Path(path).name}"

    async def submit(self, preset_id, params, **kwargs):
        self.submits.append((preset_id, params, kwargs))
        return 77


class FakeVlm:
    def __init__(self, response):
        self.response = response
        self.model_id = "qwen3vl-8b"
        self.calls = []

    @classmethod
    def is_image_path(cls, path):
        return Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}

    async def ensure_started(self, model_id):
        pass

    async def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _beats_response(n=3):
    return json.dumps(
        {
            "beats": [
                {"action": f"Something happens number {i}.", "camera": "static"}
                for i in range(n)
            ],
            "overall_soundscape": "Wind.",
            "non_diegetic_music": "None.",
        }
    )


class I2vRunnerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = DatabaseManager(root / "db")
        self.src = root / "src.png"
        self.src.write_bytes(b"\x89PNG fake")
        self.db.save_media(
            Media(
                file_path=self.src,
                file_size=1,
                width=832,
                height=1216,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )
        self.preset_id = self.db.create_workflow_preset(
            "i2v-fast",
            "ref2v",
            json.dumps(_WF),
            "{}",
            video_target="minimax",
            video_mode="i2va",
        )
        self.comfy = FakeComfy()
        self.vlm = FakeVlm(_beats_response())
        self.runner = I2vRunner(
            db=self.db,
            comfy=self.comfy,
            get_vlm=lambda: self.vlm,
            output_root=root / "out",
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def run_async(self, coro):
        return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class TestGeneratePrompt(I2vRunnerBase):
    def test_happy_path(self):
        text, warnings = self.run_async(
            self.runner.generate_prompt(str(self.src), "a cat jumps", 6)
        )
        self.assertTrue(text.startswith("For the target video"))
        self.assertIn("integrated_multimodal_description:", text)
        call = self.vlm.calls[0]
        self.assertIn("grammar", call)
        self.assertEqual(call["image_path"], self.src)

    def test_no_vlm_raises_unavailable(self):
        runner = I2vRunner(
            db=self.db,
            comfy=self.comfy,
            get_vlm=lambda: None,
            output_root=Path(self.tmp.name),
        )
        with self.assertRaises(I2vUnavailableError):
            self.run_async(runner.generate_prompt(str(self.src), "x", 6))

    def test_non_image_rejected(self):
        with self.assertRaises(I2vRequestError):
            self.run_async(self.runner.generate_prompt("/lib/movie.mp4", "x", 6))


class TestGenerate(I2vRunnerBase):
    def _generate(self, **over):
        kwargs = dict(
            source_path=str(self.src),
            prompt="For the target video, ...",
            duration_s=6.0,
            quality="fast",
            seed=123,
            width=832,
            height=1216,
            loras=[],
            preset_id=self.preset_id,
            idea="a cat",
        )
        kwargs.update(over)
        return self.run_async(self.runner.generate(**kwargs))

    def test_happy_path_uploads_and_submits(self):
        job_id = self._generate()
        self.assertEqual(job_id, 77)
        self.assertEqual(self.comfy.uploads, [self.src])
        preset_id, params, kwargs = self.comfy.submits[0]
        self.assertEqual(preset_id, self.preset_id)
        self.assertEqual(params.first_frame, "uploaded_src.png")
        self.assertEqual(params.duration_s, 6.0)
        self.assertTrue(str(kwargs["i2v_source_path"]).endswith("src.png"))
        self.assertIn("i2v", str(kwargs["output_dir"]))

    def test_missing_preset_rejected(self):
        with self.assertRaises(I2vRequestError):
            self._generate(preset_id=9999)

    def test_mismatched_tag_rejected(self):
        pid = self.db.create_workflow_preset(
            "ref",
            "ref2v",
            json.dumps(_WF),
            "{}",
            video_target="minimax",
            video_mode="ref2va",
        )
        with self.assertRaises(I2vRequestError):
            self._generate(preset_id=pid)

    def test_untagged_legacy_preset_passes(self):
        pid = self.db.create_workflow_preset(
            "legacy",
            "ref2v",
            json.dumps(_WF),
            "{}",
            video_target=None,
            video_mode=None,
        )
        self._generate(preset_id=pid)  # must not raise

    def test_loras_without_stack_rejected(self):
        with self.assertRaises(I2vRequestError):
            self._generate(loras=[{"name": "turbo.safetensors", "strength": 1}])

    def test_empty_prompt_rejected(self):
        with self.assertRaises(I2vRequestError):
            self._generate(prompt="   ")

    def test_duration_omitted_when_not_bound(self):
        wf = {k: v for k, v in _WF.items() if k != "5"}  # no MS_DURATION
        pid = self.db.create_workflow_preset(
            "nodur",
            "ref2v",
            json.dumps(wf),
            "{}",
            video_target="minimax",
            video_mode="i2va",
        )
        self._generate(preset_id=pid)
        _, params, _ = self.comfy.submits[-1]
        self.assertIsNone(params.duration_s)


class TestIngest(I2vRunnerBase):
    def test_job_outputs_creates_rows_and_emits(self):
        events = []
        self.runner.on_event(lambda ch, ev, data: events.append((ch, ev, data)))
        out = Path(self.tmp.name) / "clip.mp4"
        out.write_bytes(b"fake")
        self.db.save_media(
            Media(
                file_path=out,
                file_size=4,
                width=832,
                height=1216,
                format="mp4",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )
        job_id = self.db.create_generation_job(
            self.preset_id,
            json.dumps({"positive": "p", "seed": 5, "duration_s": 6.0}),
            i2v_source_path=str(self.src),
        )

        async def scenario():
            self.runner.handle_job_event(
                "job_outputs", {"job_id": job_id, "files": [str(out)]}
            )
            await self.runner.aclose()

        self.run_async(scenario())
        rows = self.db.list_i2v_videos(str(self.src))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["seed"], 5)
        self.assertEqual(
            [(ch, ev) for ch, ev, _ in events], [("i2v", "i2v_videos_changed")]
        )

    def test_non_i2v_job_ignored(self):
        job_id = self.db.create_generation_job(self.preset_id, "{}")

        async def scenario():
            self.runner.handle_job_event(
                "job_outputs", {"job_id": job_id, "files": ["/x.mp4"]}
            )
            await self.runner.aclose()

        self.run_async(scenario())
        self.assertEqual(self.db.list_i2v_videos(str(self.src)), [])


if __name__ == "__main__":
    unittest.main()
