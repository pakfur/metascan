"""compose_story staging against a scripted fake VLM (spec V1 §4)."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.storyboard_runner import (
    ConfirmRequiredError,
    StoryboardError,
    StoryboardRunner,
)

OUTLINE = {
    "logline": "L",
    "tone": "T",
    "pacing": "standard",
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
        "arc_beats": ["setup"],
        "charge_in": 0,
        "charge_out": -2,
        "brief": "Maya arrives.",
    }
]
SHOTS = [
    {
        "action": "Maya crosses",
        "duration_s": 10,
        "subtext": "she hopes no one sees her",
        "is_turn": False,
    }
]
BEATS = [
    {
        "duration_s": 5,
        "action": "a1",
        "reveals": "the yard's scale",
        "emotional_intent": "shoulders squared",
        "shot_size": "WS",
        "angle": "eye",
        "lens": None,
        "composition": "centered",
        "light_quality": "soft",
        "subjects": ["Maya"],
        "camera_motion": "static",
        "camera_amplitude": None,
        "camera_speed": None,
        "movement_motivation": None,
        "is_cut": False,
        "sound": None,
        "dialog": [],
    },
    {
        "duration_s": 5,
        "action": "a2",
        "reveals": "her face",
        "emotional_intent": "jaw set",
        "shot_size": "MCU",
        "angle": None,
        "lens": None,
        "composition": None,
        "light_quality": None,
        "subjects": [],
        "camera_motion": None,
        "camera_amplitude": None,
        "camera_speed": None,
        "movement_motivation": None,
        "is_cut": False,
        "sound": None,
        "dialog": [],
    },
]


class FakeVlm:
    model_id = "qwen3vl-30b-a3b"

    def __init__(self):
        self.calls = []
        self.prompts = []

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
        self.prompts.append(user_prompt)
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


@pytest.fixture
def storyboard_id(db):
    return _board(db)


@pytest.fixture
def panel_id(db, storyboard_id):
    scene = db.create_scene(storyboard_id, name="S1")
    return db.create_panel(scene, action="she opens the door")


@pytest.fixture
def media(db):
    path = "/pics/a.png"
    db.save_media(
        Media(
            file_path=Path(path),
            file_size=1,
            width=8,
            height=8,
            format="png",
            created_at=datetime.now(),
            modified_at=datetime.now(),
        )
    )
    return path


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
    assert panel["duration_s"] == 10.0
    assert [b["action"] for b in panel["beats"]] == ["a1", "a2"]
    assert panel["beats"][0]["subject_ids"] == [tree["subjects"][0]["id"]]
    assert panel["beats"][0]["shot_size"] == "WS"
    assert counts["scenes"] == 1 and counts["beats"] == 2
    names = [e[1] for e in events if e[0] == "storyboard"]
    assert "story_complete" in names
    assert names.count("story_stage_complete") == 4


def test_scenes_stage_writes_brief_and_composed_from(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db)
    asyncio.run(runner.compose_story(sb, stages=("outline", "scenes")))
    scene = db.get_storyboard_tree(sb)["scenes"][0]
    assert scene["brief"] == "Maya arrives."
    cf = scene["composed_from"]
    assert cf["stage"] == "scenes" and cf["template_id"] is None
    from metascan.core.shot_templates import outline_hash

    assert cf["outline_hash"] == outline_hash(db.get_storyboard_tree(sb)["outline"])
    assert "Premise:" in vlm.prompts[1]


def test_outline_confirm_gate(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db)
    asyncio.run(runner.compose_story(sb, stages=("outline",)))
    with pytest.raises(ConfirmRequiredError):
        asyncio.run(runner.check_compose_gates(sb, ("outline",), None, None, False))
    # confirm=True passes the gate and re-runs
    asyncio.run(runner.compose_story(sb, stages=("outline",), confirm=True))


def test_shots_confirm_gate_reports_correct_stage(db, tmp_path):
    """A gate failure on a non-outline stage, hit via compose_story (not a
    direct check_compose_gates call), must attribute story_error to the
    stage that actually failed -- not the "outline" fallback."""
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    events = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))
    sb = _board(db)
    asyncio.run(runner.compose_story(sb))  # full cascade -- scenes have shots now
    with pytest.raises(ConfirmRequiredError):
        asyncio.run(runner.compose_story(sb, stages=("shots",)))
    errs = [e for e in events if e[1] == "story_error"]
    assert len(errs) == 1 and errs[0][2]["stage"] == "shots"


def test_missing_premise_rejected(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db, premise="")
    with pytest.raises(StoryboardError, match="premise"):
        asyncio.run(runner.check_compose_gates(sb, ("outline",), None, None, False))


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


def test_stage_retries_once_on_invalid_json(db, tmp_path):
    """A truncated/invalid response is retried once per unit; a clean
    second sample lets the stage succeed (regression: a single truncated
    shots response used to abort the whole compose run)."""

    class FlakyVlm(FakeVlm):
        def __init__(self):
            super().__init__()
            self.shots_calls = 0

        async def generate_text(self, *, grammar=None, **kw):
            from metascan.core import storyboard_story as story

            if grammar == story.SHOTS_GRAMMAR:
                self.shots_calls += 1
                if self.shots_calls == 1:
                    # Truncated mid-string, as llama-server produces when
                    # generation hits max_tokens.
                    return '[{"shot_size": "WS", "angle": "eye", "lens": null, "action": "she wal'
            return await super().generate_text(grammar=grammar, **kw)

    vlm = FlakyVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db)
    counts = asyncio.run(runner.compose_story(sb))
    assert counts["shots"] == 1  # the stage recovered
    assert vlm.shots_calls == 2  # exactly one retry


def test_stage_fails_after_second_invalid_json(db, tmp_path):
    class AlwaysBadVlm(FakeVlm):
        async def generate_text(self, *, grammar=None, **kw):
            from metascan.core import storyboard_story as story

            if grammar == story.SHOTS_GRAMMAR:
                return "[{"
            return await super().generate_text(grammar=grammar, **kw)

    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: AlwaysBadVlm(), output_root=tmp_path
    )
    events = []
    runner.on_event(lambda ch, ev, d: events.append((ev, d)))
    sb = _board(db)
    with pytest.raises(Exception):
        asyncio.run(runner.compose_story(sb))
    errs = [d for ev, d in events if ev == "story_error"]
    assert len(errs) == 1 and errs[0]["stage"] == "shots"


def test_beats_stage_gated_when_beats_have_identity(
    runner, storyboard_id, panel_id, db, media
):
    beat_id = db.create_beat(panel_id, action="b")
    db.create_beat_image(beat_id, file_path=media)
    with pytest.raises(ConfirmRequiredError) as exc:
        asyncio.run(
            runner.check_compose_gates(storyboard_id, ("beats",), None, None, False)
        )
    assert getattr(exc.value, "_compose_stage", None) == "beats"


def test_beats_stage_gated_on_locked_prompt(runner, storyboard_id, panel_id, db):
    beat_id = db.create_beat(panel_id, action="b")
    db.update_beat(
        beat_id, prompt="hand-written", prompt_locked=1, prompt_source="user"
    )
    with pytest.raises(ConfirmRequiredError):
        asyncio.run(
            runner.check_compose_gates(storyboard_id, ("beats",), None, None, False)
        )


def test_beats_stage_open_for_plain_beats(runner, storyboard_id, panel_id, db):
    db.create_beat(panel_id, action="cheap to reroll")
    asyncio.run(
        runner.check_compose_gates(storyboard_id, ("beats",), None, None, False)
    )


def _events(runner):
    seen = []
    runner.on_event(lambda ch, ev, data: seen.append((ch, ev, data)))
    return seen


def test_compose_writes_pacing(db, storyboard_id):
    vlm = FakeVlm()
    r = StoryboardRunner(db=db, comfy=None, get_vlm=lambda: vlm, output_root=Path("."))
    asyncio.run(r.compose_story(storyboard_id, stages=["outline"]))
    assert db.get_storyboard(storyboard_id)["pacing"] == "standard"


def test_scenes_lint_reroll_names_violation(db, storyboard_id):
    broken = [dict(SCENES[0], charge_in=None, charge_out=None)]

    class Vlm(FakeVlm):
        def __init__(self):
            super().__init__()
            self.scene_calls = 0

        async def generate_text(self, *, grammar=None, user_prompt="", **kw):
            from metascan.core import storyboard_story as story

            self.prompts.append(user_prompt)
            if grammar == story.OUTLINE_GRAMMAR:
                return json.dumps(OUTLINE)
            if grammar == story.SCENES_GRAMMAR:
                self.scene_calls += 1
                return json.dumps(broken if self.scene_calls == 1 else SCENES)
            raise AssertionError("unexpected stage")

    vlm = Vlm()
    r = StoryboardRunner(db=db, comfy=None, get_vlm=lambda: vlm, output_root=Path("."))
    seen = _events(r)
    asyncio.run(r.compose_story(storyboard_id, stages=["outline", "scenes"]))
    assert vlm.scene_calls == 2
    assert "missing charge" in vlm.prompts[-1]  # violation named in re-roll
    done = [
        d
        for _, ev, d in seen
        if ev == "story_stage_complete" and d["stage"] == "scenes"
    ]
    assert done and done[0]["warnings"] == []  # second response was clean


def test_lint_leftovers_accepted_as_warnings(db, storyboard_id):
    broken = [dict(SCENES[0], charge_in=None, charge_out=None)]

    class Vlm(FakeVlm):
        async def generate_text(self, *, grammar=None, user_prompt="", **kw):
            from metascan.core import storyboard_story as story

            if grammar == story.OUTLINE_GRAMMAR:
                return json.dumps(OUTLINE)
            return json.dumps(broken)  # violates every time

    r = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: Vlm(), output_root=Path(".")
    )
    seen = _events(r)
    asyncio.run(r.compose_story(storyboard_id, stages=["outline", "scenes"]))
    done = [
        d
        for _, ev, d in seen
        if ev == "story_stage_complete" and d["stage"] == "scenes"
    ]
    assert done and any("missing charge" in w for w in done[0]["warnings"])
    # compose still landed the scenes despite the style violation
    assert db.get_storyboard_tree(storyboard_id)["scenes"]


def test_stray_is_turn_zeroed_outside_turn_scene(db, storyboard_id):
    stray = [dict(SHOTS[0], is_turn=True)]

    class Vlm(FakeVlm):
        async def generate_text(self, *, grammar=None, user_prompt="", **kw):
            from metascan.core import storyboard_story as story

            self.prompts.append(user_prompt)
            if grammar == story.OUTLINE_GRAMMAR:
                return json.dumps(OUTLINE)
            if grammar == story.SCENES_GRAMMAR:
                return json.dumps(SCENES)  # arc_beats == ["setup"], no turn
            return json.dumps(stray)

    r = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: Vlm(), output_root=Path(".")
    )
    asyncio.run(r.compose_story(storyboard_id, stages=["outline", "scenes", "shots"]))
    tree = db.get_storyboard_tree(storyboard_id)
    assert all(p["is_turn"] == 0 for p in tree["scenes"][0]["panels"])


def test_beats_sequential_with_prev_context(db, storyboard_id):
    two_shots = [
        dict(SHOTS[0]),
        dict(SHOTS[0], action="Maya stops", subtext="she heard something"),
    ]

    class Vlm(FakeVlm):
        async def generate_text(self, *, grammar=None, user_prompt="", **kw):
            from metascan.core import storyboard_story as story

            if grammar == story.OUTLINE_GRAMMAR:
                return json.dumps(OUTLINE)
            if grammar == story.SCENES_GRAMMAR:
                return json.dumps(SCENES)
            if grammar == story.SHOTS_GRAMMAR:
                return json.dumps(two_shots)
            self.prompts.append(user_prompt)
            return json.dumps(BEATS)

    vlm = Vlm()
    r = StoryboardRunner(db=db, comfy=None, get_vlm=lambda: vlm, output_root=Path("."))
    asyncio.run(r.compose_story(storyboard_id))
    assert len(vlm.prompts) == 2  # one beats call per panel
    assert "opening shot" in vlm.prompts[0]
    assert "Previous shot in this scene: Maya crosses" in vlm.prompts[1]
    # last BEATS beat is MCU — its framing summary reaches the second call
    assert "MCU" in vlm.prompts[1]


def test_outline_does_not_recreate_existing_non_character_subject(db, tmp_path):
    """The outline VLM echoes every 'Existing subjects' name back
    (including locations/props). Dedupe must run against the whole
    roster, not just castable characters -- otherwise a location named
    in the outline is re-created as a duplicate character on every
    outline rebuild."""
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db)
    existing = db.create_subject(
        sb, name="maya", description="x", subject_type="location"
    )
    asyncio.run(runner.compose_story(sb, stages=("outline",)))
    subjects = db.get_storyboard_tree(sb)["subjects"]
    assert [s["id"] for s in subjects] == [existing]
    assert subjects[0]["subject_type"] == "location"
    # and a rebuild still adds nothing
    asyncio.run(runner.compose_story(sb, stages=("outline",), confirm=True))
    assert len(db.get_storyboard_tree(sb)["subjects"]) == 1


def test_outline_new_subject_gets_inferred_type(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    sb = _board(db)
    db.create_subject(sb, name="Rio", description="y", subject_type="location")
    asyncio.run(runner.compose_story(sb, stages=("outline",)))
    subjects = db.get_storyboard_tree(sb)["subjects"]
    assert [s["name"] for s in subjects] == ["Rio", "Maya"]
    assert subjects[1]["subject_type"] == "character"
    assert subjects[1]["sort_order"] == 1
