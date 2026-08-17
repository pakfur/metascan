# Shot/Beat Data Model Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the per-H3-shot creative fields (framing, subjects, still-image prompt + keeper images) from `panels` (shots) down to `beats`, thin the shot into an H3-scene-like container, and hoist notes/negative to the storyboard level.

**Architecture:** SQLite drop-and-recreate migration (`user_version = 3`) reshapes `panels`/`beats` and renames `panel_images` → `beat_images`; the synthesize/generate still-image pipeline re-targets beats; the H3 compile/video pipeline stays per shot but derives subjects from beats and renders per-beat framing; API + Vue frontend follow.

**Tech Stack:** Python 3.11 / FastAPI / SQLite (raw sqlite3, threading.Lock), Vue 3 + Pinia + TypeScript, pytest, GBNF grammars for VLM output.

**Spec:** `docs/superpowers/specs/2026-08-17-shot-beat-model-design.md`

## Global Constraints

- `make quality test` (flake8 + black --check + mypy + pytest) must pass after every task's commit; `cd frontend && npm run build` must pass after every frontend task.
- mypy is strict on `metascan/core/*`; Python 3.11 syntax.
- Never import UI frameworks in `metascan/`; DB access stays synchronous, wrapped with `asyncio.to_thread` in `backend/services/`.
- GBNF: `\-` is not a valid escape — hyphens must be literal (crashes llama-server otherwise).
- DELETE endpoints return `{status: "deleted"}`, never 204.
- Stored paths are POSIX (`to_posix_path`); API responses convert with `to_native_path`.
- `PATCH` routes use `model_dump(exclude_unset=True)` + `_reject_null_for_required`.
- Every destructive path that cascades `beat_images` away must unhide (or purge) their media rows first, in the same transaction.
- Seed determinism: same beat + same variant index ⇒ same seed, run-to-run.
- The watcher test `test_file_watcher_triggers_reload` flakes on WSL2 full-suite runs — not a regression signal (re-run in isolation to confirm).

---

### Task 1: DB schema v3 — table reshape + drop-and-recreate migration

**Files:**
- Modify: `metascan/core/database_sqlite.py` (DDL around lines 614–760, updatable sets at 1364–1446, `_idempotent_add_column` block ~760–850, `user_version` gate ~866–920)
- Test: `tests/test_storyboard_db.py` (new tests appended), `tests/test_storyboard_beats_db.py`

**Interfaces:**
- Produces: new `panels` DDL (no framing/prompt/subject/notes/negative/selected_image_id columns), new `beats` DDL (adds `shot_size`, `angle`, `lens`, `subject_ids`, `brief`, `prompt`, `prompt_locked`, `prompt_source`, `selected_image_id`), `beat_images` table replacing `panel_images`, `generation_jobs.beat_id INTEGER` column, `storyboards.notes TEXT` column, `user_version = 3` migration.
- Produces: updated `_PANEL_UPDATABLE`, `_BEAT_UPDATABLE`, `_STORYBOARD_UPDATABLE` frozensets. Every later task relies on these exact column sets.

- [ ] **Step 1: Write the failing migration + schema tests**

Append to `tests/test_storyboard_db.py` (follow the file's existing fixture pattern — an isolated `DatabaseManager` on a tmp_path DB):

```python
def test_v3_schema_shapes(db):
    """Panels are thin containers; beats carry framing/prompt/keeper."""
    with db._get_connection() as conn:
        panel_cols = {r["name"] for r in conn.execute("PRAGMA table_info(panels)")}
        beat_cols = {r["name"] for r in conn.execute("PRAGMA table_info(beats)")}
        sb_cols = {r["name"] for r in conn.execute("PRAGMA table_info(storyboards)")}
        tables = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    for gone in ("shot_size", "angle", "lens", "notes", "negative", "brief",
                 "prompt", "prompt_locked", "prompt_source", "subject_ids",
                 "selected_image_id"):
        assert gone not in panel_cols, gone
    for kept in ("action", "duration_s", "video_prompt", "video_anchor",
                 "video_compiled_anchor"):
        assert kept in panel_cols, kept
    for added in ("shot_size", "angle", "lens", "subject_ids", "brief",
                  "prompt", "prompt_locked", "prompt_source",
                  "selected_image_id"):
        assert added in beat_cols, added
    assert "notes" in sb_cols
    assert "beat_images" in tables
    assert "panel_images" not in tables


def test_v3_migration_from_v2_layout(tmp_path):
    """A dev DB with the old panel-centric layout is dropped and rebuilt:
    hidden media released, panel-scoped jobs purged."""
    import sqlite3
    from metascan.core.database_sqlite import DatabaseManager

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE media (file_path TEXT PRIMARY KEY, hidden INTEGER DEFAULT 0);
        CREATE TABLE panels (id INTEGER PRIMARY KEY, prompt TEXT);
        CREATE TABLE beats (id INTEGER PRIMARY KEY, panel_id INTEGER);
        CREATE TABLE panel_images (id INTEGER PRIMARY KEY, panel_id INTEGER,
            file_path TEXT);
        CREATE TABLE generation_jobs (id INTEGER PRIMARY KEY, panel_id INTEGER);
        INSERT INTO media VALUES ('a/x.png', 1);
        INSERT INTO panel_images VALUES (1, 1, 'a/x.png');
        INSERT INTO generation_jobs VALUES (7, 1);
        PRAGMA user_version = 2;
        """
    )
    conn.commit()
    conn.close()

    db = DatabaseManager(tmp_path)  # adapt to the fixture's constructor shape
    with db._get_connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] >= 3
        assert c.execute(
            "SELECT hidden FROM media WHERE file_path='a/x.png'"
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM generation_jobs WHERE panel_id IS NOT NULL"
        ).fetchone()[0] == 0
```

Note: check how `tests/test_storyboard_db.py` constructs `DatabaseManager` (path argument shape, `db` fixture) and mirror it exactly; the second test constructs its own instance on a pre-seeded file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storyboard_db.py -k v3 -v`
Expected: FAIL (`panel_images` still exists, `prompt` still in panel_cols).

- [ ] **Step 3: Reshape the DDL**

In `_init_database`:

1. `CREATE TABLE IF NOT EXISTS panels` becomes:

```python
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS panels (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        scene_id           INTEGER NOT NULL
                           REFERENCES scenes(id) ON DELETE CASCADE,
        sort_order         INTEGER NOT NULL DEFAULT 0,
        action             TEXT NOT NULL,
        duration_s         REAL NOT NULL DEFAULT 12.0,
        created_at         TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
```

(The `video_prompt*` / `video_anchor*` columns keep arriving via the existing `_idempotent_add_column` calls — leave those calls in place; delete the `duration_s` add-column call since it's now in the base DDL.)

2. Replace the `panel_images` DDL with `beat_images`:

```python
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS beat_images (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        beat_id         INTEGER NOT NULL
                        REFERENCES beats(id) ON DELETE CASCADE,
        file_path       TEXT NOT NULL REFERENCES media(file_path)
                        ON DELETE CASCADE,
        seed            INTEGER,
        variant_index   INTEGER NOT NULL DEFAULT 0,
        prompt_used     TEXT,
        preset_id       INTEGER REFERENCES workflow_presets(id),
        comfy_prompt_id TEXT,
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
```

Order matters: `beats` must be created before `beat_images` (FK target). Move the `beats` CREATE above it.

3. `CREATE TABLE IF NOT EXISTS beats` becomes:

```python
conn.execute(
    """
    CREATE TABLE IF NOT EXISTS beats (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        panel_id    INTEGER NOT NULL
                    REFERENCES panels(id) ON DELETE CASCADE,
        sort_order  INTEGER NOT NULL DEFAULT 0,
        duration_s  REAL NOT NULL DEFAULT 4.0,
        action      TEXT NOT NULL,
        shot_size   TEXT,
        angle       TEXT,
        lens        TEXT,
        subject_ids TEXT NOT NULL DEFAULT '[]',
        camera_motion    TEXT,
        camera_amplitude TEXT,
        camera_speed     TEXT,
        is_cut      INTEGER NOT NULL DEFAULT 0,
        dialog      TEXT NOT NULL DEFAULT '[]',
        sound       TEXT,
        brief       TEXT,
        prompt      TEXT,
        prompt_locked INTEGER NOT NULL DEFAULT 0,
        prompt_source TEXT,
        selected_image_id INTEGER REFERENCES beat_images(id)
                          ON DELETE SET NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """
)
```

`beats.selected_image_id` references `beat_images`, which references `beats` — a circular FK. SQLite allows forward references in DDL as long as both tables exist before rows are inserted, so keep the creation order `beats` → `beat_images` (matching the old `panels` → `panel_images` precedent, which had the same cycle).

4. Add after the existing storyboard add-column block:

```python
_idempotent_add_column(
    conn,
    "storyboards",
    "notes",
    "ALTER TABLE storyboards ADD COLUMN notes TEXT",
)
_idempotent_add_column(
    conn,
    "generation_jobs",
    "beat_id",
    # Deliberately no REFERENCES clause -- same rationale as panel_id:
    # the release helpers delete job rows explicitly.
    "ALTER TABLE generation_jobs ADD COLUMN beat_id INTEGER",
)
conn.execute("CREATE INDEX IF NOT EXISTS idx_beat_images_beat ON beat_images(beat_id)")
```

- [ ] **Step 4: Write the v3 migration gate**

In the `user_version` block, after the `if user_version < 2:` gate:

```python
if user_version < 3:
    # Shot/beat model reorganization (spec 2026-08-17): panels thin to
    # H3-scene containers, beats carry framing/prompt/keeper,
    # panel_images becomes beat_images. Dev data is disposable by
    # decision -- drop and recreate, but first release what the old
    # tables were holding: unhide media the old panel_images kept
    # hidden, and purge panel-scoped generation_jobs so a restart
    # can't re-adopt jobs for panels that no longer exist.
    old_tables = {
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "panel_images" in old_tables:
        conn.execute(
            "UPDATE media SET hidden = 0 WHERE file_path IN "
            "(SELECT file_path FROM panel_images)"
        )
    if "generation_jobs" in old_tables:
        conn.execute("DELETE FROM generation_jobs WHERE panel_id IS NOT NULL")
    conn.execute("DROP TABLE IF EXISTS panel_images")
    conn.execute("DROP TABLE IF EXISTS beat_images")
    conn.execute("DROP TABLE IF EXISTS beats")
    conn.execute("DROP TABLE IF EXISTS panels")
    conn.execute("PRAGMA user_version = 3")
```

Placement constraint: this gate must run **before** the `CREATE TABLE IF NOT EXISTS panels/beats/beat_images` statements so the drop is followed by a clean re-create in the same `_init_database` call. Check where the existing `user_version` block sits relative to the CREATEs — currently the gates run *after* the CREATE block, so for v3 either (a) move the v3 gate to before the storyboard-tree CREATEs, or (b) after dropping, re-invoke the same CREATE statements inline. Option (a) is cleaner: read `user_version` once at the top of the storyboard DDL section, run the v3 drop there, and leave the `PRAGMA user_version = 3` write with the other gates. `media.hidden` on a fresh DB: guard every old-table read with the `old_tables` check as shown.

- [ ] **Step 5: Update the updatable-field sets**

```python
_STORYBOARD_UPDATABLE: ClassVar[frozenset] = frozenset(
    {
        "name", "source_text", "aspect_ratio", "style_block", "negative",
        "notes",
        "target_model", "architecture", "preset_id", "base_seed",
        "batch_size", "folder_id", "outline", "video_target", "video_mode",
        "video_preset_id",
    }
)
_PANEL_UPDATABLE: ClassVar[frozenset] = frozenset(
    {
        "sort_order", "action", "duration_s",
        "video_prompt", "video_prompt_locked", "video_prompt_source",
        "video_prompt_warnings", "video_anchor", "video_compiled_anchor",
    }
)
_BEAT_UPDATABLE: ClassVar[frozenset] = frozenset(
    {
        "sort_order", "duration_s", "action",
        "shot_size", "angle", "lens", "subject_ids",
        "camera_motion", "camera_amplitude", "camera_speed",
        "is_cut", "dialog", "sound",
        "brief", "prompt", "prompt_locked", "prompt_source",
    }
)
```

`selected_image_id` is deliberately absent from `_BEAT_UPDATABLE` — keeper selection goes through `select_beat_image` (Task 2), mirroring the old panel rule.

- [ ] **Step 6: Run the new tests; fix until green**

Run: `pytest tests/test_storyboard_db.py -k v3 -v`
Expected: PASS. The rest of the DB suite will still be red (CRUD methods reference dropped columns) — that's Task 2's job; do NOT try to fix the full suite here.

- [ ] **Step 7: Commit**

```bash
git add metascan/core/database_sqlite.py tests/test_storyboard_db.py
git commit -m "feat(db): v3 schema — beats carry framing/prompt/keeper, beat_images table"
```

---

### Task 2: DB CRUD — beat-level images/keeper, release helpers, tree

**Files:**
- Modify: `metascan/core/database_sqlite.py` (methods at 1731–2418)
- Modify: `backend/services/storyboard_service.py` (wrappers at 182–218)
- Test: `tests/test_storyboard_db.py`, `tests/test_storyboard_beats_db.py`

**Interfaces:**
- Consumes: Task 1 schema.
- Produces (exact signatures later tasks call):
  - `create_panel(scene_id, *, action, sort_order=0, duration_s=12.0) -> int`
  - `create_beat(panel_id, *, action, sort_order=0, duration_s=4.0, shot_size=None, angle=None, lens=None, subject_ids=None, camera_motion=None, camera_amplitude=None, camera_speed=None, is_cut=0, dialog=None, sound=None) -> int`
  - `replace_panel_beats(panel_id, beats) -> List[int]` (now releases old beats first; beat dicts may carry `shot_size`/`angle`/`lens`/`subject_ids`)
  - `create_beat_image(beat_id, *, file_path, seed=None, variant_index=0, prompt_used=None, preset_id=None, comfy_prompt_id=None) -> int`
  - `list_beat_images(beat_id) -> List[Dict]`, `count_beat_images(beat_id) -> int`
  - `select_beat_image(beat_id, image_id) -> bool`
  - `delete_beat(beat_id, purge_images=False) -> Tuple[bool, List[str]]` (**signature change** — was `-> bool`)
  - `_release_beats(conn, beat_ids, purge_images=False) -> List[str]`
  - `panel_id_for_beat(beat_id) -> Optional[int]`
  - `list_generation_jobs(..., beat_ids=None, ...)` gains a beat filter (same shape as the existing `panel_ids` filter)
  - `get_storyboard_tree` beats now include `subject_ids: List[int]` (decoded) and `images: List[Dict]` (native paths); panels lose `subject_ids`/`images` decoding.
  - Service wrappers: `select_beat_image`, `delete_beat(beat_id, purge_images=False)` (returns bool after handling purge like `delete_panel` does), `beat_exists`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_storyboard_beats_db.py` (reuse its existing fixtures for a storyboard→scene→panel chain; add a media row helper the way `tests/test_storyboard_db.py`'s image tests do):

```python
def test_beat_images_crud_and_keeper(db, panel_id, media_paths):
    """create/list/count beat_images; select_beat_image flips hidden."""
    beat_id = db.create_beat(panel_id, action="a beat")
    img1 = db.create_beat_image(beat_id, file_path=media_paths[0], seed=1,
                                variant_index=0)
    img2 = db.create_beat_image(beat_id, file_path=media_paths[1], seed=2,
                                variant_index=1)
    assert db.count_beat_images(beat_id) == 2
    assert [i["id"] for i in db.list_beat_images(beat_id)] == [img1, img2]

    db.set_media_hidden(media_paths[0], True)
    db.set_media_hidden(media_paths[1], True)
    assert db.select_beat_image(beat_id, img1) is True
    assert _hidden(db, media_paths[0]) == 0        # keeper unhidden
    assert db.select_beat_image(beat_id, img2) is True
    assert _hidden(db, media_paths[0]) == 1        # old keeper re-hidden
    assert _hidden(db, media_paths[1]) == 0
    assert db.select_beat_image(beat_id, None) is True
    assert db.get_beat(beat_id)["selected_image_id"] is None


def test_delete_beat_releases_media_and_jobs(db, panel_id, media_paths):
    beat_id = db.create_beat(panel_id, action="a beat")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    _insert_job(db, panel_id=panel_id, beat_id=beat_id)   # raw INSERT helper
    ok, purged = db.delete_beat(beat_id)
    assert ok is True and purged == []
    assert _hidden(db, media_paths[0]) == 0
    assert _job_count(db, beat_id=beat_id) == 0


def test_delete_beat_purge_returns_paths(db, panel_id, media_paths):
    beat_id = db.create_beat(panel_id, action="a beat")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    ok, purged = db.delete_beat(beat_id, purge_images=True)
    assert ok is True
    assert len(purged) == 1                       # native path returned
    with db._get_connection() as conn:            # media row deleted
        assert conn.execute(
            "SELECT 1 FROM media WHERE file_path = ?", (media_paths[0],)
        ).fetchone() is None


def test_replace_panel_beats_releases_old_beats(db, panel_id, media_paths):
    beat_id = db.create_beat(panel_id, action="old")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    new_ids = db.replace_panel_beats(panel_id, [
        {"action": "new", "shot_size": "CU", "subject_ids": [1, 2]},
    ])
    assert len(new_ids) == 1
    assert _hidden(db, media_paths[0]) == 0
    got = db.get_beat(new_ids[0])
    assert got["shot_size"] == "CU" and got["subject_ids"] == [1, 2]


def test_delete_panel_releases_beat_images(db, panel_id, media_paths):
    beat_id = db.create_beat(panel_id, action="b")
    db.create_beat_image(beat_id, file_path=media_paths[0])
    db.set_media_hidden(media_paths[0], True)
    ok, _ = db.delete_panel(panel_id)
    assert ok is True
    assert _hidden(db, media_paths[0]) == 0


def test_tree_beats_carry_images_and_subject_ids(db, panel_id, media_paths,
                                                 storyboard_id):
    beat_id = db.create_beat(panel_id, action="b", subject_ids=[3])
    db.create_beat_image(beat_id, file_path=media_paths[0])
    tree = db.get_storyboard_tree(storyboard_id)
    beat = tree["scenes"][0]["panels"][0]["beats"][0]
    assert beat["subject_ids"] == [3]
    assert len(beat["images"]) == 1
    assert "subject_ids" not in tree["scenes"][0]["panels"][0]
```

Write the small helpers (`_hidden`, `_insert_job`, `_job_count`) at module top with direct `db._get_connection()` SQL, following how the existing tests in this file poke at internals.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_storyboard_beats_db.py -v`
Expected: FAIL (`create_beat_image` doesn't exist, `delete_beat` returns bool, …).

- [ ] **Step 3: Implement the DB methods**

All in `database_sqlite.py`, replacing the panel-image block:

1. **`create_panel`** — drop `shot_size/angle/lens/subject_ids/notes` params and columns (keep `action`, `sort_order`, `duration_s`).
2. **`create_beat`** — add the new keyword args and columns:

```python
def create_beat(
    self,
    panel_id: int,
    *,
    action: str,
    sort_order: int = 0,
    duration_s: float = 4.0,
    shot_size: Optional[str] = None,
    angle: Optional[str] = None,
    lens: Optional[str] = None,
    subject_ids: Optional[List[int]] = None,
    camera_motion: Optional[str] = None,
    camera_amplitude: Optional[str] = None,
    camera_speed: Optional[str] = None,
    is_cut: int = 0,
    dialog: Optional[List[Dict[str, Any]]] = None,
    sound: Optional[str] = None,
) -> int:
    import json as _json

    with self.lock, self._get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO beats (panel_id, sort_order, duration_s, action, "
            "shot_size, angle, lens, subject_ids, camera_motion, "
            "camera_amplitude, camera_speed, is_cut, dialog, sound) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                panel_id, sort_order, duration_s, action,
                shot_size, angle, lens,
                _json.dumps(list(subject_ids or [])),
                camera_motion, camera_amplitude, camera_speed,
                is_cut, _json.dumps(list(dialog or [])), sound,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
```

3. **`_decode_beat_row`** — also decode `subject_ids` (same try/except shape as `dialog`).
4. **`update_beat`** — add `if "subject_ids" in fields: fields["subject_ids"] = _json.dumps(list(fields["subject_ids"] or []))` next to the dialog encoding.
5. **`get_panel`** — drop the `subject_ids` decode (column gone); keep `video_prompt_warnings`.
6. **`update_panel`** — drop the `subject_ids` encode branch.
7. **`_release_beats`** — new, modeled verbatim on `_release_panels` with `beat_images`/`beat_id`:

```python
def _release_beats(
    self,
    conn: sqlite3.Connection,
    beat_ids: List[int],
    purge_images: bool = False,
) -> List[str]:
    """Unhide beat_images' media rows and purge generation_jobs for
    ``beat_ids``, before those beats (and their beat_images) are
    cascade-deleted. Same transaction rule and purge contract as
    _release_panels (which now delegates the image work here)."""
    if not beat_ids:
        return []
    placeholders = ",".join("?" * len(beat_ids))
    purged: List[str] = []
    if purge_images:
        purged = [
            str(r["file_path"])
            for r in conn.execute(
                f"SELECT DISTINCT file_path FROM beat_images "
                f"WHERE beat_id IN ({placeholders})",
                beat_ids,
            ).fetchall()
        ]
    else:
        conn.execute(
            f"UPDATE media SET hidden = 0 WHERE file_path IN "
            f"(SELECT file_path FROM beat_images "
            f"WHERE beat_id IN ({placeholders}))",
            beat_ids,
        )
    conn.execute(
        f"DELETE FROM generation_jobs WHERE beat_id IN ({placeholders})",
        beat_ids,
    )
    return purged
```

8. **`_release_panels`** — rewrite: first collect the panels' beat ids and delegate image release to `_release_beats`, then delete the panels' own (video) jobs:

```python
def _release_panels(
    self,
    conn: sqlite3.Connection,
    panel_ids: List[int],
    purge_images: bool = False,
) -> List[str]:
    if not panel_ids:
        return []
    placeholders = ",".join("?" * len(panel_ids))
    beat_ids = [
        int(r["id"])
        for r in conn.execute(
            f"SELECT id FROM beats WHERE panel_id IN ({placeholders})",
            panel_ids,
        ).fetchall()
    ]
    purged = self._release_beats(conn, beat_ids, purge_images)
    conn.execute(
        f"DELETE FROM generation_jobs WHERE panel_id IN ({placeholders})",
        panel_ids,
    )
    return purged
```

Keep (adapt) the original docstring — the transaction rule and the "must not delete media rows here" purge rationale still apply.

9. **`_purge_media_rows`** — change the shared-reference check's first SELECT from `panel_images` to `beat_images` (subject/scene reference checks unchanged).
10. **`delete_beat`** — new signature + release:

```python
def delete_beat(
    self, beat_id: int, purge_images: bool = False
) -> Tuple[bool, List[str]]:
    with self.lock, self._get_connection() as conn:
        cur = conn.execute("SELECT id FROM beats WHERE id = ?", (beat_id,))
        if cur.fetchone() is None:
            return False, []
        purge_paths = self._release_beats(conn, [beat_id], purge_images)
        conn.execute("DELETE FROM beats WHERE id = ?", (beat_id,))
        deleted_files = self._purge_media_rows(conn, purge_paths)
        conn.commit()
        return True, deleted_files
```

11. **`replace_panel_beats`** — release old beats first, insert new fields:

```python
def replace_panel_beats(
    self, panel_id: int, beats: List[Dict[str, Any]]
) -> List[int]:
    """Transactionally replace a panel's beats (compose stage 4).
    Releases the old beats' images/jobs first -- beats carry identity
    (keepers, locked prompts) since the shot/beat reorg."""
    import json as _json

    with self.lock, self._get_connection() as conn:
        old_ids = [
            int(r["id"])
            for r in conn.execute(
                "SELECT id FROM beats WHERE panel_id = ?", (panel_id,)
            ).fetchall()
        ]
        self._release_beats(conn, old_ids)
        conn.execute("DELETE FROM beats WHERE panel_id = ?", (panel_id,))
        new_ids: List[int] = []
        for i, b in enumerate(beats):
            cur = conn.execute(
                "INSERT INTO beats (panel_id, sort_order, duration_s, "
                "action, shot_size, angle, lens, subject_ids, "
                "camera_motion, camera_amplitude, camera_speed, "
                "is_cut, dialog, sound) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    panel_id,
                    b.get("sort_order", i),
                    b.get("duration_s", 4.0),
                    b["action"],
                    b.get("shot_size"),
                    b.get("angle"),
                    b.get("lens"),
                    _json.dumps(list(b.get("subject_ids") or [])),
                    b.get("camera_motion"),
                    b.get("camera_amplitude"),
                    b.get("camera_speed"),
                    int(b.get("is_cut", 0)),
                    _json.dumps(list(b.get("dialog") or [])),
                    b.get("sound"),
                ),
            )
            new_ids.append(int(cur.lastrowid))
        conn.commit()
        return new_ids
```

12. **`replace_scene_panels`** — insert only `(scene_id, sort_order, action, duration_s)`.
13. **`replace_storyboard_structure`** — panels INSERT loses `shot_size/angle/lens/subject_ids` (drop the `name_to_id` subject-resolution for panels; subjects themselves are still inserted).
14. **Beat images** — rename the three panel-image methods (`create_panel_image` → `create_beat_image`, `list_panel_images` → `list_beat_images`, `count_panel_images` → `count_beat_images`), swapping table/column names; **`select_panel_image` → `select_beat_image`** — same body against `beats`/`beat_images`:

```python
def select_beat_image(self, beat_id: int, image_id: Optional[int]) -> bool:
    """Set a beat's keeper. Unhides the new keeper's media row, re-hides
    the previous keeper's. image_id=None clears the selection."""
    with self.lock:
        with self._get_connection() as conn:
            beat = conn.execute(
                "SELECT selected_image_id FROM beats WHERE id = ?",
                (beat_id,),
            ).fetchone()
            if beat is None:
                return False
            new_path = None
            if image_id is not None:
                row = conn.execute(
                    "SELECT file_path FROM beat_images "
                    "WHERE id = ? AND beat_id = ?",
                    (image_id, beat_id),
                ).fetchone()
                if row is None:
                    return False
                new_path = row["file_path"]
            old_id = beat["selected_image_id"]
            if old_id is not None and old_id != image_id:
                old = conn.execute(
                    "SELECT file_path FROM beat_images WHERE id = ?",
                    (old_id,),
                ).fetchone()
                if old is not None:
                    conn.execute(
                        "UPDATE media SET hidden = 1 WHERE file_path = ?",
                        (old["file_path"],),
                    )
            if new_path is not None:
                conn.execute(
                    "UPDATE media SET hidden = 0 WHERE file_path = ?",
                    (new_path,),
                )
            conn.execute(
                "UPDATE beats SET selected_image_id = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (image_id, beat_id),
            )
            conn.commit()
            return True
```

15. **`get_storyboard_tree`** — panels: drop `subject_ids` decode and the `panel_images` loop; beats: decode via `_decode_beat_row` (now handles `subject_ids`) and attach images:

```python
beats = []
for br in conn.execute(
    "SELECT * FROM beats WHERE panel_id = ? ORDER BY sort_order, id",
    (panel["id"],),
).fetchall():
    beat = self._decode_beat_row(br)
    beat["images"] = []
    for ir in conn.execute(
        "SELECT * FROM beat_images WHERE beat_id = ? "
        "ORDER BY variant_index, id",
        (beat["id"],),
    ).fetchall():
        image = dict(ir)
        image["file_path"] = to_native_path(image["file_path"])
        beat["images"].append(image)
    beats.append(beat)
panel["beats"] = beats
```

16. **Lookups** — next to `storyboard_id_for_panel`:

```python
def panel_id_for_beat(self, beat_id: int) -> Optional[int]:
    with self.lock, self._get_connection() as conn:
        row = conn.execute(
            "SELECT panel_id FROM beats WHERE id = ?", (beat_id,)
        ).fetchone()
        return int(row["panel_id"]) if row is not None else None
```

17. **`list_generation_jobs`** — add a `beat_ids: Optional[List[int]] = None` parameter producing `beat_id IN (...)` in the WHERE clause, exactly parallel to the existing `panel_ids` handling (find the method and mirror it). Also update `get_generation_job`/insert path in `comfy_client.py` **only** in Task 5 — here just the filter.

- [ ] **Step 4: Update the service layer**

In `backend/services/storyboard_service.py`:

```python
async def delete_beat(self, beat_id: int, purge_images: bool = False) -> bool:
    ok, purged = await asyncio.to_thread(
        self.db.delete_beat, beat_id, purge_images
    )
    if purged:
        await asyncio.to_thread(_remove_files_sync, purged)
    return ok

async def select_beat_image(self, beat_id: int, image_id: Optional[int]) -> bool:
    return await asyncio.to_thread(self.db.select_beat_image, beat_id, image_id)

async def beat_exists(self, beat_id: int) -> bool:
    return await asyncio.to_thread(_row_exists_sync, self.db, "beats", beat_id)
```

Delete `select_panel_image` from the service; mirror how `delete_panel` handles its `purged` list (read it — it already calls `_remove_files_sync`). `create_panel`'s wrapper passes through `**fields`; no change needed beyond callers.

- [ ] **Step 5: Fix the now-broken existing DB tests**

Run: `pytest tests/test_storyboard_db.py tests/test_storyboard_beats_db.py tests/test_storyboard_refs_db.py tests/test_storyboard_video_db.py tests/test_storyboard_videogen_db.py -v`

Migrate failures to the beat level: any test calling `create_panel(..., shot_size=...)` moves the framing kwargs to `create_beat`; `create_panel_image` → `create_beat_image(beat_id, ...)` (create a beat first); `select_panel_image` → `select_beat_image`; `delete_beat` unpacks the tuple. Keep test intent identical — these are migrations, not rewrites.

- [ ] **Step 6: Run and commit**

Run: `pytest tests/ -k "storyboard" -v` — compose/synthesis/runner/api tests will still fail (Tasks 3–7); confirm all `*_db` tests pass.

```bash
git add metascan/core/database_sqlite.py backend/services/storyboard_service.py tests/
git commit -m "feat(db): beat-level images/keeper/release helpers, thin panel CRUD"
```

---

### Task 3: Pure helpers — `beat_seed` + beat-level `compose_brief`

**Files:**
- Modify: `metascan/core/storyboard_brief.py:74-113`
- Test: `tests/test_storyboard_brief.py`

**Interfaces:**
- Produces: `beat_seed(base_seed: int, panel_sort_order: int, beat_sort_order: int, variant_index: int) -> int`; `compose_brief(storyboard, scene, panel, beat, subjects) -> str` (**new `beat` argument**; framing read from `beat`, action from `beat["action"]`, shot context from `panel["action"]`). `panel_seed` is deleted.
- Consumed by: Task 6 (synthesize), Task 7 (generate).

- [ ] **Step 1: Write failing tests**

In `tests/test_storyboard_brief.py`, replace the `panel_seed` tests and extend the brief tests:

```python
def test_beat_seed_deterministic_and_collision_free():
    from metascan.core.storyboard_brief import beat_seed
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


def test_compose_brief_reads_framing_from_beat():
    from metascan.core.storyboard_brief import compose_brief
    sb = {"aspect_ratio": "16:9"}
    scene = {"setting": "a dim bar", "mood": "tense"}
    panel = {"action": "The standoff"}
    beat = {"action": "She reaches for the glass", "shot_size": "CU",
            "angle": "low", "lens": "tele"}
    subjects = [{"name": "Mara", "description": "a tired detective"}]
    brief = compose_brief(sb, scene, panel, beat, subjects)
    assert "close-up" in brief and "low angle" in brief and "telephoto" in brief
    assert "She reaches for the glass" in brief
    assert "SHOT CONTEXT: The standoff" in brief
    assert "Mara" in brief
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_storyboard_brief.py -v` — FAIL (`beat_seed` undefined, `compose_brief` arity).

- [ ] **Step 3: Implement**

```python
def beat_seed(
    base_seed: int, panel_sort_order: int, beat_sort_order: int, variant_index: int
) -> int:
    """Deterministic per-beat seed; a reroll advances variant_index.
    Collision-free for < 100 beats/shot and < 1000 variants/beat."""
    return base_seed + (panel_sort_order * 100 + beat_sort_order) * 1000 + variant_index
```

Delete `panel_seed`. Rewrite `compose_brief`:

```python
def compose_brief(
    storyboard: Mapping[str, Any],
    scene: Mapping[str, Any],
    panel: Mapping[str, Any],
    beat: Mapping[str, Any],
    subjects: Sequence[Mapping[str, Any]],
) -> str:
    """Deterministic beat brief. The style block is deliberately absent:
    it is concatenated onto the final prompt outside the LLM call so the
    global look cannot drift through paraphrase (spec §7.2)."""
    shot_bits = [
        SHOT_SIZES.get(beat.get("shot_size") or ""),
        ANGLES.get(beat.get("angle") or ""),
        LENSES.get(beat.get("lens") or ""),
        storyboard.get("aspect_ratio"),
    ]
    lines = [f"SHOT: {', '.join(b for b in shot_bits if b)}"]
    for subj in subjects:
        lines.append(f"SUBJECT {subj['name']}: {subj['description']}")
    if beat.get("action"):
        lines.append(f"ACTION: {beat['action']}")
    if panel.get("action"):
        lines.append(f"SHOT CONTEXT: {panel['action']}")
    if scene.get("setting"):
        lines.append(f"SETTING: {scene['setting']}")
    if scene.get("location"):
        lines.append(f"LOCATION: {scene['location']}")
    light_bits = [scene.get("time_of_day"), scene.get("lighting"), scene.get("mood")]
    light = ", ".join(b for b in light_bits if b)
    if light:
        lines.append(f"LIGHT/MOOD: {light}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run, fix remaining brief-test fallout, commit**

Run: `pytest tests/test_storyboard_brief.py -v` → PASS.

```bash
git add metascan/core/storyboard_brief.py tests/test_storyboard_brief.py
git commit -m "feat(brief): beat_seed and beat-level compose_brief"
```

---

### Task 4: Story stages — shots grammar slims, beats grammar gains framing + subjects

**Files:**
- Modify: `metascan/core/storyboard_story.py:109-205, 335-441`
- Test: `tests/test_storyboard_story.py`

**Interfaces:**
- Consumes: enum constants `SHOT_SIZE_VALUES`/`ANGLE_VALUES`/`LENS_VALUES` (already imported from `storyboard_parse`).
- Produces: `SHOTS_GRAMMAR` (shot = action + duration only), `validate_shots_response(raw) -> List[Dict]` (**no `roster` param, no warnings tuple** — returns `[{action, duration_s}]`), `BEATS_GRAMMAR` (beat gains `shot_size`/`angle`/`lens`/`subjects`), `validate_beats_response(raw, roster) -> Tuple[List[Dict], List[str]]` (**now returns `(beats, warnings)`**; beat dicts gain `shot_size`, `angle`, `lens`, `subject_ids`), `build_beats_user_prompt(logline, scene, panel, subjects)` (unchanged signature; `subjects` is now the full roster), `build_shots_user_prompt` (unchanged).
- Consumed by: Task 5.

- [ ] **Step 1: Write failing tests**

In `tests/test_storyboard_story.py` (follow the file's existing raw-JSON-string test style):

```python
def test_shots_validator_slim_shape():
    raw = '[{"action": "The chase begins", "duration_s": 10}]'
    panels = story.validate_shots_response(raw)
    assert panels == [{"action": "The chase begins", "duration_s": 10.0}]


def test_shots_grammar_has_no_framing_or_subjects():
    for gone in ("shot_size", "subjects", "angle", "lens"):
        assert gone not in story.SHOTS_GRAMMAR


def test_beats_validator_maps_subjects_and_framing():
    roster = {"mara": 3, "june": 5}
    raw = ('[{"duration_s": 4, "action": "Mara turns", '
           '"shot_size": "CU", "angle": "low", "lens": "bogus", '
           '"subjects": ["Mara", "Nobody"], '
           '"camera_motion": "static", "camera_amplitude": null, '
           '"camera_speed": null, "is_cut": false, "sound": null, '
           '"dialog": []}]')
    beats, warnings = story.validate_beats_response(raw, roster)
    assert beats[0]["shot_size"] == "CU"
    assert beats[0]["angle"] == "low"
    assert beats[0]["lens"] is None            # bogus enum drops to NULL
    assert beats[0]["subject_ids"] == [3]      # unknown name dropped
    assert any("Nobody" in w for w in warnings)


def test_beats_grammar_carries_framing_and_subjects():
    for needed in ("shot_size", "angle", "lens", "subjects"):
        assert needed in story.BEATS_GRAMMAR
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_storyboard_story.py -v` — FAIL.

- [ ] **Step 3: Implement**

1. `_SHOTS_TEMPLATE` becomes (no format placeholders left — collapse like `SCENES_GRAMMAR` with `.format()`):

```python
SHOTS_GRAMMAR = (
    r"""root ::= "[" ws shot (ws "," ws shot){{0,5}} ws "]"
shot ::= "{{" ws "\"action\"" ws ":" ws string ws "," ws "\"duration_s\"" ws ":" ws number ws "}}"
"""
    + _COMMON_RULES
).format()
```

2. `_BEATS_TEMPLATE` beat rule gains framing + subjects (insert after `"action"`, before `"camera_motion"`):

```python
_BEATS_TEMPLATE = (
    r"""root ::= "[" ws beat (ws "," ws beat){{1,5}} ws "]"
beat ::= "{{" ws "\"duration_s\"" ws ":" ws number ws "," ws "\"action\"" ws ":" ws string ws "," ws "\"shot_size\"" ws ":" ws shotsize ws "," ws "\"angle\"" ws ":" ws angle ws "," ws "\"lens\"" ws ":" ws lens ws "," ws "\"subjects\"" ws ":" ws namelist ws "," ws "\"camera_motion\"" ws ":" ws motion ws "," ws "\"camera_amplitude\"" ws ":" ws amplitude ws "," ws "\"camera_speed\"" ws ":" ws speed ws "," ws "\"is_cut\"" ws ":" ws boolean ws "," ws "\"sound\"" ws ":" ws nullable ws "," ws "\"dialog\"" ws ":" ws dialog ws "}}"
shotsize ::= {shotsize_alts}
angle ::= {angle_alts}
lens ::= {lens_alts}
namelist ::= "[" ws (string (ws "," ws string){{0,5}})? ws "]"
motion ::= {motion_alts}
amplitude ::= {amplitude_alts}
speed ::= {speed_alts}
dialog ::= "[" ws (line (ws "," ws line){{0,3}})? ws "]"
line ::= "{{" ws "\"subject\"" ws ":" ws nullable ws "," ws "\"voice\"" ws ":" ws nullable ws "," ws "\"delivery\"" ws ":" ws nullable ws "," ws "\"language\"" ws ":" ws string ws "," ws "\"text\"" ws ":" ws string ws "}}"
"""
    + _COMMON_RULES
)

BEATS_GRAMMAR = _BEATS_TEMPLATE.format(
    shotsize_alts=_alts(SHOT_SIZE_VALUES),
    angle_alts=_alts(ANGLE_VALUES),
    lens_alts=_alts(LENS_VALUES),
    motion_alts=_alts(CAMERA_MOTION_VALUES),
    amplitude_alts=_alts(CAMERA_AMPLITUDE_VALUES),
    speed_alts=_alts(CAMERA_SPEED_VALUES),
)
```

3. `validate_shots_response(raw)` — drop the `roster` param and warnings; keep `_loads_array` + action/duration handling; the returned dicts are exactly `{"action": ..., "duration_s": ...}`.
4. `validate_beats_response(raw, roster)` — return `(beats, warnings)`; add to each beat dict (using the same enum-or-None pattern as camera fields):

```python
ids: List[int] = []
for n in b.get("subjects") or []:
    if not isinstance(n, str):
        continue
    key = n.strip().lower()
    if key in roster:
        ids.append(roster[key])
    elif key:
        warnings.append(f"unknown subject {n!r} dropped")
...
"shot_size": (
    b.get("shot_size") if b.get("shot_size") in SHOT_SIZE_VALUES else None
),
"angle": b.get("angle") if b.get("angle") in ANGLE_VALUES else None,
"lens": b.get("lens") if b.get("lens") in LENS_VALUES else None,
"subject_ids": ids,
```

5. `build_shots_user_prompt` — the roster block stays (names must be used verbatim in action text); remove nothing here. `build_beats_user_prompt` — change the subjects line label from `"Subjects in shot (exact names)"` to `"Subject roster (assign per beat; exact names)"` so the prompt matches the new semantics.
6. Update `data/meta_prompt.yml`'s `STORY_SHOTS_SYSTEM` / `STORY_BEATS_SYSTEM` prompts: shots system prompt no longer describes framing/subject fields; beats system prompt describes the new `shot_size`/`angle`/`lens`/`subjects` fields (read both prompts first; keep their voice and length, edit only the field inventory sentences).

- [ ] **Step 4: Run, fix existing story-test fallout, commit**

Run: `pytest tests/test_storyboard_story.py -v` → migrate old assertions (shots framing expectations move to beats tests). PASS, then:

```bash
git add metascan/core/storyboard_story.py data/meta_prompt.yml tests/test_storyboard_story.py
git commit -m "feat(story): framing+subjects move from shots grammar to beats grammar"
```

---### Task 5: Runner — compose stages + beats gate

**Files:**
- Modify: `metascan/core/storyboard_runner.py:195-528` (`check_compose_gates`, `_run_stage`), `metascan/core/comfy_client.py` (`submit` signature + job INSERT)
- Test: `tests/test_storyboard_compose.py`, `tests/test_storyboard_compose_api.py`

**Interfaces:**
- Consumes: Task 2 (`replace_panel_beats` releasing), Task 4 validators/grammars.
- Produces: `check_compose_gates` gains a `panel_ids` parameter (`(storyboard_id, stages, scene_ids, panel_ids, confirm)`) and a beats gate; `_run_stage`'s beats stage stores framing/subjects; `ComfyClient.submit(..., beat_id: Optional[int] = None)` writing `generation_jobs.beat_id`.

- [ ] **Step 1: Write failing gate tests**

In `tests/test_storyboard_compose.py` (reuse its fake-VLM/runner fixtures):

```python
async def test_beats_stage_gated_when_beats_have_identity(runner, storyboard_id,
                                                          panel_id, db, media):
    beat_id = db.create_beat(panel_id, action="b")
    db.create_beat_image(beat_id, file_path=media)
    with pytest.raises(ConfirmRequiredError) as exc:
        await runner.check_compose_gates(
            storyboard_id, ("beats",), None, None, False
        )
    assert getattr(exc.value, "_compose_stage", None) == "beats"


async def test_beats_stage_gated_on_locked_prompt(runner, storyboard_id,
                                                  panel_id, db):
    beat_id = db.create_beat(panel_id, action="b")
    db.update_beat(beat_id, prompt="hand-written", prompt_locked=1,
                   prompt_source="user")
    with pytest.raises(ConfirmRequiredError):
        await runner.check_compose_gates(
            storyboard_id, ("beats",), None, None, False
        )


async def test_beats_stage_open_for_plain_beats(runner, storyboard_id,
                                                panel_id, db):
    db.create_beat(panel_id, action="cheap to reroll")
    await runner.check_compose_gates(storyboard_id, ("beats",), None, None, False)
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_storyboard_compose.py -k gate -v` — FAIL (arity + no beats gate).

- [ ] **Step 3: Implement the gate**

`check_compose_gates` signature becomes `(self, storyboard_id, stages, scene_ids, panel_ids, confirm)`. After the shots gate:

```python
if "beats" in stages:
    target_panels = [
        p
        for s in tree["scenes"]
        for p in s["panels"]
        if panel_ids is None or p["id"] in panel_ids
    ]
    dirty = any(
        beat.get("images") or beat.get("prompt_locked")
        for p in target_panels
        for beat in (p.get("beats") or [])
    )
    if dirty:
        exc = ConfirmRequiredError(
            "target shots have beats with generated images or locked "
            "prompts; recomposing destroys them — pass confirm=true"
        )
        exc._compose_stage = "beats"  # type: ignore[attr-defined]
        raise exc
```

Update the two call sites: `_compose_locked` (line 295) and `backend/api/storyboard.py:540` both pass `body.panel_ids`/`panel_ids`.

- [ ] **Step 4: Update `_run_stage`**

1. Shots branch: `validate_shots_response` is called without `roster` and returns a plain list — replace

```python
panels, warnings = await generate_validated(
    f"shots ({scene['name']})",
    lambda raw: story.validate_shots_response(raw, roster),
    ...
)
for w in warnings:
    logger.warning(...)
```

with

```python
panels = await generate_validated(
    f"shots ({scene['name']})",
    story.validate_shots_response,
    ...
)
```

2. Beats branch: `subjects` becomes the full roster and the validator returns warnings:

```python
async def _beats_for(scene: Dict[str, Any], panel: Dict[str, Any]) -> int:
    nonlocal done
    async with sem:
        beats, warnings = await generate_validated(
            f"beats (panel {panel['id']})",
            lambda raw: story.validate_beats_response(raw, roster),
            system_prompt=story.STORY_BEATS_SYSTEM,
            user_prompt=story.build_beats_user_prompt(
                logline, scene, panel, tree["subjects"]
            ),
            grammar=story.BEATS_GRAMMAR,
            temperature=0.6,
            max_tokens=1600,
            timeout=300.0,
        )
    for w in warnings:
        logger.warning("compose beats (panel %s): %s", panel["id"], w)
    story.rescale_beat_durations(beats, float(panel.get("duration_s") or 12.0))
    await asyncio.to_thread(self.db.replace_panel_beats, panel["id"], beats)
    async with lock:
        done += 1
        progress(done, total)
    return len(beats)
```

(The old `subjects = [s for s in tree["subjects"] if s["id"] in panel["subject_ids"]]` line is deleted — `panel["subject_ids"]` no longer exists.)

- [ ] **Step 5: `ComfyClient.submit` gains `beat_id`**

In `metascan/core/comfy_client.py`, find `submit(...)` (grep `panel_id=`): add keyword `beat_id: Optional[int] = None`, thread it into the `generation_jobs` INSERT column list next to `panel_id`, and into `db.create_generation_job` if the insert goes through a DB helper (follow `panel_id`'s exact path — read the code, add the twin column everywhere `panel_id` appears in the submit/insert chain). `get_generation_job`'s `SELECT *` needs no change.

- [ ] **Step 6: Run, migrate compose-test fallout, commit**

Run: `pytest tests/test_storyboard_compose.py tests/test_storyboard_compose_api.py tests/test_comfy* -v`
Migrate existing tests: fake-VLM shot responses lose framing/subject keys; fake beats responses gain them; gate-arity call sites updated.

```bash
git add metascan/core/storyboard_runner.py metascan/core/comfy_client.py tests/
git commit -m "feat(runner): beats compose gate, roster-wide beat casting, beat_id on jobs"
```

---

### Task 6: Runner — synthesize per beat

**Files:**
- Modify: `metascan/core/storyboard_runner.py:853-988`
- Test: `tests/test_storyboard_synthesis.py`

**Interfaces:**
- Consumes: Task 3 `compose_brief(storyboard, scene, panel, beat, subjects)`, Task 2 `update_beat`.
- Produces: `synthesize(storyboard_id, beat_ids: Optional[List[int]] = None, force: bool = False)` (**param rename** `panel_ids` → `beat_ids`); writes `beats.brief/prompt/prompt_source/prompt_locked`; `synthesis_progress` payload gains `beat_id` (keeps `panel_id`); counts unchanged (`synthesized`/`fallback`/`skipped_locked`).

- [ ] **Step 1: Write failing tests**

In `tests/test_storyboard_synthesis.py`, migrate the panel-level tests: wherever a test sets `db.update_panel(pid, prompt_locked=1)` and asserts `skipped_locked`, create a beat and lock it instead; assertions read `db.get_beat(beat_id)["prompt"]`. Add one new test:

```python
async def test_synthesize_targets_beats(runner, db, storyboard_id, panel_id):
    b1 = db.create_beat(panel_id, action="first", subject_ids=[])
    b2 = db.create_beat(panel_id, action="second", subject_ids=[])
    db.update_beat(b2, prompt="locked", prompt_locked=1, prompt_source="user")
    counts = await runner.synthesize(storyboard_id)
    assert counts["skipped_locked"] == 1
    assert db.get_beat(b1)["prompt"]           # written
    assert db.get_beat(b2)["prompt"] == "locked"  # untouched
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_storyboard_synthesis.py -v` — FAIL.

- [ ] **Step 3: Implement `_synthesize_locked` over beats**

The unit of work becomes `(scene, panel, beat)`:

```python
explicit_ids = set(beat_ids) if beat_ids is not None else None
candidates: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
for scene in tree["scenes"]:
    for panel in scene["panels"]:
        for beat in panel.get("beats") or []:
            if explicit_ids is not None and beat["id"] not in explicit_ids:
                continue
            candidates.append((scene, panel, beat))
```

Lock skip reads `beat["prompt_locked"]`, with the same forced-override rule as before, now keyed on beats: `forced_override = force and explicit_ids is not None and beat["id"] in explicit_ids`. `_one(scene, panel, beat)` resolves `subjects` from `beat["subject_ids"]` via `subjects_by_id`, calls `compose_brief(tree, scene, panel, beat, subjects)`, and persists with:

```python
await asyncio.to_thread(
    self.db.update_beat,
    beat["id"],
    brief=brief,
    prompt=prompt,
    prompt_source=source,
    prompt_locked=0,
)
```

The progress emit adds `"beat_id": beat["id"]` alongside the existing `panel_id`. `synthesize()`'s public signature renames `panel_ids` → `beat_ids` (update the docstring). Everything else (VLM fallback, style-block `finalize_prompt`, counts, `_synth_lock`) is untouched.

- [ ] **Step 4: Run, commit**

Run: `pytest tests/test_storyboard_synthesis.py -v` → PASS.

```bash
git add metascan/core/storyboard_runner.py tests/test_storyboard_synthesis.py
git commit -m "feat(runner): synthesize per beat with beat-level lock semantics"
```

---

### Task 7: Runner — generate per beat + beat-keyed ingest

**Files:**
- Modify: `metascan/core/storyboard_runner.py:992-1161` (`generate`), `1498-1573` (`_ingest_outputs`), `1577-1597` (`cancel`)
- Test: `tests/test_storyboard_runner.py`

**Interfaces:**
- Consumes: Task 3 `beat_seed`, Task 2 `count_beat_images`/`create_beat_image`/`list_generation_jobs(beat_ids=)`/`panel_id_for_beat`, Task 5 `ComfyClient.submit(..., beat_id=)`.
- Produces: `generate(storyboard_id, beat_ids: Optional[List[int]] = None, only_failed: bool = False) -> List[int]`; `_ingest_outputs` keys on `job["beat_id"]`, writes `beat_images`, emits `beat_images_changed {storyboard_id, panel_id, beat_id, files}`.
- WS event rename: `panel_images_changed` → `beat_images_changed` (Task 10 frontend consumes).

- [ ] **Step 1: Write failing tests**

Migrate `tests/test_storyboard_runner.py`'s generate/ingest tests to beats (the file has a fake ComfyClient — reuse it). Key new/changed assertions:

```python
async def test_generate_submits_per_beat_with_beat_seed(runner, db, tree_ids):
    # tree_ids: storyboard_id, panel at sort_order 2, two beats sort 0/1,
    # both with prompts written
    jobs = await runner.generate(tree_ids.storyboard_id)
    submitted = runner.comfy.submitted           # fake records kwargs
    assert [s["beat_id"] for s in submitted] == [tree_ids.beat_a, tree_ids.beat_b]
    assert submitted[0]["params"].seed == base + (2 * 100 + 0) * 1000
    assert submitted[1]["params"].seed == base + (2 * 100 + 1) * 1000
    assert str(submitted[1]["output_dir"]).endswith(
        "scene_00/panel_02/beat_01"
    )


async def test_ingest_writes_beat_images_and_emits(runner, db, tree_ids, events):
    payload = {"job_id": tree_ids.job_with_beat, "files": [str(tree_ids.out_png)]}
    await runner._ingest_outputs(payload)
    assert db.count_beat_images(tree_ids.beat_a) == 1
    evt = [e for e in events if e[1] == "beat_images_changed"][-1]
    assert evt[2]["beat_id"] == tree_ids.beat_a
    assert evt[2]["panel_id"] == tree_ids.panel_id
```

(Adapt names to the file's actual fixture vocabulary — read its existing generate/ingest tests first and keep their structure; `runner.comfy.submitted` may need a one-line extension of the fake's `submit` to record `beat_id`.)

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_storyboard_runner.py -v` — FAIL.

- [ ] **Step 3: Rewrite `generate()` over beats**

Target selection:

```python
all_beats: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = [
    (scene, panel, beat)
    for scene in tree["scenes"]
    for panel in scene["panels"]
    for beat in (panel.get("beats") or [])
]
if beat_ids is not None:
    wanted = set(beat_ids)
    all_beats = [(s, p, b) for s, p, b in all_beats if b["id"] in wanted]
targets = [(s, p, b) for s, p, b in all_beats if b.get("prompt")]
```

`only_failed` uses a new one-liner DB helper `latest_jobs_for_beats(beat_ids)` — copy `latest_jobs_for_panels` (database_sqlite.py:2405) swapping `panel_id` → `beat_id` (add it in this task, with a direct test mirroring any existing `latest_jobs_for_panels` test).

Upfront validation loop: `effective_negative` becomes just `tree.get("negative")` (checked once, before the loop); `_primary_subject(beat)` reads `beat.get("subject_ids")`; the `ref`-kind check runs per beat.

Submit loop per `(scene, panel, beat)`:

```python
committed = await asyncio.to_thread(self.db.count_beat_images, beat["id"])
pending_jobs = await asyncio.to_thread(
    self.db.list_generation_jobs,
    states=["queued", "running"],
    beat_ids=[beat["id"]],
    limit=10000,
)
variant_base = committed + tree["batch_size"] * len(pending_jobs)
seed = beat_seed(
    tree["base_seed"], panel["sort_order"], beat["sort_order"], variant_base
)
...
params = GenerationParams(
    positive=beat["prompt"],
    seed=seed,
    ...
    negative=tree.get("negative"),
    ...
)
output_dir = (
    self.output_root
    / slug
    / f"scene_{scene['sort_order']:02d}"
    / f"panel_{panel['sort_order']:02d}"
    / f"beat_{beat['sort_order']:02d}"
)
job_id = await self.comfy.submit(
    preset_id,
    params,
    panel_id=panel["id"],
    beat_id=beat["id"],
    priority=priority,
    output_dir=output_dir,
)
```

`priority` reads `beat_ids is not None and len(beat_ids) == 1`. Update the import at the top of the file: `from metascan.core.storyboard_brief import beat_seed, bucket_dims, storyboard_slug` (drop `panel_seed`).

- [ ] **Step 4: Rewrite `_ingest_outputs` around `beat_id`**

```python
job = await asyncio.to_thread(self.db.get_generation_job, job_id)
if job is None or job.get("beat_id") is None:
    return   # video jobs (panel_id only) are ingested by the videogen path
beat_id = job["beat_id"]
beat = await asyncio.to_thread(self.db.get_beat, beat_id)
if beat is None:
    return
panel_id = beat["panel_id"]
```

Video jobs carry `panel_id` only (`beat_id` NULL) but their rendered clips ingest through this same method (spec §3.5 + "same `_ingest_outputs` path"). So the early-return above is wrong as written for them — implement both branches: `beat_id` present → ingest against that beat; `beat_id` NULL but `panel_id` present → ingest against the panel's **first beat** (`(await asyncio.to_thread(self.db.list_beats, panel_id) or [None])[0]`), and `return` with a `logger.warning("job %s has no beats to ingest into", job_id)` when the panel has no beats. Verify the branch behavior against `tests/test_storyboard_videogen.py`'s ingest expectations and migrate those tests to assert clips land on the first beat.

The insert loop calls `create_beat_image(beat_id, ...)` with `base = count_beat_images(beat_id)`; the emit becomes:

```python
self._emit(
    "storyboard",
    "beat_images_changed",
    {
        "storyboard_id": storyboard_id,
        "panel_id": panel_id,
        "beat_id": beat_id,
        "files": [to_native_path(p) for p in inserted],
    },
)
```

`storyboard_id` still resolves via `storyboard_id_for_panel(panel_id)`. `cancel()` is unchanged (video + still jobs both carry `panel_id`).

- [ ] **Step 5: Run, migrate runner-test fallout, commit**

Run: `pytest tests/test_storyboard_runner.py tests/test_storyboard_videogen.py -v` → PASS.

```bash
git add metascan/core/storyboard_runner.py metascan/core/database_sqlite.py tests/
git commit -m "feat(runner): generate/ingest per beat, beat_seed, beat_images_changed event"
```

---

### Task 8: H3 compile + video — beat-derived subjects, per-beat framing, first-beat anchor

**Files:**
- Modify: `metascan/core/h3_compiler.py:553-612` (`render_detailed_description`), `metascan/core/storyboard_runner.py:53-63` (`_panel_image_by_id`), `532-555` (`_panel_subjects`), `1263-1330` + `1395-1430` (anchor validation/upload in `generate_video`)
- Test: `tests/test_h3_compiler.py`, `tests/test_storyboard_compile.py`, `tests/test_storyboard_videogen.py`

**Interfaces:**
- Consumes: `SHOT_SIZES`/`ANGLES`/`LENSES` phrase maps imported from `metascan.core.storyboard_brief` (they stay there; `h3_compiler` imports them — no relocation).
- Produces: `render_detailed_description` renders a framing sentence per `[Shot n]` when the beat carries any of `shot_size`/`angle`/`lens`; `_panel_subjects` derives from beats; `_beat_image_by_id(beat, image_id)` replaces `_panel_image_by_id`; `keeper` anchor = first beat's keeper.

- [ ] **Step 1: Write failing tests**

`tests/test_h3_compiler.py`:

```python
def test_detailed_description_renders_beat_framing():
    beats = [{"action": "She turns.", "shot_size": "CU", "angle": "low",
              "lens": None, "duration_s": 4.0, "is_cut": 0}]
    # build timeline/speakers with the file's existing helpers
    dd = h3.render_detailed_description("cinematic", beats, timeline, speakers)
    assert "close-up" in dd and "low angle" in dd


def test_detailed_description_no_framing_no_sentence():
    beats = [{"action": "She turns.", "duration_s": 4.0, "is_cut": 0}]
    dd = h3.render_detailed_description("cinematic", beats, timeline, speakers)
    assert "framed as" not in dd
```

`tests/test_storyboard_compile.py` — migrate `_panel_subjects` tests: build a tree where beats carry `subject_ids` and dialog names an off-beat speaker; assert the union.

`tests/test_storyboard_videogen.py` — migrate keeper-anchor tests: the keeper now lives on the panel's first beat (`select_beat_image`); assert validation fails when the first beat has no keeper and passes when it does.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_h3_compiler.py tests/test_storyboard_compile.py -v` — FAIL.

- [ ] **Step 3: Implement**

1. `h3_compiler.py` — add at imports: `from metascan.core.storyboard_brief import ANGLES, LENSES, SHOT_SIZES`. In `render_detailed_description`, after the `action` sentence and before the camera sentence:

```python
framing_bits = [
    SHOT_SIZES.get(beat.get("shot_size") or ""),
    ANGLES.get(beat.get("angle") or ""),
    LENSES.get(beat.get("lens") or ""),
]
framing = ", ".join(b for b in framing_bits if b)
if framing:
    sentences.append(f"The shot is framed as a {framing}.")
```

2. `storyboard_runner.py` — `_panel_subjects` becomes:

```python
def _panel_subjects(
    self, tree: Dict[str, Any], panel: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """The subjects a panel's H3 compile -- and video generation --
    must account for: the union of every beat's ``subject_ids`` and
    every subject a beat's dialog names, in the board's subject
    ``sort_order``. Beats are the single source of casting truth since
    the shot/beat reorg -- computing the union here (compile AND
    upload both call this) makes the document/upload agreement
    structural."""
    beats = panel.get("beats") or []
    beat_subject_ids = {
        sid for beat in beats for sid in (beat.get("subject_ids") or [])
    }
    dialog_subject_ids = {
        d.get("subject_id")
        for beat in beats
        for d in (beat.get("dialog") or [])
        if d.get("subject_id") is not None
    }
    wanted_ids = beat_subject_ids | dialog_subject_ids
    return [s for s in tree["subjects"] if s["id"] in wanted_ids]
```

3. Anchor: replace `_panel_image_by_id` with

```python
def _first_beat_keeper(panel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The keeper image of the shot's first beat (lowest sort_order) --
    the resolution of video_anchor='keeper' since keyframes moved to
    beats. None when there is no beat or no keeper."""
    beats = panel.get("beats") or []
    if not beats:
        return None
    first = beats[0]  # tree orders beats by sort_order, id
    image_id = first.get("selected_image_id")
    if image_id is None:
        return None
    for img in first.get("images") or []:
        if img.get("id") == image_id:
            return img
    return None
```

In `generate_video`'s validation (lines ~1290) and upload (~1402) blocks, replace both `_panel_image_by_id(panel, panel.get("selected_image_id"))` calls (and the `prev_panel` twins) with `_first_beat_keeper(panel)` / `_first_beat_keeper(prev_panel)`; update the error strings to say `"requires the shot's first beat to have a selected keeper image"`.

- [ ] **Step 4: Run, migrate compile/videogen fallout, commit**

Run: `pytest tests/test_h3_compiler.py tests/test_storyboard_compile.py tests/test_storyboard_videogen.py tests/test_storyboard_videogen_db.py -v` → PASS.

```bash
git add metascan/core/h3_compiler.py metascan/core/storyboard_runner.py tests/
git commit -m "feat(h3): beat-derived subjects, per-beat framing, first-beat keeper anchor"
```

---

### Task 9: API routes — beat-level editing surface

**Files:**
- Modify: `backend/api/storyboard.py` (models 121–294, routes 428–881)
- Test: `tests/test_storyboard_api.py`, `tests/test_storyboard_compile_api.py`, `tests/test_storyboard_videogen_api.py`

**Interfaces:**
- Consumes: Tasks 2–8.
- Produces (frontend Task 10 consumes these exact shapes):
  - `StoryboardCreate`/`StoryboardPatch` gain `notes: Optional[str]`.
  - `PanelCreate` = `{action, sort_order=0, duration_s=12.0}`; `PanelPatch` = `{sort_order?, action?, duration_s?, video_prompt?, video_prompt_locked?, video_anchor?}`.
  - `BeatCreate`/`BeatPatch` gain `shot_size`, `angle`, `lens`, `subject_ids`, `prompt`, `prompt_locked`; BeatPatch applies the server-wins lock rule.
  - `POST /api/storyboard/beats/{beat_id}/select` `{image_id | null}` → updated beat dict; `POST /panels/{id}/select` **deleted**.
  - `DELETE /beats/{beat_id}?purge_images=true`.
  - `SynthesizeRequest.beat_ids` / `GenerateRequest.beat_ids` replace `panel_ids` (compile/generate-video/compose keep `panel_ids`).
  - `_PANEL_NOT_NULLABLE = {"sort_order", "action", "duration_s", "video_prompt_locked"}`; `_BEAT_NOT_NULLABLE = {"sort_order", "duration_s", "action", "is_cut", "dialog", "subject_ids", "prompt_locked"}`.

- [ ] **Step 1: Write failing tests**

In `tests/test_storyboard_api.py` (TestClient against isolated temp DB, existing fixture pattern):

```python
def test_beat_patch_server_wins_lock(client, beat_id):
    r = client.patch(f"/api/storyboard/beats/{beat_id}", json={
        "prompt": "hand-written", "prompt_locked": 0,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["prompt_locked"] == 1 and body["prompt_source"] == "user"


def test_beat_select_route(client, beat_id, beat_image_id):
    r = client.post(f"/api/storyboard/beats/{beat_id}/select",
                    json={"image_id": beat_image_id})
    assert r.status_code == 200
    assert r.json()["selected_image_id"] == beat_image_id


def test_panel_patch_rejects_dropped_fields(client, panel_id):
    r = client.patch(f"/api/storyboard/panels/{panel_id}",
                     json={"shot_size": "CU"})
    # Pydantic ignores unknown fields by default -> field silently absent;
    # assert the response carries no shot_size key
    assert "shot_size" not in r.json()


def test_storyboard_patch_notes(client, storyboard_id):
    r = client.patch(f"/api/storyboard/{storyboard_id}", json={"notes": "n"})
    assert r.status_code == 200


def test_delete_beat_purge_flag(client, beat_id):
    r = client.delete(f"/api/storyboard/beats/{beat_id}?purge_images=true")
    assert r.status_code == 200 and r.json() == {"status": "deleted"}
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_storyboard_api.py -v` — FAIL.

- [ ] **Step 3: Implement**

1. Models: apply the Interfaces block above verbatim. `BeatPatch` adds:

```python
shot_size: Optional[str] = None
angle: Optional[str] = None
lens: Optional[str] = None
subject_ids: Optional[List[int]] = None
brief: Optional[str] = None
prompt: Optional[str] = None
prompt_locked: Optional[int] = None
prompt_source: Optional[str] = None
# selected_image_id deliberately NOT exposed: keeper selection toggles
# media.hidden via db.select_beat_image. Use POST /beats/{id}/select.
```

2. `patch_beat` gains the server-wins block (moved verbatim from `patch_panel`):

```python
if body.prompt is not None:
    fields["prompt_locked"] = 1
    fields["prompt_source"] = "user"
```

3. `patch_panel` loses the prompt block (keep the `video_prompt` block and `_VIDEO_ANCHORS` check).
4. Replace `select_panel_image` route:

```python
@router.post("/beats/{beat_id}/select")
async def select_beat_image(beat_id: int, body: SelectRequest) -> Dict[str, Any]:
    svc = _service()
    ok = await svc.select_beat_image(beat_id, body.image_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"No beat {beat_id}, or image {body.image_id} does not "
            "belong to it",
        )
    updated = await svc.get_beat(beat_id)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"No beat {beat_id}")
    return updated
```

5. `delete_beat` route gains `purge_images: bool = False` query param, passed to the service.
6. `create_panel` route: pass only `action`, `sort_order`, `duration_s`.
7. `synthesize_storyboard`: total counts **beats** —

```python
all_beat_ids = [
    b["id"]
    for scene in tree["scenes"]
    for p in scene["panels"]
    for b in (p.get("beats") or [])
]
if body.beat_ids is not None:
    wanted = set(body.beat_ids)
    total = len([bid for bid in all_beat_ids if bid in wanted])
else:
    total = len(all_beat_ids)
task = asyncio.create_task(
    runner.synthesize(storyboard_id, beat_ids=body.beat_ids, force=body.force)
)
```

8. `generate_storyboard`: `runner.generate(storyboard_id, beat_ids=body.beat_ids, only_failed=body.only_failed)`.
9. `compose_storyboard`: `check_compose_gates(storyboard_id, stages, body.scene_ids, body.panel_ids, body.confirm)`.

- [ ] **Step 4: Run, migrate api-test fallout, commit**

Run: `pytest tests/test_storyboard_api.py tests/test_storyboard_compile_api.py tests/test_storyboard_videogen_api.py tests/test_storyboard_compose_api.py tests/test_storyboard_describe_api.py -v` → PASS. Then the full gate:

Run: `make quality test`
Expected: all green (this is the first task after which the whole backend suite must pass).

```bash
git add backend/ tests/
git commit -m "feat(api): beat-level prompt/select/purge routes, thin panel surface"
```

---

### Task 10: Frontend — types, API client, store

**Files:**
- Modify: `frontend/src/types/storyboard.ts`, `frontend/src/api/storyboard.ts`, `frontend/src/stores/storyboard.ts`
- Verify: `cd frontend && npm run build`

**Interfaces:**
- Produces (components in Task 11 consume): `BeatImage` type; `Beat` gains `shot_size/angle/lens/subject_ids/brief/prompt/prompt_locked/prompt_source/selected_image_id/images`; `Panel` loses the dropped fields; `StoryboardTree` gains `notes`; api fns `selectBeatImage(beatId, imageId)`, `deleteBeat(beatId, purgeImages?)`, `synthesizeStoryboard(id, {beat_ids?, force?})`, `generateStoryboard(id, {beat_ids?, only_failed?})`; store `selectImage(beatId, imageId)`, `jobToBeat` map, `beat_images_changed` handler.

- [ ] **Step 1: Types**

In `types/storyboard.ts`: rename `PanelImage` → `BeatImage` (`panel_id` → `beat_id`); move onto `Beat`:

```typescript
export interface Beat {
  id: number
  panel_id: number
  sort_order: number
  duration_s: number
  action: string
  shot_size: string | null
  angle: string | null
  lens: string | null
  subject_ids: number[]
  camera_motion: string | null
  camera_amplitude: string | null
  camera_speed: string | null
  is_cut: 0 | 1
  dialog: DialogLine[]
  sound: string | null
  brief: string | null
  prompt: string | null
  prompt_locked: number
  prompt_source: 'llm' | 'brief' | 'user' | null
  selected_image_id: number | null
  images: BeatImage[]
  created_at: string
  updated_at: string
}
```

`Panel` shrinks to `{id, scene_id, sort_order, action, duration_s, video_prompt, video_prompt_locked, video_prompt_source, video_prompt_warnings, video_anchor, video_compiled_anchor, created_at, updated_at, beats}`. Delete `PanelWithoutImages` (replace its uses with `Panel`; the comment's merge caveat now applies to `Beat.images` — `getBeat`-shaped PATCH responses don't carry `images`, so add the same comment on a `BeatWithoutImages = Omit<Beat, 'images'>` type used as the `patchBeat`/`selectBeatImage` return). `StoryboardSummary` gains `notes: string | null` (column lives on storyboards; summary SELECT returns `*`). `GenerationJob` gains `beat_id: number | null`.

- [ ] **Step 2: API client**

In `api/storyboard.ts`: `patchPanel` body type shrinks to the new `PanelPatch` fields; `patchBeat` body gains the new fields; replace `selectPanelImage` with:

```typescript
export function selectBeatImage(
  beatId: number,
  imageId: number | null,
): Promise<BeatWithoutImages> {
  return post(`/api/storyboard/beats/${beatId}/select`, { image_id: imageId })
}
```

`deleteBeat(beatId: number, purgeImages = false)` appends `?purge_images=true` when set (copy `deletePanel`'s query handling); `synthesizeStoryboard`/`generateStoryboard` request bodies rename `panel_ids` → `beat_ids`. `patchStoryboard` body gains `notes?: string | null`.

- [ ] **Step 3: Store**

In `stores/storyboard.ts`:

1. `jobToPanel` stays (video jobs + progress chips are per panel) and gains a sibling `jobToBeat = ref<Map<number, number>>(new Map())`; `refreshActiveJobs()` fills both from the jobs' `panel_id`/`beat_id` fields, filtering against the loaded tree's panel **and beat** id sets.
2. `selectImage` re-targets beats:

```typescript
async function selectImage(beatId: number, imageId: number | null): Promise<void> {
  const updated = await api.selectBeatImage(beatId, imageId)
  const beat = findBeat(beatId)          // helper: scan tree scenes→panels→beats
  if (beat) Object.assign(beat, updated) // images survives: not on source
}
```

3. WS: the `storyboard` channel handler renames `panel_images_changed` → `beat_images_changed` (payload `{storyboard_id, panel_id, beat_id, files}`) — behavior unchanged (full `refresh()`); `synthesis_progress` may carry `beat_id` (no UI change required this task).
4. Add `findBeat(beatId)` helper and export it if PanelDetail needs it.

- [ ] **Step 4: Build, fix compile errors project-wide, commit**

Run: `cd frontend && npm run build`
Expected: type errors in components (Task 11's files) — fix ONLY imports/types that keep the build broken in store/api/types themselves; component errors that require UI restructuring are Task 11. If the build cannot pass without touching components, do the minimal mechanical rename there (e.g. `panel.images` → keeper lookups) and leave the UI redesign to Task 11.

```bash
git add frontend/src/types frontend/src/api frontend/src/stores
git commit -m "feat(frontend): beat-level types, api client, store correlation"
```

---

### Task 11: Frontend — components

**Files:**
- Modify: `frontend/src/components/storyboard/BeatForm.vue`, `BeatRow.vue`, `BeatsEditor.vue`, `PanelDetail.vue`, `PanelSidePanel.vue`, `PanelGrid.vue`, `StoryboardSettingsDialog.vue`, `SceneStrip.vue` (check for panel framing/keeper uses), `frontend/src/views/StoryboardView.vue` (delete-flow wiring)
- Verify: `cd frontend && npm run build`

**Interfaces:**
- Consumes: Task 10 types/store/api.

- [ ] **Step 1: Inventory the panel-field usages**

Run: `cd frontend && grep -rn "shot_size\|selectPanelImage\|panel.prompt\|prompt_locked\|panel.images\|subject_ids\|\.negative\|\.notes" src/components/storyboard src/views/StoryboardView.vue`
List every hit; each must end up either deleted (panel-level) or moved to the beat editors.

- [ ] **Step 2: Move the editors down a level**

Work component by component; every moved field keeps the **commit-on-change + snapshot + resync** pattern documented in CLAUDE.md (local ref + "last synced" ref, resync watcher on `[id, updated_at]`, overwrite only when local === snapshot):

1. **`PanelDetail.vue`** — remove the framing selects (`shot_size`/`angle`/`lens`), subject picker, notes/negative inputs, prompt textarea + lock toggle, and the image strip/keeper picker from the *panel* pane. Keep: action, duration, video-prompt section, anchor chip. The removed blocks are the donor code for BeatForm — cut, don't rewrite.
2. **`BeatForm.vue`** — paste in the donor blocks, re-bound to the beat: framing selects feed `patchBeat(beat.id, {shot_size})` etc. (options from the existing `SHOT_SIZES`/`ANGLES`/`LENSES` consts in `types/storyboard.ts`); subject picker binds `beat.subject_ids` (multi-select over `tree.subjects`, same widget the panel used); prompt textarea + lock indicator bind `beat.prompt`/`beat.prompt_locked` and PATCH through `patchBeat` (server-wins: after the response, `Object.assign` the beat — lock state comes back corrected).
3. **`BeatRow.vue`** — add a keeper thumbnail + image-count chip (`beat.selected_image_id`, `beat.images.length`) so the collapsed row shows generation state; clicking opens the row's BeatForm.
4. **Image strip / keeper picker** — the strip UI moves from PanelDetail into BeatForm (or a small extracted `BeatImages.vue` if BeatForm grows past ~500 lines); keeper click calls `store.selectImage(beat.id, image.id)`; reroll button calls `generateStoryboard(id, {beat_ids: [beat.id]})`.
5. **`PanelSidePanel.vue` / `PanelGrid.vue` / `SceneStrip.vue`** — anywhere a panel thumbnail resolved from `panel.selected_image_id`/`panel.images`, resolve instead from the first beat's keeper: `panel.beats[0]?.images.find(i => i.id === panel.beats[0]?.selected_image_id)` (extract a `firstBeatKeeper(panel)` helper in the store and use it everywhere).
6. **`StoryboardSettingsDialog.vue`** — add a `notes` textarea bound like the existing `negative` field (PATCH with explicit-null-clears semantics).
7. **Delete flows** — wherever `DeleteImagesDialog` gates panel/scene/storyboard deletes, add the same purge/keep/cancel prompt to beat delete (`deleteBeat(beatId, purge)`) and to the beats-recompose confirm path (the compose call already 409s; surface the `confirm_required` message and re-post with `confirm: true` — follow the existing scenes/shots confirm flow in the compose dialog).
8. **Synthesize/generate buttons** — panel-scoped "Synthesize"/"Generate" buttons now pass the panel's beat ids: `{beat_ids: panel.beats.map(b => b.id)}`; board-scoped buttons pass nothing (all beats).

- [ ] **Step 3: Build + manual smoke**

Run: `cd frontend && npm run build` → clean.
Then launch both servers (`python run_server.py` + `npm run dev`) and walk: create storyboard → compose all stages → open a shot → edit a beat's framing/prompt → generate a beat → pick a keeper → compile → check the anchor chip. Fix what breaks.

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): beat-level editors, keeper strip, notes field"
```

---

### Task 12: Docs + final verification

**Files:**
- Modify: `CLAUDE.md` (storyboard bullets), `docs/api-reference.md`, `docs/architecture.md` (storyboard schema sections)

- [ ] **Step 1: Update CLAUDE.md**

Rewrite the affected bullets — storyboard domain table list (beats now carry framing/prompt/keeper, `beat_images` replaces `panel_images`), the `_release_panels` bullet (now delegates to `_release_beats`), the `prompt_locked` bullet (beat-level), the seeds bullet (`beat_seed` formula), the WS events bullet (`beat_images_changed`), the `_panel_subjects` bullet (beat union), the keeper-selection sentence in the stores bullet (`selectImage(beatId, …)` → `POST /beats/{id}/select`), and the compose-gates description (beats stage now gated). Add the `user_version = 3` migration to the one-shot-migrations bullet.

- [ ] **Step 2: Update docs/api-reference.md and docs/architecture.md**

Mirror the route/schema changes (beat select route, purge flag on beat delete, `beat_ids` request bodies, storyboards.notes).

- [ ] **Step 3: Full gate**

Run: `make quality test` and `cd frontend && npm run build`
Expected: all green (watcher test may flake in full-suite WSL2 runs — re-run it in isolation before treating it as a failure).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/
git commit -m "docs: shot/beat model reorganization"
```
