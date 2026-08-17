"""tests/test_storyboard_brief.py"""

import pytest

from metascan.core.storyboard_brief import (
    SUPPORTED_ASPECT_RATIOS,
    beat_seed,
    bucket_dims,
    compose_brief,
    storyboard_slug,
)


@pytest.mark.parametrize(
    "ar,expected",
    [
        ("1:1", (1024, 1024)),
        ("4:3", (1216, 832)),
        ("16:9", (1344, 768)),
        ("2.39:1", (1344, 768)),
        ("9:16", (768, 1344)),
    ],
)
def test_bucket_dims_sdxl(ar, expected):
    assert bucket_dims(ar, "sd") == expected
    assert bucket_dims(ar, "pony") == expected


@pytest.mark.parametrize(
    "ar,expected",
    [
        ("1:1", (1024, 1024)),
        ("4:3", (1184, 880)),
        ("16:9", (1360, 768)),
        ("2.39:1", (1584, 656)),
        ("9:16", (768, 1360)),
    ],
)
def test_bucket_dims_flux_family(ar, expected):
    for target in ("flux1", "flux2", "zimage", "chroma", "qwen"):
        assert bucket_dims(ar, target) == expected


def test_bucket_dims_multiple_of_16():
    for ar in SUPPORTED_ASPECT_RATIOS:
        w, h = bucket_dims(ar, "flux1")
        assert w % 16 == 0 and h % 16 == 0


def test_bucket_dims_rejects_unknown_ratio():
    with pytest.raises(ValueError, match="21:9"):
        bucket_dims("21:9", "sd")


def test_beat_seed_deterministic_and_collision_free():
    assert beat_seed(1000, 0, 0, 0) == 1000
    assert beat_seed(1000, 2, 3, 7) == 1000 + (2 * 100 + 3) * 1000 + 7
    # distinct (panel, beat, variant) triples never collide in-range
    seen = set()
    for p in range(3):
        for b in range(4):
            for v in range(5):
                s = beat_seed(0, p, b, v)
                assert s not in seen
                seen.add(s)


def test_storyboard_slug():
    assert storyboard_slug(7, "Salvage Yard Sequence!") == "7-salvage-yard-sequence"
    assert storyboard_slug(9, "***") == "9-storyboard"


def test_compose_brief_reads_framing_from_beat():
    sb = {"aspect_ratio": "16:9"}
    scene = {"setting": "a dim bar", "mood": "tense"}
    panel = {"action": "The standoff"}
    beat = {
        "action": "She reaches for the glass",
        "shot_size": "CU",
        "angle": "low",
        "lens": "tele",
    }
    subjects = [{"name": "Mara", "description": "a tired detective"}]
    brief = compose_brief(sb, scene, panel, beat, subjects)
    assert "close-up" in brief and "low angle" in brief and "telephoto" in brief
    assert "She reaches for the glass" in brief
    assert "SHOT CONTEXT: The standoff" in brief
    assert "Mara" in brief


def test_compose_brief_full():
    storyboard = {"aspect_ratio": "2.39:1", "style_block": "graphite sketch"}
    scene = {
        "setting": "derelict orbital shipbreaking yard, zero-g debris",
        "location": "salvage yard, twisted hulls",
        "time_of_day": "dusk",
        "mood": "tense",
        "lighting": "amber haze, long shadows",
    }
    panel = {"action": "her hand rests on the hull seam"}
    beat = {
        "shot_size": "ECU",
        "angle": "eye",
        "lens": None,
        "action": "she examines the seam",
    }
    subjects = [{"name": "Maya", "description": "late 20s, shaved head, red scarf"}]
    brief = compose_brief(storyboard, scene, panel, beat, subjects)
    assert brief.splitlines() == [
        "SHOT: extreme close-up, eye level, 2.39:1",
        "SUBJECT Maya: late 20s, shaved head, red scarf",
        "ACTION: she examines the seam",
        "SHOT CONTEXT: her hand rests on the hull seam",
        "SETTING: derelict orbital shipbreaking yard, zero-g debris",
        "LOCATION: salvage yard, twisted hulls",
        "LIGHT/MOOD: dusk, amber haze, long shadows, tense",
    ]
    assert "graphite" not in brief  # style is concatenated post-synthesis, never here


def test_compose_brief_omits_empty_lines():
    brief = compose_brief(
        {"aspect_ratio": "1:1", "style_block": None},
        {"location": None, "time_of_day": None, "mood": None, "lighting": None},
        {"action": None},
        {"shot_size": None, "angle": None, "lens": None, "action": "a cat"},
        [],
    )
    assert brief == "SHOT: 1:1\nACTION: a cat"
