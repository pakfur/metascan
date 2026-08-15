"""compose_story staging against a scripted fake VLM (spec V1 §4)."""

import asyncio
import json

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.storyboard_runner import (
    ConfirmRequiredError,
    StoryboardError,
    StoryboardRunner,
)

OUTLINE = {
    "logline": "L",
    "tone": "T",
    "duration_target_s": 60,
    "subjects": [{"name": "Maya", "description": "desc", "voice": None}],
    "arc": [{"beat": "setup", "summary": "s"}],
}
SCENES = [
    {
        "name": "Yard",
        "subtitle": None,
        "setting": "hulls",
        "location": None,
        "time_of_day": "dusk",
        "mood": "tense",
        "lighting": None,
        "notes": None,
    }
]
SHOTS = [
    {
        "shot_size": "WS",
        "angle": "eye",
        "lens": None,
        "action": "Maya crosses",
        "subjects": ["Maya"],
        "duration_s": 10,
    }
]
BEATS = [
    {
        "duration_s": 5,
        "action": "a1",
        "camera_motion": "static",
        "camera_amplitude": None,
        "camera_speed": None,
        "is_cut": False,
        "sound": None,
        "dialog": [],
    },
    {
        "duration_s": 5,
        "action": "a2",
        "camera_motion": None,
        "camera_amplitude": None,
        "camera_speed": None,
        "is_cut": False,
        "sound": None,
        "dialog": [],
    },
]


class FakeVlm:
    model_id = "qwen3vl-30b-a3b"

    def __init__(self):
        self.calls = []

    async def ensure_started(self, model_id):
        pass

    async def generate_text(
        self,
        *,
        system_prompt,
        user_prompt,
        grammar=None,
        temperature=0.6,
        max_tokens=250,
        timeout=120.0
    ):
        # Dispatch on the grammar object — system prompts share words
        # ("shot" appears in the beats prompt), so substring matching on
        # them misroutes.
        from metascan.core import storyboard_story as story

        self.calls.append(grammar)
        if grammar == story.OUTLINE_GRAMMAR:
            return json.dumps(OUTLINE)
        if grammar == story.SCENES_GRAMMAR:
            return json.dumps(SCENES)
        if grammar == story.SHOTS_GRAMMAR:
            return json.dumps(SHOTS)
        return json.dumps(BEATS)


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "t.db")


@pytest.fixture
def runner(db, tmp_path):
    r = StoryboardRunner(db=db, comfy=None, get_vlm=lambda: None, output_root=tmp_path)
    return r


def _board(db, premise="A scavenger finds a ship."):
    sb = db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )
    db.update_storyboard(sb, source_text=premise)
    return sb


def test_compose_requires_vlm(db, runner):
    sb = _board(db)
    with pytest.raises(StoryboardError, match="VLM"):
        asyncio.run(runner.compose_story(sb))


def test_full_cascade_builds_tree_and_emits(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    events = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))
    sb = _board(db)
    counts = asyncio.run(runner.compose_story(sb))
    tree = db.get_storyboard_tree(sb)
    assert json.loads(tree["outline"])["logline"] == "L"
    assert tree["subjects"][0]["name"] == "Maya"
    assert len(tree["scenes"]) == 1
    panel = tree["scenes"][0]["panels"][0]
    assert panel["subject_ids"] == [tree["subjects"][0]["id"]]
    assert panel["duration_s"] == 10.0
    assert [b["action"] for b in panel["beats"]] == ["a1", "a2"]
    assert counts["scenes"] == 1 and counts["beats"] == 2
    names = [e[1] for e in events if e[0] == "storyboard"]
    assert "story_complete" in names
    assert names.count("story_stage_complete") == 4


def test_outline_confirm_gate(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db)
    asyncio.run(runner.compose_story(sb, stages=("outline",)))
    with pytest.raises(ConfirmRequiredError):
        asyncio.run(runner.check_compose_gates(sb, ("outline",), None, False))
    # confirm=True passes the gate and re-runs
    asyncio.run(runner.compose_story(sb, stages=("outline",), confirm=True))


def test_missing_premise_rejected(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db, premise="")
    with pytest.raises(StoryboardError, match="premise"):
        asyncio.run(runner.check_compose_gates(sb, ("outline",), None, False))


def test_beats_only_rerun_replaces_beats(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db)
    asyncio.run(runner.compose_story(sb))
    tree = db.get_storyboard_tree(sb)
    pid = tree["scenes"][0]["panels"][0]["id"]
    db.update_beat(tree["scenes"][0]["panels"][0]["beats"][0]["id"], action="edited")
    asyncio.run(runner.compose_story(sb, stages=("beats",), panel_ids=[pid]))
    fresh = db.get_storyboard_tree(sb)
    assert [b["action"] for b in fresh["scenes"][0]["panels"][0]["beats"]] == [
        "a1",
        "a2",
    ]


def test_story_error_emitted_and_reraised(db, tmp_path):
    class BrokenVlm(FakeVlm):
        async def generate_text(self, **kw):
            return "not json"

    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: BrokenVlm(), output_root=tmp_path
    )
    events = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))
    sb = _board(db)
    with pytest.raises(Exception):
        asyncio.run(runner.compose_story(sb))
    errs = [e for e in events if e[1] == "story_error"]
    assert len(errs) == 1 and errs[0][2]["stage"] == "outline"
