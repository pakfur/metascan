# Compose Cinematic Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Thread a dramatic spine (value charges, one marked turn, subtext), a richer per-beat visual grammar, a pacing model, full bible re-injection, and lint-with-targeted-re-roll enforcement through the existing `outline → scenes → shots → beats` compose pipeline.

**Architecture:** Enrich the four existing stages in place — no new pipeline stage, no new tables. New columns land on `storyboards`/`scenes`/`panels`/`beats` via the `_idempotent_add_column` pattern (no `user_version` bump). Pure lint functions in `storyboard_story.py` drive one targeted re-roll per stage call in `StoryboardRunner._run_stage`; leftover violations become warnings on the `story_stage_complete` WS payload. Beats compose sequentially *within* a scene (for previous-shot context), scenes still in parallel.

**Tech Stack:** Python 3.11 / FastAPI / SQLite / GBNF grammars against llama-server (VLM), Vue 3 + TypeScript + Pinia frontend.

**Spec:** `docs/superpowers/specs/2026-08-20-compose-cinematic-polish-design.md`

## Global Constraints

- Python 3.11.x only; `mypy` strict on `metascan/core/*` — annotate every new function.
- `black` v25.11.0 formatting; run `make quality` before claiming a backend task done.
- GBNF: `\-` is NOT a valid escape — hyphens in character classes must be literal (start or end of class). A bad grammar SIGSEGVs llama-server.
- Never import UI frameworks in `metascan/core/`; `storyboard_story.py` and `storyboard_brief.py` stay pure (no I/O beyond the prompt store).
- All stored JSON-list columns are `TEXT NOT NULL DEFAULT '[]'`; writers `json.dumps`, readers decode with a `[]` fallback.
- System prompts live in `data/meta_prompt.yml` (hot-reloaded via `PromptStore`); grammars are built in Python from enum constants.
- Frontend: `vue-tsc --noEmit` must pass (`cd frontend && npm run build`); detail editors use the local-ref + snapshot resync pattern (see `BeatCard.vue:31-93`).
- Test suite: `make quality test` (full backend gate). Worktree note: run `make VENV_DIR=/home/jk/gws/metascan/venv quality test` when in a git worktree. The known WSL2 flake `test_file_watcher_triggers_reload` failing in a full run is not a regression signal.
- Commit after each task with a conventional-commits message ending in `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Pacing model + new enum vocabularies

**Files:**
- Modify: `metascan/core/storyboard_story.py` (constants near `CAMERA_MOTION_VALUES`, line ~22)
- Test: `tests/test_storyboard_story.py`

**Interfaces:**
- Produces: `PACING_VALUES: tuple`, `COMPOSITION_VALUES: tuple`, `LIGHT_QUALITY_VALUES: tuple`, and `pacing_guidance(pacing: str, shot_cap: float) -> Dict[str, float]` returning keys `panel_min_s`, `panel_max_s`, `shots_min`, `shots_max`, `beats_min`, `beats_max`, `beat_asl_s`. Later tasks (grammars, prompts, runner, brief, API, frontend) consume these names exactly.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_storyboard_story.py`)

```python
def test_pacing_table_and_shot_cap_clamp():
    g = story.pacing_guidance("standard", 15.0)
    assert g["panel_min_s"] == 8.0 and g["panel_max_s"] == 15.0
    assert g["shots_min"] == 2 and g["shots_max"] == 4
    assert g["beats_min"] == 2 and g["beats_max"] == 4
    assert g["beat_asl_s"] == 4.5
    clamped = story.pacing_guidance("contemplative", 8.0)
    assert clamped["panel_max_s"] == 8.0
    assert clamped["panel_min_s"] == 8.0  # min never exceeds max
    assert story.pacing_guidance("bogus", 15.0) == story.pacing_guidance(
        "standard", 15.0
    )


def test_new_vocabularies_match_spec():
    assert story.PACING_VALUES == ("contemplative", "standard", "propulsive")
    assert "negative_space" in story.COMPOSITION_VALUES
    assert len(story.COMPOSITION_VALUES) == 8
    assert story.LIGHT_QUALITY_VALUES == (
        "hard", "soft", "dappled", "practical", "window", "firelight", "ambient",
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_storyboard_story.py::test_pacing_table_and_shot_cap_clamp tests/test_storyboard_story.py::test_new_vocabularies_match_spec -v`
Expected: FAIL with `AttributeError: ... has no attribute 'pacing_guidance'`

- [ ] **Step 3: Implement** — in `storyboard_story.py`, after `ARC_BEAT_VALUES`:

```python
PACING_VALUES = ("contemplative", "standard", "propulsive")
COMPOSITION_VALUES = (
    "thirds_left",
    "thirds_right",
    "centered",
    "symmetrical",
    "negative_space",
    "frame_in_frame",
    "leading_lines",
    "deep_staging",
)
LIGHT_QUALITY_VALUES = (
    "hard",
    "soft",
    "dappled",
    "practical",
    "window",
    "firelight",
    "ambient",
)

# ASL-derived guidance (spec §4). Beats are the film-shot unit (one beat ==
# one H3 [Shot n]); panels are generation containers capped by shot_cap.
_PACING_TABLE: Dict[str, Dict[str, float]] = {
    "contemplative": {
        "panel_min_s": 10.0, "panel_max_s": 15.0,
        "shots_min": 1, "shots_max": 3,
        "beats_min": 1, "beats_max": 3, "beat_asl_s": 7.0,
    },
    "standard": {
        "panel_min_s": 8.0, "panel_max_s": 15.0,
        "shots_min": 2, "shots_max": 4,
        "beats_min": 2, "beats_max": 4, "beat_asl_s": 4.5,
    },
    "propulsive": {
        "panel_min_s": 6.0, "panel_max_s": 12.0,
        "shots_min": 3, "shots_max": 6,
        "beats_min": 3, "beats_max": 5, "beat_asl_s": 2.5,
    },
}


def pacing_guidance(pacing: str, shot_cap: float) -> Dict[str, float]:
    """Per-call prompt numbers for the shots/beats stages. Unknown pacing
    falls back to standard; panel bounds clamp to the video target's cap."""
    row = dict(_PACING_TABLE.get(pacing, _PACING_TABLE["standard"]))
    row["panel_max_s"] = min(row["panel_max_s"], float(shot_cap))
    row["panel_min_s"] = min(row["panel_min_s"], row["panel_max_s"])
    return row
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_storyboard_story.py -v`
Expected: all PASS (including pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add metascan/core/storyboard_story.py tests/test_storyboard_story.py
git commit -m "feat(storyboard): pacing model + composition/light vocabularies"
```

---

### Task 2: DB columns, writers, decoders, updatable sets

**Files:**
- Modify: `metascan/core/database_sqlite.py`
- Test: `tests/test_storyboard_db.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: columns `storyboards.pacing` (TEXT NOT NULL DEFAULT 'standard'), `scenes.arc_beats` (TEXT NOT NULL DEFAULT '[]'), `scenes.charge_in`/`scenes.charge_out` (INTEGER), `panels.is_turn` (INTEGER NOT NULL DEFAULT 0), `panels.subtext` (TEXT), `beats.composition`/`light_quality`/`emotional_intent`/`reveals`/`movement_motivation` (TEXT). Scene rows everywhere return `arc_beats` as a decoded Python list. `replace_storyboard_scenes`/`replace_scene_panels`/`replace_panel_beats` persist the new keys; `update_storyboard`/`update_scene`/`update_panel`/`update_beat` accept them.

- [ ] **Step 1: Write the failing test** (append to `tests/test_storyboard_db.py`, reusing that file's existing `db`/storyboard fixtures — check the top of the file for the fixture names in use and match them)

```python
def test_cinematic_columns_roundtrip(db):
    sb = db.create_storyboard(
        name="C", target_model="sd", architecture="t2i", base_seed=1
    )
    assert db.get_storyboard(sb)["pacing"] == "standard"
    db.update_storyboard(sb, pacing="propulsive")
    assert db.get_storyboard(sb)["pacing"] == "propulsive"

    sids = db.replace_storyboard_scenes(
        sb,
        [
            {
                "name": "S1",
                "arc_beats": ["setup", "turn"],
                "charge_in": 0,
                "charge_out": -4,
            }
        ],
    )
    tree = db.get_storyboard_tree(sb)
    scene = tree["scenes"][0]
    assert scene["arc_beats"] == ["setup", "turn"]
    assert scene["charge_in"] == 0 and scene["charge_out"] == -4
    db.update_scene(sids[0], arc_beats=["setup"], charge_in=1)
    scene = db.get_storyboard_tree(sb)["scenes"][0]
    assert scene["arc_beats"] == ["setup"] and scene["charge_in"] == 1

    pids = db.replace_scene_panels(
        sids[0],
        [{"action": "a", "duration_s": 10.0, "is_turn": 1, "subtext": "hidden"}],
    )
    panel = db.get_panel(pids[0])
    assert panel["is_turn"] == 1 and panel["subtext"] == "hidden"
    db.update_panel(pids[0], is_turn=0, subtext=None)
    panel = db.get_panel(pids[0])
    assert panel["is_turn"] == 0 and panel["subtext"] is None

    bids = db.replace_panel_beats(
        pids[0],
        [
            {
                "action": "b",
                "composition": "centered",
                "light_quality": "soft",
                "emotional_intent": "jaw set",
                "reveals": "the room",
                "movement_motivation": None,
            }
        ],
    )
    beat = db.get_beat(bids[0])
    assert beat["composition"] == "centered"
    assert beat["light_quality"] == "soft"
    assert beat["emotional_intent"] == "jaw set"
    assert beat["reveals"] == "the room"
    assert beat["movement_motivation"] is None
    db.update_beat(bids[0], movement_motivation="she pulls away")
    assert db.get_beat(bids[0])["movement_motivation"] == "she pulls away"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_storyboard_db.py::test_cinematic_columns_roundtrip -v`
Expected: FAIL (`pacing` KeyError or `Not updatable on storyboards: pacing`)

- [ ] **Step 3: Add the columns** — in `_init_database` after each table's `CREATE TABLE IF NOT EXISTS` block (`storyboards` ~line 653, `scenes` ~line 690, `panels` ~line 720, `beats` ~line 756), following the existing `_idempotent_add_column` call style:

```python
_idempotent_add_column(
    conn,
    "storyboards",
    "pacing",
    "ALTER TABLE storyboards ADD COLUMN pacing "
    "TEXT NOT NULL DEFAULT 'standard'",
)
```

and likewise:
- `scenes.arc_beats` → `"ALTER TABLE scenes ADD COLUMN arc_beats TEXT NOT NULL DEFAULT '[]'"`
- `scenes.charge_in` → `"ALTER TABLE scenes ADD COLUMN charge_in INTEGER"`
- `scenes.charge_out` → `"ALTER TABLE scenes ADD COLUMN charge_out INTEGER"`
- `panels.is_turn` → `"ALTER TABLE panels ADD COLUMN is_turn INTEGER NOT NULL DEFAULT 0"`
- `panels.subtext` → `"ALTER TABLE panels ADD COLUMN subtext TEXT"`
- `beats.composition`, `beats.light_quality`, `beats.emotional_intent`, `beats.reveals`, `beats.movement_motivation` → each `"ALTER TABLE beats ADD COLUMN <name> TEXT"`

Also add the new column names to the freshly-created table DDL itself (the `CREATE TABLE IF NOT EXISTS` bodies) so a brand-new DB matches — mirror how `subtitle`/`setting` appear both in the scenes DDL and as idempotent adds.

- [ ] **Step 4: Updatable sets** — extend the frozensets at ~line 1535:
  - `_STORYBOARD_UPDATABLE` += `"pacing"`
  - `_SCENE_UPDATABLE` += `"arc_beats", "charge_in", "charge_out"`
  - `_PANEL_UPDATABLE` += `"is_turn", "subtext"`
  - `_BEAT_UPDATABLE` += `"composition", "light_quality", "emotional_intent", "reveals", "movement_motivation"`

- [ ] **Step 5: Scene row decoding** — add next to `_decode_panel_row` (~line 1948):

```python
@staticmethod
def _decode_scene_row(row: sqlite3.Row) -> Dict[str, Any]:
    import json as _json

    d = dict(row)
    try:
        decoded = _json.loads(d.get("arc_beats") or "[]")
    except (ValueError, TypeError):
        decoded = []
    d["arc_beats"] = decoded if isinstance(decoded, list) else []
    return d
```

Then run `grep -n "FROM scenes" metascan/core/database_sqlite.py` and replace every place a scenes row becomes `dict(row)` on its way out of the class (at minimum `get_storyboard_tree`'s `scene = dict(scene_row)` and any `get_scene`/`list_scenes` accessor) with `self._decode_scene_row(row)`. Internal id-only lookups (`SELECT id FROM scenes ...`) stay as they are.

- [ ] **Step 6: Writers** — 
  - `update_scene` (~line 1858): JSON-encode like `update_beat` does for `dialog`:

```python
if "arc_beats" in fields:
    import json as _json

    fields["arc_beats"] = _json.dumps(list(fields["arc_beats"] or []))
```

  - `replace_storyboard_scenes` (~line 2444): extend the INSERT column list with `arc_beats, charge_in, charge_out` and the values tuple with:

```python
_json.dumps(list(sc.get("arc_beats") or [])),
sc.get("charge_in"),
sc.get("charge_out"),
```

(add `import json as _json` at the top of the method, matching `replace_panel_beats`).
  - `replace_scene_panels` (~line 2480): extend INSERT with `is_turn, subtext` and values `int(p.get("is_turn", 0)), p.get("subtext")`.
  - `replace_panel_beats` (~line 2330): extend INSERT with `composition, light_quality, emotional_intent, reveals, movement_motivation` and values `b.get("composition"), b.get("light_quality"), b.get("emotional_intent"), b.get("reveals"), b.get("movement_motivation")`.

- [ ] **Step 7: Run test to verify it passes**

Run: `venv/bin/pytest tests/test_storyboard_db.py -v`
Expected: PASS (all, not just the new test)

- [ ] **Step 8: Full backend gate + commit**

```bash
make quality test
git add metascan/core/database_sqlite.py tests/test_storyboard_db.py
git commit -m "feat(storyboard): cinematic-polish columns on storyboards/scenes/panels/beats"
```

---

### Task 3: Grammars + response validators

**Files:**
- Modify: `metascan/core/storyboard_story.py` (grammar templates ~lines 87-139, validators ~lines 264-436)
- Test: `tests/test_storyboard_story.py`

**Interfaces:**
- Consumes: `PACING_VALUES`, `COMPOSITION_VALUES`, `LIGHT_QUALITY_VALUES` (Task 1).
- Produces: `validate_outline_response` result gains `"pacing": str` (always valid, default `"standard"`); `validate_scenes_response` scene dicts gain `"arc_beats": List[str]`, `"charge_in"/"charge_out": Optional[int]` (clamped −5..5); `validate_shots_response` panel dicts gain `"subtext": Optional[str]`, `"is_turn": int` (0/1); `validate_beats_response` beat dicts gain the five new keys, and mechanically null `camera_amplitude`/`camera_speed`/`movement_motivation` when `camera_motion` is `None` or `"static"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_outline_pacing_validated_with_fallback():
    base = {
        "logline": "L", "tone": "T", "duration_target_s": 60,
        "subjects": [], "arc": [{"beat": "setup", "summary": "s"}],
    }
    good = dict(base, pacing="propulsive")
    assert story.validate_outline_response(json.dumps(good))["pacing"] == "propulsive"
    bad = dict(base, pacing="glacial")
    assert story.validate_outline_response(json.dumps(bad))["pacing"] == "standard"
    assert story.validate_outline_response(json.dumps(base))["pacing"] == "standard"


def test_scenes_dramatic_fields_validated():
    raw = json.dumps(
        [
            {
                "name": "A",
                "arc_beats": ["setup", "bogus", "turn"],
                "charge_in": -2,
                "charge_out": 3,
            },
            {"name": "B", "charge_in": 99, "charge_out": "x"},
        ]
    )
    scenes = story.validate_scenes_response(raw)
    assert scenes[0]["arc_beats"] == ["setup", "turn"]  # unknown stage dropped
    assert scenes[0]["charge_in"] == -2 and scenes[0]["charge_out"] == 3
    assert scenes[1]["arc_beats"] == []
    assert scenes[1]["charge_in"] is None  # out of range
    assert scenes[1]["charge_out"] is None  # garbage


def test_shots_subtext_and_is_turn():
    raw = json.dumps(
        [
            {"action": "a", "duration_s": 8, "subtext": " hides it ", "is_turn": True},
            {"action": "b", "duration_s": 8},
        ]
    )
    panels = story.validate_shots_response(raw)
    assert panels[0]["subtext"] == "hides it" and panels[0]["is_turn"] == 1
    assert panels[1]["subtext"] is None and panels[1]["is_turn"] == 0


def test_beats_visual_grammar_fields_and_static_nulling():
    beat = {
        "duration_s": 3, "action": "a", "shot_size": "CU", "angle": None,
        "lens": None, "subjects": [], "camera_motion": "static",
        "camera_amplitude": "large", "camera_speed": "fast",
        "movement_motivation": "won't matter", "is_cut": False,
        "sound": None, "dialog": [], "composition": "negative_space",
        "light_quality": "firelight", "emotional_intent": "jaw set",
        "reveals": "the empty chair",
    }
    beats, _ = story.validate_beats_response(json.dumps([beat]), {})
    b = beats[0]
    assert b["composition"] == "negative_space"
    assert b["light_quality"] == "firelight"
    assert b["emotional_intent"] == "jaw set"
    assert b["reveals"] == "the empty chair"
    # static motion mechanically nulls amplitude/speed/motivation
    assert b["camera_amplitude"] is None
    assert b["camera_speed"] is None
    assert b["movement_motivation"] is None
    bad = dict(beat, composition="rule_of_odds", light_quality="neon")
    beats, _ = story.validate_beats_response(json.dumps([bad]), {})
    assert beats[0]["composition"] is None and beats[0]["light_quality"] is None


def test_new_grammars_carry_new_fields():
    assert '"pacing"' in story.OUTLINE_GRAMMAR
    for token in ('"arc_beats"', '"charge_in"', '"charge_out"'):
        assert token in story.SCENES_GRAMMAR
    for token in ('"subtext"', '"is_turn"'):
        assert token in story.SHOTS_GRAMMAR
    for token in (
        '"composition"', '"light_quality"', '"emotional_intent"',
        '"reveals"', '"movement_motivation"',
    ):
        assert token in story.BEATS_GRAMMAR
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_storyboard_story.py -v -k "pacing_validated or dramatic_fields or subtext_and or visual_grammar or new_grammars"`
Expected: FAIL

- [ ] **Step 3: Extend the grammars.** All four stay `str.format` templates with doubled literal braces; hyphens stay literal (never `\-`).

  - `_OUTLINE_TEMPLATE`: after the `"tone"` field insert ` ws "," ws "\"pacing\"" ws ":" ws pacingv` and add the rule line `pacingv ::= {pacing_alts}`; format with `pacing_alts=_alts(PACING_VALUES, with_null=False)`.
  - `SCENES_GRAMMAR` becomes `_SCENES_TEMPLATE` (formatted with `arcbeat_alts=_alts(ARC_BEAT_VALUES, with_null=False)`); the scene rule gains, after `"notes"`: `ws "," ws "\"arc_beats\"" ws ":" ws arcbeats ws "," ws "\"charge_in\"" ws ":" ws charge ws "," ws "\"charge_out\"" ws ":" ws charge`, plus rules:

```
arcbeats ::= "[" ws (arcbeat (ws "," ws arcbeat){{0,4}})? ws "]"
arcbeat ::= {arcbeat_alts}
charge ::= "-"? [0-5]
```

  - `SHOTS_GRAMMAR`: shot rule gains, after `"duration_s"`: ` ws "," ws "\"subtext\"" ws ":" ws string ws "," ws "\"is_turn\"" ws ":" ws boolean`. The root repetition bound stays `{{0,5}}` — the grammar already allows up to 6 shots, which covers propulsive's `shots_max`.
  - `_BEATS_TEMPLATE` beat rule: after `"action"` insert ` ws "," ws "\"reveals\"" ws ":" ws string ws "," ws "\"emotional_intent\"" ws ":" ws string`; after `"lens"` insert ` ws "," ws "\"composition\"" ws ":" ws composition ws "," ws "\"light_quality\"" ws ":" ws lightq`; after `"camera_speed"` insert ` ws "," ws "\"movement_motivation\"" ws ":" ws nullable`. Add rules `composition ::= {composition_alts}` and `lightq ::= {lightq_alts}`; extend the `.format(...)` call with `composition_alts=_alts(COMPOSITION_VALUES)` and `lightq_alts=_alts(LIGHT_QUALITY_VALUES)` (nullable — default `with_null=True`).

- [ ] **Step 4: Extend the validators.**

  - `validate_outline_response`: before the return, add

```python
pacing = data.get("pacing")
if pacing not in PACING_VALUES:
    pacing = "standard"
```

and include `"pacing": pacing` in the returned dict.
  - `validate_scenes_response`: add a module-level helper and use it:

```python
def _charge(v: Any) -> Optional[int]:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if -5 <= n <= 5 else None
```

each scene dict gains:

```python
"arc_beats": [
    b for b in (sc.get("arc_beats") or []) if b in ARC_BEAT_VALUES
],
"charge_in": _charge(sc.get("charge_in")),
"charge_out": _charge(sc.get("charge_out")),
```

  - `validate_shots_response`: each panel dict gains `"subtext": _clean(p.get("subtext"))` and `"is_turn": 1 if p.get("is_turn") else 0`.
  - `validate_beats_response`: each beat dict gains

```python
"composition": (
    b.get("composition") if b.get("composition") in COMPOSITION_VALUES else None
),
"light_quality": (
    b.get("light_quality")
    if b.get("light_quality") in LIGHT_QUALITY_VALUES
    else None
),
"emotional_intent": _clean(b.get("emotional_intent")),
"reveals": _clean(b.get("reveals")),
"movement_motivation": _clean(b.get("movement_motivation")),
```

and, after building each `beat` dict (before `beats.append`), the mechanical fix:

```python
if beat["camera_motion"] in (None, "static"):
    beat["camera_amplitude"] = None
    beat["camera_speed"] = None
    beat["movement_motivation"] = None
```

- [ ] **Step 5: Run the full story test file**

Run: `venv/bin/pytest tests/test_storyboard_story.py -v`
Expected: all PASS — the pre-existing grammar hygiene tests (`test_grammars_have_no_invalid_hyphen_escape`, `test_grammars_have_root_and_balanced_quotes`, `test_grammar_whitespace_is_bounded`) iterate every grammar and will catch template mistakes.

- [ ] **Step 6: Commit**

```bash
git add metascan/core/storyboard_story.py tests/test_storyboard_story.py
git commit -m "feat(storyboard): dramatic-spine + visual-grammar fields in stage grammars/validators"
```

---

### Task 4: Lint functions

**Files:**
- Modify: `metascan/core/storyboard_story.py` (new "Cinematic lints" section after the validators)
- Test: `tests/test_storyboard_story.py`

**Interfaces:**
- Consumes: `SHOT_SIZE_VALUES` (existing, ordered tight→wide: ECU first), `ARC_BEAT_VALUES`.
- Produces (Task 6 calls these exactly):
  - `lint_scene_charges(scenes: Sequence[Mapping[str, Any]], arc: Sequence[Mapping[str, Any]]) -> List[str]`
  - `lint_shots(panels: Sequence[Mapping[str, Any]], scene_is_turn: bool) -> List[str]`
  - `lint_beats(beats, *, is_turn_panel: bool, is_scene_opener: bool, prev_shot_sizes: Sequence[Optional[str]] = ()) -> List[str]`
  - `describe_beat_framing(beat: Mapping[str, Any]) -> str`

- [ ] **Step 1: Write the failing tests**

```python
def _scene(name, arc_beats, cin, cout):
    return {"name": name, "arc_beats": arc_beats, "charge_in": cin, "charge_out": cout}


def test_lint_scene_charges_happy_path():
    arc = [{"beat": "setup"}, {"beat": "turn"}, {"beat": "resolution"}]
    scenes = [
        _scene("A", ["setup"], -1, -3),
        _scene("B", ["turn", "resolution"], -3, 2),
    ]
    assert story.lint_scene_charges(scenes, arc) == []


def test_lint_scene_charges_catches_each_rule():
    arc = [{"beat": "setup"}, {"beat": "turn"}]
    # broken chain + missing charge
    v = story.lint_scene_charges(
        [_scene("A", ["setup"], -1, -2), _scene("B", ["turn"], 1, None)], arc
    )
    assert any("chain must be continuous" in m for m in v)
    assert any("missing charge" in m for m in v)
    # coverage: arc stage never lands / wrong order
    v = story.lint_scene_charges([_scene("A", ["turn", "setup"], 0, 4)], arc)
    assert any("arc_beats" in m for m in v)
    # turn scene must swing hardest
    v = story.lint_scene_charges(
        [_scene("A", ["setup"], -4, 4), _scene("B", ["turn"], 4, 3)], arc
    )
    assert any("largest charge swing" in m for m in v)
    # polarity flip
    v = story.lint_scene_charges(
        [_scene("A", ["setup"], 2, 4), _scene("B", ["turn"], 4, 1)], arc
    )
    assert any("polarity" in m for m in v)
    # neutral open (0) never trips the polarity rule
    v = story.lint_scene_charges(
        [_scene("A", ["setup"], 0, 2), _scene("B", ["turn"], 2, 4)], arc
    )
    assert not any("polarity" in m for m in v)


def test_lint_shots_turn_and_subtext():
    ok = [
        {"action": "she waits", "subtext": "she is afraid to knock", "is_turn": 0},
        {"action": "he opens", "subtext": "he knew she'd come", "is_turn": 1},
    ]
    assert story.lint_shots(ok, scene_is_turn=True) == []
    v = story.lint_shots(ok, scene_is_turn=False)
    assert v == []  # stray turns are zeroed mechanically, not linted
    two_turns = [dict(ok[0], is_turn=1), ok[1]]
    v = story.lint_shots(two_turns, scene_is_turn=True)
    assert any("exactly one shot" in m for m in v)
    v = story.lint_shots(
        [{"action": "she waits", "subtext": " She Waits ", "is_turn": 1}],
        scene_is_turn=True,
    )
    assert any("restates" in m for m in v)
    v = story.lint_shots([{"action": "a", "subtext": None, "is_turn": 1}], True)
    assert any("empty subtext" in m for m in v)


def _beat(size, motion=None, motivation=None, reveals="something new"):
    return {
        "shot_size": size, "camera_motion": motion,
        "movement_motivation": motivation, "reveals": reveals,
    }


def test_lint_beats_rules():
    # triple repeat, including across the panel boundary
    v = story.lint_beats(
        [_beat("MCU"), _beat("MCU")],
        is_turn_panel=False, is_scene_opener=False, prev_shot_sizes=["MCU"],
    )
    assert any("three consecutive" in m for m in v)
    # opener must be wide
    v = story.lint_beats(
        [_beat("CU")], is_turn_panel=False, is_scene_opener=True
    )
    assert any("establish" in m for m in v)
    assert (
        story.lint_beats([_beat("WS")], is_turn_panel=False, is_scene_opener=True)
        == []
    )
    # unmotivated move
    v = story.lint_beats(
        [_beat("WS", motion="push_in")], is_turn_panel=False, is_scene_opener=False
    )
    assert any("movement_motivation" in m for m in v)
    # empty reveals
    v = story.lint_beats(
        [_beat("WS", reveals=None)], is_turn_panel=False, is_scene_opener=False
    )
    assert any("reveals" in m for m in v)
    # turn panel: tightest framing must not sit on beat 1
    v = story.lint_beats(
        [_beat("ECU"), _beat("WS")], is_turn_panel=True, is_scene_opener=False
    )
    assert any("tightest" in m for m in v)
    assert (
        story.lint_beats(
            [_beat("WS"), _beat("ECU")], is_turn_panel=True, is_scene_opener=False
        )
        == []
    )


def test_describe_beat_framing():
    assert story.describe_beat_framing(
        {"shot_size": "MCU", "angle": "low", "lens": None,
         "composition": "centered", "light_quality": "soft"}
    ) == "MCU, low, centered, soft"
    assert story.describe_beat_framing({}) == "unspecified framing"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_storyboard_story.py -v -k lint`
Expected: FAIL with AttributeError

- [ ] **Step 3: Implement** — add after the validators in `storyboard_story.py`:

```python
# -- Cinematic lints (spec §6) --------------------------------------------
# Each returns human-readable violations phrased as instructions: the
# runner appends them verbatim to a targeted re-roll prompt, and small
# models correct well against specific complaints.

_TIGHTNESS: Dict[str, int] = {v: i for i, v in enumerate(SHOT_SIZE_VALUES)}


def describe_beat_framing(beat: Mapping[str, Any]) -> str:
    """Short framing summary for re-injection into the next beats call."""
    bits = [
        beat.get("shot_size"),
        beat.get("angle"),
        beat.get("lens"),
        beat.get("composition"),
        beat.get("light_quality"),
    ]
    present = [str(b) for b in bits if b]
    return ", ".join(present) or "unspecified framing"


def lint_scene_charges(
    scenes: Sequence[Mapping[str, Any]], arc: Sequence[Mapping[str, Any]]
) -> List[str]:
    out: List[str] = []
    expected = [a["beat"] for a in arc if isinstance(a, dict) and a.get("beat")]
    got = [b for sc in scenes for b in (sc.get("arc_beats") or [])]
    if expected and got != expected:
        out.append(
            "the scenes' arc_beats concatenated in scene order must be "
            f"exactly {expected} (every outline arc entry in exactly one "
            f"scene, in story order, no gaps); you produced {got}"
        )
    charges = [(sc.get("charge_in"), sc.get("charge_out")) for sc in scenes]
    for i, (cin, cout) in enumerate(charges, 1):
        if cin is None or cout is None:
            out.append(f"scene {i} is missing charge_in/charge_out")
    for i in range(1, len(charges)):
        prev_out, cur_in = charges[i - 1][1], charges[i][0]
        if prev_out is not None and cur_in is not None and prev_out != cur_in:
            out.append(
                f"scene {i + 1} charge_in={cur_in} but scene {i} "
                f"charge_out={prev_out} — the chain must be continuous"
            )
    swings = [
        abs(cout - cin)
        for cin, cout in charges
        if cin is not None and cout is not None
    ]
    turn_idx = next(
        (
            i
            for i, sc in enumerate(scenes)
            if "turn" in (sc.get("arc_beats") or [])
        ),
        None,
    )
    if turn_idx is not None and swings:
        cin, cout = charges[turn_idx]
        if (
            cin is not None
            and cout is not None
            and abs(cout - cin) < max(swings)
        ):
            out.append(
                f"the turn scene (scene {turn_idx + 1}) must have the "
                "largest charge swing of any scene; another scene swings "
                "harder"
            )
    first_in = charges[0][0] if charges else None
    last_out = charges[-1][1] if charges else None
    if (
        first_in is not None
        and last_out is not None
        and first_in != 0
        and last_out != 0
        and (first_in > 0) == (last_out > 0)
    ):
        out.append(
            "the story must not end on the same charge polarity it opened on"
        )
    return out


def lint_shots(
    panels: Sequence[Mapping[str, Any]], scene_is_turn: bool
) -> List[str]:
    out: List[str] = []
    if scene_is_turn:
        turns = sum(1 for p in panels if p.get("is_turn"))
        if turns != 1:
            out.append(
                "this scene contains the story's turn: exactly one shot "
                f"must set is_turn true (you marked {turns})"
            )
    for i, p in enumerate(panels, 1):
        sub = (p.get("subtext") or "").strip()
        if not sub:
            out.append(f"shot {i} has an empty subtext")
        elif sub.lower() == (p.get("action") or "").strip().lower():
            out.append(
                f"shot {i}'s subtext restates its action — subtext is what "
                "the shot means but does not show"
            )
    return out


def lint_beats(
    beats: Sequence[Mapping[str, Any]],
    *,
    is_turn_panel: bool,
    is_scene_opener: bool,
    prev_shot_sizes: Sequence[Optional[str]] = (),
) -> List[str]:
    out: List[str] = []
    sizes = list(prev_shot_sizes) + [b.get("shot_size") for b in beats]
    run = 1
    for i in range(1, len(sizes)):
        if sizes[i] is not None and sizes[i] == sizes[i - 1]:
            run += 1
        else:
            run = 1
        if run == 3:
            out.append(
                f'three consecutive beats use shot_size "{sizes[i]}" — '
                "never the same shot size three beats running; vary the "
                "framing"
            )
    if is_scene_opener and beats:
        if beats[0].get("shot_size") not in ("WS", "EWS"):
            out.append(
                "the first beat of a scene's opening shot must establish "
                "the space wide: use WS or EWS"
            )
    for i, b in enumerate(beats, 1):
        motion = b.get("camera_motion")
        if (
            motion
            and motion != "static"
            and not (b.get("movement_motivation") or "").strip()
        ):
            out.append(
                f'beat {i} has camera_motion "{motion}" but empty '
                "movement_motivation — name what in the subject's behavior "
                'or emotional state pulls the camera, or use "static"'
            )
        if not (b.get("reveals") or "").strip():
            out.append(
                f"beat {i} has an empty reveals — state what this beat "
                "shows that the previous beat did not"
            )
    if is_turn_panel and len(beats) > 1:
        indexed = [
            (i, _TIGHTNESS[b["shot_size"]])
            for i, b in enumerate(beats)
            if b.get("shot_size") in _TIGHTNESS
        ]
        if indexed and min(indexed, key=lambda t: t[1])[0] == 0:
            out.append(
                "this shot is the story's turn: its tightest framing must "
                "land on the beat where the turn hits, not on beat 1"
            )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_storyboard_story.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add metascan/core/storyboard_story.py tests/test_storyboard_story.py
git commit -m "feat(storyboard): cinematic lint functions + beat framing summary"
```

---

### Task 5: Prompt builders + system-prompt rewrites

**Files:**
- Modify: `metascan/core/storyboard_story.py` (`build_shots_user_prompt` ~line 166, `build_beats_user_prompt` ~line 189)
- Modify: `data/meta_prompt.yml` (the four `STORY_*_SYSTEM` blocks, ~lines 472-482)
- Test: `tests/test_storyboard_story.py`

**Interfaces:**
- Consumes: `pacing_guidance` (Task 1), `describe_beat_framing` (Task 4).
- Produces — Task 6 calls these signatures exactly:

```python
def build_shots_user_prompt(
    outline_json: str,
    scene: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    prev_scene_name: Optional[str],
    next_scene_name: Optional[str],
    guidance: Mapping[str, float],
    is_turn_scene: bool,
) -> str: ...

def build_beats_user_prompt(
    outline: Mapping[str, Any],
    scene: Mapping[str, Any],
    panel: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    guidance: Mapping[str, float],
    prev_panel_action: Optional[str],
    prev_beat_summary: Optional[str],
) -> str: ...
```

Note `build_shots_user_prompt` **drops** its `max_shot_s` parameter (superseded by `guidance`) and `build_beats_user_prompt` replaces `logline: str` with `outline: Mapping` — both callers are updated in Task 6; the pre-existing test `test_build_shots_user_prompt_states_max_shot_seconds` is rewritten here.

- [ ] **Step 1: Write/adjust the failing tests.** Replace `test_build_shots_user_prompt_states_max_shot_seconds` and add:

```python
def _guidance():
    return story.pacing_guidance("standard", 15.0)


def test_build_shots_user_prompt_carries_spine_and_pacing():
    scene = {
        "name": "Yard", "setting": "hulls", "mood": "tense",
        "lighting": None, "time_of_day": "dusk",
        "arc_beats": ["turn"], "charge_in": -3, "charge_out": 2,
    }
    p = story.build_shots_user_prompt(
        "{}", scene, [], None, None, _guidance(), is_turn_scene=True
    )
    assert "Arc stages this scene covers: turn" in p
    assert "opens at -3, closes at 2" in p
    assert "exactly one shot must set is_turn to true" in p.lower()
    assert "between 2 and 4 shots" in p
    assert "8 and 15 seconds" in p
    p2 = story.build_shots_user_prompt(
        "{}", dict(scene, arc_beats=["setup"]), [], None, None,
        _guidance(), is_turn_scene=False,
    )
    assert "every shot sets is_turn false" in p2.lower()


def test_build_beats_user_prompt_reinjects_bible_and_context():
    outline = {"logline": "L", "tone": "grim"}
    scene = {
        "name": "Yard", "setting": "rusting hulls", "location": None,
        "time_of_day": "dusk", "mood": "tense", "lighting": "sodium lamps",
    }
    panel = {"action": "Maya crosses", "duration_s": 10.0,
             "subtext": "she is afraid", "is_turn": 1}
    p = story.build_beats_user_prompt(
        outline, scene, panel, [], _guidance(),
        prev_panel_action="He watches",
        prev_beat_summary="MCU, low, soft",
    )
    assert "Tone: grim" in p
    assert "rusting hulls" in p and "sodium lamps" in p and "dusk" in p
    assert "Shot subtext: she is afraid" in p
    assert "tightest" in p.lower()  # turn directive
    assert "Previous shot in this scene: He watches" in p
    assert "MCU, low, soft" in p
    assert "2 to 4 beats" in p
    # opener variant
    p2 = story.build_beats_user_prompt(
        outline, scene, dict(panel, is_turn=0), [], _guidance(), None, None
    )
    assert "opening shot" in p2 and "WS or EWS" in p2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_storyboard_story.py -v -k build`
Expected: FAIL (TypeError on new signatures)

- [ ] **Step 3: Implement the builders** (replacing the existing two functions):

```python
def build_shots_user_prompt(
    outline_json: str,
    scene: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    prev_scene_name: Optional[str],
    next_scene_name: Optional[str],
    guidance: Mapping[str, float],
    is_turn_scene: bool,
) -> str:
    arc = ", ".join(scene.get("arc_beats") or []) or "unspecified"
    turn_directive = (
        "This scene contains the story's TURN: exactly one shot must set "
        "is_turn to true — the shot where the scene's emotional value "
        "reverses. Every other shot sets is_turn false.\n"
        if is_turn_scene
        else "This scene does not contain the story's turn: every shot "
        "sets is_turn false.\n"
    )
    return (
        f"Story outline:\n{outline_json}\n\n"
        f"Scene to break into shots: {scene['name']}"
        f" — {scene.get('setting') or scene.get('location') or ''}\n"
        f"Mood: {scene.get('mood') or 'unspecified'}; "
        f"lighting: {scene.get('lighting') or 'unspecified'}; "
        f"time: {scene.get('time_of_day') or 'unspecified'}\n"
        f"Arc stages this scene covers: {arc}\n"
        f"Emotional charge (protagonist's POV, -5..+5): opens at "
        f"{scene.get('charge_in')}, closes at {scene.get('charge_out')}\n"
        f"Previous scene: {prev_scene_name or '(story opening)'}\n"
        f"Next scene: {next_scene_name or '(story ending)'}\n\n"
        f"Subjects (use these exact names):\n{_roster_lines(subjects)}\n\n"
        f"{turn_directive}"
        "Write the shot list JSON.\n"
        f"Produce between {int(guidance['shots_min'])} and "
        f"{int(guidance['shots_max'])} shots, each lasting between "
        f"{guidance['panel_min_s']:.0f} and {guidance['panel_max_s']:.0f} "
        "seconds."
    )


def build_beats_user_prompt(
    outline: Mapping[str, Any],
    scene: Mapping[str, Any],
    panel: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
    guidance: Mapping[str, float],
    prev_panel_action: Optional[str],
    prev_beat_summary: Optional[str],
) -> str:
    duration = float(panel.get("duration_s") or 12.0)
    turn_line = (
        "This shot is the story's TURN: reserve its tightest framing for "
        "the beat where the turn lands.\n"
        if panel.get("is_turn")
        else ""
    )
    if prev_panel_action:
        prev_block = f"Previous shot in this scene: {prev_panel_action}\n"
        if prev_beat_summary:
            prev_block += (
                f"The previous shot's last beat ended on: {prev_beat_summary}\n"
            )
    else:
        prev_block = (
            "This is the scene's opening shot: the first beat must "
            "establish the space wide (WS or EWS).\n"
        )
    return (
        f"Story logline: {outline.get('logline') or ''}\n"
        f"Tone: {outline.get('tone') or 'unspecified'}\n"
        f"Scene: {scene['name']} — setting: "
        f"{scene.get('setting') or scene.get('location') or 'unspecified'}\n"
        f"Time: {scene.get('time_of_day') or 'unspecified'}; "
        f"lighting: {scene.get('lighting') or 'unspecified'}; "
        f"mood: {scene.get('mood') or 'unspecified'}\n"
        f"Shot: {panel['action']}\n"
        f"Shot subtext: {panel.get('subtext') or 'unspecified'}\n"
        f"{turn_line}"
        f"{prev_block}"
        f"Target duration: {duration:.0f} seconds\n"
        f"Produce {int(guidance['beats_min'])} to "
        f"{int(guidance['beats_max'])} beats of roughly "
        f"{guidance['beat_asl_s']:.0f} seconds each.\n"
        f"Subject roster (assign per beat; exact names):\n"
        f"{_roster_lines(subjects)}\n\n"
        "Write the beat list JSON."
    )
```

- [ ] **Step 4: Rewrite the system prompts** in `data/meta_prompt.yml`. Each is a single `|-` paragraph; keep everything currently there and **insert the new sentences immediately before the existing "Do not refuse, soften, sanitize..." sentence** of each block:

  - `STORY_OUTLINE_SYSTEM`, insert: `Choose pacing from contemplative, standard, or propulsive — the rhythm that serves the premise's dominant register: grief, dread, landscape, or lyrical material is contemplative; dialogue and most drama are standard; chases, arguments, and panic are propulsive.`
  - `STORY_SCENES_SYSTEM`, insert: `Each scene also declares the dramatic work it does. arc_beats lists which of the outline's arc stages land inside this scene — every arc entry must land in exactly one scene, in story order, with no gaps. charge_in and charge_out rate the emotional value of the story from the protagonist's point of view, from -5 to +5, as the scene opens and closes; a scene where the value does not change is filler — fold it into a neighbor. charge_out of each scene must equal charge_in of the next. The scene covering the turn must have the largest charge swing of any scene, and the story must not end on the same charge polarity it opened on.`
  - `STORY_SHOTS_SYSTEM`, insert: `Each shot also carries subtext: one sentence naming what the shot means but does not show — what a character wants, hides, or refuses to say. Subtext must differ from the visible action; if they read the same, rewrite it. Set is_turn true only when the user prompt tells you this scene contains the story's turn, and then on exactly one shot — the shot where the scene's emotional value reverses; otherwise every shot sets is_turn false.`
  - `STORY_BEATS_SYSTEM`, insert: `Compose the sequence like a director, not a list-maker. Never use the same shot_size on three consecutive beats — build coverage by varying the framing. When the user prompt says this is the scene's opening shot, the first beat establishes the space wide (WS or EWS) unless the scene deliberately withholds geography. In a shot marked as the story's turn, reserve the tightest framing for the beat where the turn actually lands — push in on the turn, not on exposition. Every beat's reveals field states what this beat shows that the previous beat did not; a beat that reveals nothing is a duplicate — change it. Any camera_motion other than static requires movement_motivation naming what in the subject's behavior or emotional state pulls the camera — "for drama" is not a reason; if nothing motivates a move, use static. Assign composition and light_quality from their fixed vocabularies when the beat gives you something to decide, keeping light choices consistent with the scene's stated lighting and time of day. emotional_intent describes the visible physical evidence of the moment's feeling — jaw set, eyes fixed on the floor, shoulders dropped — never an emotion label like "she is sad": a camera cannot photograph a label.`

- [ ] **Step 5: Run tests**

Run: `venv/bin/pytest tests/test_storyboard_story.py -v`
Expected: all PASS. `test_system_prompts_resolve_from_store` re-reads the YAML — a YAML syntax slip (unescaped quotes are fine inside `|-` blocks, but indentation matters) fails here.

- [ ] **Step 6: Commit**

```bash
git add metascan/core/storyboard_story.py data/meta_prompt.yml tests/test_storyboard_story.py
git commit -m "feat(storyboard): bible re-injection prompt builders + cinematic system prompts"
```

---

### Task 6: Runner — lint re-roll, pacing write, turn directive, sequential beats, WS warnings

**Files:**
- Modify: `metascan/core/storyboard_runner.py` (`_compose_locked` ~line 328, `_run_stage` ~line 374)
- Test: `tests/test_storyboard_compose.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5 by the exact names above; `shot_cap` (existing import from `metascan.core.video_targets`).
- Produces: `_run_stage` returns `Tuple[int, List[str]]` (count, lint warnings); `story_stage_complete` WS payload gains `"warnings": List[str]` (always present, possibly empty). Frontend Task 12 reads `d.warnings`.

- [ ] **Step 1: Update the scripted fixtures** at the top of `tests/test_storyboard_compose.py` so the default flow is lint-clean (no re-roll fires unless a test wants one):

```python
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
```

Also extend `FakeVlm` to record user prompts (keep `self.calls` recording grammars — existing tests index it):

```python
def __init__(self):
    self.calls = []
    self.prompts = []
```

and in `generate_text`, after `self.calls.append(grammar)` add `self.prompts.append(user_prompt)`.

- [ ] **Step 2: Write the failing tests** (append; use the file's existing `db`/`runner`/`storyboard_id` fixtures and its existing pattern for driving `compose_story` with a FakeVlm — copy how the current happy-path compose test wires `get_vlm`):

```python
def _events(runner):
    seen = []
    runner.on_event.append(lambda ch, ev, data: seen.append((ch, ev, data)))
    return seen


def test_compose_writes_pacing(db, storyboard_id):
    vlm = FakeVlm()
    r = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=Path(".")
    )
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
    r = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=Path(".")
    )
    seen = _events(r)
    asyncio.run(r.compose_story(storyboard_id, stages=["outline", "scenes"]))
    assert vlm.scene_calls == 2
    assert "missing charge" in vlm.prompts[-1]  # violation named in re-roll
    done = [d for _, ev, d in seen if ev == "story_stage_complete"
            and d["stage"] == "scenes"]
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
    done = [d for _, ev, d in seen if ev == "story_stage_complete"
            and d["stage"] == "scenes"]
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
    asyncio.run(
        r.compose_story(storyboard_id, stages=["outline", "scenes", "shots"])
    )
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
    r = StoryboardRunner(
        db=db, comfy=None, get_vlm=lambda: vlm, output_root=Path(".")
    )
    asyncio.run(r.compose_story(storyboard_id))
    assert len(vlm.prompts) == 2  # one beats call per panel
    assert "opening shot" in vlm.prompts[0]
    assert "Previous shot in this scene: Maya crosses" in vlm.prompts[1]
    # last BEATS beat is MCU — its framing summary reaches the second call
    assert "MCU" in vlm.prompts[1]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_storyboard_compose.py -v`
Expected: new tests FAIL; pre-existing tests may also fail on the changed builder signatures — that's the work of this task.

- [ ] **Step 4: Implement the runner changes.**

  1. **`generate_validated`** (inside `_run_stage`, ~line 399) — new signature and lint pass; returns `(result, warnings)`:

```python
async def generate_validated(
    unit: str,
    validate: Any,
    lint: Optional[Any] = None,
    **gen_kwargs: Any,
) -> Any:
    """One VLM call + validation (retried once on StoryError), then an
    optional cinematic lint with ONE targeted re-roll: the violations
    are appended verbatim to the user turn (spec §6). Style rules never
    hard-fail — leftovers return as warnings."""
    raw = await vlm.generate_text(**gen_kwargs)
    try:
        result = validate(raw)
    except story.StoryError as exc:
        logger.warning(
            "compose %s: invalid response (%s); retrying once. "
            "Response tail: %r",
            unit,
            exc,
            raw[-300:],
        )
        result = validate(await vlm.generate_text(**gen_kwargs))
    if lint is None:
        return result, []
    violations = lint(result)
    if not violations:
        return result, []
    logger.info(
        "compose %s: %d style violation(s); targeted re-roll",
        unit,
        len(violations),
    )
    retry_kwargs = dict(gen_kwargs)
    retry_kwargs["user_prompt"] = (
        gen_kwargs["user_prompt"]
        + "\n\nYour previous response violated these rules — regenerate "
        "the full JSON, fixing each:\n"
        + "\n".join(f"- {v}" for v in violations)
    )
    try:
        second = validate(await vlm.generate_text(**retry_kwargs))
    except story.StoryError:
        return result, violations
    return second, lint(second)
```

  2. **`_run_stage` return type** becomes `Tuple[int, List[str]]`; every `return <n>` becomes `return n, stage_warnings` (collect a `stage_warnings: List[str] = []` per stage). In `_compose_locked`'s loop:

```python
n, stage_warnings = await self._run_stage(
    current, vlm, sem, storyboard_id, scene_ids, panel_ids
)
counts[current] = n
self._emit(
    "storyboard",
    "story_stage_complete",
    {
        "storyboard_id": storyboard_id,
        "stage": current,
        "warnings": stage_warnings,
    },
)
```

  3. **Outline stage**: unpack `outline, _ = await generate_validated(...)` (no lint) and write pacing alongside the outline JSON:

```python
await asyncio.to_thread(
    self.db.update_storyboard,
    storyboard_id,
    outline=json.dumps(outline),
    pacing=outline["pacing"],
)
```

Return `1, []`.

  4. **Scenes stage**: parse the arc once and lint:

```python
arc: List[Dict[str, Any]] = []
try:
    arc = json.loads(outline_json).get("arc") or []
except (TypeError, ValueError):
    pass
scenes, stage_warnings = await generate_validated(
    "scenes",
    story.validate_scenes_response,
    lint=lambda sc: story.lint_scene_charges(sc, arc),
    system_prompt=story.STORY_SCENES_SYSTEM,
    user_prompt=story.build_scenes_user_prompt(outline_json),
    grammar=story.SCENES_GRAMMAR,
    temperature=0.7,
    max_tokens=2048,
    timeout=300.0,
)
```

Return `len(scenes), stage_warnings`.

  5. **Shots stage**: compute guidance once before the per-scene fan-out:

```python
pacing = str(tree.get("pacing") or "standard")
guidance = story.pacing_guidance(pacing, shot_cap(tree.get("video_target")))
```

Inside `_shots_for`, derive `is_turn_scene = "turn" in (scene.get("arc_beats") or [])`, call the new builder signature, lint, and mechanically zero strays:

```python
panels, warns = await generate_validated(
    f"shots ({scene['name']})",
    story.validate_shots_response,
    lint=lambda ps: story.lint_shots(ps, is_turn_scene),
    system_prompt=story.STORY_SHOTS_SYSTEM,
    user_prompt=story.build_shots_user_prompt(
        outline_json,
        scene,
        tree["subjects"],
        prev_name,
        next_name,
        guidance,
        is_turn_scene,
    ),
    grammar=story.SHOTS_GRAMMAR,
    temperature=0.6,
    max_tokens=1600,
    timeout=300.0,
)
if not is_turn_scene:
    for p in panels:
        p["is_turn"] = 0
```

Collect each `warns` into a shared `stage_warnings` list under the existing `lock`; keep `asyncio.gather` across scenes. Return `made, stage_warnings`.

  6. **Beats stage — sequential within a scene, parallel across scenes.** Replace the flat `work` list + gather-per-panel with:

```python
outline_data: Dict[str, Any] = {}
try:
    outline_data = json.loads(outline_json) or {}
except (TypeError, ValueError):
    pass
pacing = str(tree.get("pacing") or "standard")
guidance = story.pacing_guidance(pacing, shot_cap(tree.get("video_target")))
scene_work = [
    (scene, [p["id"] for p in scene["panels"]
             if panel_ids is None or p["id"] in panel_ids])
    for scene in tree["scenes"]
]
scene_work = [(s, ids) for s, ids in scene_work if ids]
total = sum(len(ids) for _, ids in scene_work)
done = 0
lock = asyncio.Lock()
stage_warnings: List[str] = []

async def _beats_for_scene(
    scene: Dict[str, Any], target_ids: List[int]
) -> int:
    nonlocal done
    made = 0
    prev_action: Optional[str] = None
    prev_summary: Optional[str] = None
    prev_sizes: List[Optional[str]] = []
    for idx, panel in enumerate(scene["panels"]):
        if panel["id"] not in target_ids:
            # Skipped panel: its existing beats still feed continuity.
            existing = panel.get("beats") or []
            prev_action = panel.get("action")
            if existing:
                prev_summary = story.describe_beat_framing(existing[-1])
                prev_sizes = [b.get("shot_size") for b in existing[-2:]]
            continue
        is_opener = idx == 0
        is_turn_panel = bool(panel.get("is_turn"))
        async with sem:
            (beats, roster_warns), lint_warns = await generate_validated(
                f"beats (panel {panel['id']})",
                lambda raw: story.validate_beats_response(raw, roster),
                lint=lambda res: story.lint_beats(
                    res[0],
                    is_turn_panel=is_turn_panel,
                    is_scene_opener=is_opener,
                    prev_shot_sizes=prev_sizes,
                ),
                system_prompt=story.STORY_BEATS_SYSTEM,
                user_prompt=story.build_beats_user_prompt(
                    outline_data,
                    scene,
                    panel,
                    tree["subjects"],
                    guidance,
                    prev_action,
                    prev_summary,
                ),
                grammar=story.BEATS_GRAMMAR,
                temperature=0.6,
                max_tokens=1600,
                timeout=300.0,
            )
        for w in roster_warns:
            logger.warning("compose beats (panel %s): %s", panel["id"], w)
        story.rescale_beat_durations(
            beats, float(panel.get("duration_s") or 12.0)
        )
        await asyncio.to_thread(
            self.db.replace_panel_beats, panel["id"], beats
        )
        prev_action = panel.get("action")
        if beats:
            prev_summary = story.describe_beat_framing(beats[-1])
            prev_sizes = [b.get("shot_size") for b in beats[-2:]]
        made += len(beats)
        async with lock:
            done += 1
            progress(done, total)
            stage_warnings.extend(
                f"panel {panel['id']}: {w}" for w in lint_warns
            )
    return made

results = await asyncio.gather(
    *(_beats_for_scene(s, ids) for s, ids in scene_work)
)
return sum(results), stage_warnings
```

Note the lambda-in-loop closures (`is_turn_panel`, `is_opener`, `prev_sizes`) are safe because each `generate_validated` is awaited before the loop advances.

- [ ] **Step 5: Run the compose + runner + API compose tests**

Run: `venv/bin/pytest tests/test_storyboard_compose.py tests/test_storyboard_compose_api.py tests/test_storyboard_runner.py -v`
Expected: all PASS. If `test_storyboard_compose_api.py` fakes `compose_story` at the runner boundary it is unaffected; if a test stubs `_run_stage`, update its return value to the new `(n, warnings)` tuple.

- [ ] **Step 6: Full backend gate + commit**

```bash
make quality test
git add metascan/core/storyboard_runner.py tests/test_storyboard_compose.py
git commit -m "feat(storyboard): lint re-roll, turn directive, sequential beats, compose warnings"
```

---

### Task 7: `compose_brief` gains composition / light / performance lines

**Files:**
- Modify: `metascan/core/storyboard_brief.py`
- Test: `tests/test_storyboard_brief.py`

**Interfaces:**
- Consumes: beat dicts now carrying `composition`, `light_quality`, `emotional_intent`.
- Produces: `COMPOSITIONS: Mapping[str, str]`, `LIGHT_QUALITIES: Mapping[str, str]` maps (exported for tests), enriched `compose_brief` output.

- [ ] **Step 1: Write the failing test** (append to `tests/test_storyboard_brief.py`, matching its existing `compose_brief` test fixtures):

```python
def test_compose_brief_carries_cinematic_fields():
    storyboard = {"aspect_ratio": "16:9"}
    scene = {"setting": "yard", "time_of_day": "dusk", "lighting": None, "mood": None}
    panel = {"action": "she crosses"}
    beat = {
        "shot_size": "MCU",
        "angle": "low",
        "lens": None,
        "composition": "negative_space",
        "light_quality": "window",
        "emotional_intent": "jaw set, eyes on the floor",
        "action": "she stops",
    }
    brief = compose_brief(storyboard, scene, panel, beat, [])
    assert "negative space composition" in brief
    assert "PERFORMANCE: jaw set, eyes on the floor" in brief
    assert "motivated window light" in brief
    # light quality joins the LIGHT/MOOD line ahead of scene values
    light_line = next(l for l in brief.splitlines() if l.startswith("LIGHT/MOOD"))
    assert light_line.index("window") < light_line.index("dusk")


def test_compose_brief_omits_absent_cinematic_fields():
    brief = compose_brief(
        {"aspect_ratio": "16:9"}, {}, {"action": "a"}, {"action": "b"}, []
    )
    assert "PERFORMANCE" not in brief and "composition" not in brief
```

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_storyboard_brief.py -v`
Expected: FAIL

- [ ] **Step 3: Implement** — add maps next to `LENSES` and extend `compose_brief`:

```python
COMPOSITIONS: Mapping[str, str] = {
    "thirds_left": "subject on the left third",
    "thirds_right": "subject on the right third",
    "centered": "centered composition",
    "symmetrical": "symmetrical composition",
    "negative_space": "negative space composition",
    "frame_in_frame": "frame-within-frame composition",
    "leading_lines": "leading lines composition",
    "deep_staging": "deep staging, layered depth",
}
LIGHT_QUALITIES: Mapping[str, str] = {
    "hard": "hard light",
    "soft": "soft diffused light",
    "dappled": "dappled light",
    "practical": "practical light sources",
    "window": "motivated window light",
    "firelight": "firelight",
    "ambient": "ambient light",
}
```

In `compose_brief`: insert `COMPOSITIONS.get(beat.get("composition") or "")` into `shot_bits` before the aspect ratio entry; after the ACTION/SHOT CONTEXT lines add:

```python
if beat.get("emotional_intent"):
    lines.append(f"PERFORMANCE: {beat['emotional_intent']}")
```

and change `light_bits` to lead with the beat's light:

```python
light_bits = [
    LIGHT_QUALITIES.get(beat.get("light_quality") or ""),
    scene.get("time_of_day"),
    scene.get("lighting"),
    scene.get("mood"),
]
```

- [ ] **Step 4: Run tests, gate, commit**

Run: `venv/bin/pytest tests/test_storyboard_brief.py tests/test_storyboard_synthesis.py -v` — then:

```bash
git add metascan/core/storyboard_brief.py tests/test_storyboard_brief.py
git commit -m "feat(storyboard): composition/light/performance lines in beat briefs"
```

---

### Task 8: Backend PATCH surface

**Files:**
- Modify: `backend/api/storyboard.py` (models ~lines 143-311, not-nullable sets ~lines 54-79, `patch_storyboard` ~line 362)
- Test: `tests/test_storyboard_api.py`

**Interfaces:**
- Consumes: `PACING_VALUES` from `metascan.core.storyboard_story`; DB columns (Task 2).
- Produces: PATCH routes accept the new fields; invalid `pacing` → 400; nulling `pacing`/`is_turn`/`arc_beats` → 400.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_storyboard_api.py`, using its existing `client` fixture and creation helpers — mirror how the file's current PATCH tests build a storyboard/scene/panel/beat):

```python
def test_patch_cinematic_fields(client):
    sb = client.post("/api/storyboard", json={
        "name": "P", "target_model": "sd"
    }).json()["id"]
    r = client.patch(f"/api/storyboard/{sb}", json={"pacing": "propulsive"})
    assert r.status_code == 200
    assert client.get(f"/api/storyboard/{sb}").json()["pacing"] == "propulsive"
    assert client.patch(
        f"/api/storyboard/{sb}", json={"pacing": "glacial"}
    ).status_code == 400
    assert client.patch(
        f"/api/storyboard/{sb}", json={"pacing": None}
    ).status_code == 400

    scene = client.post(f"/api/storyboard/{sb}/scenes", json={"name": "S"}).json()["id"]
    r = client.patch(f"/api/storyboard/scenes/{scene}", json={
        "arc_beats": ["setup", "turn"], "charge_in": -1, "charge_out": 3
    })
    assert r.status_code == 200
    tree = client.get(f"/api/storyboard/{sb}").json()
    assert tree["scenes"][0]["arc_beats"] == ["setup", "turn"]
    assert tree["scenes"][0]["charge_out"] == 3
    assert client.patch(
        f"/api/storyboard/scenes/{scene}", json={"arc_beats": None}
    ).status_code == 400

    panel = client.post(
        f"/api/storyboard/scenes/{scene}/panels", json={"action": "a"}
    ).json()["id"]
    r = client.patch(f"/api/storyboard/panels/{panel}", json={
        "is_turn": 1, "subtext": "hidden meaning"
    })
    assert r.status_code == 200
    assert r.json()["is_turn"] == 1 and r.json()["subtext"] == "hidden meaning"
    assert client.patch(
        f"/api/storyboard/panels/{panel}", json={"is_turn": None}
    ).status_code == 400

    beat = client.post(
        f"/api/storyboard/panels/{panel}/beats", json={"action": "b"}
    ).json()["id"]
    r = client.patch(f"/api/storyboard/beats/{beat}", json={
        "composition": "centered", "light_quality": "soft",
        "emotional_intent": "jaw set", "reveals": "the door",
        "movement_motivation": "she leans in",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["composition"] == "centered"
    assert body["movement_motivation"] == "she leans in"
    # explicit null clears a nullable field
    r = client.patch(f"/api/storyboard/beats/{beat}", json={"reveals": None})
    assert r.status_code == 200 and r.json()["reveals"] is None
```

(Adjust route paths to the file's actual create-route shapes — check its existing tests for the exact `POST` paths for scenes/panels/beats and reuse them verbatim.)

- [ ] **Step 2: Run test to verify it fails**

Run: `venv/bin/pytest tests/test_storyboard_api.py -v -k cinematic`
Expected: FAIL

- [ ] **Step 3: Implement.**
  - Import: `from metascan.core.storyboard_story import PACING_VALUES` (top of `backend/api/storyboard.py`).
  - `StoryboardPatch` += `pacing: Optional[str] = None`.
  - `ScenePatch` += `arc_beats: Optional[List[str]] = None`, `charge_in: Optional[int] = None`, `charge_out: Optional[int] = None`.
  - `PanelPatch` += `is_turn: Optional[int] = None`, `subtext: Optional[str] = None`.
  - `BeatPatch` += `composition: Optional[str] = None`, `light_quality: Optional[str] = None`, `emotional_intent: Optional[str] = None`, `reveals: Optional[str] = None`, `movement_motivation: Optional[str] = None`.
  - Not-nullable sets: `_STORYBOARD_NOT_NULLABLE` += `"pacing"`; `_SCENE_NOT_NULLABLE` += `"arc_beats"`; `_PANEL_NOT_NULLABLE` += `"is_turn"`.
  - In `patch_storyboard`, after `_reject_null_for_required`:

```python
if "pacing" in fields and fields["pacing"] not in PACING_VALUES:
    raise HTTPException(
        status_code=400,
        detail=f"pacing must be one of: {', '.join(PACING_VALUES)}",
    )
```

- [ ] **Step 4: Run tests, gate, commit**

```bash
venv/bin/pytest tests/test_storyboard_api.py -v
make quality test
git add backend/api/storyboard.py tests/test_storyboard_api.py
git commit -m "feat(storyboard): PATCH surface for pacing/spine/visual-grammar fields"
```

---

### Task 9: Frontend types + pacing setting

**Files:**
- Modify: `frontend/src/types/storyboard.ts`
- Modify: `frontend/src/api/storyboard.ts` (patch body types — run `grep -n "patchStoryboard\|patchScene\|patchPanel\|patchBeat" frontend/src/api/storyboard.ts` and extend whichever typed bodies exist; if bodies are `Record<string, unknown>` no change is needed)
- Modify: `frontend/src/components/storyboard/StoryboardSettingsDialog.vue`

**Interfaces:**
- Produces: `Beat` += `composition/light_quality/emotional_intent/reveals/movement_motivation: string | null`; `Panel` += `is_turn: 0 | 1`, `subtext: string | null`; `Scene` += `arc_beats: string[]`, `charge_in: number | null`, `charge_out: number | null`; `StoryboardSummary` += `pacing: string`; constants `PACINGS`, `COMPOSITIONS`, `LIGHT_QUALITIES`. Tasks 10-12 import these names.

- [ ] **Step 1: Extend the types** — in `types/storyboard.ts` add to the interfaces exactly as listed above (`PanelWithoutBeats`/`BeatWithoutImages` are `Omit` types and pick the new fields up automatically), plus:

```ts
export const PACINGS = ['contemplative', 'standard', 'propulsive'] as const
export const COMPOSITIONS = [
  'thirds_left', 'thirds_right', 'centered', 'symmetrical',
  'negative_space', 'frame_in_frame', 'leading_lines', 'deep_staging',
] as const
export const LIGHT_QUALITIES = [
  'hard', 'soft', 'dappled', 'practical', 'window', 'firelight', 'ambient',
] as const
```

- [ ] **Step 2: Pacing select in `StoryboardSettingsDialog.vue`** — mirror the `aspectRatio` field pattern exactly (lines ~18, ~67, ~83, ~135, ~153, ~369): add `const pacing = ref('standard')`, load `pacing.value = t.pacing` where the dialog hydrates from the tree, record `pacing: string` in the `original` snapshot shape and `original.pacing`, diff on save (`if (pacing.value !== original.pacing) body.pacing = pacing.value`), and add the control near the aspect-ratio select:

```html
<label for="ss-pacing">Pacing</label>
<select id="ss-pacing" v-model="pacing">
  <option v-for="p in PACINGS" :key="p" :value="p">{{ PACING_LABELS[p] }}</option>
</select>
```

```ts
import { PACINGS } from '../../types/storyboard'
const PACING_LABELS: Record<string, string> = {
  contemplative: 'Contemplative — long held shots, ~6–8s per beat',
  standard: 'Standard — most drama, ~4–5s per beat',
  propulsive: 'Propulsive — chase/argument energy, ~2–3s per beat',
}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npm run build`
Expected: PASS (vue-tsc + vite build)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/storyboard.ts frontend/src/api/storyboard.ts frontend/src/components/storyboard/StoryboardSettingsDialog.vue
git commit -m "feat(storyboard): frontend types + pacing setting"
```

---

### Task 10: ShotHeader — turn badge + subtext

**Files:**
- Modify: `frontend/src/components/storyboard/ShotHeader.vue`

**Interfaces:**
- Consumes: `Panel.is_turn`/`Panel.subtext` (Task 9), `store.patchPanelFields` (existing).

- [ ] **Step 1: Script additions** — follow the file's existing `actionVal`/`actionSnap` + `syncField` pattern (lines ~23-69):

```ts
const subtextVal = ref('')
const subtextSnap = ref('')
// add inside the existing sync watcher body:
//   syncField(subtextVal, subtextSnap, props.panel.subtext ?? '')

function commitSubtext(e: Event): void {
  const val = (e.target as HTMLInputElement).value
  subtextVal.value = val
  subtextSnap.value = val
  const next = val.trim() || null
  if (next === (props.panel.subtext ?? null)) return
  void store.patchPanelFields(props.panel.id, { subtext: next })
}

function toggleTurn(): void {
  void store.patchPanelFields(props.panel.id, {
    is_turn: props.panel.is_turn ? 0 : 1,
  })
}
```

- [ ] **Step 2: Template** — next to the action input (~line 234) add:

```html
<button
  type="button"
  class="sh-turn"
  :class="{ on: panel.is_turn === 1 }"
  title="Mark as the story's turn"
  @click="toggleTurn"
>★ Turn</button>
```

and below the action row:

```html
<label class="sh-field">
  <span>Subtext</span>
  <input
    type="text"
    :value="subtextVal"
    placeholder="what the shot means but doesn't show"
    @change="commitSubtext"
  />
</label>
```

Style `.sh-turn` to match the file's existing button styles, with an accent color when `.on` (reuse whatever accent variable the component already uses).

- [ ] **Step 3: Type-check + visual sanity**

Run: `cd frontend && npm run build`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/storyboard/ShotHeader.vue
git commit -m "feat(storyboard): turn badge + subtext field on the shot header"
```

---

### Task 11: BeatCard — composition/light selects + Cinematography disclosure

**Files:**
- Modify: `frontend/src/components/storyboard/BeatCard.vue`

**Interfaces:**
- Consumes: `COMPOSITIONS`, `LIGHT_QUALITIES` (Task 9), `store.patchBeatFields` (existing).

- [ ] **Step 1: Script additions** — extend the imports from `types/storyboard` with `COMPOSITIONS, LIGHT_QUALITIES`; add local/snapshot refs and extend `sync()` (lines ~36-93) following the existing per-field blocks exactly:

```ts
const compositionVal = ref('')
const compositionSnap = ref('')
const lightQualityVal = ref('')
const lightQualitySnap = ref('')
const emotionalVal = ref('')
const emotionalSnap = ref('')
const revealsVal = ref('')
const revealsSnap = ref('')
const motivationVal = ref('')
const motivationSnap = ref('')
```

In `sync()` add five blocks mirroring the `shotSize` block (e.g. `const comp = props.beat.composition ?? ''` …). Commit handlers mirror `commitShotSize` for the selects and `commitPrompt`'s shape for the text fields:

```ts
function commitComposition(e: Event): void {
  const val = (e.target as HTMLSelectElement).value
  compositionVal.value = val
  compositionSnap.value = val
  const next = val === '' ? null : val
  if (next === props.beat.composition) return
  void store.patchBeatFields(props.beat.id, { composition: next })
}
// commitLightQuality identical with light_quality
function commitCineText(
  field: 'emotional_intent' | 'reveals' | 'movement_motivation',
  local: typeof emotionalVal,
  snap: typeof emotionalSnap,
  e: Event,
): void {
  const val = (e.target as HTMLInputElement).value
  local.value = val
  snap.value = val
  const next = val.trim() || null
  if (next === (props.beat[field] ?? null)) return
  void store.patchBeatFields(props.beat.id, { [field]: next })
}
```

- [ ] **Step 2: Template** — in the framing row (next to the existing shot-size/angle/lens selects, ~line 385) add:

```html
<label>Comp
  <select :value="compositionVal" @change="commitComposition">
    <option value="">—</option>
    <option v-for="c in COMPOSITIONS" :key="c" :value="c">{{ c.replace(/_/g, ' ') }}</option>
  </select>
</label>
<label>Light
  <select :value="lightQualityVal" @change="commitLightQuality">
    <option value="">—</option>
    <option v-for="l in LIGHT_QUALITIES" :key="l" :value="l">{{ l }}</option>
  </select>
</label>
```

Below the framing/camera rows add the collapsed disclosure:

```html
<details class="bc-cine">
  <summary>Cinematography</summary>
  <label>Intent
    <input type="text" :value="emotionalVal"
      placeholder="visible physical evidence — never an emotion label"
      @change="commitCineText('emotional_intent', emotionalVal, emotionalSnap, $event)" />
  </label>
  <label>Reveals
    <input type="text" :value="revealsVal"
      placeholder="what this beat shows that the last one didn't"
      @change="commitCineText('reveals', revealsVal, revealsSnap, $event)" />
  </label>
  <label>Move why
    <input type="text" :value="motivationVal"
      placeholder="what pulls the camera (required for any move)"
      @change="commitCineText('movement_motivation', motivationVal, motivationSnap, $event)" />
  </label>
</details>
```

Style `.bc-cine` consistently with the card's existing field groups (same label/input classes the file already uses).

- [ ] **Step 3: Type-check**

Run: `cd frontend && npm run build`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/storyboard/BeatCard.vue
git commit -m "feat(storyboard): composition/light selects + cinematography fields on beat cards"
```

---

### Task 12: OutlineRail chips + compose-warning toast

**Files:**
- Modify: `frontend/src/components/storyboard/OutlineRail.vue`
- Modify: `frontend/src/stores/storyboard.ts` (WS handler ~line 734)

**Interfaces:**
- Consumes: `Scene.charge_in/charge_out/arc_beats`, `Panel.is_turn` (Task 9); `story_stage_complete` payload's `warnings` (Task 6); `useToast` (existing singleton composable).

- [ ] **Step 1: OutlineRail scene chip** — after `.or-scene-name` (~line 9):

```html
<span
  v-if="scene.charge_in !== null && scene.charge_out !== null"
  class="or-charge"
  :class="chargeClass(scene)"
  :title="`Emotional charge ${scene.charge_in} → ${scene.charge_out}`"
>{{ scene.charge_in }} → {{ scene.charge_out }}</span>
```

```ts
import type { Scene } from '../../types/storyboard'

function chargeClass(scene: Scene): string {
  const ci = scene.charge_in ?? 0
  const co = scene.charge_out ?? 0
  return co > ci ? 'up' : co < ci ? 'down' : 'flat'
}
```

CSS (match the rail's existing chip/count styling scale):

```css
.or-charge {
  font-size: 10px;
  padding: 0 4px;
  border-radius: 3px;
  opacity: 0.85;
}
.or-charge.up { color: var(--ok, #4caf50); }
.or-charge.down { color: var(--warn, #e57373); }
.or-charge.flat { opacity: 0.5; }
```

(Use whatever color tokens the component's `<style>` already references; fall back to the literals above only if none exist.)

- [ ] **Step 2: Turn star on shot rows** — inside `.or-shot-line1` (~line 36):

```html
<span v-if="p.is_turn === 1" class="or-turn" title="Story turn">★</span>
{{ i + 1 }}. {{ p.action }}
```

with `.or-turn { color: var(--accent, #ffb300); margin-right: 2px; }`.

- [ ] **Step 3: Warning toast** — in `stores/storyboard.ts`, import `useToast` from `../composables/useToast` and replace the `story_stage_complete` branch:

```ts
} else if (event === 'story_stage_complete') {
  const warns = Array.isArray(d.warnings) ? (d.warnings as string[]) : []
  if (warns.length) {
    useToast().show(
      `Composed ${String(d.stage)} with ${warns.length} style warning${warns.length === 1 ? '' : 's'} (see server log)`,
      'warn',
      4000,
    )
  }
  void refresh() // each stage lands reviewable state immediately
}
```

(`useToast`'s state is a module-level singleton, so calling it from a store is safe.)

- [ ] **Step 4: Type-check + commit**

```bash
cd frontend && npm run build
git add frontend/src/components/storyboard/OutlineRail.vue frontend/src/stores/storyboard.ts
git commit -m "feat(storyboard): charge chips, turn marker, compose-warning toast"
```

---

### Task 13: Full verification gate

**Files:** none new.

- [ ] **Step 1: Backend gate**

Run: `make quality test`
Expected: flake8 clean (no E9/F63/F7/F82), black --check clean, mypy clean, pytest all pass (the WSL2 watcher flake excepted — rerun it in isolation if it fails: `venv/bin/pytest tests -k test_file_watcher_triggers_reload -v`).

- [ ] **Step 2: Frontend gate**

Run: `cd frontend && npm run build`
Expected: vue-tsc + vite build pass.

- [ ] **Step 3: Manual smoke (optional but recommended)** — start the server + frontend (`python run_server.py` / `npm run dev`), open a storyboard, run Compose with all stages against a premise, and verify: pacing lands in Settings, charge chips appear on scene rows, exactly one ★ turn shot, beat cards show composition/light/cinematography fields, and a deliberately weird premise surfaces a style-warning toast.

- [ ] **Step 4: Final commit if any fixups were needed**

```bash
git add -A
git commit -m "chore(storyboard): cinematic-polish verification fixups"
```
