"""Tests for I2vRunner: prompt expansion, generate validation, ingest."""

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.i2v_runner import (
    I2vNotFoundError,
    I2vRequestError,
    I2vRunner,
    I2vUnavailableError,
    SEED_MAX,
    plan_i2v_seeds,
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

    def test_write_prompt_names_the_model_asked(self):
        self.vlm.model_id = "qwen3vl-4b"
        text, warnings, model = self.run_async(
            self.runner.write_prompt(str(self.src), "a cat jumps", 6)
        )
        self.assertTrue(text.startswith("For the target video"))
        self.assertEqual(model, "qwen3vl-4b")

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
                "template_id": None,
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


class TestCategoryTagging(TestFormStateAtIngest):
    """A clip carries the categories chosen in the dialog that made it."""

    def _link_sources(self, path):
        with self.db.lock, self.db._get_connection() as conn:
            rows = conn.execute(
                "SELECT c.name, mc.source FROM media_categories mc "
                "JOIN categories c ON c.id = mc.category_id WHERE mc.file_path = ?",
                (Path(path).as_posix(),),
            ).fetchall()
        return {r["name"]: r["source"] for r in rows}

    def test_i2v_generate_tags_video(self):
        events = []
        self.runner.on_event(lambda ch, ev, data: events.append((ch, ev, data)))
        cid = self.db.create_category("Anime").id
        job_id = self._generate(category_ids=[cid])
        self._ingest(job_id)

        self.assertEqual(len(self.db.list_i2v_videos(str(self.src))), 1)
        self.assertEqual(
            {c.name for c in self.db.get_media_categories(str(self.out))}, {"Anime"}
        )
        self.assertEqual(self._link_sources(self.out), {"Anime": "generation"})
        self.assertIn(("categories", "categories_changed", {}), events)
        # Tagged before the clip is announced, so a refetch sees the chip.
        names = [(ch, ev) for ch, ev, _ in events]
        self.assertLess(
            names.index(("categories", "categories_changed")),
            names.index(("i2v", "i2v_videos_changed")),
        )

    def test_a_category_deleted_before_ingest_is_skipped(self):
        keep = self.db.create_category("Keep").id
        gone = self.db.create_category("Gone").id
        job_id = self._generate(category_ids=[gone, keep])
        self.assertTrue(self.db.delete_category(gone))
        with self.assertLogs("metascan.core.generation_categories", "WARNING"):
            self._ingest(job_id)
        self.assertEqual(len(self.db.list_i2v_videos(str(self.src))), 1)
        self.assertEqual(self._link_sources(self.out), {"Keep": "generation"})

    def test_no_categories_emits_no_categories_event(self):
        events = []
        self.runner.on_event(lambda ch, ev, data: events.append((ch, ev)))
        self.db.create_category("Anime")
        self._ingest(self._generate())
        self.assertEqual(self._link_sources(self.out), {})
        self.assertEqual(events, [("i2v", "i2v_videos_changed")])


class TestGeneratePromptWithTemplate(I2vRunnerBase):
    def setUp(self):
        super().setUp()
        from metascan.core.i2v_templates import get_i2v_template

        self.template = get_i2v_template("dialog_ots_15")
        roles = [
            {
                "id": "A",
                "bound": True,
                "description": "a woman in a red coat",
                "tag": "the woman",
            },
            {
                "id": "B",
                "bound": True,
                "description": "a man in a leather jacket",
                "tag": "the man",
            },
        ]
        beats = [
            {"action": "He leans in", "line": "We shouldn't be here"},
            {"action": "His jaw tightens"},
            {"action": "She folds her arms", "line": "Too late"},
            {"action": "He nods"},
        ]
        self.vlm = FakeVlm(
            json.dumps(
                {
                    "roles": roles,
                    "beats": beats,
                    "overall_soundscape": "jazz",
                    "non_diegetic_music": "none",
                }
            )
        )
        self.runner.get_vlm = lambda: self.vlm

    def test_template_drives_grammar_prompt_and_assembly(self):
        from metascan.core.i2v_templates import (
            build_i2v_template_user_prompt,
            i2v_template_grammar,
        )

        text, warnings = self.run_async(
            self.runner.generate_prompt(
                str(self.src), "an argument", 15.0, "dialog_ots_15"
            )
        )
        call = self.vlm.calls[0]
        self.assertEqual(call["grammar"], i2v_template_grammar(self.template))
        self.assertEqual(
            call["user_prompt"],
            build_i2v_template_user_prompt(self.template, "an argument"),
        )
        self.assertIn(
            "fixed shot plan",
            call["system_prompt"].lower() + call["user_prompt"].lower(),
        )
        self.assertIn(
            "[Shot 2] At 00:04.000, the shot cuts to a close-up of the man.", text
        )
        self.assertIn("[Shot 3] At 00:05.000, the woman (S2) says:", text)
        self.assertEqual([w for w in warnings if "Shot" in w], [])

    def test_off_screen_note_reaches_the_warnings(self):
        data = json.loads(self.vlm.response)
        data["roles"][0]["bound"] = False
        self.vlm.response = json.dumps(data)
        _, warnings = self.run_async(
            self.runner.generate_prompt(str(self.src), "", 15.0, "dialog_ots_15")
        )
        self.assertTrue(
            any(w.startswith("role A is not in the picture") for w in warnings)
        )

    def test_duration_must_match_the_template(self):
        with self.assertRaises(I2vRequestError) as ctx:
            self.run_async(
                self.runner.generate_prompt(str(self.src), "", 10.0, "dialog_ots_15")
            )
        self.assertIn("15", str(ctx.exception))
        self.assertIn("10", str(ctx.exception))

    def test_unknown_template_is_a_request_error(self):
        with self.assertRaises(I2vRequestError) as ctx:
            self.run_async(self.runner.generate_prompt(str(self.src), "", 15.0, "nope"))
        self.assertIn("unknown i2v template", str(ctx.exception))

    def test_no_template_uses_the_single_take_path_unchanged(self):
        from metascan.core.i2v_compiler import i2v_grammar

        self.vlm = FakeVlm(_beats_response(3))
        self.runner.get_vlm = lambda: self.vlm
        self.run_async(self.runner.generate_prompt(str(self.src), "x", 6.0))
        self.assertEqual(self.vlm.calls[0]["grammar"], i2v_grammar(6.0))


class TestGenerateRecordsTemplateId(TestFormStateAtIngest):
    def test_template_id_is_in_the_form_snapshot_at_ingest(self):
        job_id = self._generate(duration_s=15.0, template_id="dialog_ots_15")
        self._ingest(job_id)
        row = self.db.list_i2v_videos(str(self.src))[0]
        self.assertEqual(row["form_state"]["template_id"], "dialog_ots_15")

    def test_generate_does_not_require_the_template_to_exist(self):
        # The prompt text already embodies the cadence; a renamed template
        # file must not block a render.
        job_id = self._generate(template_id="gone_20")
        self._ingest(job_id)
        row = self.db.list_i2v_videos(str(self.src))[0]
        self.assertEqual(row["form_state"]["template_id"], "gone_20")

    # ---- writer lineage: who wrote the prompt, its draft, edited or not ----

    def _row(self, job_id):
        self._ingest(job_id)
        rows = self.db.list_i2v_videos(str(self.src))
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_i2v_records_writer_and_edit(self):
        job_id = self._generate(
            prompt="[Shot 1] the cat stretches slowly",
            writer_id="base",
            draft_prompt="[Shot 1] the cat stretches",
        )
        row = self._row(job_id)
        self.assertEqual(row["writer_id"], "base")
        self.assertEqual(row["draft_prompt"], "[Shot 1] the cat stretches")
        self.assertEqual(row["edited"], 1)
        self.assertEqual(row["prompt_used"], "[Shot 1] the cat stretches slowly")

    def test_whitespace_only_difference_is_not_an_edit(self):
        job_id = self._generate(
            prompt="[Shot 1]  the cat\nstretches",
            writer_id="base",
            draft_prompt="[Shot 1] the cat stretches",
        )
        self.assertEqual(self._row(job_id)["edited"], 0)

    def test_i2v_typed_prompt_is_manual(self):
        job_id = self._generate()
        row = self._row(job_id)
        self.assertEqual(row["writer_id"], "manual")
        self.assertIsNone(row["draft_prompt"])
        self.assertEqual(row["edited"], 0)

    def test_a_draft_must_name_a_drafting_writer(self):
        with self.assertRaises(I2vRequestError):
            self._generate(writer_id="manual", draft_prompt="a draft")
        with self.assertRaises(I2vRequestError):
            self._generate(writer_id=None, draft_prompt="a draft")
        self.assertEqual(self.comfy.submits, [])

    def test_writer_id_without_a_draft_is_recorded_manual(self):
        job_id = self._generate(writer_id="base")
        self.assertEqual(self._row(job_id)["writer_id"], "manual")

    def test_i2v_records_the_writer_model(self):
        job_id = self._generate(
            writer_id="base", draft_prompt="a draft", writer_model="qwen3vl-8b"
        )
        self.assertEqual(self._row(job_id)["writer_model"], "qwen3vl-8b")

    def test_a_typed_i2v_prompt_has_no_writer_model(self):
        job_id = self._generate(writer_model="qwen3vl-8b")
        row = self._row(job_id)
        self.assertEqual(row["writer_id"], "manual")
        self.assertIsNone(row["writer_model"])

    def test_an_unusable_writer_model_is_refused(self):
        for bad in ("nope", "qwen38-27b"):
            with self.assertRaises(I2vRequestError):
                self._generate(
                    writer_id="base", draft_prompt="a draft", writer_model=bad
                )
        self.assertEqual(self.comfy.submits, [])

    def test_a_restart_between_submit_and_ingest_leaves_lineage_null(self):
        job_id = self._generate(writer_id="base", draft_prompt="a draft")
        self.runner._job_meta.clear()
        row = self._row(job_id)
        self.assertIsNone(row["writer_id"])
        self.assertIsNone(row["draft_prompt"])
        self.assertIsNone(row["edited"])


class TestPlanSeeds(unittest.TestCase):
    def test_fixed_is_one_seed_per_batch(self):
        self.assertEqual(plan_i2v_seeds(5, "fixed", 3, 4), ([[5], [5], [5]], 5))

    def test_increment_runs_across_batches(self):
        self.assertEqual(plan_i2v_seeds(5, "increment", 2, 2), ([[5, 6], [7, 8]], 9))

    def test_decrement(self):
        self.assertEqual(plan_i2v_seeds(5, "decrement", 1, 3), ([[5, 4, 3]], 2))

    def test_random_uses_rng(self):
        draws = iter([1, 2, 3, 4])
        self.assertEqual(
            plan_i2v_seeds(9, "random", 1, 2, rng=lambda: next(draws)),
            ([[9, 1]], 2),
        )

    def test_last_seed_at_the_edge_has_no_next(self):
        self.assertEqual(
            plan_i2v_seeds(SEED_MAX, "increment", 1, 1), ([[SEED_MAX]], None)
        )

    def test_leaving_the_range_is_refused(self):
        with self.assertRaises(I2vRequestError):
            plan_i2v_seeds(SEED_MAX, "increment", 1, 2)
        with self.assertRaises(I2vRequestError):
            plan_i2v_seeds(0, "decrement", 2, 1)


# ---- batch runs ------------------------------------------------------------


class CountingComfy(FakeComfy):
    def __init__(self, order=None):
        super().__init__()
        self.order = order if order is not None else []
        self.cancels = []
        self._next = 100

    async def submit(self, preset_id, params, **kwargs):
        self.order.append("submit")
        self.submits.append((preset_id, params, kwargs))
        self._next += 1
        return self._next

    async def cancel(self, job_id):
        self.cancels.append(job_id)


class WritingVlm(FakeVlm):
    def __init__(self, order=None, fail_on=None):
        super().__init__(_beats_response())
        self.order = order if order is not None else []
        self.fail_on = fail_on
        self.shutdowns = 0

    async def generate_text(self, **kwargs):
        self.order.append("write")
        if self.fail_on is not None and len(self.calls) + 1 == self.fail_on:
            raise RuntimeError("model fell over")
        n = len(self.calls) + 1
        self.response = json.dumps(
            {
                "beats": [
                    {"action": f"Written take {n} happens.", "camera": "static"}
                    for _ in range(3)
                ],
                "overall_soundscape": "Wind.",
                "non_diegetic_music": "None.",
            }
        )
        return await super().generate_text(**kwargs)

    async def shutdown(self):
        self.order.append("unload")
        self.shutdowns += 1


class TestBatchRun(I2vRunnerBase):
    def setUp(self):
        super().setUp()
        self.order = []
        self.comfy = CountingComfy(self.order)
        self.vlm = WritingVlm(self.order)
        self.events = []
        self._make_runner()

    def _make_runner(self, **kw):
        self.runner = I2vRunner(
            db=self.db,
            comfy=self.comfy,
            get_vlm=lambda: self.vlm,
            output_root=Path(self.tmp.name) / "out",
            **kw,
        )
        self.runner.on_event(lambda ch, ev, data: self.events.append((ev, data)))

    def _req(self, **over):
        from metascan.core.i2v_runner import I2vBatchRequest

        kw = dict(
            source_path=str(self.src),
            prompt="BOX PROMPT",
            idea="a cat",
            duration_s=6.0,
            quality="fast",
            seed=10,
            seed_policy="increment",
            batch_size=1,
            count_per_batch=1,
            megapixels=0.75,
            loras=[],
            preset_id=self.preset_id,
        )
        kw.update(over)
        return I2vBatchRequest(**kw)

    def _finish(self, job_id, state="done"):
        self.runner.handle_job_event("job_update", {"job_id": job_id, "state": state})

    def _names(self):
        return [name for name, _ in self.events]

    def run_async(self, coro):
        async def wrapped():
            try:
                return await coro
            finally:
                await self.runner.aclose()  # no run outlives its loop

        return super().run_async(wrapped())

    async def _settle(self, n=0):
        # generate() hops threads (db reads), so give the loop real time.
        await asyncio.sleep(0.15)

    async def _run_to_end(self, req, finish="done"):
        """Start, finish every job as it appears, wait for the terminal frame."""
        started = await self.runner.start_batch(req)
        for _ in range(500):
            await asyncio.sleep(0.01)
            for job_id in range(101, 101 + len(self.comfy.submits)):
                if job_id not in self.finished:
                    self.finished.add(job_id)
                    self._finish(job_id, finish)
            if not self.runner._batches:
                break
        return started

    def run_batch(self, req, finish="done"):
        self.finished = set()
        return self.run_async(self._run_to_end(req, finish))

    def test_box_text_is_batch_one_and_later_batches_are_written(self):
        self.run_batch(self._req(batch_size=3))
        self.assertEqual(len(self.vlm.calls), 2)
        texts = [p.positive for _, p, _ in self.comfy.submits]
        self.assertEqual(texts[0], "BOX PROMPT")
        self.assertIn("Written take 1", texts[1])
        self.assertIn("Written take 2", texts[2])
        self.assertEqual(self._names()[-1], "batch_done")

    def test_empty_box_writes_every_batch(self):
        self.run_batch(self._req(prompt="", batch_size=2))
        self.assertEqual(len(self.vlm.calls), 2)
        self.assertEqual(len(self.comfy.submits), 2)

    def test_count_renders_one_prompt_per_seed(self):
        started = self.run_batch(self._req(count_per_batch=3))
        self.assertEqual(started.total_videos, 3)
        params = [p for _, p, _ in self.comfy.submits]
        self.assertEqual({p.positive for p in params}, {"BOX PROMPT"})
        self.assertEqual([p.seed for p in params], [10, 11, 12])

    def test_fixed_seed_ignores_count(self):
        self.run_batch(self._req(seed_policy="fixed", batch_size=2, count_per_batch=5))
        params = [p for _, p, _ in self.comfy.submits]
        self.assertEqual(len(params), 2)
        self.assertEqual({p.seed for p in params}, {10})

    def test_written_clip_form_holds_its_own_prompt(self):
        self.runner.unload_vlm_during_generation = False
        meta = {}

        async def go():
            await self.runner.start_batch(
                self._req(
                    batch_size=2,
                    writer_id="base",
                    draft_prompt="BOX DRAFT",
                    writer_model="qwen3vl-8b",
                )
            )
            await self._settle(100)
            meta.update(self.runner._job_meta)

        self.run_async(go())
        first, second = meta[101], meta[102]
        self.assertEqual(first["form"]["prompt"], "BOX PROMPT")
        self.assertEqual(first["draft_prompt"], "BOX DRAFT")
        self.assertEqual(first["writer_model"], "qwen3vl-8b")
        self.assertIn("Written take 1", second["form"]["prompt"])
        self.assertEqual(second["draft_prompt"], second["form"]["prompt"])
        self.assertEqual(second["writer_id"], "base")
        self.assertEqual(second["writer_model"], "qwen3vl-8b")

    def test_prompts_first_then_unload_then_submit(self):
        self.run_batch(self._req(prompt="", batch_size=2, count_per_batch=1))
        self.assertEqual(self.order, ["write", "write", "unload", "submit", "submit"])

    def test_unload_off_renders_as_each_prompt_is_ready(self):
        self._make_runner(unload_vlm_during_generation=False)
        self.run_batch(self._req(prompt="", batch_size=2))
        self.assertEqual(self.order, ["write", "submit", "write", "submit"])

    def test_window_caps_unfinished_jobs(self):
        async def go():
            await self.runner.start_batch(self._req(count_per_batch=4, window=2))
            await self._settle()
            self.assertEqual(len(self.comfy.submits), 2)
            self._finish(101)
            await self._settle()
            self.assertEqual(len(self.comfy.submits), 3)
            self._finish(102, "failed")
            await self._settle()
            self.assertEqual(len(self.comfy.submits), 4)
            self._finish(103)
            self._finish(104)
            await self._settle()

        self.run_async(go())
        self.assertEqual(self._names()[-1], "batch_done")
        self.assertNotIn("batch_failed", self._names())

    def test_cancel_stops_writing_and_cancels_jobs(self):
        async def go():
            started = await self.runner.start_batch(
                self._req(count_per_batch=2, batch_size=2, window=2)
            )
            await self._settle()
            self.assertEqual(len(self.comfy.submits), 2)
            self.assertTrue(await self.runner.cancel_batch(started.batch_id))
            self.assertFalse(await self.runner.cancel_batch(started.batch_id))
            with self.assertRaises(I2vNotFoundError):
                await self.runner.cancel_batch("never-seen")

        self.run_async(go())
        self.assertEqual(sorted(self.comfy.cancels), [101, 102])
        self.assertEqual(self._names().count("batch_cancelled"), 1)
        self.assertEqual(self.runner.active_batches(), [])

    def test_a_failed_write_keeps_what_was_queued(self):
        self._make_runner(unload_vlm_during_generation=False)
        self.vlm.fail_on = 2
        self.run_async(self._after_start(self._req(prompt="", batch_size=2)))
        self.assertEqual(self.comfy.cancels, [])
        failed = [d for n, d in self.events if n == "batch_failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("1 of 2", failed[0]["error"])

    async def _after_start(self, req):
        await self.runner.start_batch(req)
        await self._settle(100)

    def test_source_file_gone_mid_run_ends_failed_keeps_jobs(self):
        self._make_runner(unload_vlm_during_generation=False)
        orig = self.comfy.submit

        async def submit(*a, **k):
            out = await orig(*a, **k)
            self.src.unlink()
            return out

        self.comfy.submit = submit
        self.run_async(self._after_start(self._req(count_per_batch=3)))
        self.assertEqual(len(self.comfy.submits), 1)
        self.assertEqual(self.comfy.cancels, [])
        self.assertEqual(self._names().count("batch_failed"), 1)

    def test_job_that_ends_before_submit_returns_does_not_hang_the_run(self):
        orig = self.comfy.submit

        async def submit(*a, **k):
            job_id = await orig(*a, **k)
            if job_id == 102:  # the last job fails before submit returns
                self._finish(job_id, "failed")
            return job_id

        self.comfy.submit = submit
        self.finished = set()
        self.run_async(self._run_to_end(self._req(count_per_batch=2, window=1)))
        self.assertEqual(self._names()[-1], "batch_done")
        self.assertEqual(self.runner.active_batches(), [])
        self.assertNotIn(102, self.runner._job_meta)
        self.assertEqual(len(self.comfy.submits), 2)

    def test_cancelling_one_run_leaves_the_other(self):
        async def go():
            a = await self.runner.start_batch(self._req(window=1, count_per_batch=2))
            b = await self.runner.start_batch(self._req(window=1, count_per_batch=2))
            await self._settle()
            await self.runner.cancel_batch(a.batch_id)
            self.assertEqual(
                [r["batch_id"] for r in self.runner.active_batches()], [b.batch_id]
            )
            self.assertEqual(len(self.comfy.cancels), 1)

        self.run_async(go())

    def test_no_unload_while_another_run_writes(self):
        gate = asyncio.Event() if False else None
        blocked = {}

        async def go():
            release = asyncio.Event()
            real = self.vlm.generate_text

            async def slow(**kw):
                if blocked.get("slow"):
                    await release.wait()
                return await real(**kw)

            self.vlm.generate_text = slow
            blocked["slow"] = True
            await self.runner.start_batch(self._req(prompt="", batch_size=1))
            await self._settle()
            self.assertTrue(self.runner.is_prompting())
            blocked["slow"] = False
            await self.runner.start_batch(self._req())  # box text, no writing
            await self._settle()
            self.assertEqual(self.vlm.shutdowns, 0)
            release.set()
            await self._settle()
            # Same through the other runner's flag.
            baseline = self.vlm.shutdowns
            self.runner.prompting_elsewhere = lambda: True
            await self.runner.start_batch(self._req())
            await self._settle()
            self.assertEqual(self.vlm.shutdowns, baseline)

        self.run_async(go())

    def test_unload_happens_when_nobody_else_writes(self):
        self.run_batch(self._req())
        self.assertEqual(self.vlm.shutdowns, 1)

    def test_adapter_gone_refuses_at_start(self):
        from metascan.core.writer_adapters import WriterAdapterUnavailable

        with self.assertRaises(WriterAdapterUnavailable):
            self.run_async(
                self.runner.start_batch(self._req(prompt="", writer="adapter:9"))
            )
        self.assertEqual(self.runner.active_batches(), [])
        self.assertEqual(self.comfy.submits, [])

    def test_no_writer_needed_when_batch_one_is_box_text(self):
        self.runner.get_vlm = lambda: None
        self.run_batch(self._req())
        self.assertEqual(len(self.comfy.submits), 1)
        with self.assertRaises(I2vUnavailableError):
            self.run_async(self.runner.start_batch(self._req(batch_size=2)))
