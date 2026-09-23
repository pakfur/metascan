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
    render_seconds,
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
            megapixels=0.75,
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

    def test_dims_derived_from_source_aspect_and_budget(self):
        """832x1216 source -> portrait output at ~0.75 MP, snapped to 16."""
        self._generate(megapixels=0.75)
        _, params, _ = self.comfy.submits[0]
        self.assertGreater(params.height, params.width)
        self.assertEqual(params.width % 16, 0)
        self.assertEqual(params.height % 16, 0)
        self.assertAlmostEqual(
            params.width * params.height / 1_000_000, 0.75, delta=0.04
        )
        self.assertAlmostEqual(params.width / params.height, 832 / 1216, delta=0.02)

    def test_landscape_source_yields_landscape_output(self):
        self.db.save_media(
            Media(
                file_path=self.src,
                file_size=1,
                width=1920,
                height=1080,
                format="png",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )
        self._generate()
        _, params, _ = self.comfy.submits[0]
        self.assertGreater(params.width, params.height)

    def test_budget_changes_output_size(self):
        self._generate(megapixels=0.25)
        small = self.comfy.submits[0][1]
        self._generate(megapixels=1.0)
        large = self.comfy.submits[1][1]
        self.assertLess(small.width * small.height, large.width * large.height)

    def test_non_positive_megapixels_rejected(self):
        with self.assertRaises(I2vRequestError):
            self._generate(megapixels=0)

    def test_unknown_source_dimensions_rejected(self):
        orphan = Path(self.tmp.name) / "orphan.png"
        orphan.write_bytes(b"\x89PNG fake")
        with self.assertRaises(I2vRequestError):
            self._generate(source_path=str(orphan))

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

    # ---- steps (High quality only, MS_STEPS-bound presets only) ----------

    def _steps_preset(self):
        wf = dict(_WF)
        wf["6"] = {
            "class_type": "BasicScheduler",
            "inputs": {"scheduler": "simple", "steps": 20, "denoise": 1},
            "_meta": {"title": "MS_STEPS"},
        }
        return self.db.create_workflow_preset(
            "i2v-quality",
            "ref2v",
            json.dumps(wf),
            "{}",
            video_target="minimax",
            video_mode="i2va",
        )

    def test_quality_steps_reach_params(self):
        self._generate(preset_id=self._steps_preset(), quality="quality", steps=30)
        _, params, _ = self.comfy.submits[-1]
        self.assertEqual(params.steps, 30)

    def test_fast_quality_never_sends_steps(self):
        """Even against a preset that binds MS_STEPS: the Fast slot is a
        step-distilled build and must keep its baked-in count."""
        self._generate(preset_id=self._steps_preset(), quality="fast", steps=30)
        _, params, _ = self.comfy.submits[-1]
        self.assertIsNone(params.steps)

    def test_steps_omitted_when_not_bound(self):
        """The MS_DURATION precedent: a preset registered before MS_STEPS
        existed keeps its baked-in count rather than failing the submit."""
        self._generate(quality="quality", steps=30)
        _, params, _ = self.comfy.submits[-1]
        self.assertIsNone(params.steps)

    def test_steps_default_to_none(self):
        self._generate(preset_id=self._steps_preset(), quality="quality")
        _, params, _ = self.comfy.submits[-1]
        self.assertIsNone(params.steps)

    def test_non_positive_steps_rejected(self):
        with self.assertRaises(I2vRequestError):
            self._generate(preset_id=self._steps_preset(), quality="quality", steps=0)

    # ---- output placement (config: i2v.output_root / output_prefix) -------

    def test_legacy_layout_when_no_output_root(self):
        self._generate()
        _, _, kwargs = self.comfy.submits[-1]
        self.assertEqual(
            Path(kwargs["output_dir"]), Path(self.tmp.name) / "out" / "i2v" / "src"
        )
        self.assertEqual(kwargs["output_prefix"], "i2v_src")
        self.assertIsNone(kwargs.get("output_name"))

    def test_output_root_and_prefix_place_the_file(self):
        root = Path(self.tmp.name) / "library"
        root.mkdir()
        self._generate(output_root=str(root), output_prefix="/%Y-%m-%d/minimax_")
        _, _, kwargs = self.comfy.submits[-1]
        day = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(Path(kwargs["output_dir"]), root / day)
        self.assertRegex(kwargs["output_name"], r"^minimax_\d{10}$")
        self.assertIsNone(kwargs.get("output_prefix"))

    def test_output_numbers_are_unique_within_a_second(self):
        root = Path(self.tmp.name) / "library"
        root.mkdir()
        for _ in range(3):
            self._generate(output_root=str(root), output_prefix="x_")
        names = [kw["output_name"] for _, _, kw in self.comfy.submits]
        self.assertEqual(len(set(names)), 3)

    def test_missing_output_root_rejected_before_upload(self):
        with self.assertRaises(I2vRequestError) as ctx:
            self._generate(output_root=str(Path(self.tmp.name) / "nope"))
        self.assertIn("nope", str(ctx.exception))
        self.assertEqual(self.comfy.uploads, [])
        self.assertEqual(self.comfy.submits, [])

    def test_traversing_prefix_rejected(self):
        root = Path(self.tmp.name) / "library"
        root.mkdir()
        with self.assertRaises(I2vRequestError):
            self._generate(output_root=str(root), output_prefix="../x_")


class TestRenderSeconds(unittest.TestCase):
    def test_difference_between_iso_stamps(self):
        self.assertEqual(
            render_seconds(
                "2026-09-20T18:00:00+00:00", "2026-09-20T18:04:07.500000+00:00"
            ),
            247.5,
        )

    def test_missing_end_uses_now(self):
        from datetime import timedelta, timezone

        start = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()
        got = render_seconds(start, None)
        assert got is not None
        self.assertTrue(89.0 <= got <= 120.0, got)

    def test_missing_or_junk_start_is_none(self):
        self.assertIsNone(render_seconds(None, None))
        self.assertIsNone(render_seconds("not a date", None))

    def test_negative_span_is_none(self):
        self.assertIsNone(
            render_seconds("2026-09-20T18:05:00+00:00", "2026-09-20T18:00:00+00:00")
        )


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

    def test_ingest_records_generated_dimensions(self):
        """Dims come from the job's stored params, so they survive a
        server restart -- unlike the in-memory quality/idea metadata."""
        out = Path(self.tmp.name) / "clip2.mp4"
        out.write_bytes(b"fake")
        self.db.save_media(
            Media(
                file_path=out,
                file_size=4,
                width=1152,
                height=656,
                format="mp4",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )
        job_id = self.db.create_generation_job(
            self.preset_id,
            json.dumps(
                {
                    "positive": "p",
                    "seed": 5,
                    "duration_s": 6.0,
                    "width": 1152,
                    "height": 656,
                }
            ),
            i2v_source_path=str(self.src),
        )

        async def scenario():
            self.runner.handle_job_event(
                "job_outputs", {"job_id": job_id, "files": [str(out)]}
            )
            await self.runner.aclose()

        self.run_async(scenario())
        rows = self.db.list_i2v_videos(str(self.src))
        self.assertEqual(rows[0]["width"], 1152)
        self.assertEqual(rows[0]["height"], 656)

    def test_ingest_records_steps_and_render_time(self):
        """Both come from the durable job row (params JSON + started_at),
        so they survive a server restart."""
        out = Path(self.tmp.name) / "clip3.mp4"
        out.write_bytes(b"fake")
        self.db.save_media(
            Media(
                file_path=out,
                file_size=4,
                width=672,
                height=1184,
                format="mp4",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )
        job_id = self.db.create_generation_job(
            self.preset_id,
            json.dumps({"positive": "p", "seed": 5, "duration_s": 6.0, "steps": 30}),
            i2v_source_path=str(self.src),
        )
        self.db.update_generation_job(
            job_id,
            state="running",
            started_at="2026-09-20T18:00:00+00:00",
            finished_at="2026-09-20T18:04:07.500000+00:00",
        )

        async def scenario():
            self.runner.handle_job_event(
                "job_outputs", {"job_id": job_id, "files": [str(out)]}
            )
            await self.runner.aclose()

        self.run_async(scenario())
        rows = self.db.list_i2v_videos(str(self.src))
        self.assertEqual(rows[0]["steps"], 30)
        self.assertEqual(rows[0]["render_s"], 247.5)

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


class _JobCreatingComfy(FakeComfy):
    """FakeComfy whose submit writes a real generation_jobs row, so a
    generate() can be followed through to its ingest."""

    def __init__(self, db):
        super().__init__()
        self.db = db

    async def submit(self, preset_id, params, **kwargs):
        self.submits.append((preset_id, params, kwargs))
        return self.db.create_generation_job(
            preset_id,
            json.dumps(
                {
                    "positive": params.positive,
                    "seed": params.seed,
                    "duration_s": params.duration_s,
                    "width": params.width,
                    "height": params.height,
                    "steps": params.steps,
                }
            ),
            i2v_source_path=kwargs["i2v_source_path"],
        )


class TestFormStateAtIngest(I2vRunnerBase):
    """The dialog's form is persisted at ingest, and only at ingest."""

    def setUp(self):
        super().setUp()
        self.comfy = _JobCreatingComfy(self.db)
        self.runner.comfy = self.comfy
        self.out = Path(self.tmp.name) / "clip.mp4"
        self.out.write_bytes(b"fake")
        self.db.save_media(
            Media(
                file_path=self.out,
                file_size=4,
                width=640,
                height=944,
                format="mp4",
                created_at=datetime.now(),
                modified_at=datetime.now(),
            )
        )

    def _generate(self, **over):
        kwargs = dict(
            source_path=str(self.src),
            prompt="[Shot 1] the cat stretches",
            duration_s=10.0,
            quality="fast",
            seed=123,
            megapixels=0.5,
            loras=[],
            preset_id=self.preset_id,
            idea="a cat stretches",
            steps=30,
        )
        kwargs.update(over)
        return self.run_async(self.runner.generate(**kwargs))

    def _ingest(self, job_id):
        async def scenario():
            self.runner.handle_job_event(
                "job_outputs", {"job_id": job_id, "files": [str(self.out)]}
            )
            await self.runner.aclose()

        self.run_async(scenario())

    def test_the_form_as_submitted_becomes_the_clips_form_state(self):
        job_id = self._generate()
        self._ingest(job_id)

        row = self.db.list_i2v_videos(str(self.src))[0]
        self.assertEqual(
            row["form_state"],
            {
                "idea": "a cat stretches",
                "prompt": "[Shot 1] the cat stretches",
                "duration_s": 10.0,
                "quality": "fast",
                "megapixels": 0.5,
                # The form's step selection, kept even though a Fast render
                # never applies it -- it is what the user had chosen.
                "steps": 30,
                "seed": 123,
                "loras": [],
            },
        )

    def test_rendered_facts_are_recorded_alongside_and_stay_honest(self):
        job_id = self._generate()
        self._ingest(job_id)

        row = self.db.list_i2v_videos(str(self.src))[0]
        self.assertEqual(row["megapixels"], 0.5)
        self.assertEqual(row["loras"], [])
        self.assertEqual(row["quality"], "fast")
        # steps is the APPLIED count: none for a Fast render.
        self.assertIsNone(row["steps"])

    def test_nothing_is_persisted_for_a_render_that_never_ingests(self):
        self._generate()  # submitted, never finishes

        self.assertEqual(self.db.list_i2v_videos(str(self.src)), [])
        with self.db.lock, self.db._get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM i2v_videos").fetchone()[0]
        self.assertEqual(count, 0)

    def test_a_restart_between_submit_and_ingest_leaves_form_state_empty(self):
        """The form snapshot is in-memory by decision (nothing is written
        before ingest), so a fresh process has none to store. The row must
        still ingest; the API then builds a form from the rendered facts."""
        job_id = self._generate()
        self.runner._job_meta.clear()  # what a restart does

        self._ingest(job_id)

        row = self.db.list_i2v_videos(str(self.src))[0]
        self.assertIsNone(row["form_state"])
        self.assertEqual(row["seed"], 123)  # facts still come from the job row
