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
    assemble,
    assign_reference_labels,
    assign_speakers,
    build_scaffold,
    compute_timeline,
    format_timecode,
    render_camera,
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


def test_timeline_rescales_groups_and_formats() -> None:
    beats = _beats()
    refplan = RefPlan({}, "Subject 1", [], "Picture 1")
    timeline = compute_timeline(beats, 12.0, "ref2va", refplan)

    assert [(s.number, s.start_s, s.beat_indices) for s in timeline.shots] == [
        (1, 0.0, [0, 1]),
        (2, 8.0, [2]),
    ]
    assert timeline.duration_s == 12.0
    assert timeline.alignment_line is None
    assert format_timecode(timeline.shots[1].start_s) == "00:08.000"
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
    refplan2 = assign_reference_labels([], scene_no_setting)
    text2 = render_subject_definitions(refplan2, [], scene_no_setting)
    assert "<Subject 1> is the Kitchen environment: Farmhouse." in text2
    assert "in <Picture" not in text2  # no scene reference_path -> no citation


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
        "<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - "
        "a kind elderly woman with silver hair, wearing a floral apron."
    ) in text
    assert "<Subject 3> (appears in [Shot 1], [Shot 2]): fully_preserved -" in text
    assert (
        "<Picture 4> ([Shot 1] first frame): fully_preserved - "
        "the shot begins from this frame."
    ) in text

    timeline_fl2va = compute_timeline(beats, 12.0, "fl2va", refplan)
    text_fl = render_retention_analysis(refplan, subjects, scene, timeline_fl2va)
    assert (
        "<Picture 5> ([Shot 2] last frame): fully_preserved - "
        "the shot ends on this frame."
    ) in text_fl

    timeline_ref2va = compute_timeline(beats, 12.0, "ref2va", refplan)
    text_none = render_retention_analysis(refplan, subjects, scene, timeline_ref2va)
    assert "Picture 4" not in text_none
    assert "Picture 5" not in text_none


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
    assert "[Shot 2] At 00:08.000, cut." in scaffold
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
