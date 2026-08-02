# Phase B — Storyboard Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the storyboard domain on top of the Phase A ComfyUI driver: freeform-text parse, deterministic brief composition, per-panel prompt synthesis, and panel-level job orchestration — headless, fully exercisable via `curl`.

**Architecture:** Five new SQLite tables + a `media.hidden` column, three pure core modules (brief/parse/synthesis), one asyncio orchestrator (`StoryboardRunner`) that composes the existing `VlmClient` and `ComfyClient`, and a REST surface at `/api/storyboard/*` with a `storyboard` WS channel. The ComfyUI driver stays storyboard-free; correlation runs through `generation_jobs.panel_id` and the driver's `job_outputs` event.

**Tech Stack:** Python 3.11, FastAPI, SQLite (sync + `asyncio.to_thread`), llama-server via `VlmClient.generate_text` (GBNF-constrained), ComfyUI via `ComfyClient`.

**Spec:** `docs/superpowers/specs/2026-08-01-storyboard-generator-design.md` (§4.2–§4.4, §6, §7, §9, §10).

## Deviations from the spec (deliberate, recorded here)

1. **`bucket_dims` keys on `target_model`, not `architecture`.** The spec's §5.2 says `bucket_dims(aspect_ratio, architecture)` distinguishing "SDXL" from "Flux", but `meta_prompt_templates.Architecture` is the modality literal (`"t2i"` only). The SDXL-vs-Flux distinction actually lives in `TargetModel`: `{"sd","pony"}` are SDXL-family, everything else gets the multiple-of-16 rule. `storyboards.architecture` still stores the modality string per the schema.
2. **`panels.prompt_source` column added** (`'llm' | 'brief' | 'user'`). Spec §6.4 requires the UI to label whether a panel's prompt came from the LLM or the brief fallback; that needs persistence. A user prompt edit sets `prompt_source='user'` and `prompt_locked=1`.
3. **No `user_version` bump for `media.hidden`.** `ALTER TABLE media ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0` backfills existing rows with 0 at ALTER time in SQLite — the spec's "version 3 backfill" is unnecessary. `user_version` stays 2.
4. **Style block is NOT part of the brief.** Spec §6.2's example brief shows a `STYLE:` line, but §7.2 mandates the style block be concatenated *post-synthesis, outside the LLM call*. §7.2 wins: `compose_brief` omits style; `finalize_prompt(text, style_block)` appends it to both LLM output and brief-fallback prompts.
5. **`generation_jobs.output_dir` column** instead of overriding `ComfyClient.output_dir_for`. The docstring anticipated a Phase B override, but a per-job persisted directory survives restarts and avoids a DB read on the event loop. `collect_outputs` prefers `job["output_dir"]` when set.

## Global Constraints

- `make quality test` (flake8 fatal-clean, `black --check`, mypy strict on `metascan/core/*`, pytest) must pass at every commit.
- No UI-framework imports anywhere in `metascan/core/` or `backend/`.
- All SQLite access is synchronous under `db.lock`, wrapped with `asyncio.to_thread` at every async call site — **never** call a `db.*` method directly from a coroutine.
- Core modules never import `backend.*`; event dispatch uses callbacks wired in the FastAPI lifespan.
- DELETE endpoints return `{"status": "deleted"}`, never 204.
- The multiplexed WS envelope is `{channel, event, data}`; Phase B adds the `storyboard` channel (`panel_images_changed`, `synthesis_progress`) and may broadcast `folder_items_changed` on the existing `folders` channel.
- Every path inserted into a column that foreign-keys `media(file_path)` must go through `to_posix_path` (media PKs are stored POSIX).
- Covering indexes `idx_media_summary_added` / `idx_media_summary_modified` must include every column the summary SELECT projects or filters on — extending them means extending `required_cols` in `_init_database` too.
- GBNF: `\-` is not a valid escape; hyphens must be literal at the start/end of a character class. A bad grammar SIGSEGVs llama-server into a respawn loop.
- Tests use no real ComfyUI, CLIP, or VLM. DB tests use a temp DB (`tests/test_folders_db.py` pattern); API tests use `fastapi.testclient.TestClient`.
- New rows in existing test-count expectations: N/A — CI runs pytest wholesale.

---

## File Structure

| File | Responsibility |
|---|---|
| `metascan/core/database_sqlite.py` (modify) | `media.hidden`, 5 storyboard tables, `generation_jobs.output_dir`, all storyboard CRUD |
| `metascan/core/storyboard_brief.py` (create) | Pure: `bucket_dims`, `panel_seed`, `storyboard_slug`, `compose_brief` |
| `metascan/core/storyboard_parse.py` (create) | Pure: parse-stage prompts, `PARSE_GRAMMAR`, `validate_parse_response` |
| `metascan/core/storyboard_synthesis.py` (create) | Pure: per-target render prompts, `build_render_messages`, `finalize_prompt` |
| `metascan/core/storyboard_runner.py` (create) | Asyncio orchestrator: parse / synthesize / generate / cancel / ingest correlation |
| `metascan/core/vlm_client.py` (modify) | `generate_text` gains `grammar` kwarg |
| `metascan/core/comfy_client.py` (modify) | `submit`/`submit_now` gain `output_dir`; `collect_outputs` honors it |
| `backend/services/media_service.py`, `backend/api/media.py` (modify) | `include_hidden` pass-through |
| `backend/services/storyboard_service.py` (create) | `asyncio.to_thread` wrappers for storyboard CRUD |
| `backend/api/storyboard.py` (create) | REST routes + runner singleton install |
| `backend/main.py` (modify) | Runner construction, event bridge, router registration |
| `CLAUDE.md`, `docs/api-reference.md`, `docs/architecture.md` (modify) | Rules + endpoint reference |

---

### Task 1: `media.hidden` column, summary filter, API pass-through

**Files:**
- Modify: `metascan/core/database_sqlite.py` (`_init_database` ~line 230–300; `get_all_media_summaries` ~line 1046)
- Modify: `backend/services/media_service.py:25-31`, `backend/api/media.py:22-35`
- Test: `tests/test_media_hidden.py` (create)

**Interfaces:**
- Consumes: existing `_idempotent_add_column`, covering-index rebuild loop, `to_posix_path`.
- Produces: `DatabaseManager.set_media_hidden(file_path: str, hidden: bool) -> bool`; `get_all_media_summaries(favorites_only=False, sort="date_added", include_hidden=False)` where each summary dict gains `"hidden": bool`; `GET /api/media?include_hidden=true`.

- [ ] **Step 1: Write failing tests**

```python
"""tests/test_media_hidden.py"""
import sqlite3
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _media(path: str) -> Media:
    return Media(file_path=Path(path), file_size=1, width=8, height=8)


def test_hidden_column_defaults_to_zero(db):
    db.save_media(_media("/pics/a.png"))
    rows = db.get_all_media_summaries()
    assert rows[0]["hidden"] is False


def test_set_media_hidden_roundtrip(db):
    db.save_media(_media("/pics/a.png"))
    assert db.set_media_hidden("/pics/a.png", True) is True
    assert db.get_all_media_summaries(include_hidden=True)[0]["hidden"] is True


def test_default_summaries_exclude_hidden(db):
    db.save_media(_media("/pics/a.png"))
    db.save_media(_media("/pics/b.png"))
    db.set_media_hidden("/pics/b.png", True)
    default = db.get_all_media_summaries()
    assert [r["file_path"] for r in default] == ["/pics/a.png"]
    both = db.get_all_media_summaries(include_hidden=True)
    assert len(both) == 2


def test_hidden_survives_rescan_upsert(db):
    db.save_media(_media("/pics/a.png"))
    db.set_media_hidden("/pics/a.png", True)
    db.save_media(_media("/pics/a.png"))  # rescan re-saves
    assert db.get_all_media_summaries() == []


def test_favorites_and_hidden_filters_compose(db):
    db.save_media(_media("/pics/a.png"))
    db.save_media(_media("/pics/b.png"))
    db.set_favorite("/pics/a.png", True)
    db.set_favorite("/pics/b.png", True)
    db.set_media_hidden("/pics/b.png", True)
    rows = db.get_all_media_summaries(favorites_only=True)
    assert [r["file_path"] for r in rows] == ["/pics/a.png"]


def test_covering_indexes_include_hidden(db):
    with db._get_connection() as conn:
        for name in ("idx_media_summary_added", "idx_media_summary_modified"):
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                (name,),
            ).fetchone()
            assert row is not None and "hidden" in row["sql"]
```

Check `Media`'s actual constructor in `metascan/core/media.py` and `set_favorite`'s actual name in `database_sqlite.py` before running — adjust the helper to the real signatures (search for how `tests/test_folders_db.py` builds Media rows and copy that idiom).

- [ ] **Step 2: Run tests, verify they fail** — `pytest tests/test_media_hidden.py -v` → FAIL (`no such column: hidden` / unexpected kwarg).

- [ ] **Step 3: Implement**

In `_init_database`, next to the existing `_idempotent_add_column` calls for media:

```python
_idempotent_add_column(
    conn,
    "media",
    "hidden",
    "ALTER TABLE media ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
)
```

Add `"hidden"` to the `required_cols` tuple, and add `hidden` as a column in **both** `CREATE INDEX` statements (`idx_media_summary_added`, `idx_media_summary_modified`), after `orientation`.

`get_all_media_summaries`: add `include_hidden: bool = False` keyword; build the WHERE clause from a conditions list (`is_favorite = 1` when favorites_only, `hidden = 0` when not include_hidden, joined with AND); add `hidden` to the SELECT projection and emit `"hidden": bool(row["hidden"])` in each summary dict.

New method (place near `set_favorite`):

```python
def set_media_hidden(self, file_path: str, hidden: bool) -> bool:
    """Hide/unhide one media row from the default grid query."""
    try:
        with self.lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "UPDATE media SET hidden = ? WHERE file_path = ?",
                    (1 if hidden else 0, to_posix_path(file_path)),
                )
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        logger.error(f"Failed to set hidden for {file_path}: {e}")
        return False
```

`backend/services/media_service.py`: `get_all_media_summaries` gains `include_hidden: bool = False` and forwards it **by keyword** (`self.db.get_all_media_summaries, favorites_only, sort` is positional today — switch the whole call to a lambda or `functools.partial` with keywords to avoid ordering bugs: `await asyncio.to_thread(partial(self.db.get_all_media_summaries, favorites_only=favorites_only, sort=sort, include_hidden=include_hidden))`).

`backend/api/media.py`: the list route gains `include_hidden: bool = False` query param, forwarded to the service.

- [ ] **Step 4: Run tests + full quality** — `pytest tests/test_media_hidden.py -v` → PASS, then `make quality test`.

- [ ] **Step 5: Commit** — `git commit -m "feat(storyboard): add media.hidden column with grid filter and covering-index coverage"`

---

### Task 2: Storyboard tables + CRUD

**Files:**
- Modify: `metascan/core/database_sqlite.py` (tables after the `generation_jobs` block in `_init_database`; CRUD methods after the generation-job CRUD section)
- Test: `tests/test_storyboard_db.py` (create)

**Interfaces:**
- Consumes: `_get_connection` (sets `PRAGMA foreign_keys = ON`), `self.lock`, `to_posix_path`.
- Produces (all sync, all under `with self.lock, self._get_connection() as conn:`):
  - `create_storyboard(*, name, target_model, architecture, aspect_ratio="16:9", style_block=None, negative=None, preset_id=None, base_seed=0, batch_size=4, source_text=None) -> int`
  - `get_storyboard(storyboard_id: int) -> Optional[Dict]` / `list_storyboards() -> List[Dict]` / `update_storyboard(storyboard_id, **fields) -> None` (whitelist `_STORYBOARD_UPDATABLE`) / `delete_storyboard(storyboard_id) -> bool`
  - `create_subject(storyboard_id, *, name, description, lora_name=None, lora_strength=0.8, reference_path=None, sort_order=0) -> int`, `update_subject(subject_id, **fields)`, `delete_subject(subject_id) -> bool`
  - `create_scene(storyboard_id, *, name, sort_order=0, location=None, time_of_day=None, mood=None, lighting=None, notes=None) -> int`, `update_scene(scene_id, **fields)`, `delete_scene(scene_id) -> bool`
  - `create_panel(scene_id, *, action, sort_order=0, shot_size=None, angle=None, lens=None, subject_ids=None, notes=None) -> int`, `update_panel(panel_id, **fields)` (whitelist `_PANEL_UPDATABLE`, auto-touches `updated_at`), `get_panel(panel_id) -> Optional[Dict]`, `delete_panel(panel_id) -> bool` (also deletes that panel's `generation_jobs` rows — no FK exists)
  - `replace_storyboard_structure(storyboard_id: int, parsed: Dict[str, Any]) -> None` — one transaction: delete subjects+scenes (cascades), insert new tree, resolve panel subject-name refs to ids case-insensitively
  - `get_storyboard_tree(storyboard_id: int) -> Optional[Dict]` — storyboard dict + `subjects: [...]` + `scenes: [{... , panels: [{..., subject_ids: [int, ...], images: [...]}]}]`, everything ordered by `sort_order, id`
  - `create_panel_image(panel_id, *, file_path, seed=None, variant_index=0, prompt_used=None, preset_id=None, comfy_prompt_id=None) -> int` (POSIX-converts `file_path`), `list_panel_images(panel_id) -> List[Dict]`, `count_panel_images(panel_id) -> int`
  - `select_panel_image(panel_id: int, image_id: Optional[int]) -> bool` — hides old keeper's media, unhides new; `None` clears selection (re-hides old)
  - `list_generation_jobs(states=None, limit=100, panel_ids=None)` (new optional filter)
  - `latest_jobs_for_panels(panel_ids: List[int]) -> Dict[int, Dict]` — latest (max id) job row per panel

- [ ] **Step 1: Write failing tests** — `tests/test_storyboard_db.py`, temp-DB fixture as in Task 1. Cover at minimum:

```python
def test_create_and_get_tree(db):
    sb = db.create_storyboard(name="Yard", target_model="sd", architecture="t2i", base_seed=42)
    su = db.create_subject(sb, name="MAYA", description="late 20s, shaved head, red scarf")
    sc = db.create_scene(sb, name="Salvage Yard", sort_order=0, location="salvage yard")
    pa = db.create_panel(sc, action="hand rests on hull seam", sort_order=0,
                         shot_size="ECU", subject_ids=[su])
    tree = db.get_storyboard_tree(sb)
    assert tree["name"] == "Yard"
    assert tree["subjects"][0]["name"] == "MAYA"
    assert tree["scenes"][0]["panels"][0]["subject_ids"] == [su]
    assert tree["scenes"][0]["panels"][0]["images"] == []


def test_delete_storyboard_cascades(db):
    # build tree as above, then:
    assert db.delete_storyboard(sb) is True
    with db.lock, db._get_connection() as conn:
        for table in ("storyboard_subjects", "scenes", "panels"):
            assert conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0


def test_delete_media_cascades_panel_image_and_nulls_selection(db):
    # save media row, create tree + panel_image referencing it,
    # select it as keeper, then db.delete_media(path):
    # panel_images row gone, panels.selected_image_id IS NULL,
    # panel row + prompt intact.


def test_select_panel_image_swaps_hidden(db):
    # two media rows both hidden=1, two panel_images; select first
    # -> media A hidden=0; select second -> A hidden=1, B hidden=0;
    # select None -> both hidden=1, selected_image_id NULL.


def test_replace_structure_is_destructive_and_resolves_names(db):
    parsed = {
        "subjects": [{"name": "MAYA", "description": "d1"}],
        "scenes": [{"name": "S1", "location": None, "time_of_day": None,
                    "mood": None, "lighting": None,
                    "panels": [{"action": "a1", "shot_size": "CU", "angle": None,
                                "lens": None, "subjects": ["maya", "GHOST"]}]}],
    }
    db.replace_storyboard_structure(sb, parsed)
    tree = db.get_storyboard_tree(sb)
    maya_id = tree["subjects"][0]["id"]
    # case-insensitive match resolved; unknown name dropped
    assert tree["scenes"][0]["panels"][0]["subject_ids"] == [maya_id]


def test_delete_panel_removes_generation_jobs(db):
    # create preset (db.create_workflow_preset — check its real name/signature
    # in the Phase A CRUD section) + job with panel_id, delete panel,
    # assert generation_jobs row gone.


def test_update_panel_whitelist_rejects_unknown(db):
    with pytest.raises(ValueError):
        db.update_panel(pa, bogus="x")


def test_latest_jobs_for_panels(db):
    # two jobs for one panel -> only the higher id comes back.
```

Also test `list_generation_jobs(panel_ids=[...])` filtering, and that `create_panel_image` with a `file_path` for a **missing** media row raises `sqlite3.IntegrityError` (FK enforced).

- [ ] **Step 2: Run, verify failure** — `pytest tests/test_storyboard_db.py -v` → FAIL (`no such table` / `AttributeError`).

- [ ] **Step 3: Implement**

Tables, added to `_init_database` directly after the `generation_jobs` index block — use the spec's §4.2 DDL **verbatim**, with two changes: `panels` gains `prompt_source TEXT` (after `prompt_locked`), and add `_idempotent_add_column(conn, "generation_jobs", "output_dir", "ALTER TABLE generation_jobs ADD COLUMN output_dir TEXT")`. Include `CREATE INDEX IF NOT EXISTS idx_panel_images_panel ON panel_images(panel_id)` plus `idx_scenes_storyboard ON scenes(storyboard_id)`, `idx_panels_scene ON panels(scene_id)`, `idx_subjects_storyboard ON storyboard_subjects(storyboard_id)`.

Whitelists (module-level frozensets on the class, mirroring `_JOB_UPDATABLE`):

```python
_STORYBOARD_UPDATABLE = frozenset({
    "name", "source_text", "aspect_ratio", "style_block", "negative",
    "target_model", "architecture", "preset_id", "base_seed",
    "batch_size", "folder_id",
})
_SUBJECT_UPDATABLE = frozenset({
    "name", "description", "lora_name", "lora_strength",
    "reference_path", "sort_order",
})
_SCENE_UPDATABLE = frozenset({
    "name", "sort_order", "location", "time_of_day", "mood",
    "lighting", "notes",
})
_PANEL_UPDATABLE = frozenset({
    "sort_order", "shot_size", "angle", "lens", "action", "subject_ids",
    "notes", "brief", "prompt", "prompt_locked", "prompt_source", "negative",
    "selected_image_id",
})
```

Update methods follow `update_generation_job`'s shape exactly (whitelist check → interpolated column list from validated names → parameterized values). `update_storyboard` and `update_panel` additionally append `updated_at = datetime('now')`. `update_panel` and `create_panel` JSON-encode `subject_ids` (`json.dumps(list(...))`); every read path (`get_panel`, tree) decodes it.

`select_panel_image` — the one transactional method:

```python
def select_panel_image(self, panel_id: int, image_id: Optional[int]) -> bool:
    """Set a panel's keeper. Unhides the new keeper's media row, re-hides
    the previous keeper's. image_id=None clears the selection."""
    with self.lock:
        with self._get_connection() as conn:
            panel = conn.execute(
                "SELECT selected_image_id FROM panels WHERE id = ?", (panel_id,)
            ).fetchone()
            if panel is None:
                return False
            new_path = None
            if image_id is not None:
                row = conn.execute(
                    "SELECT file_path FROM panel_images WHERE id = ? AND panel_id = ?",
                    (image_id, panel_id),
                ).fetchone()
                if row is None:
                    return False
                new_path = row["file_path"]
            old_id = panel["selected_image_id"]
            if old_id is not None and old_id != image_id:
                old = conn.execute(
                    "SELECT file_path FROM panel_images WHERE id = ?", (old_id,)
                ).fetchone()
                if old is not None:
                    conn.execute(
                        "UPDATE media SET hidden = 1 WHERE file_path = ?",
                        (old["file_path"],),
                    )
            if new_path is not None:
                conn.execute(
                    "UPDATE media SET hidden = 0 WHERE file_path = ?", (new_path,)
                )
            conn.execute(
                "UPDATE panels SET selected_image_id = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (image_id, panel_id),
            )
            conn.commit()
            return True
```

`replace_storyboard_structure`:

```python
def replace_storyboard_structure(
    self, storyboard_id: int, parsed: Dict[str, Any]
) -> None:
    """Destructively replace subjects/scenes/panels from a parse result.

    ``parsed`` is the validated shape from storyboard_parse: subjects
    carry name/description; panels reference subjects BY NAME
    (case-insensitive); unknown names are dropped. One transaction.
    """
    import json as _json

    with self.lock:
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM storyboard_subjects WHERE storyboard_id = ?",
                (storyboard_id,),
            )
            conn.execute(
                "DELETE FROM scenes WHERE storyboard_id = ?", (storyboard_id,)
            )
            name_to_id: Dict[str, int] = {}
            for i, subj in enumerate(parsed.get("subjects") or []):
                cur = conn.execute(
                    "INSERT INTO storyboard_subjects "
                    "(storyboard_id, name, description, sort_order) "
                    "VALUES (?, ?, ?, ?)",
                    (storyboard_id, subj["name"], subj["description"], i),
                )
                name_to_id[subj["name"].strip().lower()] = int(cur.lastrowid)
            for si, scene in enumerate(parsed.get("scenes") or []):
                cur = conn.execute(
                    "INSERT INTO scenes (storyboard_id, sort_order, name, "
                    "location, time_of_day, mood, lighting) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (storyboard_id, si, scene["name"], scene.get("location"),
                     scene.get("time_of_day"), scene.get("mood"),
                     scene.get("lighting")),
                )
                scene_id = int(cur.lastrowid)
                for pi, panel in enumerate(scene.get("panels") or []):
                    ids = [
                        name_to_id[n.strip().lower()]
                        for n in (panel.get("subjects") or [])
                        if n.strip().lower() in name_to_id
                    ]
                    conn.execute(
                        "INSERT INTO panels (scene_id, sort_order, shot_size, "
                        "angle, lens, action, subject_ids) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (scene_id, pi, panel.get("shot_size"),
                         panel.get("angle"), panel.get("lens"),
                         panel["action"], _json.dumps(ids)),
                    )
            conn.execute(
                "UPDATE storyboards SET updated_at = datetime('now') "
                "WHERE id = ?",
                (storyboard_id,),
            )
            conn.commit()
```

`get_storyboard_tree` assembles with four SELECTs (storyboard, subjects, scenes, panels-joined-images) inside one lock hold; panels get `"subject_ids": json.loads(...)` and `"images": [...]` ordered by `variant_index, id`. `latest_jobs_for_panels` uses the MAX(id)-per-panel join:

```sql
SELECT gj.* FROM generation_jobs gj
JOIN (SELECT panel_id, MAX(id) AS mid FROM generation_jobs
      WHERE panel_id IN (?, ...) GROUP BY panel_id) m ON gj.id = m.mid
```

`delete_panel` runs `DELETE FROM generation_jobs WHERE panel_id = ?` before deleting the panel row (spec §4.1: no FK exists on purpose). `list_generation_jobs` appends `AND/WHERE panel_id IN (...)` when `panel_ids` is provided.

- [ ] **Step 4: Run tests + quality** — `pytest tests/test_storyboard_db.py tests/test_comfy_db.py -v` → PASS; `make quality test`.

- [ ] **Step 5: Commit** — `git commit -m "feat(storyboard): add storyboard tables and CRUD to DatabaseManager"`

---

### Task 3: Per-job output directory in the ComfyUI driver

**Files:**
- Modify: `metascan/core/database_sqlite.py` (`create_generation_job` ~line 906)
- Modify: `metascan/core/comfy_client.py` (`submit` ~line 399, `submit_now` ~line 250, `collect_outputs` ~line 1007)
- Test: extend `tests/test_comfy_client.py` and `tests/test_comfy_db.py`

**Interfaces:**
- Consumes: Task 2's `output_dir TEXT` column (already added there via `_idempotent_add_column` — if Task 2 hasn't landed the column, add the same call here; it's idempotent).
- Produces: `create_generation_job(preset_id, params, panel_id=None, output_dir=None) -> int`; `ComfyClient.submit(preset_id, params, panel_id=None, priority=False, output_dir: Optional[Path] = None) -> int` (same for `submit_now`); `collect_outputs` writes into `Path(job["output_dir"])` when the row has one, else `self.output_dir_for(job_id)`.

- [ ] **Step 1: Write failing tests**

In `tests/test_comfy_db.py`: `create_generation_job(..., output_dir="/x/y")` round-trips through `get_generation_job` as `job["output_dir"] == "/x/y"`; omitted → `None`.

In `tests/test_comfy_client.py` (against the fake server, following the existing submit/collect tests' fixture idiom): a job submitted with `output_dir=tmp_path / "custom"` lands its images under that directory, not under `output_root / "job_XXXXXX"`.

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement**

`create_generation_job` gains `output_dir: Optional[str] = None` and includes it in the INSERT column list. Both `submit` and `submit_now` gain `output_dir: Optional[Path] = None` and pass `str(output_dir) if output_dir else None` to `create_generation_job`. In `collect_outputs`, replace `target_dir = self.output_dir_for(job_id)` with:

```python
stored = job.get("output_dir")
target_dir = Path(stored) if stored else self.output_dir_for(job_id)
```

(`job` is the row already fetched at the top of `collect_outputs` — no new DB read.)

- [ ] **Step 4: Run** — `pytest tests/test_comfy_client.py tests/test_comfy_db.py -v` → PASS; `make quality test`.

- [ ] **Step 5: Commit** — `git commit -m "feat(comfy): support a persisted per-job output directory"`

---

### Task 4: Pure domain helpers — `storyboard_brief.py`

**Files:**
- Create: `metascan/core/storyboard_brief.py`
- Test: `tests/test_storyboard_brief.py` (create)

**Interfaces:**
- Consumes: nothing (pure, stdlib only — mypy strict applies).
- Produces:
  - `SUPPORTED_ASPECT_RATIOS: tuple[str, ...] = ("1:1", "4:3", "16:9", "2.39:1", "9:16")`
  - `SDXL_TARGETS: frozenset[str] = frozenset({"sd", "pony"})`
  - `bucket_dims(aspect_ratio: str, target_model: str) -> tuple[int, int]` (raises `ValueError` on unsupported aspect ratio)
  - `panel_seed(base_seed: int, panel_sort_order: int, variant_index: int) -> int`
  - `storyboard_slug(storyboard_id: int, name: str) -> str`
  - `compose_brief(storyboard: Mapping[str, Any], scene: Mapping[str, Any], panel: Mapping[str, Any], subjects: Sequence[Mapping[str, Any]]) -> str`
  - Vocab maps: `SHOT_SIZES`, `ANGLES`, `LENSES` (code → human phrase)

- [ ] **Step 1: Write failing tests**

```python
"""tests/test_storyboard_brief.py"""
import pytest

from metascan.core.storyboard_brief import (
    SUPPORTED_ASPECT_RATIOS,
    bucket_dims,
    compose_brief,
    panel_seed,
    storyboard_slug,
)


@pytest.mark.parametrize(
    "ar,expected",
    [("1:1", (1024, 1024)), ("4:3", (1216, 832)), ("16:9", (1344, 768)),
     ("2.39:1", (1344, 768)), ("9:16", (768, 1344))],
)
def test_bucket_dims_sdxl(ar, expected):
    assert bucket_dims(ar, "sd") == expected
    assert bucket_dims(ar, "pony") == expected


@pytest.mark.parametrize(
    "ar,expected",
    [("1:1", (1024, 1024)), ("4:3", (1184, 880)), ("16:9", (1360, 768)),
     ("2.39:1", (1584, 656)), ("9:16", (768, 1360))],
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


def test_panel_seed_formula_and_reroll_advance():
    assert panel_seed(1000, 3, 0) == 4000
    assert panel_seed(1000, 3, 4) == 4004
    assert panel_seed(1000, 3, 4) == panel_seed(1000, 3, 4)  # reproducible


def test_storyboard_slug():
    assert storyboard_slug(7, "Salvage Yard Sequence!") == "7-salvage-yard-sequence"
    assert storyboard_slug(9, "***") == "9-storyboard"


def test_compose_brief_full():
    storyboard = {"aspect_ratio": "2.39:1", "style_block": "graphite sketch"}
    scene = {"location": "salvage yard, twisted hulls", "time_of_day": "dusk",
             "mood": "tense", "lighting": "amber haze, long shadows"}
    panel = {"shot_size": "ECU", "angle": "eye", "lens": None,
             "action": "her hand rests on the hull seam"}
    subjects = [{"name": "Maya", "description": "late 20s, shaved head, red scarf"}]
    brief = compose_brief(storyboard, scene, panel, subjects)
    assert brief.splitlines() == [
        "SHOT: extreme close-up, eye level, 2.39:1",
        "SUBJECT Maya: late 20s, shaved head, red scarf",
        "ACTION: her hand rests on the hull seam",
        "LOCATION: salvage yard, twisted hulls",
        "LIGHT/MOOD: dusk, amber haze, long shadows, tense",
    ]
    assert "graphite" not in brief  # style is concatenated post-synthesis, never here


def test_compose_brief_omits_empty_lines():
    brief = compose_brief(
        {"aspect_ratio": "1:1", "style_block": None},
        {"location": None, "time_of_day": None, "mood": None, "lighting": None},
        {"shot_size": None, "angle": None, "lens": None, "action": "a cat"},
        [],
    )
    assert brief == "SHOT: 1:1\nACTION: a cat"
```

- [ ] **Step 2: Run, verify failure** — module not found.

- [ ] **Step 3: Implement**

```python
"""Pure storyboard domain helpers: dimension bucketing, seeds, briefs.

No I/O, no model calls — the photo_exif precedent. Everything here is
exercised by tests that need no server.
"""

from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

SUPPORTED_ASPECT_RATIOS: tuple[str, ...] = ("1:1", "4:3", "16:9", "2.39:1", "9:16")

# SDXL-family targets snap to the model's trained buckets; everything
# else (Flux and later) takes the nearest multiple of 16 at a ~1MP budget.
SDXL_TARGETS: frozenset[str] = frozenset({"sd", "pony"})

_SDXL_BUCKETS: tuple[tuple[int, int], ...] = (
    (1024, 1024), (1216, 832), (832, 1216), (1344, 768), (768, 1344),
)
_PIXEL_BUDGET = 1024 * 1024

SHOT_SIZES: Mapping[str, str] = {
    "ECU": "extreme close-up", "CU": "close-up", "MCU": "medium close-up",
    "MS": "medium shot", "MLS": "medium long shot", "WS": "wide shot",
    "EWS": "extreme wide shot",
}
ANGLES: Mapping[str, str] = {
    "eye": "eye level", "low": "low angle", "high": "high angle",
    "overhead": "overhead", "dutch": "dutch angle",
    "ots": "over-the-shoulder", "pov": "POV",
}
LENSES: Mapping[str, str] = {
    "wide": "wide-angle lens", "normal": "normal lens",
    "tele": "telephoto lens", "macro": "macro lens",
}


def _aspect_value(aspect_ratio: str) -> float:
    if aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise ValueError(
            f"Unsupported aspect ratio {aspect_ratio!r}; "
            f"supported: {', '.join(SUPPORTED_ASPECT_RATIOS)}"
        )
    w_s, h_s = aspect_ratio.split(":")
    return float(w_s) / float(h_s)


def bucket_dims(aspect_ratio: str, target_model: str) -> tuple[int, int]:
    """Snap an aspect ratio to generation dimensions for a target model."""
    ar = _aspect_value(aspect_ratio)
    if target_model in SDXL_TARGETS:
        return min(
            _SDXL_BUCKETS, key=lambda wh: abs(math.log(wh[0] / wh[1] / ar))
        )
    w = round(math.sqrt(_PIXEL_BUDGET * ar) / 16) * 16
    h = round(math.sqrt(_PIXEL_BUDGET / ar) / 16) * 16
    return (int(w), int(h))


def panel_seed(base_seed: int, panel_sort_order: int, variant_index: int) -> int:
    """Deterministic per-panel seed; a reroll advances variant_index."""
    return base_seed + panel_sort_order * 1000 + variant_index


def storyboard_slug(storyboard_id: int, name: str) -> str:
    """Filesystem-safe directory name: '<id>-<kebab-name>'."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    return f"{storyboard_id}-{slug or 'storyboard'}"


def compose_brief(
    storyboard: Mapping[str, Any],
    scene: Mapping[str, Any],
    panel: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
) -> str:
    """Deterministic panel brief. The style block is deliberately absent:
    it is concatenated onto the final prompt outside the LLM call so the
    global look cannot drift through paraphrase (spec §7.2)."""
    shot_bits = [
        SHOT_SIZES.get(panel.get("shot_size") or ""),
        ANGLES.get(panel.get("angle") or ""),
        LENSES.get(panel.get("lens") or ""),
        storyboard.get("aspect_ratio"),
    ]
    lines = [f"SHOT: {', '.join(b for b in shot_bits if b)}"]
    for subj in subjects:
        lines.append(f"SUBJECT {subj['name']}: {subj['description']}")
    if panel.get("action"):
        lines.append(f"ACTION: {panel['action']}")
    if scene.get("location"):
        lines.append(f"LOCATION: {scene['location']}")
    light_bits = [scene.get("time_of_day"), scene.get("lighting"), scene.get("mood")]
    light = ", ".join(b for b in light_bits if b)
    if light:
        lines.append(f"LIGHT/MOOD: {light}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run** — `pytest tests/test_storyboard_brief.py -v` → PASS; `make quality test` (mypy strict covers this module).

- [ ] **Step 5: Commit** — `git commit -m "feat(storyboard): pure brief composer, dimension bucketing, seed derivation"`

---

### Task 5: Parse stage — `storyboard_parse.py` + `generate_text` grammar support

**Files:**
- Create: `metascan/core/storyboard_parse.py`
- Modify: `metascan/core/vlm_client.py` (`generate_text`, ~line 493)
- Test: `tests/test_storyboard_parse.py` (create); extend `tests/test_vlm_generate_text.py`

**Interfaces:**
- Consumes: nothing at parse time (pure); `VlmClient.generate_text` body dict (grammar transported exactly as `generate_tags` does at `vlm_client.py:457` — top-level `"grammar"` key).
- Produces:
  - `ParseError(ValueError)`
  - `PARSE_SYSTEM_PROMPT: str`, `PARSE_GRAMMAR: str`, `build_parse_user_prompt(text: str) -> str`
  - `validate_parse_response(raw: str) -> Dict[str, Any]` — returns the exact nested-dict shape `replace_storyboard_structure` consumes (see Task 2), raising `ParseError` on garbage
  - `VlmClient.generate_text(..., grammar: Optional[str] = None)` — includes `"grammar"` in the request body when set

- [ ] **Step 1: Write failing tests**

```python
"""tests/test_storyboard_parse.py"""
import json

import pytest

from metascan.core.storyboard_parse import (
    PARSE_GRAMMAR,
    PARSE_SYSTEM_PROMPT,
    ParseError,
    build_parse_user_prompt,
    validate_parse_response,
)


def _payload():
    return {
        "subjects": [{"name": "MAYA", "description": "late 20s, red scarf"}],
        "scenes": [{
            "name": "Salvage Yard", "location": "salvage yard",
            "time_of_day": "dusk", "mood": "tense", "lighting": "amber haze",
            "panels": [{"shot_size": "ECU", "angle": "eye", "lens": None,
                        "action": "hand rests on hull seam",
                        "subjects": ["MAYA"]}],
        }],
    }


def test_validate_roundtrip():
    out = validate_parse_response(json.dumps(_payload()))
    assert out["subjects"][0]["name"] == "MAYA"
    assert out["scenes"][0]["panels"][0]["shot_size"] == "ECU"


def test_unknown_enum_values_become_none():
    p = _payload()
    p["scenes"][0]["panels"][0]["shot_size"] = "SUPERWIDE"
    p["scenes"][0]["panels"][0]["angle"] = "worm"
    out = validate_parse_response(json.dumps(p))
    panel = out["scenes"][0]["panels"][0]
    assert panel["shot_size"] is None and panel["angle"] is None


def test_panel_without_action_is_dropped():
    p = _payload()
    p["scenes"][0]["panels"].append(
        {"shot_size": None, "angle": None, "lens": None,
         "action": "  ", "subjects": []})
    out = validate_parse_response(json.dumps(p))
    assert len(out["scenes"][0]["panels"]) == 1


def test_no_scenes_raises():
    with pytest.raises(ParseError):
        validate_parse_response(json.dumps({"subjects": [], "scenes": []}))


def test_garbage_raises():
    with pytest.raises(ParseError):
        validate_parse_response("not json")


def test_grammar_has_no_backslash_hyphen_escape():
    assert "\\-" not in PARSE_GRAMMAR  # invalid GBNF escape -> llama SIGSEGV
    assert "::=" in PARSE_GRAMMAR


def test_grammar_terminals_cover_shape():
    for token in ('"subjects"', '"scenes"', '"panels"', '"action"',
                  '"shot_size"', '"ECU"', '"pov"', '"macro"'):
        assert token.replace('"', '\\"') in PARSE_GRAMMAR or token in PARSE_GRAMMAR


def test_system_prompt_and_user_template():
    assert "json" in PARSE_SYSTEM_PROMPT.lower()
    assert "verbatim" in PARSE_SYSTEM_PROMPT.lower() or \
        "exact" in PARSE_SYSTEM_PROMPT.lower()
    up = build_parse_user_prompt("INT. YARD - DUSK")
    assert "INT. YARD - DUSK" in up
```

`tests/test_vlm_generate_text.py` extension (follow that file's existing fake-server fixture; it records request bodies):

```python
async def test_generate_text_forwards_grammar(...):
    # call generate_text(..., grammar='root ::= "x"')
    # assert the captured /v1/chat/completions body has body["grammar"] == 'root ::= "x"'
    # and a call WITHOUT grammar sends no "grammar" key at all.
```

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement**

`generate_text`: add `grammar: Optional[str] = None` to the signature; after the `body = {...}` literal add:

```python
if grammar is not None:
    body["grammar"] = grammar
```

`metascan/core/storyboard_parse.py`:

```python
"""Freeform screenplay text -> structured storyboard, via one
GBNF-constrained VlmClient.generate_text call.

This module is pure: prompt text, the grammar, and response validation.
The VLM call itself lives in storyboard_runner.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

SHOT_SIZE_VALUES = ("ECU", "CU", "MCU", "MS", "MLS", "WS", "EWS")
ANGLE_VALUES = ("eye", "low", "high", "overhead", "dutch", "ots", "pov")
LENS_VALUES = ("wide", "normal", "tele", "macro")


class ParseError(ValueError):
    """The VLM response could not be validated into a storyboard tree."""


PARSE_SYSTEM_PROMPT = """\
You convert loosely written scene text into structured storyboard JSON.

Rules:
- Every named character or recurring subject becomes a subjects[] entry.
  Copy the author's physical description of each subject verbatim into
  description — do not paraphrase, embellish, or invent details.
- Each scene heading or clear location change becomes a scenes[] entry.
- Each shot or keyframe sentence becomes one panel. Keep the author's
  action wording; do not merge shots.
- shot_size, angle, and lens: fill only when the text states or clearly
  implies them; otherwise use null.
- A panel's subjects array lists the names of subjects visible in that
  shot, most important first.
Output only the JSON object.
"""


def build_parse_user_prompt(text: str) -> str:
    return f"Scene text:\n\n{text}\n\nProduce the storyboard JSON."


def _alts(values: tuple) -> str:
    quoted = " | ".join(f'"\\"{v}\\""' for v in values)
    return f"{quoted} | \"null\""


# JSON-shaped GBNF. Hyphens appear only as literal range operators inside
# character classes (never the invalid escape "\-"): see CLAUDE.md.
PARSE_GRAMMAR = f"""\
root ::= "{{" ws "\\"subjects\\"" ws ":" ws subjects ws "," ws "\\"scenes\\"" ws ":" ws scenes ws "}}"
subjects ::= "[" ws (subject (ws "," ws subject)*)? ws "]"
subject ::= "{{" ws "\\"name\\"" ws ":" ws string ws "," ws "\\"description\\"" ws ":" ws string ws "}}"
scenes ::= "[" ws (scene (ws "," ws scene)*)? ws "]"
scene ::= "{{" ws "\\"name\\"" ws ":" ws string ws "," ws "\\"location\\"" ws ":" ws nullable ws "," ws "\\"time_of_day\\"" ws ":" ws nullable ws "," ws "\\"mood\\"" ws ":" ws nullable ws "," ws "\\"lighting\\"" ws ":" ws nullable ws "," ws "\\"panels\\"" ws ":" ws panels ws "}}"
panels ::= "[" ws (panel (ws "," ws panel)*)? ws "]"
panel ::= "{{" ws "\\"shot_size\\"" ws ":" ws shotsize ws "," ws "\\"angle\\"" ws ":" ws angle ws "," ws "\\"lens\\"" ws ":" ws lens ws "," ws "\\"action\\"" ws ":" ws string ws "," ws "\\"subjects\\"" ws ":" ws namelist ws "}}"
shotsize ::= {_alts(SHOT_SIZE_VALUES)}
angle ::= {_alts(ANGLE_VALUES)}
lens ::= {_alts(LENS_VALUES)}
namelist ::= "[" ws (string (ws "," ws string)*)? ws "]"
nullable ::= string | "null"
string ::= "\\"" char* "\\""
char ::= [^"\\\\\\x7F\\x00-\\x1F] | "\\\\" (["\\\\bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
ws ::= [ \\t\\n]*
"""
```

**Careful with the escaping above** — the target is a *plain string* whose content, when printed, is valid GBNF. Prefer building `PARSE_GRAMMAR` from a raw triple-quoted string (`r'''...'''`) with `{shotsize_alts}`-style `.format()` slots over the f-string spaghetti if that reads cleaner; the tests pin the observable content (`\-` absent, terminals present). Sanity-check by `print(PARSE_GRAMMAR)` and eyeballing that every rule is `name ::= body` with balanced quotes.

```python
def _clean_str(v: Any) -> Optional[str]:
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return None


def validate_parse_response(raw: str) -> Dict[str, Any]:
    """Validate + normalize the model's JSON into the shape
    DatabaseManager.replace_storyboard_structure consumes."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise ParseError(f"response is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise ParseError("response is not a JSON object")

    subjects = []
    for s in data.get("subjects") or []:
        if not isinstance(s, dict):
            continue
        name = _clean_str(s.get("name"))
        desc = _clean_str(s.get("description"))
        if name and desc:
            subjects.append({"name": name, "description": desc})

    scenes = []
    for sc in data.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        name = _clean_str(sc.get("name"))
        if not name:
            continue
        panels = []
        for p in sc.get("panels") or []:
            if not isinstance(p, dict):
                continue
            action = _clean_str(p.get("action"))
            if not action:
                continue
            shot = p.get("shot_size")
            angle = p.get("angle")
            lens = p.get("lens")
            panels.append({
                "shot_size": shot if shot in SHOT_SIZE_VALUES else None,
                "angle": angle if angle in ANGLE_VALUES else None,
                "lens": lens if lens in LENS_VALUES else None,
                "action": action,
                "subjects": [
                    n for n in (p.get("subjects") or [])
                    if isinstance(n, str) and n.strip()
                ],
            })
        scenes.append({
            "name": name,
            "location": _clean_str(sc.get("location")),
            "time_of_day": _clean_str(sc.get("time_of_day")),
            "mood": _clean_str(sc.get("mood")),
            "lighting": _clean_str(sc.get("lighting")),
            "panels": panels,
        })

    if not scenes:
        raise ParseError("no scenes found in the response")
    return {"subjects": subjects, "scenes": scenes}
```

- [ ] **Step 4: Run** — `pytest tests/test_storyboard_parse.py tests/test_vlm_generate_text.py -v` → PASS; `make quality test`.

- [ ] **Step 5: Commit** — `git commit -m "feat(storyboard): parse-stage prompts, GBNF grammar, and response validation"`

---

### Task 6: Render stage — `storyboard_synthesis.py`

**Files:**
- Create: `metascan/core/storyboard_synthesis.py`
- Test: `tests/test_storyboard_synthesis.py` (create)

**Interfaces:**
- Consumes: `TargetModel` values from `meta_prompt_templates` (`"sd" | "pony" | "flux1" | "flux2" | "zimage" | "chroma" | "qwen"`) — do **not** import the module; the strings are the contract.
- Produces:
  - `build_render_messages(brief: str, target_model: str) -> tuple[str, str]` — (system, user)
  - `finalize_prompt(text: str, style_block: Optional[str]) -> str`
  - `TAG_STYLE_TARGETS: frozenset[str]`

- [ ] **Step 1: Write failing tests**

```python
"""tests/test_storyboard_synthesis.py"""
from metascan.core.storyboard_synthesis import (
    TAG_STYLE_TARGETS,
    build_render_messages,
    finalize_prompt,
)

BRIEF = "SHOT: close-up\nSUBJECT Maya: red scarf\nACTION: she turns"


def test_tag_style_targets():
    assert TAG_STYLE_TARGETS == frozenset({"sd", "pony"})


def test_tag_style_system_prompt():
    system, user = build_render_messages(BRIEF, "sd")
    assert "comma" in system.lower() and "verbatim" in system.lower()
    assert BRIEF in user


def test_natural_language_system_prompt():
    system, user = build_render_messages(BRIEF, "flux1")
    assert "paragraph" in system.lower() and "verbatim" in system.lower()
    assert BRIEF in user


def test_unknown_target_gets_natural_default():
    s_known, _ = build_render_messages(BRIEF, "qwen")
    s_unknown, _ = build_render_messages(BRIEF, "somefuturemodel")
    assert s_unknown == s_known


def test_finalize_prompt_appends_style():
    assert finalize_prompt(" a cat ", "graphite sketch") == "a cat, graphite sketch"
    assert finalize_prompt("a cat", None) == "a cat"
    assert finalize_prompt("a cat", "  ") == "a cat"
```

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement**

```python
"""Panel brief -> target-model prompt dialect. Pure: prompt text only;
the VLM call lives in storyboard_runner.

The verbatim-descriptor rule is consistency mechanic #1 (spec §7):
paraphrase drift on subject descriptions is the main reason
panel-to-panel identity decays, so both dialect instructions forbid it.
"""

from __future__ import annotations

from typing import Optional

TAG_STYLE_TARGETS: frozenset[str] = frozenset({"sd", "pony"})

_TAG_STYLE_SYSTEM = """\
You convert a storyboard panel brief into a prompt for an SDXL-class
image model. Output one comma-separated, tag-style prompt.

Rules:
- Carry every SUBJECT description through verbatim, word for word.
  Never paraphrase, shorten, reorder, or embellish subject descriptors.
- Translate the SHOT line into composition tags (framing, camera angle,
  lens character).
- Include the location, lighting, and mood as tags.
- Describe exactly the moment in ACTION — one frame, not a sequence.
- Output only the prompt text: no quotes, no labels, no explanations.
"""

_NATURAL_SYSTEM = """\
You convert a storyboard panel brief into a prompt for a
natural-language image model. Write one flowing paragraph of two to
five sentences describing exactly the keyframe.

Rules:
- Carry every SUBJECT description through verbatim, word for word.
  Never paraphrase, shorten, reorder, or embellish subject descriptors.
- Establish the framing and camera angle from the SHOT line in the
  first sentence.
- Weave the location, lighting, and mood into the description.
- Describe exactly the moment in ACTION — one frame, not a sequence.
- Output only the prompt text: no quotes, no labels, no explanations.
"""

_USER_TEMPLATE = "Brief:\n{brief}\n\nWrite the image prompt."


def build_render_messages(brief: str, target_model: str) -> tuple[str, str]:
    system = (
        _TAG_STYLE_SYSTEM if target_model in TAG_STYLE_TARGETS else _NATURAL_SYSTEM
    )
    return system, _USER_TEMPLATE.format(brief=brief)


def finalize_prompt(text: str, style_block: Optional[str]) -> str:
    """Concatenate the storyboard's style block outside the LLM call so
    the global look cannot drift through paraphrase (spec §7.2)."""
    out = text.strip()
    style = (style_block or "").strip()
    return f"{out}, {style}" if style else out
```

- [ ] **Step 4: Run** — `pytest tests/test_storyboard_synthesis.py -v` → PASS; `make quality test`.

- [ ] **Step 5: Commit** — `git commit -m "feat(storyboard): per-target render prompts and post-synthesis style concat"`

---

### Task 7: `StoryboardRunner` orchestrator

**Files:**
- Create: `metascan/core/storyboard_runner.py`
- Test: `tests/test_storyboard_runner.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 2–6: `db.get_storyboard_tree`, `db.replace_storyboard_structure`, `db.update_panel`, `db.create_panel_image`, `db.count_panel_images`, `db.set_media_hidden`, `db.latest_jobs_for_panels`, `db.list_generation_jobs(panel_ids=...)`, `db.get_generation_job`, `db.create_folder`, `db.add_folder_items`, `db.update_storyboard`, `db.get_workflow_preset` (Phase A — check the exact name in the preset CRUD section of `database_sqlite.py` and use it); `compose_brief` / `bucket_dims` / `panel_seed` / `storyboard_slug`; `build_parse_user_prompt` / `PARSE_SYSTEM_PROMPT` / `PARSE_GRAMMAR` / `validate_parse_response` / `ParseError`; `build_render_messages` / `finalize_prompt`; `ComfyClient.submit(preset_id, params, panel_id, priority, output_dir)` / `.cancel` / `.upload_image`; `Bindings.from_json`; `GenerationParams`; `VlmClient.ensure_started` / `.generate_text` / `.shutdown` / `.model_id`; `VlmError`; `metascan.core.vlm_models.REGISTRY[mid].parallel_slots`; `metascan.core.hardware.detect_hardware` / `feature_gates`; `to_posix_path` from wherever `database_sqlite` imports it (`metascan.utils` — check the import at the top of `database_sqlite.py`).
- Produces:

```python
class StoryboardError(RuntimeError): ...
class ConfirmRequiredError(StoryboardError): ...

class StoryboardRunner:
    def __init__(self, db, comfy, get_vlm: Callable[[], Optional[Any]],
                 output_root: Path, unload_vlm_during_generation: bool = True) -> None
    def on_event(self, cb: Callable[[str, str, Dict[str, Any]], None]) -> None
        # (channel, event, data) — lifespan bridges to ws_manager.broadcast_sync
    def handle_job_event(self, event: str, payload: Dict[str, Any]) -> None
        # registered via comfy_client.on_job_event; sync, schedules tasks
    async def parse(self, storyboard_id: int, text: str, confirm: bool = False) -> Dict[str, Any]
    async def synthesize(self, storyboard_id: int,
                         panel_ids: Optional[List[int]] = None,
                         force: bool = False) -> Dict[str, int]
        # returns {"synthesized": n, "fallback": n, "skipped_locked": n}
    async def generate(self, storyboard_id: int,
                       panel_ids: Optional[List[int]] = None,
                       only_failed: bool = False) -> List[int]  # job ids
    async def cancel(self, storyboard_id: int) -> int  # jobs cancelled
    async def aclose(self) -> None  # awaits outstanding ingest tasks
```

- [ ] **Step 1: Write failing tests**

`tests/test_storyboard_runner.py` uses a **real temp `DatabaseManager`** (behavior under FK cascades matters) plus **stub** comfy/vlm objects:

```python
class StubComfy:
    def __init__(self):
        self.submitted = []          # (preset_id, params, panel_id, priority, output_dir)
        self.cancelled = []
        self._next_job_id = 100
    async def submit(self, preset_id, params, panel_id=None, priority=False,
                     output_dir=None):
        self.submitted.append((preset_id, params, panel_id, priority, output_dir))
        self._next_job_id += 1
        return self._next_job_id
    async def cancel(self, job_id):
        self.cancelled.append(job_id)
    async def upload_image(self, path):
        return f"uploaded-{path.name}"

class StubVlm:
    def __init__(self, responses=None, fail=False):
        self.model_id = "qwen3vl-4b"
        self.responses = list(responses or [])
        self.fail = fail
        self.calls = []
        self.shutdowns = 0
    async def ensure_started(self, model_id):
        pass
    async def shutdown(self):
        self.shutdowns += 1
    async def generate_text(self, *, system_prompt, user_prompt, grammar=None,
                            temperature=0.6, max_tokens=250, timeout=120.0,
                            image_path=None):
        from metascan.core.vlm_client import VlmError
        self.calls.append({"system": system_prompt, "user": user_prompt,
                           "grammar": grammar})
        if self.fail:
            raise VlmError("boom")
        return self.responses.pop(0)
```

Fixture builds: preset row (insert a minimal t2i preset via the Phase A preset-create DB method with a tiny workflow JSON whose bindings resolve — copy the fixture workflow from `tests/test_comfy_bindings.py`), storyboard (`target_model="sd"`, `base_seed=1000`, `batch_size=2`, `aspect_ratio="16:9"`, `style_block="graphite sketch"`, `negative=None`), one subject, one scene, two panels (`sort_order` 0 and 1, panel 0's `subject_ids=[subject]`).

Required tests:

```python
async def test_parse_writes_structure(runner, db, sb_id):
    # StubVlm response = json.dumps(valid parse payload)
    tree = await runner.parse(sb_id, "INT. YARD - DUSK ...")
    assert tree["scenes"], "returns the fresh tree"
    assert db.get_storyboard(sb_id)["source_text"].startswith("INT. YARD")
    call = vlm.calls[0]
    assert call["grammar"] is not None            # GBNF-constrained
    assert "INT. YARD" in call["user"]

async def test_parse_requires_confirm_when_structure_exists(runner, sb_with_scene):
    with pytest.raises(ConfirmRequiredError):
        await runner.parse(sb_id, "new text")
    await runner.parse(sb_id, "new text", confirm=True)   # succeeds

async def test_parse_without_vlm_raises(runner_no_vlm):
    with pytest.raises(StoryboardError):
        await runner_no_vlm.parse(sb_id, "text")

async def test_synthesize_llm_path(runner, db):
    # StubVlm responses = ["a prompt", "b prompt"]
    out = await runner.synthesize(sb_id)
    assert out == {"synthesized": 2, "fallback": 0, "skipped_locked": 0}
    p = db.get_panel(panel0)
    assert p["prompt"] == "a prompt, graphite sketch"     # style appended post-LLM
    assert p["prompt_source"] == "llm"
    assert p["brief"].startswith("SHOT:")
    # subject description reached the LLM via the brief:
    assert "SUBJECT" in vlm.calls[0]["user"]

async def test_synthesize_fallback_on_vlm_error(runner_failing_vlm, db):
    out = await runner_failing_vlm.synthesize(sb_id)
    assert out["fallback"] == 2
    p = db.get_panel(panel0)
    assert p["prompt_source"] == "brief"
    assert p["prompt"].startswith("SHOT:") and p["prompt"].endswith("graphite sketch")

async def test_synthesize_no_vlm_falls_back(runner_no_vlm, db): ...

async def test_synthesize_skips_locked(runner, db):
    db.update_panel(panel0, prompt="hand tuned", prompt_locked=1, prompt_source="user")
    out = await runner.synthesize(sb_id)
    assert out["skipped_locked"] == 1
    assert db.get_panel(panel0)["prompt"] == "hand tuned"

async def test_synthesize_single_panel_force_overrides_lock(runner, db):
    out = await runner.synthesize(sb_id, panel_ids=[panel0], force=True)
    assert out["synthesized"] == 1
    p = db.get_panel(panel0)
    assert p["prompt_locked"] == 0

async def test_synthesize_emits_progress(runner, events):
    await runner.synthesize(sb_id)
    kinds = [(ch, ev) for ch, ev, _ in events]
    assert ("storyboard", "synthesis_progress") in kinds

async def test_generate_submits_with_derived_params(runner, comfy, db):
    await runner.synthesize(sb_id)      # gives panels prompts
    jobs = await runner.generate(sb_id)
    assert len(jobs) == 2
    preset_id, params, panel_id, priority, output_dir = comfy.submitted[0]
    assert params.width == 1344 and params.height == 768   # 16:9 on sd
    assert params.batch_size == 2
    assert params.seed == 1000 + 0 * 1000 + 0              # panel_seed
    assert priority is False
    assert "scene_00" in str(output_dir) and "panel_00" in str(output_dir)

async def test_generate_creates_folder_once(runner, db):
    await runner.generate(sb_id)
    fid = db.get_storyboard(sb_id)["folder_id"]
    assert fid is not None
    await runner.generate(sb_id)
    assert db.get_storyboard(sb_id)["folder_id"] == fid    # reused

async def test_generate_skips_panels_without_prompt(runner, comfy):
    jobs = await runner.generate(sb_id)     # no synthesize ran
    assert jobs == [] and comfy.submitted == []

async def test_generate_single_panel_is_priority_and_advances_seed(runner, comfy, db):
    # insert 2 existing panel_images rows for panel0 (with media rows),
    # then generate(panel_ids=[panel0]):
    _, params, _, priority, _ = comfy.submitted[0]
    assert priority is True
    assert params.seed == 1000 + 0 + 2      # variant base = existing count

async def test_generate_only_failed(runner, comfy, db):
    # panel0 latest job failed, panel1 latest done ->
    jobs = await runner.generate(sb_id, only_failed=True)
    assert [s[2] for s in comfy.submitted] == [panel0]

async def test_generate_unloads_vlm(runner, vlm):
    await runner.generate(sb_id)
    assert vlm.shutdowns == 1               # unload_vlm_during_generation=True

async def test_generate_negative_without_binding_fails_before_submitting(runner, comfy, db):
    db.update_storyboard(sb_id, negative="blurry")
    # preset fixture has no MS_NEGATIVE ->
    with pytest.raises(StoryboardError, match="MS_NEGATIVE"):
        await runner.generate(sb_id)
    assert comfy.submitted == []            # validated before any submit

async def test_ingest_on_job_outputs(runner, db, tmp_path, events):
    # create a real job row with panel_id + params via db.create_generation_job,
    # save two media rows for the "downloaded" files, then:
    runner.handle_job_event("job_outputs", {"job_id": job_id,
                                            "files": [str(f1), str(f2)]})
    await runner.aclose()                   # drains the ingest task
    imgs = db.list_panel_images(panel0)
    assert [i["variant_index"] for i in imgs] == [0, 1]
    assert imgs[0]["seed"] == json.loads(job_row["params"])["seed"]
    assert imgs[0]["prompt_used"] == json.loads(job_row["params"])["positive"]
    # hidden on ingest:
    assert db.get_all_media_summaries() == []
    # folder membership:
    fid = db.get_storyboard(sb_id)["folder_id"]
    assert set(p["file_path"] for p in imgs) <= set(db.get_folder(fid)["items"])
    assert ("storyboard", "panel_images_changed") in [(c, e) for c, e, _ in events]

async def test_ingest_ignores_jobs_without_panel(runner, db):
    runner.handle_job_event("job_outputs", {"job_id": bare_job_id, "files": ["/x.png"]})
    await runner.aclose()   # no exception, no panel_images rows

async def test_cancel_cancels_active_jobs(runner, comfy, db):
    # two jobs queued/running with this storyboard's panel ids, one done ->
    n = await runner.cancel(sb_id)
    assert n == 2 and sorted(comfy.cancelled) == [j1, j2]
```

(Check `db.get_folder`'s return shape for the `items` key against the Phase A/folders code before asserting; adjust to reality.)

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement `metascan/core/storyboard_runner.py`**

Structure (implementer writes the real thing; the logic below is normative):

```python
"""Storyboard orchestration: parse, synthesize, generate, ingest.

Composes VlmClient + ComfyClient + DatabaseManager. Knows nothing about
FastAPI: events go out through on_event callbacks as
(channel, event, data) tuples that the lifespan bridges onto the
multiplexed WebSocket.
"""
```

- `__init__` stores args; `self._listeners: List[...] = []`; `self._ingest_tasks: set[asyncio.Task] = set()`; `self._synth_lock = asyncio.Lock()` (one synthesis run at a time — a second concurrent run would double-write prompts).
- `_emit(channel, event, data)`: iterate listeners under try/except like `ComfyClient._emit`.
- `_pick_vlm_model(vlm)`: `vlm.model_id` if set, else scan `feature_gates(detect_hardware())` for the first `qwen3vl-*` gate with `recommended`; raise `StoryboardError("no VLM model available on this hardware")` if none.
- **`parse`**: `vlm = self.get_vlm()`; `None` → `StoryboardError("no VLM client — parsing requires a VLM")`. Tree via `to_thread(db.get_storyboard_tree)`; missing → `StoryboardError`. If `tree["scenes"]` and not `confirm` → `ConfirmRequiredError("storyboard already has scenes; re-parsing destroys panel identity — pass confirm=true")`. `ensure_started(_pick_vlm_model(vlm))`; `raw = await vlm.generate_text(system_prompt=PARSE_SYSTEM_PROMPT, user_prompt=build_parse_user_prompt(text), grammar=PARSE_GRAMMAR, temperature=0.2, max_tokens=4096, timeout=600.0)`; `parsed = validate_parse_response(raw)` (let `ParseError` propagate — the route maps it); `to_thread(db.replace_storyboard_structure, storyboard_id, parsed)`; `to_thread(db.update_storyboard, storyboard_id, source_text=text)`; return fresh tree.
- **`synthesize`**: under `self._synth_lock`. Load tree; walk scenes→panels collecting `(scene, panel)` for targets (`panel_ids` filter if given). Partition: locked panels skip unless (`force` and explicitly listed in `panel_ids`). For each target panel compute `brief = compose_brief(...)` with `subjects` resolved from `panel["subject_ids"]` order. VLM path: `vlm = self.get_vlm()`; if vlm is not None: `ensure_started`; `sem = asyncio.Semaphore(REGISTRY[model_id].parallel_slots if model_id in REGISTRY else 2)`; per panel task: `generate_text(system, user, temperature=0.6, max_tokens=400, timeout=180.0)` → success: `prompt_source="llm"`, text = response; `VlmError` → `prompt_source="brief"`, text = brief. No VLM at all → every panel `prompt_source="brief"`. Then `prompt = finalize_prompt(text, storyboard["style_block"])`; `to_thread(db.update_panel, panel_id, brief=brief, prompt=prompt, prompt_source=source, prompt_locked=0)`; emit `("storyboard", "synthesis_progress", {"storyboard_id": ..., "panel_id": ..., "done": n, "total": total, "prompt_source": source})` after each. Gather with `asyncio.gather`. Return counts dict.
- **`generate`**:
  1. Load tree; `preset_id = tree["preset_id"]`; `None` → `StoryboardError("storyboard has no workflow preset")`. Load preset row (Phase A getter), `bindings = Bindings.from_json(preset["bindings"])`, `kind = preset["kind"]`.
  2. Select target panels: `panel_ids` if given, else all; drop panels with empty `prompt`; `only_failed` → keep only panels whose `latest_jobs_for_panels` entry has `state == "failed"`.
  3. **Validate everything before submitting anything** (spec §9 half-run avoidance): effective negative (`panel["negative"] or tree["negative"]`) non-null but `bindings.negative is None` → `StoryboardError` naming MS_NEGATIVE; primary subject has `lora_name` but `bindings.lora is None` → `StoryboardError` naming MS_LORA and the panel; `kind == "ref"` and primary subject's `reference_path` missing → `StoryboardError` naming the panel.
  4. Folder: if `tree["folder_id"]` is None → `to_thread(db.create_folder, str(uuid4()), "manual", f"Storyboard: {tree['name']}")` then `to_thread(db.update_storyboard, sb_id, folder_id=<id>)`.
  5. VRAM: if `self.unload_vlm_during_generation` and `(vlm := self.get_vlm())` is not None and `vlm.model_id` → `await vlm.shutdown()`.
  6. `width, height = bucket_dims(tree["aspect_ratio"], tree["target_model"])`; `slug = storyboard_slug(...)`; `priority = panel_ids is not None and len(panel_ids) == 1`.
  7. Per panel: `variant_base = to_thread(db.count_panel_images, panel_id)`; `seed = panel_seed(tree["base_seed"], panel["sort_order"], variant_base)`; ref upload only when `kind == "ref"`: `ref_name = await comfy.upload_image(Path(reference_path))`; primary subject = first of `subject_ids` resolved against tree subjects; `params = GenerationParams(positive=..., seed=..., width=..., height=..., batch_size=tree["batch_size"], negative=effective_negative, lora_name=..., lora_strength=..., ref_image=ref_name_or_None)`; `output_dir = self.output_root / slug / f"scene_{scene['sort_order']:02d}" / f"panel_{panel['sort_order']:02d}"`; `job_id = await comfy.submit(preset_id, params, panel_id=panel_id, priority=priority, output_dir=output_dir)`. Collect job ids; return.
- **`handle_job_event`** (sync): only `event == "job_outputs"` matters; `task = asyncio.get_running_loop().create_task(self._ingest_outputs(payload))` held in `self._ingest_tasks` with done-callback discard (the `ComfyClient._collect_tasks` idiom). Never raises.
- **`_ingest_outputs(payload)`**: `job = to_thread(db.get_generation_job, job_id)`; bail if `None` or `job["panel_id"] is None`. `panel = to_thread(db.get_panel, ...)`; bail if gone. Resolve scene→storyboard→`folder_id` (a `get_panel` alone doesn't give storyboard — add a small sync helper `db.storyboard_id_for_panel(panel_id) -> Optional[int]` in this task with a two-JOIN SELECT, or reuse the tree; the helper is cheaper). `params = json.loads(job["params"])`; `base = to_thread(db.count_panel_images, panel_id)`; for `i, f` in `enumerate(payload["files"])`: `to_thread(db.set_media_hidden, f, True)` then `to_thread(db.create_panel_image, panel_id, file_path=f, seed=params.get("seed"), variant_index=base + i, prompt_used=params.get("positive"), preset_id=job["preset_id"], comfy_prompt_id=job["comfy_prompt_id"])` — wrap each `create_panel_image` in try/except `Exception` (FK failure when ingest of that file failed) with a `logger.warning`, continue. Add all successfully inserted paths to the folder (`to_thread(db.add_folder_items, folder_id, paths)`) when `folder_id`; emit `("storyboard", "panel_images_changed", {"storyboard_id": sb_id, "panel_id": panel_id, "files": paths})` and `("folders", "folder_items_changed", {"folder_id": folder_id})`.
- **`cancel`**: tree → all panel ids → `to_thread(db.list_generation_jobs, states=["queued", "running"], panel_ids=ids)` → `await comfy.cancel(j["id"])` each → return count.
- **`aclose`**: `await asyncio.gather(*self._ingest_tasks, return_exceptions=True)`.

- [ ] **Step 4: Run** — `pytest tests/test_storyboard_runner.py -v` → PASS; `make quality test` (mypy strict applies — annotate fully).

- [ ] **Step 5: Commit** — `git commit -m "feat(storyboard): StoryboardRunner orchestrator (parse/synthesize/generate/ingest)"`

---

### Task 8: Service layer, REST API, lifespan wiring, docs

**Files:**
- Create: `backend/services/storyboard_service.py`, `backend/api/storyboard.py`
- Modify: `backend/main.py` (imports, lifespan ~line 175–200 and shutdown ~line 265–275, `include_router` block ~line 346)
- Modify: `CLAUDE.md`, `docs/api-reference.md`, `docs/architecture.md`
- Test: `tests/test_storyboard_api.py` (create)

**Interfaces:**
- Consumes: Task 2 CRUD, Task 7 runner. Lifespan already holds `comfy_cfg`, `comfy_client`, `vlm_client`.
- Produces: routes below; `set_storyboard_runner(runner)` / `get_storyboard_runner()` in `backend/api/storyboard.py` (the `set_comfy_client` idiom); `StoryboardService(db)` with `asyncio.to_thread` wrappers mirroring `ComfyService`'s shape.

**Routes** (`APIRouter(prefix="/api/storyboard", tags=["storyboard"])`), Pydantic request models with explicit fields:

| Route | Behavior |
|---|---|
| `GET /api/storyboard` | list storyboards |
| `POST /api/storyboard` | create; body `{name, target_model, architecture="t2i", aspect_ratio="16:9", style_block?, negative?, preset_id?, base_seed?, batch_size=4}`; validate `bucket_dims(aspect_ratio, target_model)` → 400 with the ValueError message on mismatch (spec: fail at save time); `base_seed` omitted → `random.randint(0, 2**31 - 1)`; `batch_size` clamped 1–16; returns `{id}` |
| `GET /api/storyboard/{id}` | full tree; 404 |
| `PATCH /api/storyboard/{id}` | partial update; re-validate `bucket_dims` when `aspect_ratio` or `target_model` present in the effective result; 404 / 400 |
| `DELETE /api/storyboard/{id}` | `{"status": "deleted"}`; 404. Folder row survives (spec §4.4) |
| `POST /api/storyboard/{id}/parse` | body `{text, confirm=false}`; `ConfirmRequiredError` → **409** `{code: "confirm_required"}`; `ParseError` → 422; `StoryboardError` (no VLM) → 503; returns the fresh tree |
| `POST /api/storyboard/{id}/synthesize` | body `{panel_ids?, force=false}`; fires `asyncio.create_task(runner.synthesize(...))` and returns 202 `{"status": "started", "total": n}` where n = panel count in scope (compute from the tree); progress arrives on the `storyboard` WS channel |
| `POST /api/storyboard/{id}/generate` | body `{panel_ids?, only_failed=false}`; awaited inline (submission just enqueues); `StoryboardError` → 400; returns `{"jobs": [ids]}` |
| `POST /api/storyboard/{id}/cancel` | `{"cancelled": n}` |
| `POST /api/storyboard/{id}/subjects` · `PATCH /api/storyboard/subjects/{sid}` · `DELETE /api/storyboard/subjects/{sid}` | subject CRUD; DELETE returns `{"status": "deleted"}` |
| `POST /api/storyboard/{id}/scenes` · `PATCH /api/storyboard/scenes/{sid}` · `DELETE /api/storyboard/scenes/{sid}` | scene CRUD |
| `POST /api/storyboard/scenes/{sid}/panels` · `PATCH /api/storyboard/panels/{pid}` · `DELETE /api/storyboard/panels/{pid}` | panel CRUD; a PATCH whose body includes `prompt` also sets `prompt_locked=1, prompt_source="user"` server-side |
| `POST /api/storyboard/panels/{pid}/select` | body `{image_id}` (nullable); 404 when panel/image mismatch; returns updated panel |

Unknown-id updates → 404. All DB access through `StoryboardService`; runner endpoints call the installed runner and answer 503 `"storyboard runner not initialized"` when absent (the `_require_client` idiom from `backend/api/comfy.py`).

**Lifespan wiring** (`backend/main.py`, after `comfy_client` construction):

```python
from metascan.core.storyboard_runner import StoryboardRunner
from backend.api.storyboard import set_storyboard_runner
from backend.api.vlm import get_vlm_client

storyboard_runner = StoryboardRunner(
    db=db,
    comfy=comfy_client,
    get_vlm=get_vlm_client,
    output_root=Path(comfy_cfg["output_root"]),
    unload_vlm_during_generation=comfy_cfg["unload_vlm_during_generation"],
)
storyboard_runner.on_event(
    lambda channel, event, data: ws_manager.broadcast_sync(channel, event, data)
)
comfy_client.on_job_event(storyboard_runner.handle_job_event)
set_storyboard_runner(storyboard_runner)
```

(`db` is whatever name the lifespan already uses for the `DatabaseManager` — match it.) Shutdown block: `await storyboard_runner.aclose()` in its own try/except, before `comfy_client.shutdown()`. Register `storyboard_api.router` next to `comfy_api.router`.

- [ ] **Step 1: Write failing tests**

`tests/test_storyboard_api.py`, modeled on `tests/test_comfy_api.py`'s app/fixture bootstrapping (temp DB, `TestClient`, runner installed via `set_storyboard_runner`). Use a `StubRunner` recording calls for `/parse`, `/synthesize`, `/generate`, `/cancel`; use the real service+DB for CRUD routes. Cover: create → get tree → patch → delete round-trip; `POST /api/storyboard` with `aspect_ratio="21:9"` → 400; parse 409 (`StubRunner.parse` raising `ConfirmRequiredError`) / 422 (`ParseError`) / 503 (no runner installed); synthesize returns 202 + `{"status": "started"}`; generate maps `StoryboardError` → 400; panel PATCH with `prompt` sets `prompt_locked=1` and `prompt_source="user"` in the DB; select route 404s on an image id from a different panel; all three DELETEs return `{"status": "deleted"}`.

- [ ] **Step 2: Run, verify failure.**

- [ ] **Step 3: Implement** service, router, wiring per the tables above.

- [ ] **Step 4: Docs.**
  - `CLAUDE.md`: extend the architecture bullets — storyboard tables + `media.hidden` lifecycle (ingest hidden, keeper unhidden, swap re-hides); `prompt_locked`/`prompt_source` semantics (user edit locks; re-synthesis skips locked); style-block post-synthesis concat rule; seed formula; `bucket_dims` keyed on TargetModel (sd/pony = SDXL buckets); runner layering (core runner + callbacks, comfy driver stays storyboard-free, correlation via `panel_id` + `job_outputs`); `generation_jobs.output_dir`; covering-index note for `hidden`; add `storyboard` to the WS channel list. Keep each bullet to CLAUDE.md's existing density.
  - `docs/api-reference.md`: `/api/storyboard/*` route table, `storyboard` WS events, `include_hidden` param on `/api/media`.
  - `docs/architecture.md`: five tables + `media.hidden` in the schema section.

- [ ] **Step 5: Run everything** — `pytest tests/test_storyboard_api.py -v` → PASS; `make quality test` full suite; `cd frontend && npm run build` (should be untouched, verify no accidental breakage).

- [ ] **Step 6: Commit** — `git commit -m "feat(storyboard): REST API, service layer, lifespan wiring, and docs"`

---

## Self-Review Notes

- Spec coverage: §4.2/§4.3 → Task 2; §4.4 → Tasks 1, 7 (lifecycle), 2 (`select_panel_image`); §5.2 dims → Task 4; §6.1 → Tasks 5, 7; §6.2 → Task 4; §6.3 → Tasks 6, 7; §6.4 → Tasks 6, 7 (+ `prompt_source`); §7 consistency 1–5 → Tasks 6 (verbatim), 6 (style), 4 (seeds), 7 (LoRA, ref); §9 error rows → Tasks 7, 8; §10 test list → distributed per task. Contact sheets / video / multi-reference: deferred per spec.
- Phase C consumes: tree endpoint, `storyboard` + `comfy` WS channels, `select`, `synthesize`/`generate`/`cancel`, `include_hidden`.
- Type consistency spot-checks: `GenerationParams` field names match Phase A (`positive/seed/width/height/batch_size/negative/lora_name/lora_strength/ref_image`); `submit(preset_id, params, panel_id, priority, output_dir)` matches Task 3; `replace_storyboard_structure` input shape matches `validate_parse_response` output shape.
