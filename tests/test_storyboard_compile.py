"""StoryboardRunner.compile_video against a scripted fake VLM.

Mirrors tests/test_storyboard_compose.py's structure: a real (temp) DB,
a FakeVlm dispatching on the grammar object, and a board built through
real DB calls (no mocking of the DB layer itself).

FakeVlm dispatches on ``grammar is None`` (body stage) vs
``grammar == h3.SOUND_GRAMMAR`` (sound stage) -- system prompts alone
aren't reliable discriminators here any more than in the compose tests.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

import metascan.core.h3_compiler as h3
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.storyboard_runner import StoryboardError, StoryboardRunner
from metascan.core.vlm_client import VlmError

_VALID_SOUND = json.dumps(
    {
        "overall_soundscape": (
            "Soft indoor room tone continues throughout, with a kettle "
            "whistling faintly in the background."
        ),
        "non_diegetic_music": "N/A",
    }
)


# -- Board construction (real DB calls) -------------------------------------


def _make_storyboard(
    db: DatabaseManager, video_target: Optional[str] = "minimax"
) -> int:
    sb = db.create_storyboard(
        name="B", target_model="sd", architecture="t2i", base_seed=1
    )
    if video_target is not None:
        db.update_storyboard(sb, video_target=video_target)
    return sb


def _make_panel(
    db: DatabaseManager,
    storyboard_id: int,
    *,
    action: str = "Grandma and Rex share a quiet morning",
    duration_s: float = 8.0,
    with_beats: bool = True,
) -> Tuple[int, int, int]:
    subject_id = db.create_subject(
        storyboard_id,
        name="Grandma Rose",
        description=("a kind elderly woman with silver hair, wearing a floral apron"),
        voice="warm, elderly voice",
        sort_order=0,
    )
    scene_id = db.create_scene(
        storyboard_id,
        name="Kitchen",
        setting="a sunlit farmhouse kitchen with a wooden table",
        location="Farmhouse",
    )
    panel_id = db.create_panel(
        scene_id, action=action, subject_ids=[subject_id], duration_s=duration_s
    )
    if with_beats:
        beats = [
            {
                "duration_s": 4.0,
                "action": "Grandma pets the dog",
                "camera_motion": "push_in",
                "camera_amplitude": "small",
                "camera_speed": "slow",
                "is_cut": 0,
                "dialog": [
                    {
                        "subject_id": subject_id,
                        "voice": None,
                        "delivery": "soft",
                        "language": "English",
                        "text": "Hello, old girl.",
                    }
                ],
                "sound": "a kettle whistles",
            },
            {
                "duration_s": 4.0,
                "action": "Grandma opens the door",
                "camera_motion": "static",
                "camera_amplitude": None,
                "camera_speed": None,
                "is_cut": 0,
                "dialog": [],
                "sound": None,
            },
        ]
        db.replace_panel_beats(panel_id, beats)
    return scene_id, panel_id, subject_id


def _expect_and_scaffold(
    db: DatabaseManager, storyboard_id: int, panel_id: int, mode: str = "ref2va"
) -> Tuple[str, h3.LintExpectations]:
    """Independently reproduce what ``_compile_panel`` computes for a given
    panel, so a test can build a lint-clean body/scaffold pair up front."""
    tree = db.get_storyboard_tree(storyboard_id)
    assert tree is not None
    subjects_by_id = {s["id"]: s for s in tree["subjects"]}
    scene = panel = None
    for sc in tree["scenes"]:
        for p in sc["panels"]:
            if p["id"] == panel_id:
                scene, panel = sc, p
    assert scene is not None and panel is not None
    subjects = [
        subjects_by_id[sid] for sid in panel["subject_ids"] if sid in subjects_by_id
    ]
    beats = panel["beats"] or [
        {
            "duration_s": panel.get("duration_s") or 12.0,
            "action": panel.get("action") or "",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "sound": None,
            "dialog": [],
        }
    ]
    refplan = h3.assign_reference_labels(subjects, scene)
    speakers = h3.assign_speakers(beats, subjects, refplan)
    duration_s = float(
        panel.get("duration_s")
        or sum(float(b.get("duration_s") or 0) for b in beats)
        or 12.0
    )
    timeline = h3.compute_timeline(beats, duration_s, mode, refplan)
    scaffold_panel = dict(panel)
    scaffold_panel["beats"] = beats
    scaffold = h3.build_scaffold(
        scaffold_panel, scene, tree, subjects, refplan, speakers, timeline
    )
    expect = h3.build_expectations(refplan, speakers, timeline, mode, beats=beats)
    return scaffold, expect


def make_valid_body(scaffold: str, expect: h3.LintExpectations) -> str:
    """Render the scaffold's shots into simple, lint-compliant prose,
    including every expected dialog line (verbatim, inside <d>...</d>,
    adjacent to its (Sx) id) and every expected camera phrase -- the same
    shape H3_BODY_SYSTEM asks the VLM to produce."""
    style_line = "cinematic, live-action"
    for line in scaffold.splitlines():
        if line.startswith("STYLE: "):
            style_line = line[len("STYLE: ") :]
            break

    dialog_by_beat: Dict[int, List[Any]] = {}
    for sl in expect.dialog_lines:
        dialog_by_beat.setdefault(sl.beat_index, []).append(sl)

    parts = [f"The target video opens in a {style_line} style, calm and unhurried."]
    for i, shot in enumerate(expect.timeline.shots):
        header = (
            "[Shot 1]"
            if shot.number == 1
            else f"[Shot {shot.number}] At {h3.format_timecode(shot.start_s)}, "
            "the shot cuts to"
        )
        sentences = [
            "the camera settles on the scene as gentle morning light fills "
            "the frame, and every detail of the room comes softly into view "
            "around the people gathered there, warm and unhurried and "
            "entirely ordinary"
        ]
        camera_phrases = (
            expect.shot_camera_phrases[i] if expect.shot_camera_phrases else ()
        )
        for phrase in camera_phrases:
            sentences.append(f"The camera {phrase} as the moment unfolds")
        for bi in shot.beat_indices:
            for sl in sorted(dialog_by_beat.get(bi, []), key=lambda x: x.line_index):
                speaker_part = (
                    f"{sl.subject_label} ({sl.speaker_id})"
                    if sl.subject_label
                    else f"the {sl.voice or 'voice'} ({sl.speaker_id})"
                )
                sentences.append(
                    f"{speaker_part} turns and says, "
                    f"<d>[{sl.language}] {sl.text}</d>"
                )
        parts.append(f"{header} {', '.join(sentences)}.")

    body = "\n".join(parts)
    words = body.split()
    if len(words) < 360:
        pad = " ".join(f"detail{i}" for i in range(360 - len(words) + 20))
        body = f"{body} {pad}"
    return body


# -- FakeVlm ------------------------------------------------------------


class FakeVlm:
    model_id = "qwen3vl-30b-a3b"

    def __init__(
        self,
        body_response: str,
        sound_response: str = _VALID_SOUND,
        body_fails_first: bool = False,
        sound_fails_first: bool = False,
    ) -> None:
        self.calls: List[Tuple[str, str, Optional[str]]] = []
        self.body_calls = 0
        self.sound_calls = 0
        self._body_response = body_response
        self._sound_response = sound_response
        self._body_fails_first = body_fails_first
        self._first_body_seen = False
        self._sound_fails_first = sound_fails_first
        self._first_sound_seen = False

    async def ensure_started(self, model_id: str) -> None:
        pass

    async def generate_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        grammar: Optional[str] = None,
        temperature: float = 0.6,
        max_tokens: int = 250,
        timeout: float = 120.0,
    ) -> str:
        self.calls.append((system_prompt, user_prompt, grammar))
        if grammar is None:
            self.body_calls += 1
            if self._body_fails_first and not self._first_body_seen:
                self._first_body_seen = True
                raise VlmError("simulated VLM crash")
            return self._body_response
        self.sound_calls += 1
        if self._sound_fails_first and not self._first_sound_seen:
            self._first_sound_seen = True
            # SOUND_GRAMMAR's "string" rule permits zero characters --
            # this is grammar-valid JSON that validate_sound_response
            # nonetheless rejects (empty overall_soundscape) with H3Error.
            return json.dumps({"overall_soundscape": "", "non_diegetic_music": "N/A"})
        return self._sound_response


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "t.db")


# -- Tests ----------------------------------------------------------------


def test_compile_requires_minimax_target(db, tmp_path):
    sb = _make_storyboard(db, video_target=None)
    _make_panel(db, sb)
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: None, output_root=tmp_path
    )
    with pytest.raises(StoryboardError, match="minimax"):
        asyncio.run(runner.compile_video(sb))


def test_compile_requires_vlm_unless_deterministic(db, tmp_path):
    sb = _make_storyboard(db)
    _make_panel(db, sb)
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: None, output_root=tmp_path
    )
    with pytest.raises(StoryboardError, match="VLM"):
        asyncio.run(runner.compile_video(sb))
    # deterministic_only=True never needs a VLM at all.
    counts = asyncio.run(runner.compile_video(sb, deterministic_only=True))
    assert counts["compiled"] + counts["failed"] == 1


def test_full_compile_writes_doc_and_events(db, tmp_path):
    sb = _make_storyboard(db)
    _, panel_id, _ = _make_panel(db, sb)
    scaffold, expect = _expect_and_scaffold(db, sb, panel_id)
    valid_body = make_valid_body(scaffold, expect)
    vlm = FakeVlm(body_response=valid_body)
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    events: List[Tuple[str, str, Dict[str, Any]]] = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))

    counts = asyncio.run(runner.compile_video(sb))
    assert counts == {"compiled": 1, "failed": 0, "skipped_locked": 0}
    assert vlm.body_calls == 1  # no retry needed
    assert vlm.sound_calls == 1

    panel = db.get_panel(panel_id)
    assert panel["video_prompt_source"] == "compiled"
    assert panel["video_prompt_locked"] == 0
    assert panel["video_prompt_warnings"] == []
    doc = panel["video_prompt"]
    for header in (
        "subject_definitions:",
        "summary:",
        "retention_analysis:",
        "detailed_description:",
        "overall_soundscape:",
        "non_diegetic_music:",
    ):
        assert header in doc

    progress = [e for e in events if e[1] == "compile_progress"]
    assert len(progress) == 1
    assert progress[0][2]["panel_id"] == panel_id
    assert progress[0][2]["done"] == 1 and progress[0][2]["total"] == 1
    complete = [e for e in events if e[1] == "compile_complete"]
    assert len(complete) == 1
    assert complete[0][2]["compiled"] == 1


def test_lint_failure_retries_once_then_stores_with_errors(db, tmp_path):
    sb = _make_storyboard(db)
    _, panel_id, _ = _make_panel(db, sb)
    scaffold, expect = _expect_and_scaffold(db, sb, panel_id)
    valid_body = make_valid_body(scaffold, expect)
    # Drop the only dialog line -> dialog_missing error, both attempts.
    broken_body = valid_body.replace("<d>[English] Hello, old girl.</d>", "")
    vlm = FakeVlm(body_response=broken_body)
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )

    counts = asyncio.run(runner.compile_video(sb))
    assert counts == {"compiled": 0, "failed": 1, "skipped_locked": 0}
    assert vlm.body_calls == 2  # one retry
    assert vlm.sound_calls == 1  # sound is not retried

    panel = db.get_panel(panel_id)
    assert panel["video_prompt"]  # stored regardless
    assert panel["video_prompt_source"] == "compiled"
    assert panel["video_prompt_warnings"]
    assert any("dialog" in w for w in panel["video_prompt_warnings"])


def test_locked_panel_skipped_unless_forced(db, tmp_path):
    sb = _make_storyboard(db)
    _, panel_id, _ = _make_panel(db, sb)
    db.update_panel(panel_id, video_prompt_locked=1)
    scaffold, expect = _expect_and_scaffold(db, sb, panel_id)
    vlm = FakeVlm(body_response=make_valid_body(scaffold, expect))
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )

    counts = asyncio.run(runner.compile_video(sb))
    assert counts == {"compiled": 0, "failed": 0, "skipped_locked": 1}
    assert vlm.body_calls == 0

    counts2 = asyncio.run(runner.compile_video(sb, panel_ids=[panel_id], force=True))
    assert counts2["skipped_locked"] == 0
    assert counts2["compiled"] + counts2["failed"] == 1
    assert vlm.body_calls >= 1


def test_no_beats_panel_uses_single_shot_fallback(db, tmp_path):
    sb = _make_storyboard(db)
    _, panel_id, _ = _make_panel(db, sb, with_beats=False)
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: None, output_root=tmp_path
    )

    counts = asyncio.run(runner.compile_video(sb, deterministic_only=True))
    assert counts["compiled"] + counts["failed"] == 1

    panel = db.get_panel(panel_id)
    doc = panel["video_prompt"]
    assert "[Shot 1]" in doc
    assert "[Shot 2]" not in doc
    assert panel["video_prompt_source"] == "compiled"


def test_vlm_error_marks_panel_failed_not_run(db, tmp_path):
    sb = _make_storyboard(db)
    _, panel1_id, _ = _make_panel(db, sb, action="Panel one")
    _, panel2_id, _ = _make_panel(db, sb, action="Panel two")
    scaffold, expect = _expect_and_scaffold(db, sb, panel1_id)
    body = make_valid_body(scaffold, expect)
    vlm = FakeVlm(body_response=body, body_fails_first=True)
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )

    counts = asyncio.run(runner.compile_video(sb))
    assert counts["failed"] == 1
    assert counts["compiled"] == 1
    assert counts["skipped_locked"] == 0

    panels = [db.get_panel(panel1_id), db.get_panel(panel2_id)]
    failed_panels = [p for p in panels if not p["video_prompt"]]
    ok_panels = [p for p in panels if p["video_prompt"]]
    assert len(failed_panels) == 1
    assert len(ok_panels) == 1
    assert failed_panels[0]["video_prompt_warnings"] == ["simulated VLM crash"]


def test_h3_error_marks_panel_failed_not_run(db, tmp_path):
    """validate_sound_response's H3Error (grammar-valid JSON, empty
    overall_soundscape) must stay panel-scoped like VlmError/TimeoutError/
    RuntimeError -- not escape asyncio.gather and blow up the whole run."""
    sb = _make_storyboard(db)
    _, panel1_id, _ = _make_panel(db, sb, action="Panel one")
    _, panel2_id, _ = _make_panel(db, sb, action="Panel two")
    scaffold, expect = _expect_and_scaffold(db, sb, panel1_id)
    body = make_valid_body(scaffold, expect)
    vlm = FakeVlm(body_response=body, sound_fails_first=True)
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    events: List[Tuple[str, str, Dict[str, Any]]] = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))

    counts = asyncio.run(runner.compile_video(sb))
    assert counts["failed"] == 1
    assert counts["compiled"] == 1
    assert counts["skipped_locked"] == 0

    complete = [e for e in events if e[1] == "compile_complete"]
    assert len(complete) == 1
    assert [e for e in events if e[1] == "compile_error"] == []

    panels = [db.get_panel(panel1_id), db.get_panel(panel2_id)]
    failed_panels = [p for p in panels if not p["video_prompt"]]
    ok_panels = [p for p in panels if p["video_prompt"]]
    assert len(failed_panels) == 1
    assert len(ok_panels) == 1
    assert failed_panels[0]["video_prompt_warnings"] == ["overall_soundscape is empty"]


def test_offpanel_dialog_subject_gets_definition_no_crash(db, tmp_path):
    """Beat dialog subject_ids are picked from the whole board roster
    (BeatForm's picker + validate_beats_response), not just the panel's
    own subject_ids. A dialog line naming an off-panel subject must not
    raise -- it gets a real <Subject N> definition and the panel compiles
    normally, with no compile_error escaping the run."""
    sb = _make_storyboard(db)
    subject_a = db.create_subject(
        sb,
        name="Grandma Rose",
        description="a kind elderly woman with silver hair, wearing a floral apron",
        voice="warm, elderly voice",
        sort_order=0,
    )
    subject_b = db.create_subject(
        sb,
        name="Rex",
        description="a scruffy grey terrier with one floppy ear",
        voice="a low growl",
        sort_order=1,
    )
    scene_id = db.create_scene(
        sb,
        name="Kitchen",
        setting="a sunlit farmhouse kitchen with a wooden table",
        location="Farmhouse",
    )
    # Panel only lists subject_a -- subject_b is off-panel.
    panel_id = db.create_panel(
        scene_id, action="Grandma putters", subject_ids=[subject_a], duration_s=8.0
    )
    beats = [
        {
            "duration_s": 8.0,
            "action": "Grandma putters while Rex barks from just outside",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": subject_b,  # off-panel speaker
                    "voice": None,
                    "delivery": "loud",
                    "language": "English",
                    "text": "Woof!",
                }
            ],
            "sound": None,
        }
    ]
    db.replace_panel_beats(panel_id, beats)

    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: None, output_root=tmp_path
    )
    events: List[Tuple[str, str, Dict[str, Any]]] = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))

    counts = asyncio.run(runner.compile_video(sb, deterministic_only=True))
    assert counts["compiled"] + counts["failed"] == 1
    assert [e for e in events if e[1] == "compile_error"] == []
    assert len([e for e in events if e[1] == "compile_complete"]) == 1

    panel = db.get_panel(panel_id)
    doc = panel["video_prompt"]
    assert doc is not None
    subject_definitions = doc.split("summary:")[0]
    assert "Rex" in subject_definitions
    assert "<Subject 2>" in subject_definitions


def test_unexpected_exception_isolates_to_one_panel(db, tmp_path):
    """A plain Exception (not one of the previously-named types) raised for
    one panel's body call must still be isolated -- the other panel
    compiles, and exactly one compile_complete (never compile_error) is
    emitted for the run."""

    class ExplodingVlm:
        model_id = "qwen3vl-30b-a3b"

        def __init__(self, good_body: str) -> None:
            self._good_body = good_body
            self.body_calls = 0

        async def ensure_started(self, model_id: str) -> None:
            pass

        async def generate_text(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            grammar: Optional[str] = None,
            temperature: float = 0.6,
            max_tokens: int = 250,
            timeout: float = 120.0,
        ) -> str:
            if grammar is None:
                self.body_calls += 1
                if self.body_calls == 1:
                    raise KeyError("boom")  # a plain, unexpected exception
                return self._good_body
            return _VALID_SOUND

    sb = _make_storyboard(db)
    _, panel1_id, _ = _make_panel(db, sb, action="Panel one")
    _, panel2_id, _ = _make_panel(db, sb, action="Panel two")
    scaffold, expect = _expect_and_scaffold(db, sb, panel1_id)
    body = make_valid_body(scaffold, expect)
    vlm = ExplodingVlm(good_body=body)
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    events: List[Tuple[str, str, Dict[str, Any]]] = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))

    counts = asyncio.run(runner.compile_video(sb))
    assert counts["failed"] == 1
    assert counts["compiled"] == 1
    assert counts["skipped_locked"] == 0

    assert [e for e in events if e[1] == "compile_error"] == []
    complete = [e for e in events if e[1] == "compile_complete"]
    assert len(complete) == 1

    panels = [db.get_panel(panel1_id), db.get_panel(panel2_id)]
    failed_panels = [p for p in panels if not p["video_prompt"]]
    ok_panels = [p for p in panels if p["video_prompt"]]
    assert len(failed_panels) == 1
    assert len(ok_panels) == 1
    assert failed_panels[0]["video_prompt_warnings"] == ["'boom'"]


def test_deterministic_only_produces_doc_without_vlm(db, tmp_path):
    sb = _make_storyboard(db)
    _, panel_id, _ = _make_panel(db, sb)

    def _poison_get_vlm():
        raise AssertionError("get_vlm should not be called in deterministic_only mode")

    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=_poison_get_vlm, output_root=tmp_path
    )

    counts = asyncio.run(runner.compile_video(sb, deterministic_only=True))
    assert counts["compiled"] + counts["failed"] == 1

    panel = db.get_panel(panel_id)
    doc = panel["video_prompt"]
    assert doc is not None
    assert "detailed_description:" in doc
    assert panel["video_prompt_source"] == "compiled"
