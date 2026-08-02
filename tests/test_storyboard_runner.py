"""Tests for StoryboardRunner: parse/synthesize/generate/ingest orchestration.

Uses a real temp DatabaseManager (behavior under FK cascades and the
storyboard schema matters) plus stub comfy/vlm objects -- ComfyClient and
VlmClient each have their own test suites; this module only needs to prove
StoryboardRunner wires the three together correctly.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.comfy_bindings import GenerationParams, resolve_bindings
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.storyboard_runner import (
    ConfirmRequiredError,
    StoryboardError,
    StoryboardRunner,
)
from metascan.core.vlm_client import VlmError


# ---- fixtures / stubs -------------------------------------------------


def _node(class_type: str, title: str, inputs: dict) -> dict:
    return {"class_type": class_type, "inputs": inputs, "_meta": {"title": title}}


def t2i_workflow() -> dict:
    """A valid t2i graph with no MS_NEGATIVE / MS_LORA node -- deliberately,
    so the negative/LoRA validation tests exercise a real missing binding."""
    return {
        "3": _node("KSampler", "MS_SEED", {"seed": 0, "steps": 20}),
        "5": _node(
            "EmptyLatentImage",
            "MS_LATENT",
            {"width": 512, "height": 512, "batch_size": 1},
        ),
        "6": _node("CLIPTextEncode", "MS_POSITIVE", {"text": ""}),
        "9": _node("SaveImage", "MS_SAVE", {"filename_prefix": "ms"}),
    }


def _media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


class StubComfy:
    def __init__(self):
        self.submitted = []  # (preset_id, params, panel_id, priority, output_dir)
        self.cancelled = []
        self.uploaded = []
        self._next_job_id = 100

    async def submit(
        self, preset_id, params, panel_id=None, priority=False, output_dir=None
    ):
        self.submitted.append((preset_id, params, panel_id, priority, output_dir))
        self._next_job_id += 1
        return self._next_job_id

    async def cancel(self, job_id):
        self.cancelled.append(job_id)

    async def upload_image(self, path):
        self.uploaded.append(path)
        return f"uploaded-{path.name}"


class StubVlm:
    def __init__(self, responses=None, fail=False):
        self.model_id = "qwen3vl-4b"
        self.responses = list(responses or [])
        self.fail = fail
        self.calls = []
        self.shutdowns = 0

    async def ensure_started(self, model_id):
        pass

    async def shutdown(self):
        self.shutdowns += 1

    async def generate_text(
        self,
        *,
        system_prompt,
        user_prompt,
        grammar=None,
        temperature=0.6,
        max_tokens=250,
        timeout=120.0,
        image_path=None,
    ):
        self.calls.append(
            {"system": system_prompt, "user": user_prompt, "grammar": grammar}
        )
        if self.fail:
            raise VlmError("boom")
        return self.responses.pop(0)


VALID_PARSE_PAYLOAD = {
    "subjects": [{"name": "MAYA", "description": "late 20s, shaved head"}],
    "scenes": [
        {
            "name": "S1",
            "location": "yard",
            "time_of_day": "dusk",
            "mood": None,
            "lighting": None,
            "panels": [
                {
                    "shot_size": "CU",
                    "angle": None,
                    "lens": None,
                    "action": "MAYA looks up",
                    "subjects": ["MAYA"],
                }
            ],
        }
    ],
}


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture
def preset_id(db):
    workflow = t2i_workflow()
    bindings = resolve_bindings(workflow, "t2i")
    return db.create_workflow_preset(
        "sdxl", "t2i", json.dumps(workflow), bindings.to_json()
    )


@pytest.fixture
def events():
    return []


@pytest.fixture
def comfy():
    return StubComfy()


class Board:
    """Handles for a built storyboard tree, so tests can refer to ids by
    name instead of tuple-unpacking everywhere."""

    def __init__(self, sb_id, subject_id, scene_id, panel0, panel1):
        self.sb_id = sb_id
        self.subject_id = subject_id
        self.scene_id = scene_id
        self.panel0 = panel0
        self.panel1 = panel1


@pytest.fixture
def board(db, preset_id) -> Board:
    """storyboard (target_model=sd, base_seed=1000, batch_size=2,
    aspect_ratio=16:9, style_block='graphite sketch', negative=None), one
    subject, one scene, two panels (panel 0 references the subject)."""
    sb_id = db.create_storyboard(
        name="Yard Chase",
        target_model="sd",
        architecture="t2i",
        aspect_ratio="16:9",
        style_block="graphite sketch",
        negative=None,
        preset_id=preset_id,
        base_seed=1000,
        batch_size=2,
    )
    subject_id = db.create_subject(
        sb_id, name="MAYA", description="late 20s, shaved head, red scarf"
    )
    scene_id = db.create_scene(sb_id, name="Salvage Yard", sort_order=0)
    panel0 = db.create_panel(
        scene_id,
        action="hand rests on hull seam",
        sort_order=0,
        shot_size="ECU",
        subject_ids=[subject_id],
    )
    panel1 = db.create_panel(
        scene_id, action="wide shot of the yard at dusk", sort_order=1
    )
    return Board(sb_id, subject_id, scene_id, panel0, panel1)


@pytest.fixture
def bare_board(db, preset_id) -> int:
    """A storyboard with no scenes yet -- for the fresh-parse test."""
    return db.create_storyboard(
        name="Empty",
        target_model="sd",
        architecture="t2i",
        preset_id=preset_id,
        base_seed=1,
        batch_size=1,
    )


def make_runner(db, comfy, vlm, events, tmp_path) -> StoryboardRunner:
    runner = StoryboardRunner(db, comfy, (lambda: vlm), output_root=tmp_path / "out")
    runner.on_event(lambda ch, ev, data: events.append((ch, ev, data)))
    return runner


# ---- parse -------------------------------------------------------------


async def test_parse_writes_structure(db, comfy, events, tmp_path, bare_board):
    vlm = StubVlm(responses=[json.dumps(VALID_PARSE_PAYLOAD)])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    tree = await runner.parse(bare_board, "INT. YARD - DUSK. Maya kneels by the hull.")

    assert tree["scenes"], "returns the fresh tree"
    assert db.get_storyboard(bare_board)["source_text"].startswith("INT. YARD")
    call = vlm.calls[0]
    assert call["grammar"] is not None
    assert "INT. YARD" in call["user"]


async def test_parse_requires_confirm_when_structure_exists(
    db, comfy, events, tmp_path, board
):
    vlm = StubVlm(responses=[json.dumps(VALID_PARSE_PAYLOAD)])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    with pytest.raises(ConfirmRequiredError):
        await runner.parse(board.sb_id, "new text")
    assert vlm.calls == []  # never called the VLM before the confirm check

    tree = await runner.parse(board.sb_id, "new text", confirm=True)
    assert tree["scenes"]


async def test_parse_without_vlm_raises(db, comfy, events, tmp_path, bare_board):
    runner = make_runner(db, comfy, None, events, tmp_path)
    with pytest.raises(StoryboardError):
        await runner.parse(bare_board, "text")


# ---- synthesize ----------------------------------------------------------


async def test_synthesize_llm_path(db, comfy, events, tmp_path, board):
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    out = await runner.synthesize(board.sb_id)

    assert out == {"synthesized": 2, "fallback": 0, "skipped_locked": 0}
    p = db.get_panel(board.panel0)
    assert p["prompt"] == "a prompt, graphite sketch"
    assert p["prompt_source"] == "llm"
    assert p["brief"].startswith("SHOT:")
    assert "SUBJECT" in vlm.calls[0]["user"]


async def test_synthesize_fallback_on_vlm_error(db, comfy, events, tmp_path, board):
    vlm = StubVlm(fail=True)
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    out = await runner.synthesize(board.sb_id)

    assert out["fallback"] == 2
    p = db.get_panel(board.panel0)
    assert p["prompt_source"] == "brief"
    assert p["prompt"].startswith("SHOT:") and p["prompt"].endswith("graphite sketch")


async def test_synthesize_no_vlm_falls_back(db, comfy, events, tmp_path, board):
    runner = make_runner(db, comfy, None, events, tmp_path)

    out = await runner.synthesize(board.sb_id)

    assert out == {"synthesized": 0, "fallback": 2, "skipped_locked": 0}
    p = db.get_panel(board.panel0)
    assert p["prompt_source"] == "brief"


async def test_synthesize_skips_locked(db, comfy, events, tmp_path, board):
    db.update_panel(
        board.panel0, prompt="hand tuned", prompt_locked=1, prompt_source="user"
    )
    vlm = StubVlm(responses=["b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    out = await runner.synthesize(board.sb_id)

    assert out["skipped_locked"] == 1
    assert db.get_panel(board.panel0)["prompt"] == "hand tuned"


async def test_synthesize_single_panel_force_overrides_lock(
    db, comfy, events, tmp_path, board
):
    db.update_panel(
        board.panel0, prompt="hand tuned", prompt_locked=1, prompt_source="user"
    )
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    out = await runner.synthesize(board.sb_id, panel_ids=[board.panel0], force=True)

    assert out["synthesized"] == 1
    p = db.get_panel(board.panel0)
    assert p["prompt_locked"] == 0


async def test_synthesize_emits_progress(db, comfy, events, tmp_path, board):
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    await runner.synthesize(board.sb_id)

    kinds = [(ch, ev) for ch, ev, _ in events]
    assert ("storyboard", "synthesis_progress") in kinds


# ---- generate --------------------------------------------------------


async def test_generate_submits_with_derived_params(db, comfy, events, tmp_path, board):
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)  # gives panels prompts

    jobs = await runner.generate(board.sb_id)

    assert len(jobs) == 2
    preset_id, params, panel_id, priority, output_dir = comfy.submitted[0]
    assert params.width == 1344 and params.height == 768  # 16:9 on sd
    assert params.batch_size == 2
    assert params.seed == 1000 + 0 * 1000 + 0  # panel_seed
    assert priority is False
    assert "scene_00" in str(output_dir) and "panel_00" in str(output_dir)


async def test_generate_creates_folder_once(db, comfy, events, tmp_path, board):
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    await runner.generate(board.sb_id)
    fid = db.get_storyboard(board.sb_id)["folder_id"]
    assert fid is not None

    await runner.generate(board.sb_id)
    assert db.get_storyboard(board.sb_id)["folder_id"] == fid  # reused


async def test_generate_skips_panels_without_prompt(db, comfy, events, tmp_path, board):
    vlm = StubVlm(responses=[])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    jobs = await runner.generate(board.sb_id)  # no synthesize ran

    assert jobs == [] and comfy.submitted == []


async def test_generate_single_panel_is_priority_and_advances_seed(
    db, comfy, events, tmp_path, board
):
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    db.save_media(_media("/pics/v0.png"))
    db.save_media(_media("/pics/v1.png"))
    db.create_panel_image(board.panel0, file_path="/pics/v0.png", variant_index=0)
    db.create_panel_image(board.panel0, file_path="/pics/v1.png", variant_index=1)

    jobs = await runner.generate(board.sb_id, panel_ids=[board.panel0])

    assert len(jobs) == 1
    _, params, _, priority, _ = comfy.submitted[0]
    assert priority is True
    assert params.seed == 1000 + 0 + 2  # variant base = existing count


async def test_generate_only_failed(db, comfy, events, tmp_path, board, preset_id):
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    j0 = db.create_generation_job(preset_id, "{}", panel_id=board.panel0)
    db.update_generation_job(j0, state="failed")
    j1 = db.create_generation_job(preset_id, "{}", panel_id=board.panel1)
    db.update_generation_job(j1, state="done")

    jobs = await runner.generate(board.sb_id, only_failed=True)

    assert [s[2] for s in comfy.submitted] == [board.panel0]
    assert jobs


async def test_generate_unloads_vlm(db, comfy, events, tmp_path, board):
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    await runner.generate(board.sb_id)

    assert vlm.shutdowns == 1  # unload_vlm_during_generation=True (default)


async def test_generate_negative_without_binding_fails_before_submitting(
    db, comfy, events, tmp_path, board
):
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)
    db.update_storyboard(board.sb_id, negative="blurry")

    with pytest.raises(StoryboardError, match="MS_NEGATIVE"):
        await runner.generate(board.sb_id)

    assert comfy.submitted == []  # validated before any submit


# ---- ingest ------------------------------------------------------------


async def test_ingest_on_job_outputs(db, comfy, events, tmp_path, board, preset_id):
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)
    await runner.generate(board.sb_id)  # establishes the storyboard's folder

    params = GenerationParams(
        positive="a prompt, graphite sketch",
        seed=1234,
        width=1344,
        height=768,
        batch_size=2,
    )
    job_id = db.create_generation_job(
        preset_id, params.to_json(), panel_id=board.panel0
    )
    job_row = db.get_generation_job(job_id)

    f1 = tmp_path / "out1.png"
    f2 = tmp_path / "out2.png"
    f1.write_bytes(b"x")
    f2.write_bytes(b"x")
    db.save_media(_media(str(f1)))
    db.save_media(_media(str(f2)))

    runner.handle_job_event(
        "job_outputs", {"job_id": job_id, "files": [str(f1), str(f2)]}
    )
    await runner.aclose()  # drains the ingest task

    imgs = db.list_panel_images(board.panel0)
    assert [i["variant_index"] for i in imgs] == [0, 1]
    assert imgs[0]["seed"] == json.loads(job_row["params"])["seed"]
    assert imgs[0]["prompt_used"] == json.loads(job_row["params"])["positive"]

    # hidden on ingest:
    assert db.get_all_media_summaries() == []

    # folder membership:
    fid = db.get_storyboard(board.sb_id)["folder_id"]
    assert set(p["file_path"] for p in imgs) <= set(db.get_folder(fid)["items"])
    assert ("storyboard", "panel_images_changed") in [(c, e) for c, e, _ in events]


async def test_ingest_ignores_jobs_without_panel(
    db, comfy, events, tmp_path, board, preset_id
):
    vlm = StubVlm(responses=[])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    bare_job_id = db.create_generation_job(preset_id, "{}", panel_id=None)
    runner.handle_job_event("job_outputs", {"job_id": bare_job_id, "files": ["/x.png"]})
    await runner.aclose()  # no exception, no panel_images rows

    with db.lock, db._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM panel_images").fetchone()["n"]
        assert n == 0


# ---- cancel --------------------------------------------------------------


async def test_cancel_cancels_active_jobs(
    db, comfy, events, tmp_path, board, preset_id
):
    vlm = StubVlm(responses=[])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    j1 = db.create_generation_job(preset_id, "{}", panel_id=board.panel0)  # queued
    j2 = db.create_generation_job(preset_id, "{}", panel_id=board.panel1)
    db.update_generation_job(j2, state="running")
    j3 = db.create_generation_job(preset_id, "{}", panel_id=board.panel0)
    db.update_generation_job(j3, state="done")

    n = await runner.cancel(board.sb_id)

    assert n == 2 and sorted(comfy.cancelled) == sorted([j1, j2])
