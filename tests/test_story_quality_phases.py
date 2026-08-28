"""Spec refactor-spec-visual-story-quality Phases B-D: dialogue lint,
roster hygiene (subject_type), scene function."""

import json

from metascan.core import storyboard_story as story
from metascan.core.database_sqlite import DatabaseManager, infer_subject_type

# ---- C1: subject_type ---------------------------------------------------


def test_infer_subject_type_heuristic():
    assert infer_subject_type("College", "a sun-drenched college lawn") == "location"
    assert infer_subject_type("Cabin", "a weathered timber cabin") == "location"
    assert infer_subject_type("Party Girl", "a woman in a flannel shirt") == "character"
    assert infer_subject_type("Younger Sister", "Late teens female") == "character"
    assert infer_subject_type("The Key", "a rusted iron key") == "prop"
    assert infer_subject_type("Rex", "a scruffy grey terrier") == "character"


def test_castable_subjects_filters_non_characters():
    subs = [
        {"id": 1, "name": "A", "subject_type": "character"},
        {"id": 2, "name": "Cabin", "subject_type": "location"},
        {"id": 3, "name": "Knife", "subject_type": "prop"},
        {"id": 4, "name": "Legacy"},  # column absent -> character
    ]
    assert [s["id"] for s in story.castable_subjects(subs)] == [1, 4]


def test_subject_type_persists_and_migration_infers(tmp_path):
    db = DatabaseManager(tmp_path / "t.db")
    sb = db.create_storyboard(name="B", target_model="sd", architecture="t2i")
    a = db.create_subject(sb, name="Ana", description="a woman", sort_order=0)
    loc = db.create_subject(
        sb, name="Cabin", description="a cabin", subject_type="location"
    )
    tree = db.get_storyboard_tree(sb)
    by_id = {s["id"]: s for s in tree["subjects"]}
    assert by_id[a]["subject_type"] == "character"
    assert by_id[loc]["subject_type"] == "location"
    db.update_subject(a, subject_type="prop")
    assert db.get_subject(a)["subject_type"] == "prop"

    # Migration: a pre-C1 DB (user_version 3) with a location-named row
    # gets inferred on the next open.
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE storyboard_subjects SET subject_type = 'character' " "WHERE id = ?",
            (loc,),
        )
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    db2 = DatabaseManager(tmp_path / "t.db")
    assert db2.get_subject(loc)["subject_type"] == "location"
    with db2._get_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4


# ---- D: scene function ----------------------------------------------------


def test_scenes_grammar_and_validator_carry_function():
    assert '"\\"function\\""' in story.SCENES_GRAMMAR
    for v in story.SCENE_FUNCTION_VALUES:
        assert f'"\\"{v}\\""' in story.SCENES_GRAMMAR
    raw = json.dumps(
        [
            {
                "name": "Lawn",
                "arc_beats": ["setup"],
                "charge_in": 0,
                "charge_out": 1,
                "function": "negotiation",
            },
            {"name": "Road", "arc_beats": [], "function": "bogus"},
        ]
    )
    scenes = story.validate_scenes_response(raw)
    assert scenes[0]["function"] == "negotiation"
    assert scenes[1]["function"] is None


def test_scene_function_round_trips_db(tmp_path):
    db = DatabaseManager(tmp_path / "t.db")
    sb = db.create_storyboard(name="B", target_model="sd", architecture="t2i")
    sid = db.create_scene(sb, name="S", function="reveal")
    assert db.get_scene(sid)["function"] == "reveal"
    db.update_scene(sid, function=None)
    assert db.get_scene(sid)["function"] is None
    ids, _ = db.replace_storyboard_scenes(
        sb, [{"name": "X", "function": "arrival", "arc_beats": []}]
    )
    assert db.get_scene(ids[0])["function"] == "arrival"


# ---- B2: dialogue lint ----------------------------------------------------

_SUBJECTS = [
    {"id": 1, "name": "Ana", "subject_type": "character"},
    {"id": 2, "name": "Bo", "subject_type": "character"},
    {"id": 3, "name": "Cabin", "subject_type": "location"},
]


def _beat(subject_ids, speaker=None):
    return {
        "subject_ids": subject_ids,
        "dialog": (
            [{"subject_id": speaker, "text": "..."}] if speaker is not None else []
        ),
    }


def test_lint_scene_dialogue_only_for_verbal_functions():
    panels = [{"beats": [_beat([1, 2]), _beat([1, 2]), _beat([1, 2])]}]
    assert story.lint_scene_dialogue(panels, _SUBJECTS, "transit") == []
    assert story.lint_scene_dialogue(panels, _SUBJECTS, None) == []
    # Fewer than two characters: silent.
    assert story.lint_scene_dialogue(panels, _SUBJECTS[:1], "negotiation") == []


def test_lint_scene_dialogue_catches_each_rule():
    # 1 of 5 beats has dialogue (20% < 40%); Bo present in 5 beats and
    # never speaks; Ana holds 100% of the (single) line -- share rule only
    # fires with >= 2 lines, so only two warnings here.
    panels = [
        {"beats": [_beat([1, 2], 1), _beat([1, 2]), _beat([1, 2])]},
        {"beats": [_beat([1, 2]), _beat([2])]},
    ]
    warns = story.lint_scene_dialogue(panels, _SUBJECTS, "negotiation")
    assert len(warns) == 2
    assert any("40%" in w and "1 of 5" in w for w in warns)
    assert any(w.startswith("Bo is present in 5 beats") for w in warns)

    # Speaker share: Ana 3 of 3 lines with Bo speaking never but present
    # in only 2 beats (below the presence floor).
    panels = [
        {"beats": [_beat([1], 1), _beat([1, 2], 1), _beat([1, 2], 1)]},
    ]
    warns = story.lint_scene_dialogue(panels, _SUBJECTS, "confession")
    assert len(warns) == 1
    assert warns[0].startswith("Ana holds 3 of 3 lines")


def test_lint_scene_dialogue_happy_path():
    panels = [
        {"beats": [_beat([1, 2]), _beat([1], 1), _beat([2], 2)]},
        {"beats": [_beat([1], 1), _beat([2]), _beat([1, 2], 2)]},
    ]
    assert story.lint_scene_dialogue(panels, _SUBJECTS, "confrontation") == []
