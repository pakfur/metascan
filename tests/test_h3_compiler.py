"""Tests for metascan.core.h3_compiler — the pure H3 dialect compiler core.

Fixtures mirror real DB rows: subjects have
id/name/description/voice/reference_path/reference_path_2/sort_order;
scenes carry name/setting/location/reference_path; beats carry
duration_s/action/camera_motion/camera_amplitude/camera_speed/is_cut/
dialog/sound. Assertions are checked against the vendored guides in
data/prompt_guides/minimax-h3/.
"""

from __future__ import annotations

from typing import Any, Dict, List

from metascan.core.h3_compiler import (
    RefPlan,
    active_audio_refs,
    assemble,
    assign_reference_labels,
    assign_speakers,
    build_expectations,
    build_scaffold,
    compute_timeline,
    format_timecode,
    lint_h3_prompt,
    render_audio_definition_lines,
    render_audio_retention_lines,
    render_camera,
    render_detailed_description,
    render_retention_analysis,
    render_subject_definitions,
    render_summary,
)


def _subjects() -> List[Dict[str, Any]]:
    return [
        {
            "id": 7,
            "name": "Grandma Rose",
            "description": (
                "a kind elderly woman with silver hair, wearing a floral apron"
            ),
            "voice": "warm, elderly voice",
            "reference_path": "/refs/grandma1.png",
            "reference_path_2": "/refs/grandma2.png",
            "sort_order": 0,
        },
        {
            "id": 9,
            "name": "Rex",
            "description": "a scruffy grey terrier with one floppy ear",
            "voice": None,
            "reference_path": None,
            "reference_path_2": None,
            "sort_order": 1,
        },
    ]


def _scene() -> Dict[str, Any]:
    return {
        "id": 1,
        "name": "Kitchen",
        "setting": "a sunlit farmhouse kitchen with a wooden table",
        "location": "Farmhouse",
        "reference_path": "/refs/kitchen.png",
    }


def _beats() -> List[Dict[str, Any]]:
    return [
        {
            "duration_s": 2.0,
            "action": "Grandma pets the dog",
            "camera_motion": "push_in",
            "camera_amplitude": "small",
            "camera_speed": "slow",
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": 7,
                    "voice": None,
                    "delivery": "soft",
                    "language": "English",
                    "text": "Hello, old girl.",
                }
            ],
            "sound": "a kettle whistles",
        },
        {
            "duration_s": 2.0,
            "action": "Rex barks at the door",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": None,
                    "voice": "gravelly voice",
                    "delivery": None,
                    "language": "English",
                    "text": "Watch it!",
                }
            ],
            "sound": None,
        },
        {
            "duration_s": 2.0,
            "action": "Grandma opens the door",
            "camera_motion": "static",
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 1,
            "dialog": [],
            "sound": None,
        },
    ]


def test_refplan_numbers_subjects_pictures_and_keyframe() -> None:
    subjects = _subjects()
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)

    assert refplan.subject_labels == {7: "Subject 1", 9: "Subject 2"}
    assert refplan.environment_label == "Subject 3"
    assert refplan.picture_labels == [
        ("/refs/grandma1.png", "Picture 1"),
        ("/refs/grandma2.png", "Picture 2"),
        ("/refs/kitchen.png", "Picture 3"),
    ]
    assert refplan.keyframe_picture_label == "Picture 4"


def test_timeline_beat_equals_shot_and_formats() -> None:
    beats = _beats()
    refplan = RefPlan({}, "Subject 1", [], "Picture 1")
    timeline = compute_timeline(beats, 12.0, "ref2va", refplan)

    # beat == shot: every beat maps to its own [Shot n], regardless of
    # is_cut. 3 beats of 2.0s rescaled to sum to 12.0s -> 4.0s each.
    assert [(s.number, s.start_s, s.beat_indices) for s in timeline.shots] == [
        (1, 0.0, [0]),
        (2, 4.0, [1]),
        (3, 8.0, [2]),
    ]
    assert timeline.duration_s == 12.0
    assert timeline.alignment_line is None
    assert format_timecode(timeline.shots[1].start_s) == "00:04.000"
    assert format_timecode(timeline.shots[2].start_s) == "00:08.000"
    assert format_timecode(3.5) == "00:03.500"


def test_alignment_lines_per_mode() -> None:
    beats = _beats()
    subjects = _subjects()
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)  # keyframe "Picture 4"

    t_i2va = compute_timeline(beats, 12.0, "i2va", refplan)
    assert t_i2va.alignment_line is not None
    assert "at 0.00 seconds" in t_i2va.alignment_line
    assert "Picture 4" in t_i2va.alignment_line
    assert "[Shot 1]" in t_i2va.alignment_line

    t_fl2va = compute_timeline(beats, 12.0, "fl2va", refplan)
    assert t_fl2va.alignment_line is not None
    assert "Picture 4" in t_fl2va.alignment_line
    assert "0.00-second mark" in t_fl2va.alignment_line
    assert "Picture 5" in t_fl2va.alignment_line
    assert f"{12.00:.2f}-second mark" in t_fl2va.alignment_line

    assert compute_timeline(beats, 12.0, "ref2va", refplan).alignment_line is None
    assert compute_timeline(beats, 12.0, "t2va", refplan).alignment_line is None


def test_assign_speakers_stable_ids_and_voice_fallbacks() -> None:
    subjects = _subjects()
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)
    beats = [
        {
            "duration_s": 2.0,
            "action": "a",
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": 7,
                    "voice": None,
                    "delivery": "soft",
                    "language": "English",
                    "text": "Hello, old girl.",
                }
            ],
            "sound": None,
        },
        {
            "duration_s": 2.0,
            "action": "b",
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": None,
                    "voice": "gravelly voice",
                    "delivery": None,
                    "language": "English",
                    "text": "Watch it!",
                }
            ],
            "sound": None,
        },
        {
            "duration_s": 2.0,
            "action": "c",
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": 7,
                    "voice": None,
                    "delivery": "warm",
                    "language": "English",
                    "text": "There, there.",
                },
                {
                    "subject_id": None,
                    "voice": "Gravelly Voice",
                    "delivery": None,
                    "language": "English",
                    "text": "Still me.",
                },
            ],
            "sound": None,
        },
    ]

    speakers = assign_speakers(beats, subjects, refplan)
    ids = [line.speaker_id for line in speakers.lines]
    assert ids == ["S1", "S2", "S1", "S2"]
    assert speakers.voice_by_id["S1"] == "warm, elderly voice"
    assert speakers.voice_by_id["S2"] == "gravelly voice"
    assert speakers.lines[0].subject_label == "<Subject 1>"
    assert speakers.lines[1].subject_label is None
    assert speakers.lines[3].speaker_id == "S2"  # case-insensitive voice reuse


def test_assign_speakers_unknown_subject_id_falls_back_to_anonymous_voice() -> None:
    """A dialog line naming a subject_id absent from ``refplan.subject_labels``
    (e.g. a caller that built the RefPlan from a narrower subject list than
    the dialog actually references) must not raise KeyError -- it falls
    back to the same anonymous-voice path as an unnamed voice line."""
    subjects = _subjects()
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)
    beats = [
        {
            "duration_s": 2.0,
            "action": "a",
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": 999,  # not present in refplan.subject_labels
                    "voice": "a distant voice",
                    "delivery": None,
                    "language": "English",
                    "text": "Hello?",
                }
            ],
            "sound": None,
        }
    ]

    speakers = assign_speakers(beats, subjects, refplan)
    assert len(speakers.lines) == 1
    line = speakers.lines[0]
    assert line.subject_label is None
    assert line.speaker_id == "S1"
    assert speakers.voice_by_id["S1"] == "a distant voice"


def test_render_camera_full_partial_none() -> None:
    assert (
        render_camera("push_in", "small", "slow")
        == "pushes in, small amplitude, slow speed"
    )
    assert render_camera("static", None, None) == "holds a static shot"
    assert render_camera(None, "large", None) == "large amplitude"
    assert render_camera(None, None, None) is None


def test_subject_definitions_verbatim_and_env() -> None:
    subjects = _subjects()
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)
    text = render_subject_definitions(refplan, subjects, scene)

    assert (
        "<Subject 1> is the Grandma Rose in <Picture 1> and <Picture 2>, "
        "a kind elderly woman with silver hair, wearing a floral apron."
    ) in text
    assert (
        "<Subject 2> is the Rex: a scruffy grey terrier with one floppy ear."
    ) in text
    # scene has a reference_path (allocated "Picture 3" by assign_reference_labels
    # above), so ref-guide §2.2 requires the environment line to cite it rather
    # than create a standalone picture entry — mirrors the §7 worked example
    # ("<Subject 1> is the coffee-shop environment in <Picture 1>, featuring...").
    assert (
        refplan.keyframe_picture_label == "Picture 4"
    )  # confirms scene ref -> Picture 3
    assert (
        "<Subject 3> is the Kitchen environment in <Picture 3>, "
        "a sunlit farmhouse kitchen with a wooden table."
    ) in text

    scene_no_setting = {
        "id": 1,
        "name": "Kitchen",
        "setting": "",
        "location": "Farmhouse",
        "reference_path": None,
    }
    # No scene reference_path -> the environment is not referenced content,
    # so it gets no Subject slot and no definition line at all.
    refplan2 = assign_reference_labels([], scene_no_setting)
    assert refplan2.environment_label is None
    text2 = render_subject_definitions(refplan2, [], scene_no_setting)
    assert "environment" not in text2
    assert "in <Picture" not in text2


def test_subject_definitions_sheet_ref_boilerplate() -> None:
    """sheet_ref emits the three-view character-sheet boilerplate with the
    panel's ACTUAL labels (not a hardcoded "<Picture 1>"), appends the
    description after it, and cites a second reference when present."""
    subjects = _subjects()
    # Make the SECOND subject a single-ref sheet so the assigned labels
    # differ from "<Subject 1>"/"<Picture 1>".
    subjects[1]["reference_path"] = "/refs/rex_sheet.png"
    subjects[1]["sheet_ref"] = 1
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)
    text = render_subject_definitions(refplan, subjects, scene)
    assert (
        "<Subject 2> is Rex, the person shown in <Picture 3>, a three-view "
        "character reference sheet (full front, full back, facial close-up) "
        "of one single individual. a scruffy grey terrier with one floppy ear"
    ) in text
    # Subject 1 has no sheet_ref -- unchanged rendering.
    assert "<Subject 1> is the Grandma Rose in <Picture 1> and <Picture 2>," in text

    # A second reference picture rides along; an empty description appends
    # nothing after the boilerplate sentence.
    subjects[0]["sheet_ref"] = 1
    subjects[0]["description"] = ""
    refplan = assign_reference_labels(subjects, scene)
    text = render_subject_definitions(refplan, subjects, scene)
    assert (
        "<Subject 1> is Grandma Rose, the person shown in <Picture 1>, "
        "a three-view character reference sheet (full front, full back, "
        "facial close-up) of one single individual, also shown in <Picture 2>."
    ) in text

    # sheet_ref without any reference picture falls back to the plain form.
    bare = [
        {
            "id": 3,
            "name": "Ghost",
            "description": "d",
            "sort_order": 0,
            "reference_path": None,
            "reference_path_2": None,
            "sheet_ref": 1,
        }
    ]
    refplan = assign_reference_labels(
        bare, {"id": 1, "name": "S", "reference_path": None}
    )
    text = render_subject_definitions(
        refplan, bare, {"id": 1, "name": "S", "reference_path": None}
    )
    assert "<Subject 1> is the Ghost: d." in text


def test_summary_prefix_modes() -> None:
    subjects = _subjects()
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)
    panel = {"action": "share a quiet morning together"}

    s_ref2va = render_summary(refplan, panel, subjects, "ref2va")
    assert s_ref2va.startswith("[reference generation] ")
    assert (
        "The target video shows <Subject 1> and <Subject 2> in <Subject 3>: "
        "share a quiet morning together."
    ) in s_ref2va

    s_t2va = render_summary(refplan, panel, subjects, "t2va")
    assert s_t2va.startswith("[reference generation] ")
    assert "keyframe completion" not in s_t2va

    s_i2va = render_summary(refplan, panel, subjects, "i2va")
    assert s_i2va.startswith("[reference generation + keyframe completion] ")

    s_fl2va = render_summary(refplan, panel, subjects, "fl2va")
    assert "[reference generation + keyframe completion] " in s_fl2va


def test_retention_lines_and_keyframe_entry() -> None:
    subjects = _subjects()
    scene = _scene()
    beats = _beats()
    refplan = assign_reference_labels(subjects, scene)
    assert refplan.keyframe_picture_label == "Picture 4"

    timeline_i2va = compute_timeline(beats, 12.0, "i2va", refplan)
    text = render_retention_analysis(refplan, subjects, scene, timeline_i2va)
    assert (
        "<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3]): "
        "fully_preserved - a kind elderly woman with silver hair, "
        "wearing a floral apron."
    ) in text
    assert (
        "<Subject 3> (appears in [Shot 1], [Shot 2], [Shot 3]): " "fully_preserved -"
    ) in text
    assert (
        "<Picture 4> ([Shot 1] first frame): fully_preserved - "
        "the shot begins from this frame."
    ) in text

    timeline_fl2va = compute_timeline(beats, 12.0, "fl2va", refplan)
    text_fl = render_retention_analysis(refplan, subjects, scene, timeline_fl2va)
    assert (
        "<Picture 5> ([Shot 3] last frame): fully_preserved - "
        "the shot ends on this frame."
    ) in text_fl

    timeline_ref2va = compute_timeline(beats, 12.0, "ref2va", refplan)
    text_none = render_retention_analysis(refplan, subjects, scene, timeline_ref2va)
    assert "Picture 4" not in text_none
    assert "Picture 5" not in text_none


def test_audio_labels_assigned_only_for_voice_ref_subjects() -> None:
    subjects = _subjects()
    subjects[0]["voice_ref_path"] = "/refs/grandma_voice.wav"  # id 7 (sort_order 0)
    # subjects[1] (id 9, Rex) has no voice_ref_path key at all.
    scene = _scene()

    refplan = assign_reference_labels(subjects, scene)
    assert refplan.audio_labels == ((7, "Audio 1"),)


def test_audio_definition_and_retention_for_speaking_subject_only() -> None:
    subjects = _subjects()
    subjects[0]["voice_ref_path"] = "/refs/grandma_voice.wav"  # id 7, speaks
    subjects[1]["voice_ref_path"] = "/refs/rex_voice.wav"  # id 9, silent
    scene = _scene()
    beats = _beats()  # beat 0 dialog subject_id=7; beat 1 dialog is an
    # unnamed voice (subject_id=None) -- subject 9 never actually speaks.
    refplan = assign_reference_labels(subjects, scene)
    speakers = assign_speakers(beats, subjects, refplan)
    assert refplan.audio_labels == ((7, "Audio 1"), (9, "Audio 2"))

    definition_lines = render_audio_definition_lines(refplan, speakers, subjects)
    assert definition_lines == [
        "<Audio 1> is the voice-timbre reference for <Subject 1> (S1)."
    ]

    retention_lines = render_audio_retention_lines(refplan, speakers, subjects)
    assert retention_lines == [
        "<Audio 1>: reference - the target speaker follows <Audio 1>'s "
        "voice timbre and delivery without copying the original signal."
    ]

    active = active_audio_refs(refplan, speakers, subjects)
    assert active == [("/refs/grandma_voice.wav", "Audio 1")]


def test_active_audio_refs_order_and_paths() -> None:
    """Audio labels are numbered by subject sort_order, independent of the
    order subjects actually speak in -- active_audio_refs/definition lines
    must follow label order (Audio 1, Audio 2, ...), not speaker-id order."""
    subjects = [
        {
            "id": 3,
            "name": "Uncle Theo",
            "description": "a gruff man with a beard",
            "voice": "gruff baritone",
            "reference_path": None,
            "reference_path_2": None,
            "sort_order": 2,
            "voice_ref_path": "/refs/theo_voice.wav",
        },
        {
            "id": 1,
            "name": "Grandma Rose",
            "description": "an elderly woman",
            "voice": "warm voice",
            "reference_path": None,
            "reference_path_2": None,
            "sort_order": 0,
            "voice_ref_path": "/refs/grandma_voice.wav",
        },
        {
            "id": 2,
            "name": "Rex",
            "description": "a scruffy terrier",
            "voice": None,
            "reference_path": None,
            "reference_path_2": None,
            "sort_order": 1,
        },
    ]
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)
    # sort_order: id1 -> Subject 1, id2 -> Subject 2, id3 -> Subject 3.
    assert refplan.audio_labels == ((1, "Audio 1"), (3, "Audio 2"))

    beats = [
        {
            "duration_s": 2.0,
            "action": "Theo speaks first",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": 3,
                    "voice": None,
                    "delivery": None,
                    "language": "English",
                    "text": "Hello there.",
                }
            ],
            "sound": None,
        },
        {
            "duration_s": 2.0,
            "action": "Grandma replies",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": 1,
                    "voice": None,
                    "delivery": None,
                    "language": "English",
                    "text": "Good morning.",
                }
            ],
            "sound": None,
        },
    ]
    speakers = assign_speakers(beats, subjects, refplan)
    # Theo (Subject 3) speaks first -> S1; Grandma (Subject 1) -> S2.
    assert speakers.voice_by_id["S1"] == "gruff baritone"
    assert speakers.voice_by_id["S2"] == "warm voice"

    active = active_audio_refs(refplan, speakers, subjects)
    assert active == [
        ("/refs/grandma_voice.wav", "Audio 1"),
        ("/refs/theo_voice.wav", "Audio 2"),
    ]

    definition_lines = render_audio_definition_lines(refplan, speakers, subjects)
    assert definition_lines == [
        "<Audio 1> is the voice-timbre reference for <Subject 1> (S2).",
        "<Audio 2> is the voice-timbre reference for <Subject 3> (S1).",
    ]


def test_scaffold_contains_beats_dialog_and_timecodes() -> None:
    subjects = _subjects()
    scene = _scene()
    beats = _beats()
    refplan = assign_reference_labels(subjects, scene)
    timeline = compute_timeline(beats, 12.0, "ref2va", refplan)
    speakers = assign_speakers(beats, subjects, refplan)
    panel = {
        "id": 1,
        "action": "Grandma and Rex share a quiet morning",
        "duration_s": 12.0,
        "subject_ids": [7, 9],
        "beats": beats,
    }
    storyboard = {"style_block": "warm, nostalgic 16mm film look"}

    scaffold = build_scaffold(
        panel, scene, storyboard, subjects, refplan, speakers, timeline
    )

    assert "STYLE: warm, nostalgic 16mm film look" in scaffold
    assert "DURATION: 12.0s" in scaffold
    assert "SUBJECTS PRESENT: <Subject 1> Grandma Rose" in scaffold
    assert "<Subject 2> Rex" in scaffold
    assert "[Shot 1] starts 00:00.000" in scaffold
    assert "[Shot 2] At 00:04.000, cut." in scaffold
    assert "[Shot 3] At 00:08.000, cut." in scaffold
    assert "- action: Grandma pets the dog" in scaffold
    assert "- camera: pushes in, small amplitude, slow speed" in scaffold
    assert '- dialog: <Subject 1> (S1) [soft] (English): "Hello, old girl."' in scaffold
    assert '- dialog: the gravelly voice (S2) (English): "Watch it!"' in scaffold
    assert "- sound: a kettle whistles" in scaffold
    assert "- camera: unspecified" in scaffold
    assert "- camera: holds a static shot" in scaffold
    assert "- action: Grandma opens the door" in scaffold


def test_scaffold_no_beats_no_dialog_no_camera_omits_lines() -> None:
    subjects = _subjects()
    scene = _scene()
    beats = [
        {
            "duration_s": 5.0,
            "action": "Nothing much happens",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "dialog": [],
            "sound": None,
        }
    ]
    refplan = assign_reference_labels(subjects, scene)
    timeline = compute_timeline(beats, 5.0, "ref2va", refplan)
    speakers = assign_speakers(beats, subjects, refplan)
    panel = {
        "id": 2,
        "action": "quiet",
        "duration_s": 5.0,
        "subject_ids": [7],
        "beats": beats,
    }
    storyboard: Dict[str, Any] = {"style_block": None}

    scaffold = build_scaffold(
        panel, scene, storyboard, subjects, refplan, speakers, timeline
    )
    assert "STYLE: cinematic, live-action" in scaffold
    assert "- dialog:" not in scaffold
    assert "- sound:" not in scaffold
    assert "- camera: unspecified" in scaffold


def test_render_detailed_description_beat_equals_shot() -> None:
    """One [Shot n] per beat: shot 1 has no timestamp, every later shot
    carries its ``At MM:SS.mmm`` start time and the is_cut-driven cut vs
    continuing phrasing, dialog renders voice/delivery per the ref-guide
    §5.4 clause shape, and a beat's sound becomes its own sentence."""
    subjects = _subjects()
    scene = _scene()
    beats = _beats()
    refplan = assign_reference_labels(subjects, scene)
    speakers = assign_speakers(beats, subjects, refplan)
    timeline = compute_timeline(beats, 12.0, "ref2va", refplan)

    text = render_detailed_description(
        "cinematic, live-action", beats, timeline, speakers
    )
    lines = text.split("\n")

    assert lines[0] == "The target video is in a cinematic, live-action style."
    assert len(lines) == 4  # style line + one line per beat (3 beats)

    # Shot 1: no timestamp; beat 0's action/camera/dialog(no voice, has
    # delivery)/sound all present.
    assert lines[1] == (
        "[Shot 1] Grandma pets the dog. The camera pushes in, small "
        "amplitude, slow speed. <Subject 1> (S1) says, soft, "
        "<d>[English] Hello, old girl.</d> A kettle whistles."
    )

    # Shot 2: is_cut=0 -> "continuing without a cut."; beat 1's dialog has
    # a voice (no subject) and no delivery -> "says in a {voice}" clause,
    # with " voice" NOT appended because "voice" is already in the string.
    assert lines[2] == (
        "[Shot 2] At 00:04.000, continuing without a cut. Rex barks at "
        "the door. the gravelly voice (S2) says in a gravelly voice "
        "<d>[English] Watch it!</d>"
    )

    # Shot 3: is_cut=1 -> "the shot cuts."; no dialog, no sound.
    assert lines[3] == (
        "[Shot 3] At 00:08.000, the shot cuts. Grandma opens the door. "
        "The camera holds a static shot."
    )


def test_render_detailed_description_voice_suffix_and_dialog_only_line() -> None:
    """A voice string that doesn't already say "voice" gets " voice"
    appended; a beat with only dialog (no action/camera/sound) renders
    just the dialog clause after its header."""
    subjects = _subjects()
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)
    beats = [
        {
            "duration_s": 3.0,
            "action": "",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "dialog": [
                {
                    "subject_id": None,
                    "voice": "a low rasp",
                    "delivery": None,
                    "language": "English",
                    "text": "Who's there?",
                }
            ],
            "sound": None,
        }
    ]
    speakers = assign_speakers(beats, subjects, refplan)
    timeline = compute_timeline(beats, 3.0, "ref2va", refplan)

    text = render_detailed_description("noir", beats, timeline, speakers)
    lines = text.split("\n")
    assert lines[1] == (
        "[Shot 1] the a low rasp (S1) says in a a low rasp voice "
        "<d>[English] Who's there?</d>"
    )


def test_detailed_description_renders_beat_framing() -> None:
    """A beat carrying any of shot_size/angle/lens gets a trailing framing
    sentence rendered from the SHOT_SIZES/ANGLES/LENSES phrase maps."""
    beats: List[Dict[str, Any]] = [
        {
            "duration_s": 4.0,
            "action": "She turns.",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "shot_size": "CU",
            "angle": "low",
            "lens": None,
            "dialog": [],
            "sound": None,
        }
    ]
    refplan = RefPlan({}, "Subject 1", [], "Picture 1")
    speakers = assign_speakers(beats, [], refplan)
    timeline = compute_timeline(beats, 4.0, "ref2va", refplan)

    dd = render_detailed_description("cinematic", beats, timeline, speakers)
    assert "close-up" in dd and "low angle" in dd


def test_detailed_description_no_framing_no_sentence() -> None:
    """A beat with no shot_size/angle/lens gets no framing sentence."""
    beats: List[Dict[str, Any]] = [
        {
            "duration_s": 4.0,
            "action": "She turns.",
            "camera_motion": None,
            "camera_amplitude": None,
            "camera_speed": None,
            "is_cut": 0,
            "dialog": [],
            "sound": None,
        }
    ]
    refplan = RefPlan({}, "Subject 1", [], "Picture 1")
    speakers = assign_speakers(beats, [], refplan)
    timeline = compute_timeline(beats, 4.0, "ref2va", refplan)

    dd = render_detailed_description("cinematic", beats, timeline, speakers)
    assert "framed as" not in dd


def test_assemble_order_and_alignment_first() -> None:
    doc = assemble(
        "ALIGN LINE",
        "SUBJDEF",
        "SUMMARY TEXT",
        "RETENTION TEXT",
        "DETAIL TEXT",
        "SOUND TEXT",
        "MUSIC TEXT",
    )
    expected = (
        "ALIGN LINE\n\n"
        "subject_definitions:\nSUBJDEF\n\n"
        "summary:\nSUMMARY TEXT\n\n"
        "retention_analysis:\nRETENTION TEXT\n\n"
        "detailed_description:\nDETAIL TEXT\n\n"
        "overall_soundscape:\nSOUND TEXT\n\n"
        "non_diegetic_music:\nMUSIC TEXT"
    )
    assert doc == expected

    doc_no_align = assemble(
        None,
        "SUBJDEF",
        "SUMMARY TEXT",
        "RETENTION TEXT",
        "DETAIL TEXT",
        "SOUND TEXT",
        "MUSIC TEXT",
    )
    assert doc_no_align.startswith("subject_definitions:\nSUBJDEF")
    assert "ALIGN LINE" not in doc_no_align


# -- Lint fixtures -----------------------------------------------------
#
# One hand-written, guide-compliant `detailed_description` body over the
# three shots produced by `_beats()`/`_subjects()`/`_scene()` at
# duration_s=12.0, mode="ref2va" (beat == shot: shot 2 starts at
# 00:04.000, shot 3 at 00:08.000, per
# test_timeline_beat_equals_shot_and_formats above). Both dialog lines
# from `_beats()` appear verbatim, adjacent (same line) to their `(Sx)`
# id, inside `<d>[English] ...</d>`.

_SHOT1 = (
    "[Shot 1] Cinematic, live-action, a wide establishing shot opens on "
    "<Subject 3>, the sunlit farmhouse kitchen with its wooden table "
    "catching the early morning light through gauzy curtains. <Subject 1>, "
    "Grandma Rose, a kind elderly woman with silver hair and a floral "
    "apron, kneels beside the wooden table, her weathered hands resting "
    "gently on the head of <Subject 2>, a scruffy grey terrier with one "
    "floppy ear who leans contentedly into her palm. The camera pushes in "
    "with small amplitude at slow speed as steam curls from a kettle on "
    "the stove behind them, catching soft flecks of dust suspended in the "
    "morning light. Grandma Rose (S1) smiles warmly down at the dog and "
    "says, <d>[English] Hello, old girl.</d> Her voice carries the same "
    "warmth as the kettle's rising steam, and the dog's tail thumps twice "
    "against the worn floorboards in reply."
)

_SHOT2 = (
    "[Shot 2] At 00:04.000, continuing without a cut. Outside the window, "
    "a light breeze stirs the curtains, and somewhere down the hallway a "
    "floorboard creaks under an unseen footstep. The gravelly voice (S2), "
    "low and rough, calls out from just beyond the doorway, unseen but "
    "unmistakably present in the next room, cutting through the quiet "
    "morning hush with sudden alertness, <d>[English] Watch it!</d> "
    "Grandma Rose glances toward the sound, her expression shifting from "
    "warmth to mild concern, while the terrier's ears perk upward and its "
    "head turns sharply toward the hallway, a low growl rumbling faintly "
    "in its throat as the kettle continues to whistle softly in the "
    "background, its steady rising pitch filling the small kitchen with "
    "an anticipatory hum that mirrors the sudden tension now settling "
    "over the room."
)

_SHOT3 = (
    "[Shot 3] At 00:08.000, the shot cuts to a medium shot of Grandma "
    "Rose stepping toward the kitchen door, her floral apron swaying "
    "gently as she crosses the sunlit floor. The camera holds a static "
    "shot as she reaches for the wooden door handle, her silver hair "
    "catching a last warm shaft of morning light before she pulls the "
    "door open, revealing the hallway beyond in soft shadow. <Subject 2> "
    "trots close behind her heels, ears still raised, tail held low but "
    "steady, its floppy ear bouncing gently with each step across the "
    "worn floorboards. The kettle's whistle fades slightly into the "
    "background as the door creaks open on old hinges, and the farmhouse "
    "settles into a watchful stillness, the morning light spilling "
    "further across the wooden table behind them as the scene closes on "
    "the threshold between kitchen and hallway."
)

_SOUNDSCAPE = (
    "A kettle whistles steadily on the stove while the old floorboards "
    "creak faintly underfoot. A light breeze stirs the curtains, and the "
    "wooden door creaks open on its hinges near the end of the scene."
)

_MUSIC = (
    "A gentle acoustic guitar plays at a slow, warm tempo, joined by soft "
    "strings that swell briefly before settling as the door opens."
)


def _fixture_parts() -> Dict[str, Any]:
    subjects = _subjects()
    scene = _scene()
    beats = _beats()
    refplan = assign_reference_labels(subjects, scene)
    speakers = assign_speakers(beats, subjects, refplan)
    timeline = compute_timeline(beats, 12.0, "ref2va", refplan)
    expect = build_expectations(refplan, speakers, timeline, "ref2va")
    panel = {"action": "share a quiet morning together"}
    return {
        "expect": expect,
        "alignment_line": timeline.alignment_line,
        "subject_definitions": render_subject_definitions(refplan, subjects, scene),
        "summary": render_summary(refplan, panel, subjects, "ref2va"),
        "retention_analysis": render_retention_analysis(
            refplan, subjects, scene, timeline
        ),
        "overall_soundscape": _SOUNDSCAPE,
        "non_diegetic_music": _MUSIC,
    }


def _assemble_doc(parts: Dict[str, Any], detailed_description: str) -> str:
    return assemble(
        parts["alignment_line"],
        parts["subject_definitions"],
        parts["summary"],
        parts["retention_analysis"],
        detailed_description,
        parts["overall_soundscape"],
        parts["non_diegetic_music"],
    )


def _known_good_doc() -> "tuple[str, Any]":
    parts = _fixture_parts()
    dd = _SHOT1 + "\n" + _SHOT2 + "\n" + _SHOT3
    return _assemble_doc(parts, dd), parts["expect"]


def _filler_words(n: int) -> str:
    return " ".join(f"word{i}" for i in range(n))


# -- Lint: known-good document ------------------------------------------


def test_lint_known_good_document_passes() -> None:
    text, expect = _known_good_doc()
    assert lint_h3_prompt(text, expect) == []


# -- Lint: rule 1, missing_section ---------------------------------------


def test_lint_missing_section_flags_absent_header() -> None:
    parts = _fixture_parts()
    dd = _SHOT1 + "\n" + _SHOT2 + "\n" + _SHOT3
    text = "\n\n".join(
        [
            f"subject_definitions:\n{parts['subject_definitions']}",
            f"summary:\n{parts['summary']}",
            # retention_analysis omitted entirely
            f"detailed_description:\n{dd}",
            f"overall_soundscape:\n{parts['overall_soundscape']}",
            f"non_diegetic_music:\n{parts['non_diegetic_music']}",
        ]
    )
    errors = lint_h3_prompt(text, parts["expect"])
    assert any(e.code == "missing_section" and e.severity == "error" for e in errors)


def test_lint_missing_section_flags_out_of_order() -> None:
    parts = _fixture_parts()
    dd = _SHOT1 + "\n" + _SHOT2 + "\n" + _SHOT3
    text = "\n\n".join(
        [
            f"summary:\n{parts['summary']}",
            f"subject_definitions:\n{parts['subject_definitions']}",
            f"retention_analysis:\n{parts['retention_analysis']}",
            f"detailed_description:\n{dd}",
            f"overall_soundscape:\n{parts['overall_soundscape']}",
            f"non_diegetic_music:\n{parts['non_diegetic_music']}",
        ]
    )
    errors = lint_h3_prompt(text, parts["expect"])
    assert any(e.code == "missing_section" and e.severity == "error" for e in errors)


# -- Lint: rule 2, timestamp_order ----------------------------------------


def test_lint_timestamp_order_flags_shot1_with_timestamp() -> None:
    text, expect = _known_good_doc()
    mutated = text.replace("[Shot 1] Cinematic", "[Shot 1] At 00:00.000, Cinematic", 1)
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "timestamp_order" and e.severity == "error" for e in errors)


def test_lint_timestamp_order_flags_out_of_tolerance() -> None:
    text, expect = _known_good_doc()
    # prescribed shot-2 start is 00:08.000; 00:09.500 is 1.5s off (> 0.5s).
    mutated = text.replace("At 00:08.000,", "At 00:09.500,", 1)
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "timestamp_order" and e.severity == "error" for e in errors)


def test_lint_timestamp_order_boundary_at_prescribed_plus_half_second_ok() -> None:
    text, expect = _known_good_doc()
    # exactly prescribed (8.0s) + 0.5s tolerance -> must NOT be flagged.
    mutated = text.replace("At 00:08.000,", "At 00:08.500,", 1)
    errors = lint_h3_prompt(mutated, expect)
    assert not any(e.code == "timestamp_order" for e in errors)


# -- Lint: rule 3, unknown_label -------------------------------------------


def test_lint_unknown_label_flags_undeclared_subject() -> None:
    text, expect = _known_good_doc()
    mutated = text.replace(
        "the threshold between kitchen and hallway.",
        "the threshold between kitchen and hallway, where <Subject 9> "
        "watches silently from the shadows.",
        1,
    )
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "unknown_label" and e.severity == "error" for e in errors)


# -- Lint: rule 4, dialog_missing / dialog_mutated / dialog_invented -------


def test_lint_dialog_missing_when_line_dropped() -> None:
    text, expect = _known_good_doc()
    mutated = text.replace(
        "Grandma Rose (S1) smiles warmly down at the dog and says, "
        "<d>[English] Hello, old girl.</d> Her voice carries the same "
        "warmth as the kettle's rising steam, and the dog's tail thumps "
        "twice against the worn floorboards in reply.",
        "",
        1,
    )
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "dialog_missing" and e.severity == "error" for e in errors)


def test_lint_dialog_mutated_when_text_altered() -> None:
    text, expect = _known_good_doc()
    mutated = text.replace(
        "<d>[English] Hello, old girl.</d>",
        "<d>[English] Hello there, old girl!</d>",
        1,
    )
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "dialog_mutated" and e.severity == "error" for e in errors)


def test_lint_dialog_invented_when_extra_line_added() -> None:
    text, expect = _known_good_doc()
    mutated = text.replace(
        "the threshold between kitchen and hallway.",
        "the threshold between kitchen and hallway. The mail carrier "
        "(S3) calls out from the porch steps, "
        "<d>[English] Special delivery!</d>",
        1,
    )
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "dialog_invented" and e.severity == "error" for e in errors)


# -- Lint: rule 5, camera_vocab ---------------------------------------------


def test_lint_camera_vocab_flags_contradictory_motion_in_same_shot() -> None:
    text, expect = _known_good_doc()
    # Insert the contradictory phrase inside Shot 1's own segment (which
    # carries "pushes in" from beat 0's camera) -- the check is per-shot,
    # so a contradiction in a different shot's segment wouldn't fire.
    mutated = text.replace(
        "against the worn floorboards in reply.",
        "against the worn floorboards in reply. Moments later the camera "
        "pulls out sharply to reveal the whole kitchen.",
        1,
    )
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "camera_vocab" and e.severity == "warning" for e in errors)


# -- Lint: rule 6, word_count (boundaries: 149/150/350/500) -----------------


# -- Lint: camera_vocab cross-checked against beat camera data (Task 4's
# controller ruling: build_expectations(..., beats=...) populates
# shot_camera_phrases; camera_vocab warns when a shot's text uses a
# canonical motion phrase not among that shot's beats' expected motions
# AND none of the expected motions appear at all -- conservative). --------


def test_lint_camera_vocab_flags_motion_not_among_beat_expected_phrases() -> None:
    parts = _fixture_parts()
    beats = _beats()
    refplan = assign_reference_labels(_subjects(), _scene())
    speakers = assign_speakers(beats, _subjects(), refplan)
    timeline = compute_timeline(beats, 12.0, "ref2va", refplan)
    expect = build_expectations(refplan, speakers, timeline, "ref2va", beats=beats)

    # Shot 3's only beat has camera_motion="static" (expected phrase "holds
    # a static shot"); replace that phrase with an unrelated one ("pans
    # right") so no expected phrase remains in the shot's text at all.
    dd = (
        _SHOT1
        + "\n"
        + _SHOT2
        + "\n"
        + _SHOT3.replace(
            "The camera holds a static shot as she reaches",
            "The camera pans right as she reaches",
            1,
        )
    )
    text = _assemble_doc(parts, dd)
    errors = lint_h3_prompt(text, expect)
    # Contradicting an explicitly-set beat camera is a hard error (spec
    # §3.3 rule 5) -- distinct from the internal opposite-pair check above,
    # which stays a "warning".
    assert any(
        e.code == "camera_vocab" and e.severity == "error" and "Shot 3" in e.message
        for e in errors
    )


def test_lint_camera_vocab_does_not_flag_when_expected_phrase_still_present() -> None:
    parts = _fixture_parts()
    beats = _beats()
    refplan = assign_reference_labels(_subjects(), _scene())
    speakers = assign_speakers(beats, _subjects(), refplan)
    timeline = compute_timeline(beats, 12.0, "ref2va", refplan)
    expect = build_expectations(refplan, speakers, timeline, "ref2va", beats=beats)

    # Shot 1's expected phrase ("pushes in") is still present alongside an
    # unrelated mention -- the conservative rule only fires when NONE of
    # the shot's expected phrases appear, so this must stay clean.
    dd = (
        _SHOT1.replace(
            "against the worn floorboards in reply.",
            "against the worn floorboards in reply. The camera also pans "
            "right briefly.",
            1,
        )
        + "\n"
        + _SHOT2
        + "\n"
        + _SHOT3
    )
    text = _assemble_doc(parts, dd)
    errors = lint_h3_prompt(text, expect)
    assert not any(e.code == "camera_vocab" for e in errors)


def test_lint_word_count_warning_below_floor() -> None:
    parts = _fixture_parts()
    dd = _filler_words(149)
    text = _assemble_doc(parts, dd)
    errors = [
        e for e in lint_h3_prompt(text, parts["expect"]) if e.code == "word_count"
    ]
    assert len(errors) == 1
    # Below-floor is advisory only: the description is rendered
    # deterministically from the beat script, so a terse script
    # legitimately compiles short -- it must not be a hard error.
    assert errors[0].severity == "warning"


def test_lint_word_count_warning_at_150() -> None:
    parts = _fixture_parts()
    dd = _filler_words(150)
    text = _assemble_doc(parts, dd)
    errors = [
        e for e in lint_h3_prompt(text, parts["expect"]) if e.code == "word_count"
    ]
    assert len(errors) == 1
    assert errors[0].severity == "warning"


def test_lint_word_count_ok_at_350() -> None:
    parts = _fixture_parts()
    dd = _filler_words(350)
    text = _assemble_doc(parts, dd)
    errors = [
        e for e in lint_h3_prompt(text, parts["expect"]) if e.code == "word_count"
    ]
    assert errors == []


def test_lint_word_count_ok_at_500() -> None:
    parts = _fixture_parts()
    dd = _filler_words(500)
    text = _assemble_doc(parts, dd)
    errors = [
        e for e in lint_h3_prompt(text, parts["expect"]) if e.code == "word_count"
    ]
    assert errors == []


# -- Lint: rule 7, retention_marker / soundscape_missing / music_missing ----


def test_lint_retention_marker_flags_unknown_value() -> None:
    text, expect = _known_good_doc()
    mutated = text.replace(
        "fully_preserved - a kind elderly woman",
        "kinda_preserved - a kind elderly woman",
        1,
    )
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "retention_marker" and e.severity == "error" for e in errors)


def test_lint_accepts_audio_reference_marker_but_rejects_it_on_subject_lines() -> None:
    """ref-guide §4.2: <Audio N> retention lines use their own marker
    vocabulary (fully_copy/partially_copy/reference/weak_reference),
    distinct from the §4.1 set that applies to <Subject N>/<Picture N>
    lines -- a merged marker set would wrongly accept "fully_copy" on a
    Subject line, which this also checks for."""
    parts = _fixture_parts()
    dd = _SHOT1 + "\n" + _SHOT2 + "\n" + _SHOT3

    audio_line = (
        "<Audio 1>: reference - the target speaker follows <Audio 1>'s "
        "voice timbre and delivery without copying the original signal."
    )
    parts_with_audio = dict(parts)
    parts_with_audio["retention_analysis"] = "\n".join(
        [parts["retention_analysis"], audio_line]
    )
    text = _assemble_doc(parts_with_audio, dd)
    errors = lint_h3_prompt(text, parts["expect"])
    assert not any(e.code == "retention_marker" for e in errors)

    bad_line = "<Subject 1>: fully_copy - reused from the reference verbatim."
    parts_with_bad = dict(parts)
    parts_with_bad["retention_analysis"] = "\n".join(
        [parts["retention_analysis"], bad_line]
    )
    bad_text = _assemble_doc(parts_with_bad, dd)
    bad_errors = lint_h3_prompt(bad_text, parts["expect"])
    assert any(
        e.code == "retention_marker" and e.severity == "error" for e in bad_errors
    )


def test_lint_soundscape_missing_when_empty() -> None:
    text, expect = _known_good_doc()
    mutated = text.replace(_SOUNDSCAPE, "", 1)
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "soundscape_missing" and e.severity == "error" for e in errors)


def test_lint_music_missing_when_empty() -> None:
    text, expect = _known_good_doc()
    mutated = text.replace(_MUSIC, "", 1)
    errors = lint_h3_prompt(mutated, expect)
    assert any(e.code == "music_missing" and e.severity == "error" for e in errors)


def test_render_detailed_description_substitutes_subject_labels():
    # Roster-name mentions in beat action/sound prose must carry their
    # <Subject N> labels (bare-label substitution, article folded in).
    subs = [
        {"id": 1, "name": "Business Woman", "description": "d", "sort_order": 0},
        {"id": 2, "name": "Young Escort", "description": "d", "sort_order": 1},
    ]
    scene = {"name": "Hotel Room", "setting": "s"}
    plan = assign_reference_labels(subs, scene)
    beats = [
        {
            "duration_s": 4,
            "action": "The Young Escort waves at the Business Woman's reflection",
            "is_cut": 0,
            "dialog": [],
        },
    ]
    speakers = assign_speakers(beats, subs, plan)
    tl = compute_timeline(beats, 4.0, "ref2va", plan)
    dd = render_detailed_description("cinematic", beats, tl, speakers, subs, plan)
    assert "<Subject 2> waves at <Subject 1>'s reflection" in dd
    assert "Young Escort" not in dd and "Business Woman" not in dd


# -- Environment subject is emitted only for referenced scenes -----------


def _scene_no_ref() -> Dict[str, Any]:
    return {
        "id": 1,
        "name": "Kitchen",
        "setting": "a sunlit farmhouse kitchen with a wooden table",
        "location": "Farmhouse",
        "reference_path": None,
    }


def test_environment_label_none_without_scene_reference() -> None:
    refplan = assign_reference_labels(_subjects(), _scene_no_ref())
    assert refplan.environment_label is None
    # Picture numbering is unaffected: two subject refs, then the keyframe.
    assert refplan.keyframe_picture_label == "Picture 3"


def test_definitions_and_retention_omit_unreferenced_environment() -> None:
    subjects = _subjects()
    scene = _scene_no_ref()
    refplan = assign_reference_labels(subjects, scene)

    text = render_subject_definitions(refplan, subjects, scene)
    assert "environment" not in text
    assert "<Subject 3>" not in text

    timeline = compute_timeline(_beats(), 6.0, "ref2va", refplan)
    retention = render_retention_analysis(refplan, subjects, scene, timeline)
    assert "environment" not in retention
    assert "<Subject 3>" not in retention


def test_summary_names_scene_in_prose_without_environment_subject() -> None:
    subjects = _subjects()
    refplan = assign_reference_labels(subjects, _scene_no_ref())
    summary = render_summary(
        refplan,
        {"action": "share a quiet morning together"},
        subjects,
        "ref2va",
        _scene_no_ref(),
    )
    assert (
        "The target video shows <Subject 1> and <Subject 2> in the Kitchen "
        "setting: share a quiet morning together." in summary
    )
    assert "<Subject 3>" not in summary


def test_expectations_exclude_skipped_environment_label() -> None:
    subjects = _subjects()
    scene = _scene_no_ref()
    refplan = assign_reference_labels(subjects, scene)
    beats = _beats()
    timeline = compute_timeline(beats, 6.0, "ref2va", refplan)
    speakers = assign_speakers(beats, subjects, refplan)
    expectations = build_expectations(refplan, speakers, timeline, "ref2va", beats)
    assert "Subject 3" not in expectations.subject_labels
    assert "Subject 1" in expectations.subject_labels
