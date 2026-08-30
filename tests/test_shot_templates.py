"""Spec Phase E: shot-list templates -- pure module + runner + API."""

import asyncio
import copy
import json
import re

import pytest
from fastapi.testclient import TestClient

from backend.api import storyboard as storyboard_api
from backend.main import create_app
from metascan.core import shot_templates as t
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.storyboard_runner import StoryboardError, StoryboardRunner

PILOT = "two_party_negotiation_18"


def _pilot():
    return t.get_template(PILOT)


# ---- loading / validation ---------------------------------------------------


def test_pilot_template_loads_and_matches_spec_table():
    tp = _pilot()
    assert tp.function == "negotiation"
    assert [r.id for r in tp.roles] == ["A", "B"]
    assert tp.slot_count == 18
    assert tp.duration_s == 63.5
    assert [s.duration_s for s in tp.sections] == [13.0, 12.5, 12.0, 14.5, 11.5]
    slots = [sl for s in tp.sections for sl in s.slots]
    assert [sl.beat_index for sl in slots] == list(range(18))
    # Dialogue on 11 of 18 slots, five reactions, no close-up before slot 8.
    assert sum(1 for sl in slots if sl.dialog_slot) == 11
    assert sum(1 for sl in slots if sl.kind == "reaction") == 5
    assert all(sl.camera.shot_size not in ("CU", "ECU") for sl in slots[:7])
    assert slots[7].camera.shot_size == "CU"
    # Speaker balance: no side holds more than 70%.
    speakers = [sl.dialog_slot for sl in slots if sl.dialog_slot]
    assert speakers.count("A") == 5 and speakers.count("B") == 6
    # First slot of each section is never a cut; the rest are.
    for s in tp.sections:
        assert not s.slots[0].is_cut
        assert all(sl.is_cut for sl in s.slots[1:])
    # Duration range 1.5-6.0.
    assert min(sl.duration_s for sl in slots) == 1.5
    assert max(sl.duration_s for sl in slots) == 6.0


def _raw_pilot():
    return json.loads((t.TEMPLATES_DIR / f"{PILOT}.json").read_text())


@pytest.mark.parametrize(
    "mutate, needle",
    [
        (
            lambda d: d["sections"][0]["slots"][0]["camera"].update(shot_size="WIDE"),
            "shot_size",
        ),
        (
            lambda d: d["sections"][0]["slots"][0]["camera"].update(
                composition="THIRDS_L"
            ),
            "composition",
        ),
        (
            lambda d: d["sections"][0]["slots"][0]["camera"].update(
                camera_motion="STATIC"
            ),
            "camera_motion",
        ),
        (lambda d: d["sections"][0]["slots"][1].update(cast=["C"]), "unknown role"),
        (
            lambda d: d["sections"][0]["slots"][1].update(dialog_slot="Z"),
            "unknown role",
        ),
        (lambda d: d["sections"][0]["slots"][0].update(is_cut=True), "never a cut"),
        (lambda d: d["sections"][0].update(duration_s=99.0), "sum of slot"),
        (lambda d: d["sections"][1]["slots"][0].update(beat_index=0), "beat_index"),
        (lambda d: d.update(function="chase"), "function"),
        (lambda d: d["sections"][0]["slots"][0].update(kind="hold"), "kind"),
    ],
)
def test_parse_template_fails_loudly_on_vocabulary_mismatch(mutate, needle):
    data = _raw_pilot()
    mutate(data)
    with pytest.raises(t.TemplateError) as exc:
        t.parse_template(data)
    assert needle in str(exc.value)


def test_load_templates_from_directory_and_unknown_id(tmp_path):
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(t.TemplateError) as exc:
        t.load_templates(tmp_path)
    assert "bad.json" in str(exc.value)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert t.load_templates(empty) == {}
    with pytest.raises(t.TemplateError) as exc:
        t.get_template("nope", empty)
    assert "unknown template" in str(exc.value)
    assert [s["id"] for s in t.list_templates()] == [PILOT]
    summary = t.summarize(_pilot())
    assert summary["slot_count"] == 18 and len(summary["sections"]) == 5


# ---- grammars ----------------------------------------------------------------


def test_role_bind_grammar_restricts_names():
    g = t.role_bind_grammar(_pilot(), ["Party Girl", "Friend"])
    assert g.startswith('root ::= "{"')
    assert 'name ::= "\\"Party Girl\\"" | "\\"Friend\\""' in g
    assert r"\-" not in g and "{{" not in g
    with pytest.raises(t.TemplateError):
        t.role_bind_grammar(_pilot(), [])


def test_fill_grammar_bakes_slot_count_and_dialog_nullability():
    sec = _pilot().sections[0]  # WS (silent), MS-A, MS-B
    g = t.fill_grammar(sec)
    assert g.startswith('root ::= "[" ws slot0 ws "," ws slot1 ws "," ws slot2 ws "]"')
    lines = {ln.split(" ::= ")[0]: ln for ln in g.splitlines()}
    assert lines["slot0"].endswith('"\\"dialog\\"" ws ":" ws "null" ws "}"')
    assert lines["slot1"].endswith('"\\"dialog\\"" ws ":" ws string ws "}"')
    assert "{{" not in g and r"\-" not in g


# ---- bind / instantiate / fill / conform --------------------------------------

ROSTER = {"party girl": 30, "friend": 31}


def test_validate_role_bind_response():
    tp = _pilot()
    assert t.validate_role_bind_response(
        json.dumps({"A": "Party Girl", "B": "Friend"}), tp, ROSTER
    ) == {"A": 30, "B": 31}
    with pytest.raises(t.TemplateError, match="unknown character"):
        t.validate_role_bind_response(
            json.dumps({"A": "Party Girl", "B": "Bob"}), tp, ROSTER
        )
    with pytest.raises(t.TemplateError, match="more than one role"):
        t.validate_role_bind_response(
            json.dumps({"A": "Party Girl", "B": "party girl"}), tp, ROSTER
        )


def _fill_for(section):
    out = []
    for i, sl in enumerate(section.slots):
        out.append(
            {
                "action": f"{sl.kind} action {i}: her hands tighten",
                "reveals": f"reveal {i}",
                "emotional_intent": "jaw set",
                "sound": None if i % 2 else "wind",
                "dialog": f"Line for slot {i}." if sl.dialog_slot else None,
            }
        )
    return out


def test_instantiate_fill_and_conform_round_trip():
    tp = _pilot()
    role_map = {"A": 30, "B": 31}
    panels = t.instantiate(tp, role_map)
    assert len(panels) == 5
    assert [len(p["beats"]) for p in panels] == [3, 4, 3, 4, 4]
    b = panels[0]["beats"][0]
    assert b["subject_ids"] == [30, 31] and b["kind"] == "establishing"
    assert b["shot_size"] == "WS" and b["composition"] == "deep_staging"
    assert b["camera_motion"] == "static" and b["camera_amplitude"] is None
    assert panels[2]["beats"][0]["camera_amplitude"] == "small"  # push-in CU
    assert panels[1]["beats"][3]["angle"] == "ots"

    # Unfilled panels do not conform (empty actions / unfilled dialog).
    with pytest.raises(t.TemplateConformanceError):
        t.conform(tp, panels, role_map)

    for sec, panel in zip(tp.sections, panels):
        fill = t.validate_fill_response(json.dumps(_fill_for(sec)), sec)
        t.apply_fill(panel, sec, fill, role_map, {31: "bright voice"})
    t.conform(tp, panels, role_map)  # no raise

    beat = panels[0]["beats"][1]  # MS-A, dialog A
    assert beat["dialog"] == [
        {
            "subject_id": 30,
            "voice": None,
            "delivery": None,
            "language": "English",
            "text": "Line for slot 1.",
        }
    ]
    assert panels[0]["beats"][2]["dialog"][0]["voice"] == "bright voice"
    assert panels[0]["beats"][0]["dialog"] == []
    assert panels[0]["action"].startswith("establishing action 0")


def test_validate_fill_response_enforces_shape():
    sec = _pilot().sections[0]
    good = _fill_for(sec)
    with pytest.raises(t.TemplateError, match="has 2 slots"):
        t.validate_fill_response(json.dumps(good[:2]), sec)
    bad = copy.deepcopy(good)
    bad[1]["dialog"] = ""
    with pytest.raises(t.TemplateError, match="requires a spoken line"):
        t.validate_fill_response(json.dumps(bad), sec)
    bad = copy.deepcopy(good)
    bad[0]["action"] = " "
    with pytest.raises(t.TemplateError, match="empty action"):
        t.validate_fill_response(json.dumps(bad), sec)
    # A silent slot's stray dialog is dropped, not an error.
    stray = copy.deepcopy(good)
    stray[0]["dialog"] = "psst"
    assert t.validate_fill_response(json.dumps(stray), sec)[0]["dialog"] is None


def test_conform_reports_every_miss():
    tp = _pilot()
    role_map = {"A": 30, "B": 31}
    panels = t.instantiate(tp, role_map)
    for sec, panel in zip(tp.sections, panels):
        t.apply_fill(panel, sec, _fill_for(sec), role_map)
    panels[0]["beats"][0]["shot_size"] = "MS"
    panels[1]["beats"][1]["dialog"] = [{"subject_id": 31, "text": "x"}]
    panels[2]["beats"][0]["subject_ids"] = [30]
    panels[3]["duration_s"] = 1.0
    with pytest.raises(t.TemplateConformanceError) as exc:
        t.conform(tp, panels, role_map)
    msg = str(exc.value)
    assert "slot 1: shot_size 'MS' != 'WS'" in msg
    assert "slot 5: silent slot carries dialog" in msg
    assert "slot 8: subject_ids [30] != [31]" in msg
    assert "section 4 (The concession): duration_s 1.0" in msg


def test_prompts_carry_slot_specs_and_previous_sections():
    tp = _pilot()
    names = {"A": "Party Girl", "B": "Friend"}
    scene = {"name": "Lawn", "setting": "a college lawn", "notes": None}
    subjects = [
        {"name": "Party Girl", "description": "d1"},
        {"name": "Friend", "description": "d2"},
    ]
    p0 = t.build_fill_user_prompt(
        tp, tp.sections[0], names, scene, subjects, {"logline": "L"}
    )
    assert "first section" in p0
    assert (
        "Slot 1 — establishing, 5.0s, continuous; on screen: Party Girl, Friend; speaks: nobody speaks"
        in p0
    )
    assert "Slot 2 — action, 3.0s, cut; on screen: Party Girl; speaks: Party Girl" in p0
    assert "WS, eye, wide lens, deep staging, static" in p0
    prev = [(tp.sections[0], _fill_for(tp.sections[0]))]
    p1 = t.build_fill_user_prompt(tp, tp.sections[1], names, scene, subjects, {}, prev)
    assert "[The pitch]" in p1 and 'Party Girl: "Line for slot 1."' in p1
    assert "Sections still to come" in p1
    bind = t.build_role_bind_user_prompt(tp, scene, subjects, {"logline": "L"})
    assert "- A (left of frame): initiator" in bind and "- Friend: d2" in bind


def test_template_prompts_carry_brief_and_arc():
    tp = t.get_template(PILOT)
    outline = {"logline": "L", "arc": [{"beat": "turn", "summary": "T"}]}
    scene = {
        "name": "S",
        "arc_beats": ["turn"],
        "brief": "B must break.",
        "setting": "x",
    }
    subjects = [
        {"id": 1, "name": "A", "description": "d"},
        {"id": 2, "name": "B", "description": "d"},
    ]
    bind = t.build_role_bind_user_prompt(tp, scene, subjects, outline)
    fill = t.build_fill_user_prompt(
        tp, tp.sections[0], {"A": "A", "B": "B"}, scene, subjects, outline
    )
    for p in (bind, fill):
        assert "Scene brief: B must break." in p
        assert "Arc covered by this scene:\n- turn: T" in p


# ---- runner ------------------------------------------------------------------


class FakeVlm:
    model_id = "qwen3vl-30b-a3b"

    def __init__(self, bind=None):
        self.bind = bind or {"A": "Party Girl", "B": "Friend"}
        self.calls = []

    async def ensure_started(self, model_id):
        pass

    async def generate_text(self, *, system_prompt, user_prompt, grammar=None, **kw):
        self.calls.append((system_prompt, user_prompt, grammar))
        if grammar.startswith('root ::= "{"'):
            return json.dumps(self.bind)
        n = len(re.findall(r"^slot\d+ ::=", grammar, re.M))
        # Which slots require dialog: read it off the grammar.
        out = []
        for i in range(n):
            rule = re.search(rf"^slot{i} ::= .*$", grammar, re.M).group(0)
            needs = rule.endswith('ws string ws "}"')
            out.append(
                {
                    "action": f"slot {i}: her hands close",
                    "reveals": "r",
                    "emotional_intent": "jaw set",
                    "sound": None,
                    "dialog": f"line {i}" if needs else None,
                }
            )
        return json.dumps(out)


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(tmp_path / "t.db")


def _board(db):
    sb = db.create_storyboard(name="WG", target_model="sd", architecture="t2i")
    db.update_storyboard(
        sb,
        source_text="premise",
        outline=json.dumps(
            {"logline": "Three friends go to a cabin.", "tone": "tense"}
        ),
    )
    db.create_subject(
        sb, name="Party Girl", description="a woman", sort_order=0, voice="bright"
    )
    db.create_subject(sb, name="Friend", description="a woman", sort_order=1)
    db.create_subject(
        sb, name="College", description="a lawn", sort_order=2, subject_type="location"
    )
    scene = db.create_scene(sb, name="Lawn Pitch", setting="a college lawn")
    return sb, scene


def _shots_vlm():
    """Template FakeVlm that also answers the free-form shots/beats grammars."""
    from metascan.core import storyboard_story as story

    class Both(FakeVlm):
        async def generate_text(
            self, *, system_prompt, user_prompt, grammar=None, **kw
        ):
            if grammar == story.SHOTS_GRAMMAR:
                self.calls.append((system_prompt, user_prompt, grammar))
                return json.dumps(
                    [
                        {
                            "action": "free",
                            "duration_s": 8,
                            "subtext": "s",
                            "is_turn": False,
                        }
                    ]
                )
            if grammar == story.BEATS_GRAMMAR:
                self.calls.append((system_prompt, user_prompt, grammar))
                return json.dumps(
                    [
                        {
                            "duration_s": 8,
                            "action": "b",
                            "reveals": None,
                            "emotional_intent": None,
                            "shot_size": "WS",
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
                        }
                    ]
                )
            return await super().generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                grammar=grammar,
                **kw,
            )

    return Both()


def test_shots_stage_uses_scene_template_and_records_provenance(db, tmp_path):
    sb, scene = _board(db)
    free = db.create_scene(sb, name="Free scene", sort_order=1)
    db.update_scene(scene, template_id=PILOT)
    vlm = _shots_vlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    events = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))
    asyncio.run(runner.compose_story(sb, stages=("shots", "beats")))
    tree = db.get_storyboard_tree(sb)
    templated, freeform = tree["scenes"]
    assert freeform["id"] == free
    assert len(templated["panels"]) == 5
    assert sum(len(p["beats"]) for p in templated["panels"]) == 18
    assert templated["composed_from"]["stage"] == "shots"
    assert templated["composed_from"]["template_id"] == PILOT
    assert len(freeform["panels"]) == 1 and len(freeform["panels"][0]["beats"]) == 1
    assert freeform["composed_from"]["template_id"] is None
    done = [
        d
        for ch, ev, d in events
        if ev == "story_stage_complete" and d["stage"] == "shots"
    ]
    assert done[0]["template_scenes"] == [scene]
    # Beats stage must not have re-run over the template-built scene.
    assert templated["panels"][0]["beats"][0]["action"].startswith("slot 0")


def test_shots_gate_lists_every_selection_problem(db, tmp_path):
    sb, scene = _board(db)
    other = db.create_scene(sb, name="Other", sort_order=1)
    db.update_scene(scene, template_id="nope")
    db.update_scene(other, template_id=PILOT)
    friend = db.get_storyboard_tree(sb)["subjects"][1]["id"]
    db.update_subject(friend, subject_type="prop")
    vlm = FakeVlm()
    runner = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
    )
    with pytest.raises(StoryboardError) as exc:
        asyncio.run(runner.check_compose_gates(sb, ("shots",), None, None, False))
    msg = str(exc.value)
    assert "unknown template 'nope'" in msg and "needs 2 characters" in msg
    assert vlm.calls == []


# ---- API ----------------------------------------------------------------------


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    vlm = FakeVlm()
    made = []

    def make_runner(*args, **kwargs):
        r = StoryboardRunner(
            db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path
        )
        made.append(r)
        return r

    monkeypatch.setattr("backend.dependencies.get_db", lambda: db)
    monkeypatch.setattr("backend.api.storyboard.get_db", lambda: db)
    monkeypatch.setattr("backend.main.StoryboardRunner", make_runner)
    app = create_app()
    with TestClient(app) as c:
        assert made
        yield c, db
    storyboard_api.set_storyboard_runner(None)


def test_templates_endpoint_lists_pilot(client):
    c, db = client
    r = c.get("/api/storyboard/templates")
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [PILOT]
    assert r.json()[0]["slot_count"] == 18
    sb, scene = _board(db)
    # The standalone apply-template route is gone (the shots stage is the
    # single path); the path no longer resolves.
    assert c.post(
        f"/api/storyboard/{sb}/scenes/{scene}/apply-template",
        json={"template_id": PILOT},
    ).status_code in (404, 405)


def test_compose_shots_400_on_template_problem(client):
    c, db = client
    sb = db.create_storyboard(name="B", target_model="sd", architecture="t2i")
    db.update_storyboard(sb, source_text="p", outline="{}")
    db.create_scene(sb, name="S", template_id=PILOT)
    r = c.post(f"/api/storyboard/{sb}/compose", json={"stages": ["shots"]})
    assert r.status_code == 400 and "needs 2 characters" in r.json()["detail"]


# ---- Selection validation (user-selected templates) ----------------------------


def _chars(n):
    return [{"id": i, "name": f"C{i}", "subject_type": "character"} for i in range(n)]


def test_validate_assignment_free_form_is_clean():
    assert t.validate_assignment(None, {"name": "S"}, [], 10.0) == ([], [])


def test_validate_assignment_errors():
    errs, _ = t.validate_assignment("nope", {"name": "S"}, _chars(2), None)
    assert errs == ["Scene 'S': unknown template 'nope'"]
    errs, _ = t.validate_assignment(PILOT, {"name": "S"}, _chars(1), None)
    assert errs == [
        "Scene 'S': template 'two_party_negotiation_18' needs 2 characters, "
        "the storyboard has 1"
    ]


def test_validate_assignment_warnings():
    scene = {"name": "S", "function": "confrontation"}
    errs, warns = t.validate_assignment(PILOT, scene, _chars(2), 18.0)
    assert errs == []
    assert warns == [
        "Scene 'S': template function 'negotiation' differs from the scene's "
        "'confrontation'",
        "Scene 'S': template runs 63.5s but this scene's share of the story "
        "is about 18s",
    ]
    _, warns = t.validate_assignment(
        PILOT, {"name": "S", "function": "negotiation"}, _chars(2), 60.0
    )
    assert warns == []


def test_annotate_tree_marks_problems_and_staleness():
    outline = json.dumps({"duration_target_s": 36, "arc": []})
    h = t.outline_hash(outline)
    tree = {
        "outline": outline,
        "subjects": [{"id": 1, "name": "A", "subject_type": "character"}],
        "scenes": [
            {
                "id": 1,
                "name": "S1",
                "function": "negotiation",
                "template_id": PILOT,
                "composed_from": {"stage": "scenes", "outline_hash": h, "at": "x"},
            },
            {
                "id": 2,
                "name": "S2",
                "function": None,
                "template_id": None,
                "composed_from": {"stage": "scenes", "outline_hash": "old", "at": "x"},
            },
        ],
    }
    t.annotate_tree(tree)
    assert tree["outline_hash"] == h
    s1, s2 = tree["scenes"]
    assert s1["template_problems"] == [
        "Scene 'S1': template 'two_party_negotiation_18' needs 2 characters, "
        "the storyboard has 1"
    ]
    assert s1["template_warnings"] == [
        "Scene 'S1': template runs 63.5s but this scene's share of the story "
        "is about 18s"
    ]
    assert s1["outline_stale"] is False
    assert s2["template_problems"] == [] and s2["template_warnings"] == []
    assert s2["outline_stale"] is True
