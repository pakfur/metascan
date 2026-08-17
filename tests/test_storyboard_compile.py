"""StoryboardRunner.compile_video against a scripted fake VLM.

Mirrors tests/test_storyboard_compose.py's structure: a real (temp) DB,
a FakeVlm, and a board built through real DB calls (no mocking of the DB
layer itself).

Under the current H3 contract, ``_compile_panel`` never calls the VLM for
the ``detailed_description`` body -- it's always rendered deterministically
from the beat script by ``h3.render_detailed_description`` and passes its
own lint by construction (labels/timestamps/dialog are derived from the
exact same plans it renders from). The only VLM call left is the
grammar-constrained sound stage (``h3.SOUND_GRAMMAR``); ``FakeVlm`` asserts
it is never called with ``grammar=None`` so a regression that reintroduces
a body call fails loudly here rather than silently.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

import pytest

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
    voice_ref_path: Optional[str] = None,
) -> Tuple[int, int, int]:
    subject_id = db.create_subject(
        storyboard_id,
        name="Grandma Rose",
        description=("a kind elderly woman with silver hair, wearing a floral apron"),
        voice="warm, elderly voice",
        sort_order=0,
        voice_ref_path=voice_ref_path,
    )
    scene_id = db.create_scene(
        storyboard_id,
        name="Kitchen",
        setting="a sunlit farmhouse kitchen with a wooden table",
        location="Farmhouse",
    )
    panel_id = db.create_panel(scene_id, action=action, duration_s=duration_s)
    if with_beats:
        beats = [
            {
                "duration_s": 4.0,
                "action": "Grandma pets the dog",
                "camera_motion": "push_in",
                "camera_amplitude": "small",
                "camera_speed": "slow",
                "is_cut": 0,
                "subject_ids": [subject_id],
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
                "subject_ids": [],
                "dialog": [],
                "sound": None,
            },
        ]
        db.replace_panel_beats(panel_id, beats)
    return scene_id, panel_id, subject_id


# -- FakeVlm ------------------------------------------------------------


class FakeVlm:
    """A VLM stub for the sound stage only -- the current contract has no
    other VLM call in ``_compile_panel``. ``crash_first_sound`` raises a
    ``VlmError`` on the first sound call (panel-scoped failure);
    ``sound_fails_first`` instead returns grammar-valid-but-semantically-
    invalid JSON (empty ``overall_soundscape``), which
    ``validate_sound_response`` rejects with ``H3Error``."""

    model_id = "qwen3vl-30b-a3b"

    def __init__(
        self,
        sound_response: str = _VALID_SOUND,
        sound_fails_first: bool = False,
        crash_first_sound: bool = False,
    ) -> None:
        self.calls: List[Tuple[str, str, Optional[str]]] = []
        self.sound_calls = 0
        self._sound_response = sound_response
        self._sound_fails_first = sound_fails_first
        self._crash_first_sound = crash_first_sound
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
        assert grammar is not None, (
            "no VLM body stage remains under the current H3 contract -- "
            "_compile_panel must only call generate_text for the "
            "grammar-constrained sound stage"
        )
        self.sound_calls += 1
        if self._crash_first_sound and not self._first_sound_seen:
            self._first_sound_seen = True
            raise VlmError("simulated VLM crash")
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
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    events: List[Tuple[str, str, Dict[str, Any]]] = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))

    counts = asyncio.run(runner.compile_video(sb))
    assert counts == {"compiled": 1, "failed": 0, "skipped_locked": 0}
    assert vlm.sound_calls == 1

    panel = db.get_panel(panel_id)
    assert panel["video_prompt_source"] == "compiled"
    assert panel["video_prompt_locked"] == 0
    # _make_panel's beats are short -- the deterministic
    # detailed_description passes its own lint by construction but comes
    # in under the 150-word floor, which is advisory-only (a warning, not
    # an error -- see test_lint_word_count_warning_below_floor).
    assert len(panel["video_prompt_warnings"]) == 1
    assert "word floor" in panel["video_prompt_warnings"][0]
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


def test_word_count_shortfall_is_warning_not_failure(db, tmp_path):
    """The old VLM-body/retry contract is gone: the detailed_description is
    always rendered deterministically from the beat script, and it passes
    its own lint by construction (labels/timestamps/dialog are derived
    from the exact plans it was rendered from, so they can't drift). The
    only lint issue a short beat script like _make_panel's can produce is
    a below-floor word_count warning -- it must not fail the panel and
    must not trigger any extra VLM call (there is no retry mechanism left
    at all)."""
    sb = _make_storyboard(db)
    _, panel_id, _ = _make_panel(db, sb)
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )

    counts = asyncio.run(runner.compile_video(sb))
    assert counts == {"compiled": 1, "failed": 0, "skipped_locked": 0}
    assert vlm.sound_calls == 1  # exactly one call -- nothing retries

    panel = db.get_panel(panel_id)
    assert panel["video_prompt"]
    assert panel["video_prompt_source"] == "compiled"
    assert len(panel["video_prompt_warnings"]) == 1
    assert "word floor" in panel["video_prompt_warnings"][0]


def test_locked_panel_skipped_unless_forced(db, tmp_path):
    sb = _make_storyboard(db)
    _, panel_id, _ = _make_panel(db, sb)
    db.update_panel(panel_id, video_prompt_locked=1)
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )

    counts = asyncio.run(runner.compile_video(sb))
    assert counts == {"compiled": 0, "failed": 0, "skipped_locked": 1}
    assert vlm.sound_calls == 0

    counts2 = asyncio.run(runner.compile_video(sb, panel_ids=[panel_id], force=True))
    assert counts2["skipped_locked"] == 0
    assert counts2["compiled"] + counts2["failed"] == 1
    assert vlm.sound_calls >= 1


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
    # The only VLM call left is the sound stage -- crash on the first one.
    vlm = FakeVlm(crash_first_sound=True)
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
    vlm = FakeVlm(sound_fails_first=True)
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
    (BeatForm's picker + validate_beats_response), not just the beat's
    own subject_ids. A dialog line naming an off-beat subject must not
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
    # Beat's subject_ids only lists subject_a -- subject_b is off-beat.
    panel_id = db.create_panel(scene_id, action="Grandma putters", duration_s=8.0)
    beats = [
        {
            "duration_s": 8.0,
            "action": "Grandma putters while Rex barks from just outside",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "subject_ids": [subject_a],
            "dialog": [
                {
                    "subject_id": subject_b,  # off-beat speaker
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
    one panel's sound call must still be isolated -- the other panel
    compiles, and exactly one compile_complete (never compile_error) is
    emitted for the run."""

    class ExplodingVlm:
        model_id = "qwen3vl-30b-a3b"

        def __init__(self) -> None:
            self.sound_calls = 0

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
            self.sound_calls += 1
            if self.sound_calls == 1:
                raise KeyError("boom")  # a plain, unexpected exception
            return _VALID_SOUND

    sb = _make_storyboard(db)
    _, panel1_id, _ = _make_panel(db, sb, action="Panel one")
    _, panel2_id, _ = _make_panel(db, sb, action="Panel two")
    vlm = ExplodingVlm()
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


def test_compile_appends_audio_sections_and_records_anchor(db, tmp_path):
    """A voice_ref-carrying subject who actually speaks in the panel's
    beats gets <Audio N> definition/retention lines appended, those lines
    lint clean (regression coverage for the retention_marker vocabulary
    split -- §4.2 audio markers vs §4.1 subject markers), and the success
    write records video_compiled_anchor from the panel's video_anchor at
    compile time. Uses deterministic_only=True -- the detailed_description
    passes its own lint by construction (see the FakeVlm docstring), so a
    short beat script no longer fails the compile and this no longer needs
    a hand-built, lint-clean VLM body to exercise the audio lines."""
    sb = _make_storyboard(db)
    _, panel_id, _ = _make_panel(db, sb, voice_ref_path="/refs/grandma_voice.wav")
    db.update_panel(panel_id, video_anchor="keeper")
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: None, output_root=tmp_path
    )

    counts = asyncio.run(runner.compile_video(sb, deterministic_only=True))
    assert counts["compiled"] == 1
    assert counts["failed"] == 0

    panel = db.get_panel(panel_id)
    assert not any("retention_marker" in w for w in panel["video_prompt_warnings"])
    doc = panel["video_prompt"]
    assert doc is not None
    assert "<Audio 1> is the voice-timbre reference for <Subject 1> (S1)." in doc
    assert (
        "<Audio 1>: reference - the target speaker follows <Audio 1>'s "
        "voice timbre and delivery without copying the original signal."
    ) in doc
    assert panel["video_compiled_anchor"] == "keeper"
