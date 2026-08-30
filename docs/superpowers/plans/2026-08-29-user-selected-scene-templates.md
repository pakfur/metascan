# User-Selected Scene Templates + Story Traceability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The user picks a shot-list template per scene (or Free-form) before shots are built; the shots stage is the single code path that honours it; selection problems block the build; the story arc and a per-scene brief reach every downstream prompt; scenes record what they were composed from.

**Architecture:** Three new nullable columns on `scenes` (`template_id`, `brief`, `composed_from`). A pure `validate_assignment` in `shot_templates.py` feeds both `check_compose_gates` (400 before the 202 task) and a tree annotation (`scene.template_problems`/`template_warnings`) the UI renders live. The existing `_apply_template_locked` body becomes a per-scene helper the shots stage calls when `template_id` is set; the standalone apply-template route/button are removed. Prompt builders gain `brief` + arc summaries.

**Tech Stack:** Python 3.11 / FastAPI / SQLite (`DatabaseManager`), pytest; Vue 3 `<script setup>` + Pinia + TypeScript (vue-tsc).

**Spec:** `docs/superpowers/specs/2026-08-29-user-selected-scene-templates-design.md`

## Global Constraints

- Run Python via `venv/bin/python` / `venv/bin/pytest` from `/home/jk/gws/metascan` (no bare `python` on PATH). Full gate: `make quality test` (flake8 + black --check + mypy strict on `metascan/core/*` + pytest). Known WSL2 flake: `tests/test_prompt_store.py::test_file_watcher_triggers_reload` — passes in isolation, not a regression.
- Frontend gate: `cd frontend && npm run build` (vue-tsc + vite). No test runner in frontend.
- `database_sqlite.py` convention: `import json as _json` locally inside each method that needs it.
- New scene columns are added with `_idempotent_add_column(conn, "scenes", <col>, "ALTER TABLE scenes ADD COLUMN …")` next to the existing `function` add at `metascan/core/database_sqlite.py:1163`.
- PATCH routes use `exclude_unset`; explicit `null` clears a nullable column. `_SCENE_NOT_NULLABLE` stays `{"name","sort_order","arc_beats"}`.
- Duration mismatch is a **warning**, never an error (user decision).
- Commit after each task (messages end with the Co-Authored-By / Claude-Session trailer from the environment rules). Branch: `feat/shotlist-templates`.

---

### Task 1: Schema + PATCH for `template_id`, `brief`, `composed_from`

**Files:**
- Modify: `metascan/core/database_sqlite.py` (~line 1163 column adds; `_SCENE_UPDATABLE` ~line 1933; `_decode_scene_row` ~line 2494; `replace_storyboard_scenes` ~line 3013; `create_scene`)
- Modify: `backend/api/storyboard.py` (`ScenePatch` ~line 235; `patch_scene` ~line 878; `SceneCreate`)
- Modify: `frontend/src/types/storyboard.ts` (`Scene` interface ~line 133)
- Test: `tests/test_storyboard_db.py`, `tests/test_storyboard_api.py`

**Interfaces:**
- Produces: `scenes.template_id TEXT NULL`, `scenes.brief TEXT NULL`, `scenes.composed_from TEXT NULL` (JSON string). `_decode_scene_row` returns `composed_from` decoded to a dict-or-None. `db.update_scene(id, template_id=…, brief=…, composed_from=<dict|None>)` accepted. `replace_storyboard_scenes` writes `brief` from each scene dict. `PATCH /api/storyboard/scenes/{id}` accepts `template_id` (400 on unknown id) and `brief`.

- [ ] **Step 1: Write the failing DB test**

Append to `tests/test_storyboard_db.py`:

```python
def test_scene_template_brief_composed_from_round_trip(db):
    sb, su, sc, pa = _build_tree(db)
    db.update_scene(
        sc,
        template_id="two_party_negotiation_18",
        brief="A asks; B refuses.",
        composed_from={"stage": "scenes", "outline_hash": "abc", "at": "2026-08-29"},
    )
    scene = db.get_storyboard_tree(sb)["scenes"][0]
    assert scene["template_id"] == "two_party_negotiation_18"
    assert scene["brief"] == "A asks; B refuses."
    assert scene["composed_from"] == {
        "stage": "scenes",
        "outline_hash": "abc",
        "at": "2026-08-29",
    }
    db.update_scene(sc, template_id=None, composed_from=None)
    scene = db.get_storyboard_tree(sb)["scenes"][0]
    assert scene["template_id"] is None and scene["composed_from"] is None


def test_replace_storyboard_scenes_writes_brief(db):
    sb = db.create_storyboard(name="B", target_model="sd", architecture="t2i")
    ids, _ = db.replace_storyboard_scenes(
        sb, [{"name": "S", "brief": "what must happen", "arc_beats": ["setup"]}]
    )
    assert db.get_scene(ids[0])["brief"] == "what must happen"
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/pytest tests/test_storyboard_db.py -k "template_brief or writes_brief" -v`
Expected: FAIL — `ValueError: Not updatable on scenes: brief, composed_from, template_id`.

- [ ] **Step 3: Implement the schema**

In `_init_database`, immediately after the existing `function` add (`database_sqlite.py:1163-1168`):

```python
            _idempotent_add_column(
                conn, "scenes", "template_id", "ALTER TABLE scenes ADD COLUMN template_id TEXT"
            )
            _idempotent_add_column(
                conn, "scenes", "brief", "ALTER TABLE scenes ADD COLUMN brief TEXT"
            )
            _idempotent_add_column(
                conn,
                "scenes",
                "composed_from",
                "ALTER TABLE scenes ADD COLUMN composed_from TEXT",
            )
```

Add `"template_id"`, `"brief"`, `"composed_from"` to `_SCENE_UPDATABLE`.

In `update_scene`, after the `arc_beats` block:

```python
        if "composed_from" in fields:
            import json as _json

            cf = fields["composed_from"]
            fields["composed_from"] = _json.dumps(cf) if cf is not None else None
```

In `_decode_scene_row`, after the `arc_beats` decode:

```python
        cf_raw = d.get("composed_from")
        try:
            cf = _json.loads(cf_raw) if cf_raw else None
        except (ValueError, TypeError):
            cf = None
        d["composed_from"] = cf if isinstance(cf, dict) else None
```

In `replace_storyboard_scenes`, extend the INSERT column list with `brief` (after `function`), add a 15th `?`, and append `sc.get("brief")` to the tuple. In `create_scene` (find `def create_scene`), accept `brief: Optional[str] = None` and `template_id: Optional[str] = None` and write them the same way the method writes `function`.

- [ ] **Step 4: Run DB tests**

Run: `venv/bin/pytest tests/test_storyboard_db.py -v`
Expected: all PASS.

- [ ] **Step 5: Write the failing API test**

Append to `tests/test_storyboard_api.py` (use the file's existing `client` fixture and `_board`/scene helper pattern — look at `test_scene_crud` for the shape):

```python
def test_patch_scene_template_id_and_brief(client):
    c, db = client
    sb = db.create_storyboard(name="B", target_model="sd", architecture="t2i")
    sc = db.create_scene(sb, name="S")
    r = c.patch(f"/api/storyboard/scenes/{sc}", json={"template_id": "nope"})
    assert r.status_code == 400 and "unknown template" in r.json()["detail"]
    r = c.patch(
        f"/api/storyboard/scenes/{sc}",
        json={"template_id": "two_party_negotiation_18", "brief": "b"},
    )
    assert r.status_code == 200
    scene = db.get_scene(sc)
    assert scene["template_id"] == "two_party_negotiation_18" and scene["brief"] == "b"
    r = c.patch(f"/api/storyboard/scenes/{sc}", json={"template_id": None})
    assert r.status_code == 200 and db.get_scene(sc)["template_id"] is None
```

- [ ] **Step 6: Run to verify failure**

Run: `venv/bin/pytest tests/test_storyboard_api.py -k template_id_and_brief -v`
Expected: FAIL (422 or field silently dropped).

- [ ] **Step 7: Implement the route**

In `backend/api/storyboard.py`, add to `ScenePatch` and `SceneCreate`:

```python
    template_id: Optional[str] = None
    brief: Optional[str] = None
```

Add a validator next to `_validate_scene_function`:

```python
def _validate_template_id(value: Optional[str]) -> None:
    if value is None:
        return
    from metascan.core import shot_templates

    try:
        shot_templates.get_template(value)
    except shot_templates.TemplateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

In `patch_scene` after the `function` check: `if "template_id" in fields: _validate_template_id(fields["template_id"])`. Do the same in the scene create route. (`composed_from` is server-owned — not exposed on the PATCH model.)

- [ ] **Step 8: Frontend type**

In `frontend/src/types/storyboard.ts` `Scene`, after `function: string | null`:

```ts
  brief: string | null
  template_id: string | null
  composed_from: { stage: string; template_id?: string | null; outline_hash: string; at: string } | null
  template_problems: string[]
  template_warnings: string[]
  outline_stale: boolean
```

(The last three are populated by Task 3's annotation; typing them now avoids a second edit.)

- [ ] **Step 9: Run gates**

Run: `venv/bin/pytest tests/test_storyboard_db.py tests/test_storyboard_api.py -q && venv/bin/black metascan backend tests && cd frontend && npx vue-tsc --noEmit`
Expected: PASS / clean.

- [ ] **Step 10: Commit**

```bash
git add metascan/core/database_sqlite.py backend/api/storyboard.py frontend/src/types/storyboard.ts tests/test_storyboard_db.py tests/test_storyboard_api.py
git commit -m "feat(storyboard): scenes.template_id/brief/composed_from columns + PATCH"
```

---

### Task 2: Pure `validate_assignment` in `shot_templates.py`

**Files:**
- Modify: `metascan/core/shot_templates.py` (append after `summarize`, ~line 368)
- Test: `tests/test_shot_templates.py`

**Interfaces:**
- Produces:
  ```python
  def validate_assignment(
      template_id: Optional[str],
      scene: Mapping[str, Any],
      castable: Sequence[Mapping[str, Any]],
      share_s: Optional[float],
      directory: Optional[Path] = None,
  ) -> Tuple[List[str], List[str]]  # (errors, warnings)
  ```
  `template_id None` → `([], [])`. Errors: unknown template; roles > castable. Warnings: function mismatch; `share_s` given and `abs(template.duration_s - share_s) > 0.35 * share_s`. Messages start with `f"Scene {scene['name']!r}: "`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_shot_templates.py` (module already imports `shot_templates as t` and defines `PILOT`):

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/pytest tests/test_shot_templates.py -k validate_assignment -v`
Expected: FAIL — `AttributeError: module … has no attribute 'validate_assignment'`.

- [ ] **Step 3: Implement**

Append to `metascan/core/shot_templates.py` after `summarize`:

```python
# ---- Selection validation (user-selected templates) -------------------------

_DURATION_TOLERANCE = 0.35


def validate_assignment(
    template_id: Optional[str],
    scene: Mapping[str, Any],
    castable: Sequence[Mapping[str, Any]],
    share_s: Optional[float],
    directory: Optional[Path] = None,
) -> Tuple[List[str], List[str]]:
    """Check a user's template choice for one scene. Returns
    ``(errors, warnings)``; errors block the shots stage, warnings are
    advisory (duration mismatch is a warning by decision -- the user
    chose the template knowing its length). ``template_id`` None is
    free-form and always clean."""
    if not template_id:
        return [], []
    who = f"Scene {scene.get('name')!r}: "
    try:
        template = get_template(template_id, directory)
    except TemplateError:
        return [f"{who}unknown template {template_id!r}"], []
    errors: List[str] = []
    warnings: List[str] = []
    if len(castable) < len(template.roles):
        errors.append(
            f"{who}template {template.id!r} needs {len(template.roles)} "
            f"characters, the storyboard has {len(castable)}"
        )
    fn = scene.get("function")
    if fn and fn != template.function:
        warnings.append(
            f"{who}template function {template.function!r} differs from the "
            f"scene's {fn!r}"
        )
    if share_s and abs(template.duration_s - share_s) > _DURATION_TOLERANCE * share_s:
        warnings.append(
            f"{who}template runs {template.duration_s:g}s but this scene's "
            f"share of the story is about {share_s:.0f}s"
        )
    return errors, warnings
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/pytest tests/test_shot_templates.py -k validate_assignment -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/shot_templates.py tests/test_shot_templates.py
git commit -m "feat(templates): validate_assignment for user-selected scene templates"
```

---

### Task 3: Tree annotation — `template_problems`, `template_warnings`, `outline_stale`

**Files:**
- Modify: `metascan/core/shot_templates.py` (append)
- Modify: `backend/api/storyboard.py` (`get_storyboard` route ~line 447)
- Test: `tests/test_shot_templates.py`

**Interfaces:**
- Produces: `shot_templates.outline_hash(outline_json: Optional[str]) -> str` (sha1 hex of the stripped string, `""` for empty) and `shot_templates.annotate_tree(tree: Dict[str, Any]) -> Dict[str, Any]` (mutates + returns; sets on every scene `template_problems: List[str]`, `template_warnings: List[str]`, `outline_stale: bool`; sets `tree["outline_hash"]`). `share_s` per scene = `duration_target_s / len(scenes)` where `duration_target_s` comes from the parsed outline (None when absent).
- Consumes: Task 2's `validate_assignment`; `story.castable_subjects`.

- [ ] **Step 1: Write the failing test**

```python
def test_annotate_tree_marks_problems_and_staleness():
    outline = json.dumps({"duration_target_s": 36, "arc": []})
    h = t.outline_hash(outline)
    tree = {
        "outline": outline,
        "subjects": [{"id": 1, "name": "A", "subject_type": "character"}],
        "scenes": [
            {"id": 1, "name": "S1", "function": "negotiation", "template_id": PILOT,
             "composed_from": {"stage": "scenes", "outline_hash": h, "at": "x"}},
            {"id": 2, "name": "S2", "function": None, "template_id": None,
             "composed_from": {"stage": "scenes", "outline_hash": "old", "at": "x"}},
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
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/pytest tests/test_shot_templates.py -k annotate_tree -v`
Expected: FAIL (`outline_hash` missing).

- [ ] **Step 3: Implement**

```python
import hashlib  # at top of shot_templates.py


def outline_hash(outline_json: Optional[str]) -> str:
    text = (outline_json or "").strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest() if text else ""


def scene_share_s(tree: Mapping[str, Any]) -> Optional[float]:
    """Each scene's even share of the outline's duration target, or None
    when there's no target / no scenes."""
    try:
        outline = json.loads(tree.get("outline") or "{}") or {}
    except (TypeError, ValueError):
        return None
    target = outline.get("duration_target_s")
    n = len(tree.get("scenes") or [])
    if not target or n == 0:
        return None
    return float(target) / n


def annotate_tree(tree: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the live template-selection check and outline staleness to
    every scene of a storyboard tree (GET /api/storyboard/{id})."""
    from metascan.core.storyboard_story import castable_subjects

    castable = castable_subjects(tree.get("subjects") or [])
    share = scene_share_s(tree)
    current = outline_hash(tree.get("outline"))
    tree["outline_hash"] = current
    for scene in tree.get("scenes") or []:
        errors, warnings = validate_assignment(
            scene.get("template_id"), scene, castable, share
        )
        scene["template_problems"] = errors
        scene["template_warnings"] = warnings
        cf = scene.get("composed_from") or {}
        scene["outline_stale"] = bool(cf) and cf.get("outline_hash") != current
    return tree
```

Check `castable_subjects` is importable without a cycle (`shot_templates` already imports from `storyboard_story` at module top — move this import to the top-level import block instead of a local import).

In `backend/api/storyboard.py` `get_storyboard`, after the 404 check:

```python
    from metascan.core import shot_templates

    return shot_templates.annotate_tree(tree)
```

- [ ] **Step 4: Run tests**

Run: `venv/bin/pytest tests/test_shot_templates.py tests/test_storyboard_api.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/shot_templates.py backend/api/storyboard.py tests/test_shot_templates.py
git commit -m "feat(storyboard): annotate tree with template problems + outline staleness"
```

---

### Task 4: Scenes stage emits `brief`, sees the premise, records `composed_from`

**Files:**
- Modify: `metascan/core/storyboard_story.py` (`_SCENES_TEMPLATE` ~line 252; `validate_scenes_response` ~line 579; `build_scenes_user_prompt` ~line 375)
- Modify: `data/meta_prompt.yml` (`STORY_SCENES_SYSTEM` line 475)
- Modify: `metascan/core/storyboard_runner.py` (scenes stage ~line 548-573)
- Test: `tests/test_storyboard_story.py` (or wherever `validate_scenes_response` is tested — `grep -ln validate_scenes_response tests/`), `tests/test_storyboard_compose.py`

**Interfaces:**
- Produces: scenes grammar has a required `"brief": string` field after `"function"`; `validate_scenes_response` returns `brief` (via `_clean`); `build_scenes_user_prompt(outline_json, premise)`; each written scene has `composed_from = {"stage": "scenes", "template_id": None, "outline_hash": <hash>, "at": <iso>}`.

- [ ] **Step 1: Write the failing tests**

In the story test module:

```python
def test_scenes_grammar_and_validator_carry_brief():
    assert '"\\"brief\\""' in story.SCENES_GRAMMAR
    raw = json.dumps([{"name": "S", "arc_beats": [], "charge_in": 0, "charge_out": 1,
                       "function": "negotiation", "brief": "A asks B."}])
    assert story.validate_scenes_response(raw)[0]["brief"] == "A asks B."


def test_scenes_prompt_includes_premise():
    p = story.build_scenes_user_prompt('{"logline": "L"}', "Two rivals meet.")
    assert "Premise:\nTwo rivals meet." in p and "Story outline:" in p
```

In `tests/test_storyboard_compose.py`, add `"brief": "Maya arrives."` to the `SCENES` constant, and:

```python
def test_scenes_stage_writes_brief_and_composed_from(db, tmp_path):
    vlm = FakeVlm()
    runner = StoryboardRunner(db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path)
    sb = _board(db)
    asyncio.run(runner.compose_story(sb, stages=("outline", "scenes")))
    scene = db.get_storyboard_tree(sb)["scenes"][0]
    assert scene["brief"] == "Maya arrives."
    cf = scene["composed_from"]
    assert cf["stage"] == "scenes" and cf["template_id"] is None
    from metascan.core.shot_templates import outline_hash
    assert cf["outline_hash"] == outline_hash(db.get_storyboard_tree(sb)["outline"])
    assert "Premise:" in vlm.prompts[1]
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/pytest tests/test_storyboard_story.py tests/test_storyboard_compose.py -k "brief or premise" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`_SCENES_TEMPLATE`: append to the `scene ::=` rule, after `"\"function\"" ws ":" ws scenefn`, the fragment `ws "," ws "\"brief\"" ws ":" ws string` (before the closing `ws "}}"`).

`validate_scenes_response`: add `"brief": _clean(sc.get("brief")),` after `"function"`.

`build_scenes_user_prompt`:

```python
def build_scenes_user_prompt(outline_json: str, premise: str = "") -> str:
    premise_block = f"Premise:\n{premise.strip()}\n\n" if premise.strip() else ""
    return (
        f"{premise_block}Story outline:\n{outline_json}\n\n"
        "Write the scene list JSON."
    )
```

`data/meta_prompt.yml` `STORY_SCENES_SYSTEM`: append one sentence to the existing block: `brief states, in one to three sentences, what this scene must accomplish for the story — derived from the arc entries it covers and the premise, phrased as dramatic intent (what changes, for whom), never as camera direction.`

Runner scenes stage: pass `tree.get("source_text") or ""` as the second argument to `build_scenes_user_prompt`; after `replace_storyboard_scenes` returns `new_ids`, write provenance:

```python
            from metascan.core.shot_templates import outline_hash
            from datetime import datetime, timezone

            stamp = {
                "stage": "scenes",
                "template_id": None,
                "outline_hash": outline_hash(outline_json),
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            for sid in new_ids:
                await asyncio.to_thread(self.db.update_scene, sid, composed_from=stamp)
```

(Capture `new_ids` from the `replace_storyboard_scenes` tuple — currently discarded as `_`.)

- [ ] **Step 4: Run tests + fake-VLM fixture checks**

Run: `venv/bin/pytest tests/test_storyboard_story.py tests/test_storyboard_compose.py tests/test_prompt_store.py -q`
Expected: PASS (except the known watcher flake).

- [ ] **Step 5: Commit**

```bash
git add metascan/core/storyboard_story.py metascan/core/storyboard_runner.py data/meta_prompt.yml tests/
git commit -m "feat(story): scenes stage emits brief, sees premise, records composed_from"
```

---

### Task 5: Arc summaries + brief reach beats, bind and fill prompts

**Files:**
- Modify: `metascan/core/storyboard_story.py` (`build_beats_user_prompt` ~line 419; add `arc_summary_lines`)
- Modify: `metascan/core/shot_templates.py` (`build_role_bind_user_prompt` ~line 396, `build_fill_user_prompt` ~line 549)
- Test: `tests/test_storyboard_story.py`, `tests/test_shot_templates.py`

**Interfaces:**
- Produces: `story.arc_summary_lines(outline: Mapping, scene: Mapping) -> str` — one `- <beat>: <summary>` line per outline arc entry whose `beat` is in `scene["arc_beats"]`; `""` when none. All three builders emit `Scene brief: …` and `Arc covered by this scene:\n…` blocks (omitted when empty).

- [ ] **Step 1: Write the failing tests**

Story test module:

```python
def test_arc_summary_lines_filters_to_scene():
    outline = {"arc": [{"beat": "setup", "summary": "S"}, {"beat": "turn", "summary": "T"}]}
    assert story.arc_summary_lines(outline, {"arc_beats": ["turn"]}) == "- turn: T"
    assert story.arc_summary_lines(outline, {"arc_beats": []}) == ""


def test_beats_prompt_carries_brief_and_arc():
    outline = {"logline": "L", "arc": [{"beat": "turn", "summary": "T"}]}
    scene = {"name": "S", "arc_beats": ["turn"], "brief": "B must break."}
    panel = {"action": "act", "duration_s": 10}
    p = story.build_beats_user_prompt(outline, scene, panel, [], story.pacing_guidance("standard", 15.0), None, None)
    assert "Scene brief: B must break." in p
    assert "Arc covered by this scene:\n- turn: T" in p
```

Template test module (extend `test_prompts_carry_slot_specs_and_previous_sections`'s setup):

```python
def test_template_prompts_carry_brief_and_arc():
    tp = t.get_template(PILOT)
    outline = {"logline": "L", "arc": [{"beat": "turn", "summary": "T"}]}
    scene = {"name": "S", "arc_beats": ["turn"], "brief": "B must break.", "setting": "x"}
    subjects = [{"id": 1, "name": "A", "description": "d"}, {"id": 2, "name": "B", "description": "d"}]
    bind = t.build_role_bind_user_prompt(tp, scene, subjects, outline)
    fill = t.build_fill_user_prompt(tp, tp.sections[0], {"A": "A", "B": "B"}, scene, subjects, outline)
    for p in (bind, fill):
        assert "Scene brief: B must break." in p
        assert "Arc covered by this scene:\n- turn: T" in p
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/pytest tests/test_storyboard_story.py tests/test_shot_templates.py -k "arc or brief" -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `storyboard_story.py`, before `build_beats_user_prompt`:

```python
def arc_summary_lines(outline: Mapping[str, Any], scene: Mapping[str, Any]) -> str:
    covered = set(scene.get("arc_beats") or [])
    return "\n".join(
        f"- {e.get('beat')}: {e.get('summary')}"
        for e in (outline.get("arc") or [])
        if isinstance(e, dict) and e.get("beat") in covered
    )


def scene_context_block(outline: Mapping[str, Any], scene: Mapping[str, Any]) -> str:
    """Brief + arc summaries, formatted for a user prompt; empty when the
    scene carries neither."""
    parts = []
    if scene.get("brief"):
        parts.append(f"Scene brief: {scene['brief']}\n")
    arc = arc_summary_lines(outline, scene)
    if arc:
        parts.append(f"Arc covered by this scene:\n{arc}\n")
    return "".join(parts)
```

In `build_beats_user_prompt`, insert `f"{scene_context_block(outline, scene)}"` right after the `mood:` line. In `shot_templates.py`, import `scene_context_block` from `storyboard_story` and insert `f"{scene_context_block(outline, scene)}"` after the `Scene notes:` line in both builders.

- [ ] **Step 4: Run tests**

Run: `venv/bin/pytest tests/test_storyboard_story.py tests/test_shot_templates.py tests/test_storyboard_compose.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/storyboard_story.py metascan/core/shot_templates.py tests/
git commit -m "feat(story): feed scene brief + arc summaries to beats and template prompts"
```

---

### Task 6: Shots stage honours `scene.template_id`; gates block bad selections; standalone route removed

**Files:**
- Modify: `metascan/core/storyboard_runner.py` (`check_compose_gates` ~line 253; shots stage ~line 575; beats stage `scene_work` ~line 655; template block ~line 753-968)
- Modify: `backend/api/storyboard.py` (remove `ApplyTemplateRequest` + `apply_shot_template` route)
- Modify: `tests/test_shot_templates.py` (runner + API tests), `tests/test_storyboard_compose.py`
- Modify: `CLAUDE.md` (template bullet)

**Interfaces:**
- Consumes: Task 2 `validate_assignment`, Task 3 `scene_share_s`, `outline_hash`.
- Produces: `StoryboardRunner._template_scene(tree, scene, template, vlm, purge) -> Tuple[int, int]` (panels, beats) — the former `_apply_template_locked` body minus gating/locking/events. Shots stage: per target scene, `template_id` → `_template_scene`, else free-form; sets `composed_from = {"stage": "shots", "template_id": …, "outline_hash": …, "at": …}` on the scene. Beats stage: skips scenes where `template_id` is set AND every target panel already has beats. `check_compose_gates` with `"shots"` raises `StoryboardError` listing all `validate_assignment` errors across target scenes (before the confirm gate, regardless of `confirm`). `check_template_gates`, `apply_template`, `_apply_template_locked`, the route, and `ApplyTemplateRequest` are deleted. `story_stage_complete` for `shots` carries `template_scenes: [scene_id, …]`.

- [ ] **Step 1: Write the failing tests**

Replace `test_apply_template_writes_scene_and_emits_events`, `test_apply_template_gates`, and `test_templates_endpoint_and_apply_route` in `tests/test_shot_templates.py` with:

```python
def _shots_vlm():
    """Template FakeVlm that also answers the free-form shots/beats grammars."""
    from metascan.core import storyboard_story as story

    class Both(FakeVlm):
        async def generate_text(self, *, system_prompt, user_prompt, grammar=None, **kw):
            if grammar == story.SHOTS_GRAMMAR:
                return json.dumps([{"action": "free", "duration_s": 8, "subtext": "s", "is_turn": False}])
            if grammar == story.BEATS_GRAMMAR:
                return json.dumps([{"duration_s": 8, "action": "b", "reveals": None, "emotional_intent": None,
                                    "shot_size": "WS", "angle": None, "lens": None, "composition": None,
                                    "light_quality": None, "subjects": [], "camera_motion": None,
                                    "camera_amplitude": None, "camera_speed": None,
                                    "movement_motivation": None, "is_cut": False, "sound": None, "dialog": []}])
            return await super().generate_text(system_prompt=system_prompt, user_prompt=user_prompt, grammar=grammar, **kw)

    return Both()


def test_shots_stage_uses_scene_template_and_records_provenance(db, tmp_path):
    sb, scene = _board(db)
    free = db.create_scene(sb, name="Free scene")
    db.update_scene(scene, template_id=PILOT)
    vlm = _shots_vlm()
    runner = StoryboardRunner(db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path)
    events = []
    runner.on_event(lambda ch, ev, d: events.append((ch, ev, d)))
    asyncio.run(runner.compose_story(sb, stages=("shots", "beats")))
    tree = db.get_storyboard_tree(sb)
    templated, freeform = tree["scenes"]
    assert len(templated["panels"]) == 5
    assert sum(len(p["beats"]) for p in templated["panels"]) == 18
    assert templated["composed_from"]["stage"] == "shots"
    assert templated["composed_from"]["template_id"] == PILOT
    assert len(freeform["panels"]) == 1 and len(freeform["panels"][0]["beats"]) == 1
    assert freeform["composed_from"]["template_id"] is None
    done = [d for ch, ev, d in events if ev == "story_stage_complete" and d["stage"] == "shots"]
    assert done[0]["template_scenes"] == [scene]
    # Beats stage must not have re-run over the template-built scene.
    assert templated["panels"][0]["beats"][0]["action"].startswith("slot 0")


def test_shots_gate_lists_every_selection_problem(db, tmp_path):
    sb, scene = _board(db)
    other = db.create_scene(sb, name="Other")
    db.update_scene(scene, template_id="nope")
    db.update_scene(other, template_id=PILOT)
    friend = db.get_storyboard_tree(sb)["subjects"][1]["id"]
    db.update_subject(friend, subject_type="prop")
    vlm = FakeVlm()
    runner = StoryboardRunner(db=db, comfy=None, get_vlm=lambda: vlm, output_root=tmp_path)
    with pytest.raises(StoryboardError) as exc:
        asyncio.run(runner.check_compose_gates(sb, ("shots",), None, None, False))
    msg = str(exc.value)
    assert "unknown template 'nope'" in msg and "needs 2 characters" in msg
    assert vlm.calls == []


def test_templates_endpoint_lists_pilot(client):
    c, db = client
    r = c.get("/api/storyboard/templates")
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [PILOT]
    sb, scene = _board(db)
    assert c.post(f"/api/storyboard/{sb}/scenes/{scene}/apply-template", json={"template_id": PILOT}).status_code in (404, 405)
```

Also in `tests/test_storyboard_compose.py`, check the API gate surfaces as 400 — add to `tests/test_storyboard_api.py`'s compose tests (find `test_compose_route`-style test):

```python
def test_compose_shots_400_on_template_problem(client):
    c, db = client
    sb = db.create_storyboard(name="B", target_model="sd", architecture="t2i")
    db.update_storyboard(sb, source_text="p", outline="{}")
    sc = db.create_scene(sb, name="S", template_id="two_party_negotiation_18")
    r = c.post(f"/api/storyboard/{sb}/compose", json={"stages": ["shots"]})
    assert r.status_code == 400 and "needs 2 characters" in r.json()["detail"]
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/pytest tests/test_shot_templates.py tests/test_storyboard_api.py -k "shots_stage or shots_gate or lists_pilot or template_problem" -v`
Expected: FAIL.

- [ ] **Step 3: Gate**

In `check_compose_gates`, right after the outline/premise check and **before** `if confirm: return`:

```python
        if "shots" in stages:
            castable = story.castable_subjects(tree["subjects"])
            share = templates.scene_share_s(tree)
            problems: List[str] = []
            for s in tree["scenes"]:
                if scene_ids is not None and s["id"] not in scene_ids:
                    continue
                errs, _ = templates.validate_assignment(
                    s.get("template_id"), s, castable, share
                )
                problems.extend(errs)
            if problems:
                exc = StoryboardError(
                    "fix these template selections before building shots: "
                    + "; ".join(problems)
                )
                exc._compose_stage = "shots"  # type: ignore[attr-defined]
                raise exc
```

- [ ] **Step 4: Refactor the template body into `_template_scene`**

Delete `check_template_gates`, `apply_template`, `_apply_template_locked`. Add, in the same place:

```python
    async def _template_scene(
        self,
        tree: Dict[str, Any],
        scene: Dict[str, Any],
        template: templates.ShotTemplate,
        vlm: Any,
        purge: bool,
    ) -> Tuple[int, int]:
        """Build one scene's shots + beats from its selected template:
        bind roles (one grammar-constrained VLM call), instantiate, fill
        prose per section (sequential so later sections see the earlier
        exchange), conform (hard-fails), write. Called from the shots
        stage under _synth_lock; returns (panels, beats)."""
        characters = story.castable_subjects(tree["subjects"])
        roster = {s["name"].strip().lower(): int(s["id"]) for s in characters}
        outline: Dict[str, Any] = {}
        try:
            outline = json.loads(tree.get("outline") or "{}") or {}
        except (TypeError, ValueError):
            pass
        # …then the existing body from "# 1. Bind roles." through the
        # replace_scene_panels / replace_panel_beats loop, verbatim, with
        # `scene_id` → `scene["id"]`, `confirm` → `purge`, and the
        # progress()/self._emit calls removed (the shots stage reports
        # progress per scene). Keep the `if not scene.get("function")`
        # backfill. Return (len(panel_ids), n_beats).
```

- [ ] **Step 5: Shots stage branches per scene**

In the shots stage, replace the body of `_shots_for` so it branches:

```python
            template_scenes: List[int] = []
            stamp_hash = templates.outline_hash(outline_json)

            def _stamp(template_id: Optional[str]) -> Dict[str, Any]:
                from datetime import datetime, timezone

                return {
                    "stage": "shots",
                    "template_id": template_id,
                    "outline_hash": stamp_hash,
                    "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }

            async def _shots_for(idx: int, scene: Dict[str, Any]) -> int:
                nonlocal done
                tid = scene.get("template_id")
                if tid:
                    template = templates.get_template(tid)
                    async with sem:
                        n_panels, _ = await self._template_scene(
                            tree, scene, template, vlm, purge
                        )
                    await asyncio.to_thread(
                        self.db.update_scene, scene["id"], composed_from=_stamp(tid)
                    )
                    async with lock:
                        template_scenes.append(scene["id"])
                        done += 1
                        progress(done, total)
                    return n_panels
                # … existing free-form body unchanged …
                await asyncio.to_thread(
                    self.db.update_scene, scene["id"], composed_from=_stamp(None)
                )
                # (before the existing `async with lock:` block)
```

Have `_run_stage` return the extra payload: change its return type to `Tuple[int, List[str], Dict[str, Any]]` where the third element is `{"template_scenes": template_scenes}` for shots and `{}` otherwise; `_compose_locked` merges it into the `story_stage_complete` event dict. (Update the two other `return` statements in `_run_stage` accordingly.)

- [ ] **Step 6: Beats stage skips template-built scenes**

Where `scene_work` is built, filter:

```python
        def _template_done(scene: Dict[str, Any]) -> bool:
            return bool(scene.get("template_id")) and all(
                p.get("beats") for p in scene["panels"]
            )

        scene_work = [(s, ids) for s, ids in scene_work if ids and not _template_done(s)]
```

Explicit `panel_ids` targeting a template-built shot (the "Re-beat shot" button) still gets through because `_template_done` is only consulted when the whole scene's panels have beats — add `and panel_ids is None` to the filter condition so an explicit re-beat is always honoured.

- [ ] **Step 7: Remove the route**

Delete `ApplyTemplateRequest` and `apply_shot_template` from `backend/api/storyboard.py`. Keep `GET /templates`.

- [ ] **Step 8: Run the suite**

Run: `venv/bin/pytest tests/test_shot_templates.py tests/test_storyboard_compose.py tests/test_storyboard_api.py -q`
Expected: PASS. Then `make quality test` (mypy strict on core — watch the `_run_stage` return-type change and the `Tuple` import).

- [ ] **Step 9: CLAUDE.md**

Rewrite the "Shot-list templates replace the shots+beats stages per scene" bullet: the user selects `scenes.template_id` (PATCH, 400 on unknown id); the shots stage is the single path (`_template_scene` when set, free-form when NULL); `check_compose_gates` 400s listing every `validate_assignment` error across target scenes; the beats stage skips template-built scenes unless `panel_ids` is explicit; `scenes.brief`/`composed_from` provenance and `annotate_tree` (`template_problems`/`template_warnings`/`outline_stale`) on `GET /api/storyboard/{id}`; the apply-template route is gone. Note duration mismatch is a warning by decision.

- [ ] **Step 10: Commit**

```bash
git add metascan/core/storyboard_runner.py backend/api/storyboard.py tests/ CLAUDE.md
git commit -m "feat(storyboard): shots stage honours user-selected scene templates; gate blocks bad selections"
```

---

### Task 7: Frontend — API/store, scene editor select, deps table, rail badge

**Files:**
- Modify: `frontend/src/api/storyboard.ts` (remove `applyTemplate` ~line 108)
- Modify: `frontend/src/stores/storyboard.ts` (remove `applyTemplate` ~line 603 and its export ~line 981; `story.stage` union drops `'template'`)
- Modify: `frontend/src/utils/storyboardDeps.ts` (scene rule fields)
- Modify: `frontend/src/components/storyboard/SceneEditDialog.vue`
- Modify: `frontend/src/components/storyboard/OutlineRail.vue`

**Interfaces:**
- Consumes: `Scene.template_id/brief/template_problems/template_warnings/composed_from/outline_stale` (Task 1), `store.templates` + `loadTemplates()` (existing).
- Produces: scene editor PATCHes `template_id` (`null` = Free-form) and `brief`; `storyboardDeps` scene rule includes `'template_id'` and `'brief'` so a change raises the existing "Rebuild shots?" prompt.

- [ ] **Step 1: API + store**

Delete `applyTemplate` from `api/storyboard.ts` and from the store (function + export). In the store's `story` state type, change `stage: ComposeStage | 'template' | null` to `stage: ComposeStage | null` and fix any usage the compiler flags.

- [ ] **Step 2: Deps table**

In `storyboardDeps.ts`, the scene rule's `fields` array gains `'template_id', 'brief'`.

- [ ] **Step 3: SceneEditDialog**

Replace the `// ---- shot template` script block with:

```ts
// ---- shot template (user-selected; the shots stage honours it) --------
const templateId = ref(props.scene?.template_id ?? '')
const brief = ref(props.scene?.brief ?? '')
onMounted(() => { void store.loadTemplates() })

function templateLabel(t: ShotTemplateSummary): string {
  const match = t.function === sceneFunction.value ? '✓ ' : ''
  return `${match}${t.id} · ${t.function} · ${t.slot_count} slots / ${t.duration_s}s`
}
const sortedTemplates = computed(() =>
  [...store.templates].sort((a, b) =>
    Number(b.function === sceneFunction.value) - Number(a.function === sceneFunction.value)),
)
```

Delete `templateBusy`, `templateError`, the auto-select watcher, and `onApplyTemplate`. In `save()`'s PATCH diff add:

```ts
      const newTemplate = templateId.value || null
      if (newTemplate !== (props.scene.template_id ?? null)) body.template_id = newTemplate
      const newBrief = brief.value.trim() || null
      if (newBrief !== (props.scene.brief ?? null)) body.brief = newBrief
```

and to the create branch: `template_id: templateId.value || null, brief: brief.value.trim() || null`.

Template markup — replace the `template-section` div (now shown in create mode too) and add a brief field above Setting:

```html
      <div class="field">
        <label for="se-brief">Brief <span class="hint-inline">what this scene must accomplish</span></label>
        <textarea id="se-brief" v-model="brief" rows="3" placeholder="Derived from the arc entries this scene covers" />
        <p v-if="props.scene?.arc_beats?.length" class="arc-line">
          Covers: <span v-for="b in props.scene.arc_beats" :key="b" class="arc-chip">{{ b }}</span>
          <template v-if="props.scene.charge_in !== null"> · charge {{ props.scene.charge_in }} → {{ props.scene.charge_out }}</template>
        </p>
      </div>

      <div class="field template-section">
        <label for="se-template">Shot template</label>
        <select id="se-template" v-model="templateId">
          <option value="">Free-form (VLM decides the shots)</option>
          <option v-for="t in sortedTemplates" :key="t.id" :value="t.id">{{ templateLabel(t) }}</option>
        </select>
        <p v-for="m in props.scene?.template_problems ?? []" :key="m" class="error inline">{{ m }}</p>
        <p v-for="m in props.scene?.template_warnings ?? []" :key="m" class="warn inline">{{ m }}</p>
      </div>
```

Add `.warn.inline { color: var(--yellow-500, #d4a017); font-size: 12px; margin: 4px 0 0; }`, `.arc-chip { display:inline-block; padding:0 6px; margin-right:4px; border-radius:8px; background: var(--surface-border); font-size:11px; }`, `.arc-line { font-size: 12px; color: var(--text-color-secondary); margin: 6px 0 0; }`. Remove the now-unused `.template-row` rules.

- [ ] **Step 4: OutlineRail badge**

In the scene head, after `.or-charge`:

```html
          <span v-if="scene.template_id" class="or-template" :title="`Shots from template ${scene.template_id}`">⧉</span>
          <span v-if="scene.template_problems.length" class="or-problem" :title="scene.template_problems.join('\n')">!</span>
          <span v-else-if="scene.outline_stale" class="or-stale" title="Outline changed since this scene was built">↻</span>
```

CSS: `.or-template { font-size: 11px; opacity: .7; } .or-problem { color: var(--red-500); font-weight: 700; } .or-stale { color: var(--yellow-500, #d4a017); }`.

- [ ] **Step 5: Type-check**

Run: `cd frontend && npx vue-tsc --noEmit`
Expected: clean (every `Scene` literal in the frontend — e.g. test fixtures or optimistic inserts — must include the new required fields; grep `panels: []` to find them).

- [ ] **Step 6: Commit**

```bash
git add frontend/src
git commit -m "feat(storyboard-ui): scene template select + brief in scene editor; rail badges"
```

---

### Task 8: Outline dialog — arc list + per-scene template table

**Files:**
- Modify: `frontend/src/components/storyboard/OutlineDialog.vue`

**Interfaces:**
- Consumes: `store.tree.outline` (JSON string), `store.tree.scenes[*].{name, arc_beats, charge_in, charge_out, brief, template_id, template_problems, template_warnings}`, `store.templates`, `store.patchSceneFields`.

- [ ] **Step 1: Parse the outline for display**

Add to the script:

```ts
interface ArcEntry { beat: string; summary: string }
const parsedOutline = computed(() => {
  try {
    const o = JSON.parse(outlineText.value || '{}')
    return {
      logline: String(o.logline ?? ''),
      tone: String(o.tone ?? ''),
      pacing: String(o.pacing ?? ''),
      duration: Number(o.duration_target_s ?? 0),
      arc: (Array.isArray(o.arc) ? o.arc : []) as ArcEntry[],
    }
  } catch {
    return null
  }
})
const showRaw = ref(false)

function editArcSummary(i: number, text: string): void {
  const o = JSON.parse(outlineText.value)
  o.arc[i].summary = text
  outlineText.value = JSON.stringify(o, null, 2)
}

onMounted(() => { void store.loadTemplates() })

function onSceneTemplate(sceneId: number, e: Event): void {
  const val = (e.target as HTMLSelectElement).value || null
  void store.patchSceneFields(sceneId, { template_id: val })
}

const shotsBlocked = computed(() =>
  (store.tree?.scenes ?? []).some((s) => s.template_problems.length > 0),
)
```

(`run()` already PATCHes the edited `outlineText` before non-outline stages, so arc edits flow through unchanged.)

- [ ] **Step 2: Markup**

Replace the raw outline textarea block with:

```html
        <label class="field-label">Outline
          <button type="button" class="link" @click="showRaw = !showRaw">{{ showRaw ? 'structured' : 'raw JSON' }}</button>
        </label>
        <textarea v-if="showRaw || !parsedOutline" v-model="outlineText" rows="10" class="import-textarea outline-textarea" :disabled="store.story.running" />
        <div v-else class="outline-view">
          <p class="logline">{{ parsedOutline.logline }}</p>
          <p class="meta">{{ parsedOutline.tone }} · {{ parsedOutline.pacing }} · {{ parsedOutline.duration }}s</p>
          <ol class="arc">
            <li v-for="(e, i) in parsedOutline.arc" :key="i">
              <span class="arc-chip">{{ e.beat }}</span>
              <input :value="e.summary" :disabled="store.story.running" @change="editArcSummary(i, ($event.target as HTMLInputElement).value)" />
            </li>
          </ol>
        </div>

        <template v-if="store.tree?.scenes.length">
          <label class="field-label">Scenes</label>
          <table class="scene-table">
            <tr v-for="s in store.tree.scenes" :key="s.id">
              <td class="name">{{ s.name }}<div class="brief">{{ s.brief }}</div></td>
              <td><span v-for="b in s.arc_beats" :key="b" class="arc-chip">{{ b }}</span></td>
              <td class="charge" v-if="s.charge_in !== null">{{ s.charge_in }} → {{ s.charge_out }}</td>
              <td v-else />
              <td>
                <select :value="s.template_id ?? ''" :disabled="store.story.running" @change="onSceneTemplate(s.id, $event)">
                  <option value="">Free-form</option>
                  <option v-for="t in store.templates" :key="t.id" :value="t.id">{{ t.function === s.function ? '✓ ' : '' }}{{ t.id }}</option>
                </select>
                <p v-for="m in s.template_problems" :key="m" class="error inline">{{ m }}</p>
                <p v-for="m in s.template_warnings" :key="m" class="warn inline">{{ m }}</p>
              </td>
            </tr>
          </table>
        </template>
```

Disable the shots checkbox while blocked: on the `<input type="checkbox">` add `:disabled="store.story.running || (stage === 'shots' && shotsBlocked)"`, and under the picker `<p v-if="shotsBlocked" class="error">Fix the template problems above before building shots.</p>`. Also exclude `shots` from `checkedStages` when blocked:

```ts
const checkedStages = computed<ComposeStage[]>(() =>
  COMPOSE_STAGES.filter((s) => stageChecks[s] && !(s === 'shots' && shotsBlocked.value)),
)
```

CSS: `.scene-table { width:100%; font-size:12px; border-collapse: collapse; } .scene-table td { padding: 6px 4px; vertical-align: top; border-top: 1px solid var(--surface-border); } .scene-table .brief { color: var(--text-color-secondary); } .arc { padding-left: 18px; } .arc li { display:flex; gap:8px; align-items:center; margin: 4px 0; } .arc input { flex:1; }` plus reuse the `.arc-chip`, `.warn.inline`, `.error.inline` rules from Task 7 (copy them — styles are scoped).

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/storyboard/OutlineDialog.vue
git commit -m "feat(storyboard-ui): outline dialog renders the arc and a per-scene template table"
```

---

### Task 9: Full gates + docs

- [ ] **Step 1:** `make quality test` from `/home/jk/gws/metascan` — all green except the documented watcher flake.
- [ ] **Step 2:** `cd frontend && npm run build` — clean.
- [ ] **Step 3:** Update `docs/api-reference.md`: remove `apply-template`, document `template_id`/`brief` on scene PATCH and `template_problems`/`template_warnings`/`outline_stale`/`outline_hash` on the tree; note `POST /compose` 400 for template problems.
- [ ] **Step 4:** Commit: `git commit -am "docs: user-selected scene templates"`.
