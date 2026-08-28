"""Spec Phase A1: per-beat retention analysis in the H3 compiler."""

from typing import Any, Dict, List

from metascan.core.h3_compiler import (
    assign_reference_labels,
    compute_timeline,
    render_retention_analysis,
)


def _subjects() -> List[Dict[str, Any]]:
    return [
        {
            "id": 7,
            "name": "Grandma Rose",
            "description": "a kind elderly woman with silver hair",
            "voice": "warm, elderly voice",
            "reference_path": "/refs/grandma1.png",
            "reference_path_2": None,
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
        {
            "id": 11,
            "name": "Narrator",
            "description": "an unseen radio announcer",
            "voice": "crackly voice",
            "reference_path": None,
            "reference_path_2": None,
            "sort_order": 2,
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
        {"duration_s": 2.0, "subject_ids": [7, 9], "dialog": [], "is_cut": 0},
        {"duration_s": 2.0, "subject_ids": [7], "dialog": [], "is_cut": 1},
        {
            "duration_s": 2.0,
            "subject_ids": [9],
            "dialog": [
                {"subject_id": 11, "text": "Good evening.", "language": "English"}
            ],
            "is_cut": 1,
        },
    ]


def test_retention_analysis_is_per_beat_when_beats_given() -> None:
    """A subject's (appears in ...) list follows the beats that actually
    cast it, not the whole panel. Dialog-only speakers fall back to the
    beats whose dialog names them; a subject cast nowhere keeps the whole
    list; the environment keeps the whole list."""
    subjects = _subjects()
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)
    beats = _beats()
    timeline = compute_timeline(beats, 6.0, "ref2va", refplan)
    text = render_retention_analysis(refplan, subjects, scene, timeline, beats=beats)
    lines = text.split("\n")
    assert lines[0].startswith("<Subject 1> (appears in [Shot 1], [Shot 2]):")
    assert lines[1].startswith("<Subject 2> (appears in [Shot 1], [Shot 3]):")
    assert lines[2].startswith("<Subject 3> (appears in [Shot 3]):")
    assert "<Subject 4> (appears in [Shot 1], [Shot 2], [Shot 3])" in lines[3]

    old = render_retention_analysis(refplan, subjects, scene, timeline)
    assert "<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3])" in old

    none_cast = [dict(b, subject_ids=[], dialog=[]) for b in beats]
    text2 = render_retention_analysis(
        refplan, subjects, scene, timeline, beats=none_cast
    )
    assert "<Subject 1> (appears in [Shot 1], [Shot 2], [Shot 3])" in text2


def test_retention_analysis_pov_ignores_per_beat_casting() -> None:
    subjects = _subjects()
    subjects[0]["pov_ref"] = 1
    scene = _scene()
    refplan = assign_reference_labels(subjects, scene)
    beats = _beats()
    timeline = compute_timeline(beats, 6.0, "ref2va", refplan, pov=True)
    text = render_retention_analysis(refplan, subjects, scene, timeline, beats=beats)
    assert "<Subject 2> (appears in [Shot 1]):" in text
