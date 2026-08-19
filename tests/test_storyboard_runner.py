"""Tests for StoryboardRunner: parse/synthesize/generate/ingest orchestration.

Uses a real temp DatabaseManager (behavior under FK cascades and the
storyboard schema matters) plus stub comfy/vlm objects -- ComfyClient and
VlmClient each have their own test suites; this module only needs to prove
StoryboardRunner wires the three together correctly.
"""

from __future__ import annotations

import asyncio
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


def t2i_workflow_full() -> dict:
    """Like ``t2i_workflow()`` but also carries MS_NEGATIVE and MS_LORA, for
    the negative/LoRA happy-path test (params.negative/.lora_name/
    .lora_strength are otherwise never exercised against a preset that can
    actually accept them)."""
    wf = t2i_workflow()
    wf["7"] = _node("CLIPTextEncode", "MS_NEGATIVE", {"text": ""})
    wf["8"] = _node(
        "LoraLoader",
        "MS_LORA",
        {"lora_name": "", "strength_model": 1.0, "strength_clip": 1.0},
    )
    return wf


def t2i_workflow_stack() -> dict:
    """``t2i_workflow()`` plus an MS_LORA_STACK Power-Lora-Loader-style
    node, for the per-shot image_loras tests."""
    wf = t2i_workflow()
    wf["10"] = _node(
        "Power Lora Loader (rgthree)",
        "MS_LORA_STACK",
        {"model": ["4", 0], "clip": ["4", 1]},
    )
    return wf


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
    """Mirrors the parts of ComfyClient the runner touches.

    ``submit`` also writes a real ``generation_jobs`` row (state defaults
    to 'queued'), matching ``ComfyClient.submit`` -- the runner's seed
    dedup logic (finding 5) reads pending jobs back via
    ``db.list_generation_jobs``, so the stub has to behave like the real
    thing here even though it never simulates ComfyUI actually running the
    job (no ``job_outputs`` event fires unless a test calls
    ``handle_job_event`` itself).
    """

    def __init__(self, db=None):
        self.db = db
        self.submitted = []  # dicts: preset_id, params, panel_id, beat_id,
        # priority, output_dir
        self.cancelled = []
        self.uploaded = []
        self._next_job_id = 100

    async def submit(
        self,
        preset_id,
        params,
        panel_id=None,
        priority=False,
        output_dir=None,
        beat_id=None,
    ):
        self.submitted.append(
            {
                "preset_id": preset_id,
                "params": params,
                "panel_id": panel_id,
                "beat_id": beat_id,
                "priority": priority,
                "output_dir": output_dir,
            }
        )
        if self.db is not None:
            return int(
                self.db.create_generation_job(
                    preset_id,
                    params.to_json(),
                    panel_id,
                    str(output_dir) if output_dir else None,
                    beat_id,
                )
            )
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
def full_preset_id(db):
    """A t2i preset whose workflow binds MS_NEGATIVE and MS_LORA, for the
    negative/LoRA happy-path test."""
    workflow = t2i_workflow_full()
    bindings = resolve_bindings(workflow, "t2i")
    return db.create_workflow_preset(
        "sdxl-full", "t2i", json.dumps(workflow), bindings.to_json()
    )


@pytest.fixture
def events():
    return []


@pytest.fixture
def comfy(db):
    return StubComfy(db)


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
    b0 = db.create_beat(
        board.panel0, action="hand rests", subject_ids=[board.subject_id]
    )
    db.create_beat(board.panel1, action="wide shot", subject_ids=[])
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    out = await runner.synthesize(board.sb_id)

    assert out == {"synthesized": 2, "fallback": 0, "skipped_locked": 0}
    beat = db.get_beat(b0)
    assert beat["prompt"] == "a prompt, graphite sketch"
    assert beat["prompt_source"] == "llm"
    assert beat["brief"].startswith("SHOT:")
    assert "SUBJECT" in vlm.calls[0]["user"]


async def test_synthesize_fallback_on_vlm_error(db, comfy, events, tmp_path, board):
    b0 = db.create_beat(
        board.panel0, action="hand rests", subject_ids=[board.subject_id]
    )
    db.create_beat(board.panel1, action="wide shot", subject_ids=[])
    vlm = StubVlm(fail=True)
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    out = await runner.synthesize(board.sb_id)

    assert out["fallback"] == 2
    beat = db.get_beat(b0)
    assert beat["prompt_source"] == "brief"
    assert beat["prompt"].startswith("SHOT:") and beat["prompt"].endswith(
        "graphite sketch"
    )


async def test_synthesize_no_vlm_falls_back(db, comfy, events, tmp_path, board):
    b0 = db.create_beat(
        board.panel0, action="hand rests", subject_ids=[board.subject_id]
    )
    db.create_beat(board.panel1, action="wide shot", subject_ids=[])
    runner = make_runner(db, comfy, None, events, tmp_path)

    out = await runner.synthesize(board.sb_id)

    assert out == {"synthesized": 0, "fallback": 2, "skipped_locked": 0}
    beat = db.get_beat(b0)
    assert beat["prompt_source"] == "brief"


async def test_synthesize_skips_locked(db, comfy, events, tmp_path, board):
    b0 = db.create_beat(board.panel0, action="hand rests", subject_ids=[])
    db.update_beat(b0, prompt="hand tuned", prompt_locked=1, prompt_source="user")
    db.create_beat(board.panel1, action="wide shot", subject_ids=[])
    vlm = StubVlm(responses=["b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    out = await runner.synthesize(board.sb_id)

    assert out["skipped_locked"] == 1
    assert db.get_beat(b0)["prompt"] == "hand tuned"


async def test_synthesize_targets_beats(db, comfy, events, tmp_path, board):
    """Locks and synthesis both act on beats, not panels -- one panel can
    carry a mix of locked and unlocked beats."""
    b1 = db.create_beat(board.panel0, action="first", subject_ids=[])
    b2 = db.create_beat(board.panel0, action="second", subject_ids=[])
    db.update_beat(b2, prompt="locked", prompt_locked=1, prompt_source="user")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    counts = await runner.synthesize(board.sb_id)

    assert counts["skipped_locked"] == 1
    assert db.get_beat(b1)["prompt"]  # written
    assert db.get_beat(b2)["prompt"] == "locked"  # untouched


async def test_synthesize_single_beat_force_overrides_lock(
    db, comfy, events, tmp_path, board
):
    b0 = db.create_beat(board.panel0, action="hand rests", subject_ids=[])
    db.update_beat(b0, prompt="hand tuned", prompt_locked=1, prompt_source="user")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    out = await runner.synthesize(board.sb_id, beat_ids=[b0], force=True)

    assert out["synthesized"] == 1
    beat = db.get_beat(b0)
    assert beat["prompt_locked"] == 0


async def test_synthesize_emits_progress(db, comfy, events, tmp_path, board):
    b0 = db.create_beat(board.panel0, action="hand rests", subject_ids=[])
    b1 = db.create_beat(board.panel1, action="wide shot", subject_ids=[])
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    await runner.synthesize(board.sb_id)

    progress = [
        data
        for ch, ev, data in events
        if ch == "storyboard" and ev == "synthesis_progress"
    ]
    assert progress
    assert {d["beat_id"] for d in progress} == {b0, b1}
    assert {d["panel_id"] for d in progress} == {board.panel0, board.panel1}


async def test_synthesize_emits_complete_on_success(db, comfy, events, tmp_path, board):
    """POST .../synthesize is 202 fire-and-forget; synthesis_complete is
    the only signal a client gets that the background run finished."""
    db.create_beat(board.panel0, action="hand rests", subject_ids=[])
    db.create_beat(board.panel1, action="wide shot", subject_ids=[])
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    out = await runner.synthesize(board.sb_id)

    complete = [
        data
        for ch, ev, data in events
        if ch == "storyboard" and ev == "synthesis_complete"
    ]
    assert len(complete) == 1
    assert complete[0] == {"storyboard_id": board.sb_id, **out}
    assert not any(ev == "synthesis_error" for _, ev, _ in events)


async def test_synthesize_emits_error_and_reraises_on_failure(
    db, comfy, events, tmp_path, board
):
    """If the background run raises (e.g. the VLM won't load), the runner
    must emit synthesis_error rather than fail silently, and still
    re-raise for any direct (non-route) caller."""
    db.create_beat(board.panel0, action="hand rests", subject_ids=[])

    class ExplodingVlm(StubVlm):
        async def ensure_started(self, model_id):
            raise RuntimeError("model failed to load")

    vlm = ExplodingVlm()
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    with pytest.raises(RuntimeError, match="model failed to load"):
        await runner.synthesize(board.sb_id)

    errors = [
        data
        for ch, ev, data in events
        if ch == "storyboard" and ev == "synthesis_error"
    ]
    assert len(errors) == 1
    assert errors[0]["storyboard_id"] == board.sb_id
    assert "model failed to load" in errors[0]["error"]
    assert not any(ev == "synthesis_complete" for _, ev, _ in events)


# ---- generate --------------------------------------------------------


async def test_generate_submits_with_derived_params(db, comfy, events, tmp_path, board):
    db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)  # gives the beat a prompt

    jobs = await runner.generate(board.sb_id)

    assert len(jobs) == 1
    submitted = comfy.submitted[0]
    params = submitted["params"]
    assert params.width == 1344 and params.height == 768  # 16:9 on sd
    assert params.batch_size == 2
    assert params.seed == 1000 + (0 * 100 + 0) * 1000 + 0  # beat_seed
    assert submitted["priority"] is False
    output_dir = str(submitted["output_dir"])
    assert "scene_00" in output_dir
    assert "panel_00" in output_dir
    assert "beat_00" in output_dir


async def test_generate_submits_per_beat_with_beat_seed(
    db, comfy, events, tmp_path, board
):
    """generate() submits one job per beat (not per panel), with a
    beat_seed keyed on both panel and beat sort_order, and an output_dir
    that carries a per-beat suffix."""
    beat_a = db.create_beat(board.panel1, action="first", sort_order=0)
    beat_b = db.create_beat(board.panel1, action="second", sort_order=1)
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    jobs = await runner.generate(board.sb_id)

    assert len(jobs) == 2
    submitted = comfy.submitted
    assert [s["beat_id"] for s in submitted] == [beat_a, beat_b]
    assert submitted[0]["params"].seed == 1000 + (1 * 100 + 0) * 1000
    assert submitted[1]["params"].seed == 1000 + (1 * 100 + 1) * 1000
    assert str(submitted[1]["output_dir"]).endswith(
        str(Path("scene_00") / "panel_01" / "beat_01")
    )


async def test_generate_creates_folder_once(db, comfy, events, tmp_path, board):
    db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    await runner.generate(board.sb_id)
    fid = db.get_storyboard(board.sb_id)["folder_id"]
    assert fid is not None

    await runner.generate(board.sb_id)
    assert db.get_storyboard(board.sb_id)["folder_id"] == fid  # reused

    # folder_created is broadcast exactly once, on the call that actually
    # created the folder -- not on the second, reuse-only call.
    created_events = [
        (ch, ev, data)
        for ch, ev, data in events
        if ch == "folders" and ev == "folder_created"
    ]
    assert len(created_events) == 1
    assert created_events[0][2]["folder"]["id"] == fid


async def test_generate_concurrent_calls_create_folder_once(
    db, comfy, events, tmp_path, board
):
    """Two concurrent generate() calls for the same storyboard must not
    race the folder-ensure step: exactly one folder gets created, no
    orphan folder is left behind, and folder_created fires exactly once."""
    db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    await asyncio.gather(
        runner.generate(board.sb_id),
        runner.generate(board.sb_id),
    )

    fid = db.get_storyboard(board.sb_id)["folder_id"]
    assert fid is not None

    all_folders = db.list_folders()
    assert len(all_folders) == 1  # no orphan folder from the losing race
    assert all_folders[0]["id"] == fid

    created_events = [
        (ch, ev, data)
        for ch, ev, data in events
        if ch == "folders" and ev == "folder_created"
    ]
    assert len(created_events) == 1
    assert created_events[0][2]["folder"]["id"] == fid


async def test_generate_skips_beats_without_prompt(db, comfy, events, tmp_path, board):
    db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=[])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    jobs = await runner.generate(board.sb_id)  # no synthesize ran

    assert jobs == [] and comfy.submitted == []


async def test_generate_single_beat_is_priority_and_advances_seed(
    db, comfy, events, tmp_path, board
):
    beat_id = db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    db.save_media(_media("/pics/v0.png"))
    db.save_media(_media("/pics/v1.png"))
    db.create_beat_image(beat_id, file_path="/pics/v0.png", variant_index=0)
    db.create_beat_image(beat_id, file_path="/pics/v1.png", variant_index=1)

    jobs = await runner.generate(board.sb_id, beat_ids=[beat_id])

    assert len(jobs) == 1
    submitted = comfy.submitted[0]
    assert submitted["priority"] is True
    assert submitted["params"].seed == 1000 + 0 + 2  # variant base = existing count


async def test_generate_reroll_before_ingest_advances_seed(
    db, comfy, events, tmp_path, board
):
    """Two generate() calls for the same beat *before the first has
    ingested* (no beat_images pre-seeded, and StubComfy never fires
    job_outputs) must not submit the same seed twice -- count_beat_images
    alone can't see the first call's still-queued job."""
    beat_id = db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    await runner.generate(board.sb_id, beat_ids=[beat_id])
    await runner.generate(board.sb_id, beat_ids=[beat_id])

    seeds = [s["params"].seed for s in comfy.submitted if s["beat_id"] == beat_id]
    assert len(seeds) == 2
    assert seeds[0] != seeds[1]
    # board fixture's batch_size=2 (see the `board` fixture docstring).
    assert seeds[1] - seeds[0] == 2


async def test_generate_only_failed(db, comfy, events, tmp_path, board, preset_id):
    beat0 = db.create_beat(board.panel0, action="hand rests")
    beat1 = db.create_beat(board.panel1, action="wide shot")
    vlm = StubVlm(responses=["a prompt", "b prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    j0 = db.create_generation_job(preset_id, "{}", panel_id=board.panel0, beat_id=beat0)
    db.update_generation_job(j0, state="failed")
    j1 = db.create_generation_job(preset_id, "{}", panel_id=board.panel1, beat_id=beat1)
    db.update_generation_job(j1, state="done")

    jobs = await runner.generate(board.sb_id, only_failed=True)

    assert [s["beat_id"] for s in comfy.submitted] == [beat0]
    assert jobs


async def test_generate_unloads_vlm(db, comfy, events, tmp_path, board):
    db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)

    await runner.generate(board.sb_id)

    assert vlm.shutdowns == 1  # unload_vlm_during_generation=True (default)


async def test_generate_waits_for_synth_lock_before_unloading_vlm(
    db, comfy, events, tmp_path, board
):
    """generate()'s VLM-unload step must not run while a synthesize() (or
    anything else holding _synth_lock) is in progress -- it would tear the
    VLM out from under it. Race this deterministically by holding the lock
    in the test itself: while held, generate() must not be able to finish
    (asyncio.Lock guarantees vlm.shutdown() can't run), and once released
    it must proceed and unload."""
    db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)
    await runner.generate(board.sb_id)  # pre-create the folder so the
    # second generate() call below doesn't also need the (separate)
    # folder lock -- keeps the only await-then-block point the synth lock.
    vlm.shutdowns = 0  # reset the count from the priming generate() above

    await runner._synth_lock.acquire()
    try:
        task = asyncio.create_task(runner.generate(board.sb_id))
        with pytest.raises(asyncio.TimeoutError):
            # If generate() acquires the lock correctly, it cannot finish
            # (and therefore cannot call vlm.shutdown()) until we release
            # it below, so this must time out. A generate() that forgot
            # to take the lock would finish almost immediately instead,
            # and this would NOT raise -- failing the test.
            await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
        assert vlm.shutdowns == 0, "unloaded the VLM while the lock was held"
    finally:
        runner._synth_lock.release()

    jobs = await task
    assert jobs
    assert vlm.shutdowns == 1


async def test_generate_negative_without_binding_fails_before_submitting(
    db, comfy, events, tmp_path, board
):
    db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)
    db.update_storyboard(board.sb_id, negative="blurry")

    with pytest.raises(StoryboardError, match="MS_NEGATIVE"):
        await runner.generate(board.sb_id)

    assert comfy.submitted == []  # validated before any submit


async def test_generate_carries_negative_and_lora_through(
    db, comfy, events, tmp_path, full_preset_id
):
    """Happy path for negative/LoRA -- exercised against a preset whose
    workflow actually binds MS_NEGATIVE/MS_LORA (t2i_workflow(), used by
    every other generate() test, deliberately omits both so the
    missing-binding validation tests mean something). Negative is
    storyboard-level only (panels/beats carry none); LoRA comes from the
    beat's primary subject."""
    sb_id = db.create_storyboard(
        name="Full Params",
        target_model="sd",
        architecture="t2i",
        aspect_ratio="16:9",
        style_block="graphite sketch",
        negative="storyboard-level blur",
        preset_id=full_preset_id,
        base_seed=2000,
        batch_size=1,
    )
    subject_id = db.create_subject(
        sb_id,
        name="MAYA",
        description="late 20s, shaved head",
        lora_name="maya_lora",
        lora_strength=0.65,
    )
    scene_id = db.create_scene(sb_id, name="Yard", sort_order=0)
    panel0 = db.create_panel(scene_id, action="close on her hands", sort_order=0)
    panel1 = db.create_panel(scene_id, action="wide shot", sort_order=1)
    beat0 = db.create_beat(
        panel0, action="close on her hands", subject_ids=[subject_id]
    )
    beat1 = db.create_beat(panel1, action="wide shot", subject_ids=[subject_id])

    vlm = StubVlm(responses=["prompt one", "prompt two"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(sb_id)

    await runner.generate(sb_id)

    by_beat = {s["beat_id"]: s["params"] for s in comfy.submitted}
    p0 = by_beat[beat0]
    p1 = by_beat[beat1]

    assert p0.negative == "storyboard-level blur"
    assert p0.lora_name == "maya_lora"
    assert p0.lora_strength == 0.65

    assert p1.negative == "storyboard-level blur"
    assert p1.lora_name == "maya_lora"
    assert p1.lora_strength == 0.65


def _stack_board(db, preset):
    """Storyboard with one panel/beat, prompt pre-set, using ``preset``."""
    sb_id = db.create_storyboard(
        name="Stack",
        target_model="sd",
        architecture="t2i",
        preset_id=preset,
        base_seed=100,
        batch_size=1,
    )
    scene_id = db.create_scene(sb_id, name="Yard", sort_order=0)
    panel0 = db.create_panel(scene_id, action="close on hands", sort_order=0)
    beat0 = db.create_beat(panel0, action="close on hands")
    db.update_beat(beat0, prompt="a prompt", prompt_source="user", prompt_locked=1)
    return sb_id, panel0, beat0


async def test_generate_carries_image_loras_through(db, comfy, events, tmp_path):
    workflow = t2i_workflow_stack()
    preset = db.create_workflow_preset(
        "sdxl-stack",
        "t2i",
        json.dumps(workflow),
        resolve_bindings(workflow, "t2i").to_json(),
    )
    sb_id, panel0, beat0 = _stack_board(db, preset)
    image_loras = [
        {"name": "style.safetensors", "strength": 0.7},
        {"name": "detail.safetensors", "strength": 1.0},
    ]
    db.update_panel(panel0, image_loras=image_loras)
    # video_loras must NOT leak into still-image jobs
    db.update_panel(
        panel0, video_loras=[{"name": "motion.safetensors", "strength": 1.0}]
    )

    runner = make_runner(db, comfy, StubVlm(), events, tmp_path)
    await runner.generate(sb_id)

    assert comfy.submitted[0]["params"].loras == image_loras


async def test_generate_image_loras_without_stack_fails_before_submitting(
    db, comfy, events, tmp_path
):
    workflow = t2i_workflow()
    preset = db.create_workflow_preset(
        "sdxl",
        "t2i",
        json.dumps(workflow),
        resolve_bindings(workflow, "t2i").to_json(),
    )
    sb_id, panel0, _ = _stack_board(db, preset)
    db.update_panel(
        panel0, image_loras=[{"name": "style.safetensors", "strength": 1.0}]
    )

    runner = make_runner(db, comfy, StubVlm(), events, tmp_path)
    with pytest.raises(StoryboardError, match="MS_LORA_STACK"):
        await runner.generate(sb_id)

    assert comfy.submitted == []


async def test_generate_resolves_bindings_from_workflow_not_stored_snapshot(
    db, comfy, events, tmp_path
):
    """A preset registered before MS_LORA_STACK existed has a stored
    bindings snapshot without lora_stack even though its workflow carries
    the node. generate() must resolve from workflow_json (as
    generate_video already does) so such presets work without
    re-registration."""
    workflow = t2i_workflow_stack()
    stale = t2i_workflow()  # bindings snapshot resolved without the stack node
    preset = db.create_workflow_preset(
        "sdxl-stale",
        "t2i",
        json.dumps(workflow),
        resolve_bindings(stale, "t2i").to_json(),
    )
    sb_id, panel0, _ = _stack_board(db, preset)
    db.update_panel(
        panel0, image_loras=[{"name": "style.safetensors", "strength": 1.0}]
    )

    runner = make_runner(db, comfy, StubVlm(), events, tmp_path)
    await runner.generate(sb_id)

    assert comfy.submitted[0]["params"].loras == [
        {"name": "style.safetensors", "strength": 1.0}
    ]


# ---- ingest ------------------------------------------------------------


async def test_ingest_writes_beat_images_and_emits(
    db, comfy, events, tmp_path, board, preset_id
):
    beat_id = db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
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
        preset_id, params.to_json(), panel_id=board.panel0, beat_id=beat_id
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

    assert db.count_beat_images(beat_id) == 2
    imgs = db.list_beat_images(beat_id)
    assert [i["variant_index"] for i in imgs] == [0, 1]
    assert imgs[0]["seed"] == json.loads(job_row["params"])["seed"]
    assert imgs[0]["prompt_used"] == json.loads(job_row["params"])["positive"]

    # hidden on ingest:
    assert db.get_all_media_summaries() == []

    # folder membership:
    fid = db.get_storyboard(board.sb_id)["folder_id"]
    assert set(p["file_path"] for p in imgs) <= set(db.get_folder(fid)["items"])
    evt = [
        data
        for ch, ev, data in events
        if ch == "storyboard" and ev == "beat_images_changed"
    ][-1]
    assert evt["beat_id"] == beat_id
    assert evt["panel_id"] == board.panel0


async def test_ingest_event_payload_uses_normalized_paths(
    db, comfy, events, tmp_path, board, preset_id, monkeypatch
):
    """beat_images_changed's ``files`` must reflect the SAME normalized
    (to_posix_path-stored, then to_native_path-converted) value that was
    actually inserted into beat_images -- not the raw pre-conversion
    string from the comfy job_outputs payload. Patch to_native_path with a
    distinguishable transform (mirroring the DB-layer test) so the
    assertion can't pass merely because POSIX round-trips as a no-op on a
    Linux test host."""

    def fake_to_native(p):
        return f"NATIVE::{p}"

    monkeypatch.setattr(
        "metascan.core.storyboard_runner.to_native_path", fake_to_native
    )

    beat_id = db.create_beat(board.panel0, action="hand rests")
    vlm = StubVlm(responses=["a prompt"])
    runner = make_runner(db, comfy, vlm, events, tmp_path)
    await runner.synthesize(board.sb_id)
    await runner.generate(board.sb_id)

    params = GenerationParams(
        positive="a prompt, graphite sketch",
        seed=1,
        width=1344,
        height=768,
        batch_size=1,
    )
    job_id = db.create_generation_job(
        preset_id, params.to_json(), panel_id=board.panel0, beat_id=beat_id
    )

    f1 = tmp_path / "raw_from_comfy.png"
    f1.write_bytes(b"x")
    db.save_media(_media(str(f1)))

    runner.handle_job_event("job_outputs", {"job_id": job_id, "files": [str(f1)]})
    await runner.aclose()

    changed = [
        data
        for ch, ev, data in events
        if ch == "storyboard" and ev == "beat_images_changed" and data["files"]
    ]
    assert changed
    assert changed[-1]["files"] == [f"NATIVE::{str(f1)}"]


async def test_ingest_video_job_lands_on_panels_first_beat(
    db, comfy, events, tmp_path, board, preset_id
):
    """A job carrying panel_id but no beat_id (the video-generation path --
    Task 8) ingests its output(s) against the panel's first beat by
    sort_order, not the panel itself."""
    beat0 = db.create_beat(board.panel0, action="first", sort_order=0)
    db.create_beat(board.panel0, action="second", sort_order=1)
    vlm = StubVlm(responses=[])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    job_id = db.create_generation_job(preset_id, "{}", panel_id=board.panel0)

    f1 = tmp_path / "clip.mp4"
    f1.write_bytes(b"x")
    db.save_media(_media(str(f1)))

    runner.handle_job_event("job_outputs", {"job_id": job_id, "files": [str(f1)]})
    await runner.aclose()

    assert db.count_beat_images(beat0) == 1
    evt = [
        data
        for ch, ev, data in events
        if ch == "storyboard" and ev == "beat_images_changed"
    ][-1]
    assert evt["beat_id"] == beat0
    assert evt["panel_id"] == board.panel0


async def test_ingest_video_job_warns_when_panel_has_no_beats(
    db, comfy, events, tmp_path, board, preset_id, caplog
):
    """A panel-only job against a beat-less panel can't ingest anywhere --
    logs a warning and leaves no beat_images / beat_images_changed behind,
    rather than raising."""
    vlm = StubVlm(responses=[])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    job_id = db.create_generation_job(preset_id, "{}", panel_id=board.panel0)

    f1 = tmp_path / "clip.mp4"
    f1.write_bytes(b"x")
    db.save_media(_media(str(f1)))

    with caplog.at_level("WARNING"):
        runner.handle_job_event("job_outputs", {"job_id": job_id, "files": [str(f1)]})
        await runner.aclose()

    assert "no beats to ingest into" in caplog.text
    assert not any(ev == "beat_images_changed" for _, ev, _ in events)
    with db.lock, db._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM beat_images").fetchone()["n"]
        assert n == 0


async def test_ingest_ignores_jobs_without_panel_or_beat(
    db, comfy, events, tmp_path, board, preset_id
):
    vlm = StubVlm(responses=[])
    runner = make_runner(db, comfy, vlm, events, tmp_path)

    bare_job_id = db.create_generation_job(preset_id, "{}", panel_id=None)
    runner.handle_job_event("job_outputs", {"job_id": bare_job_id, "files": ["/x.png"]})
    await runner.aclose()  # no exception, no beat_images rows

    with db.lock, db._get_connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM beat_images").fetchone()["n"]
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
