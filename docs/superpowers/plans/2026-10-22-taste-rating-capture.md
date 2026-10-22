# Taste Learning — Project 1: Rating Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture the owner's taste while they use metascan normally — favorite / pass / reasons / seen / categories in the media viewer, categories on new generations and in bulk via a CLI, generation lineage, and tombstones on delete — so later projects have clean training data.

**Architecture:** New SQLite tables (`media_judgments` current state, `judgment_events` append-only log, `media_tombstones`, `categories`, `media_categories`) behind sync `DatabaseManager` methods; thin services (`asyncio.to_thread`) and `/api/judgments` + `/api/categories` routers; `media.is_favorite` stays the source of truth for favorite. Tombstones are written inside the DB layer at the three choke points that delete media rows. The media viewer gets a side rail, rating keys and a category picker; the T2I/I2V dialogs get a Category picker; `scripts/assign_category.py` bulk-assigns over HTTP.

**Tech Stack:** Python 3.11, FastAPI, SQLite, pytest; Vue 3 `<script setup>`, Pinia, TypeScript (`vue-tsc`).

**Spec:** `docs/superpowers/specs/2026-10-22-taste-learning-design.md` (project 1 section, plus "Weighting" under project 2 for the reconfirm rule).

## Global Constraints

- Python 3.11 only; `black` 25.11.0; `make quality test` must pass before every commit.
- DB access is synchronous in `DatabaseManager`, wrapped with `asyncio.to_thread` in a service. Never call DB methods from async code directly.
- One-shot data migrations gated on `PRAGMA user_version`; this plan adds **`user_version = 5`**.
- Do **not** add columns to the `/api/media` summary SELECT (so the covering indexes `idx_media_summary_added` / `idx_media_summary_modified` stay untouched).
- DELETE endpoints return `{status: "deleted"}` (never 204); a DELETE with a body uses the frontend `del(path, body)` helper.
- Categories: user-created, case-insensitively unique, trimmed, 1–40 chars; an item can have many. Link `source ∈ {viewer, sticky, generation, bulk}`.
- New WS channel `categories`, event `categories_changed` with payload `{}` (clients refetch), broadcast on every category or link mutation. Add it to the channel list in CLAUDE.md.
- Sticky category lives in a Pinia store and is cleared on page reload.
- Reasons, in key order: `1 realism`, `2 composition`, `3 emotion`, `4 light`, `5 concept`, `6 motion`.
- "Seen" = ~1 s on screen for an image (1000 ms), or playback start for a video. Grid scrolling never counts.
- Reasons require a verdict. Favorite and pass are mutually exclusive.
- Existing favorites are migrated with judged/confirmed time = migration time.
- Existing viewer keys unchanged: Esc, ← →, Space, `,` `.`, M, ↑ ↓, H/?, F, Ctrl+D.
- Rating UI is only active when `MediaViewer`'s `allowDestructive` is true (the storyboard picker passes false).
- Never read the owner's prompts; all test data is synthetic.

**Spec correction (planner's call):** the spec says "`H` hides the whole overlay as today" — `H` actually toggles the shortcut help. The rail is always visible when `allowDestructive`; no hide key is added. Review mode (`R`) and the score/batch-position display belong to project 2 and are **not** built here.

## Review Focus

1. Every delete path (viewer delete, T2I tile delete, I2V delete, storyboard purge, scan stale-file removal) writes **exactly one** tombstone for a judged or generated item, and none for an untouched one — tests in Task 4.
2. The legacy `PATCH /api/media {is_favorite:false}` on a *passed* item must leave the pass intact; `{is_favorite:true}` on a passed item flips it to favorite and clears pass reasons — tests in Task 2.
3. A rescan (`save_media` upsert of an existing path) must not wipe judgments — test in Task 1.
4. Viewing a judged item repeatedly reconfirms at most once per 24 h, so the event log doesn't flood — test in Task 2.
5. Fast key presses while navigating: a response for item N must never paint onto item N+1 — manual check in Task 8 (no frontend test runner exists).

Also pinned (not in the top five but cheap): the CLI's folder match must not treat `/lib/sci` as a prefix of `/lib/scifi`, and must match Windows paths case-insensitively — tests in Task 7. Deleting a category removes its links but leaves judgments alone — test in Task 5.

---

### Task 1: Schema, lineage columns, migration v5

**Files:**
- Create: `metascan/core/judgments.py`
- Modify: `metascan/core/database_sqlite.py` (table creation near the `t2i_images` block ~line 1197; `_idempotent_add_column` calls; migration block ~line 1582)
- Modify: `.gitignore` (add `.superpowers/`)
- Test: `tests/test_judgments_db.py`

**Interfaces:**
- Produces (`metascan/core/judgments.py`):
  - `REASONS: Tuple[str, ...] = ("realism", "composition", "emotion", "light", "concept", "motion")`
  - `Verdict = Literal["favorite", "pass"]`
  - `RECONFIRM_MIN_INTERVAL_S: int = 86_400`
  - `SEEN_DWELL_MS: int = 1000` (documented constant mirrored in the frontend)
  - `WRITER_BASE = "base"`, `WRITER_FALLBACK = "fallback"`, `WRITER_MANUAL = "manual"`
  - `CATEGORY_SOURCES: Tuple[str, ...] = ("viewer", "sticky", "generation", "bulk")`, `CATEGORY_NAME_MAX = 40`
  - `@dataclass CategoryRef(id: int, name: str)`
  - `@dataclass JudgmentState(file_path: str, verdict: Optional[Verdict], reasons: List[str], seen_at: Optional[str], confirmed_at: Optional[str], is_favorite: bool, categories: List[CategoryRef])` with `to_dict() -> Dict[str, Any]` (`categories` as `[{id, name}]`, sorted by name)
- Produces (tables):
  - `media_judgments(file_path TEXT PRIMARY KEY REFERENCES media(file_path) ON DELETE CASCADE, passed INTEGER NOT NULL DEFAULT 0, reasons TEXT NOT NULL DEFAULT '[]', seen_at TEXT, judged_at TEXT, confirmed_at TEXT)`
  - `judgment_events(id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT NOT NULL, event TEXT NOT NULL, reason TEXT, category TEXT, score_shown REAL, created_at TEXT NOT NULL)` — no FK (must outlive the media row); index on `file_path`. `event ∈ {seen, reconfirm, favorite, unfavorite, pass, unpass, reason_on, reason_off, category_on, category_off}`; `category` holds the category **name** for the last two.
  - `media_tombstones(id INTEGER PRIMARY KEY AUTOINCREMENT, file_path TEXT NOT NULL, deleted_at TEXT NOT NULL, is_favorite INTEGER NOT NULL, passed INTEGER NOT NULL, reasons TEXT NOT NULL, categories TEXT NOT NULL DEFAULT '[]', seen_at TEXT, confirmed_at TEXT, embedding BLOB, embedding_model TEXT, lineage TEXT)` — `categories` is a JSON array of names.
  - `categories(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, created_at TEXT NOT NULL)`
  - `media_categories(file_path TEXT NOT NULL REFERENCES media(file_path) ON DELETE CASCADE, category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE, source TEXT NOT NULL, assigned_at TEXT NOT NULL, PRIMARY KEY (file_path, category_id))`, index on `category_id`.
  - `t2i_images` and `i2v_videos` each gain nullable `writer_id TEXT`, `variant_group TEXT`, `variant_index INTEGER`.
- Timestamps: ISO-8601 UTC strings (`datetime.now(timezone.utc).isoformat()`), the shape CLAUDE.md says both sides already tolerate.

- [ ] **Step 1: Write the failing tests** in `tests/test_judgments_db.py` (fixture and `_media()` helper copied from `tests/test_media_hidden.py`):

```python
def test_new_tables_exist(db):
    with db._get_connection() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"media_judgments", "judgment_events", "media_tombstones", "categories", "media_categories"} <= names

def test_lineage_columns_added(db):
    with db._get_connection() as conn:
        for table in ("t2i_images", "i2v_videos"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert {"writer_id", "variant_group", "variant_index"} <= cols

def test_user_version_is_5(db):
    with db._get_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5

def test_migration_seeds_existing_favorites(tmp_path):
    # create a DB, add a favorite, force user_version back to 4, reopen
    mgr = DatabaseManager(tmp_path / "db")
    mgr.save_media(_media("/pics/fav.png")); mgr.set_favorite(Path("/pics/fav.png"), True)
    with mgr._get_connection() as conn:
        conn.execute("DELETE FROM media_judgments"); conn.execute("DELETE FROM judgment_events")
        conn.execute("PRAGMA user_version = 4"); conn.commit()
    mgr.close()
    mgr = DatabaseManager(tmp_path / "db")
    with mgr._get_connection() as conn:
        row = conn.execute("SELECT * FROM media_judgments WHERE file_path = '/pics/fav.png'").fetchone()
        assert row["confirmed_at"] is not None and row["judged_at"] == row["confirmed_at"]
        assert conn.execute("SELECT event FROM judgment_events").fetchall()[0]["event"] == "favorite"
    mgr.close()

def test_judgment_survives_rescan_upsert(db):
    db.save_media(_media("/pics/a.png"))
    with db._get_connection() as conn:
        conn.execute("INSERT INTO media_judgments (file_path, passed) VALUES ('/pics/a.png', 1)"); conn.commit()
    db.save_media(_media("/pics/a.png"))
    with db._get_connection() as conn:
        assert conn.execute("SELECT passed FROM media_judgments").fetchone()["passed"] == 1
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_judgments_db.py -v`
Expected: FAIL (`media_judgments` missing / `user_version` 4).

- [ ] **Step 3: Implement** `metascan/core/judgments.py` (constants + dataclass) and the DDL in `_init_database`: three `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS idx_judgment_events_path ON judgment_events(file_path)`, six `_idempotent_add_column` calls, and an `if user_version < 5:` block that inserts a `media_judgments` row (`seen_at = judged_at = confirmed_at = now`) plus a `favorite` event for every `media.is_favorite = 1` row (`ON CONFLICT(file_path) DO NOTHING`), then `PRAGMA user_version = 5`. Add `.superpowers/` to `.gitignore`.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `pytest tests/test_judgments_db.py -v && make quality test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/judgments.py metascan/core/database_sqlite.py tests/test_judgments_db.py .gitignore
git commit -m "feat(taste): judgment, event and tombstone tables; lineage columns; migration v5"
```

---

### Task 2: Judgment operations (verdict, reasons, seen) and legacy favorite path

**Files:**
- Modify: `metascan/core/database_sqlite.py` (new methods; rewrite `set_favorite` ~line 4717 and `toggle_favorite` ~line 4688)
- Test: `tests/test_judgments_db.py`

**Interfaces:**
- Consumes: Task 1 constants and tables.
- Produces (`DatabaseManager`, all take native or posix path strings and key by `to_posix_path`; all return `None` when the media row does not exist):
  - `get_judgment(file_path: str) -> Optional[JudgmentState]` — no row in `media_judgments` ⇒ verdict from `is_favorite`, empty reasons, null times; `categories` always read from `media_categories`.
  - `set_verdict(file_path: str, verdict: Optional[Verdict], score_shown: Optional[float] = None, now: Optional[datetime] = None, sticky_category_id: Optional[int] = None) -> Optional[JudgmentState]` — when `verdict` is not `None` and `sticky_category_id` is given, also link that category with source `sticky` (logging `category_on` only if the link is new); an unknown category id raises `ValueError("unknown_category")`.
  - `set_reason(file_path: str, reason: str, on: bool, score_shown: Optional[float] = None, now: Optional[datetime] = None) -> Optional[JudgmentState]` — raises `ValueError("unknown_reason")` for a reason not in `REASONS`, `ValueError("no_verdict")` when the item has no verdict.
  - `mark_seen(file_path: str, now: Optional[datetime] = None) -> Optional[JudgmentState]`
  - `list_judgment_events(file_path: Optional[str] = None) -> List[Dict[str, Any]]` ordered by `id`.
  - `set_favorite` / `toggle_favorite` keep their signatures and return values.

Rules (the implementer cannot infer these):
- `set_verdict("favorite")`: `is_favorite=1`, `passed=0`; if the previous verdict was `pass`, clear reasons and log `unpass` before `favorite`. `"pass"` mirrors it (`unfavorite` then `pass`). `None`: clear both flags and reasons, log `unfavorite`/`unpass` for whichever was set. Setting the **same** verdict again logs nothing and only refreshes `confirmed_at`. Every verdict change sets `judged_at = confirmed_at = now` and `seen_at = COALESCE(seen_at, now)`. `score_shown` is written on every event the call logs.
- `set_reason(on=True)` on an already-on reason is a no-op (no event); same for off.
- `mark_seen`: first time ⇒ set `seen_at`, log `seen`. Otherwise, if the item has a verdict and `confirmed_at` is null or older than `RECONFIRM_MIN_INTERVAL_S` ⇒ set `confirmed_at = now`, log `reconfirm`. Otherwise no write.
- Legacy: `set_favorite(p, True)` ≡ `set_verdict(p, "favorite")`; `set_favorite(p, False)` ≡ `set_verdict(p, None)` **only if currently favorite**, else no change (keeps a pass). `toggle_favorite` flips between favorite and none, and turns a passed item into a favorite.
- `self.lock` is a plain `threading.Lock` — put the logic in a private `_apply_verdict(conn, posix_path, verdict, score_shown, now)` used by all public methods so nothing re-acquires the lock.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_judgments_db.py`; `T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)`):

```python
def _events(db, p): return [e["event"] for e in db.list_judgment_events(p)]

def test_favorite_then_pass_is_exclusive_and_clears_reasons(db):
    db.save_media(_media("/p/a.png"))
    db.set_verdict("/p/a.png", "favorite"); db.set_reason("/p/a.png", "realism", True)
    s = db.set_verdict("/p/a.png", "pass")
    assert (s.verdict, s.is_favorite, s.reasons) == ("pass", False, [])
    assert _events(db, "/p/a.png") == ["favorite", "reason_on", "unfavorite", "pass"]

def test_reason_requires_verdict_and_known_name(db):
    db.save_media(_media("/p/a.png"))
    with pytest.raises(ValueError, match="no_verdict"): db.set_reason("/p/a.png", "light", True)
    db.set_verdict("/p/a.png", "pass")
    with pytest.raises(ValueError, match="unknown_reason"): db.set_reason("/p/a.png", "vibes", True)

def test_reasons_keep_canonical_order(db):
    db.save_media(_media("/p/a.png")); db.set_verdict("/p/a.png", "favorite")
    db.set_reason("/p/a.png", "motion", True); db.set_reason("/p/a.png", "realism", True)
    assert db.get_judgment("/p/a.png").reasons == ["realism", "motion"]

def test_legacy_unfavorite_keeps_pass(db):
    db.save_media(_media("/p/a.png")); db.set_verdict("/p/a.png", "pass")
    db.set_favorite(Path("/p/a.png"), False)
    assert db.get_judgment("/p/a.png").verdict == "pass"

def test_legacy_favorite_on_passed_item_flips(db):
    db.save_media(_media("/p/a.png")); db.set_verdict("/p/a.png", "pass"); db.set_reason("/p/a.png", "light", True)
    assert db.toggle_favorite(Path("/p/a.png")) is True
    s = db.get_judgment("/p/a.png"); assert (s.verdict, s.reasons) == ("favorite", [])

def test_seen_then_reconfirm_throttled(db):
    db.save_media(_media("/p/a.png"))
    db.mark_seen("/p/a.png", now=T0)
    db.set_verdict("/p/a.png", "favorite", now=T0)
    db.mark_seen("/p/a.png", now=T0 + timedelta(hours=1))      # within 24 h: no event
    db.mark_seen("/p/a.png", now=T0 + timedelta(hours=25))     # reconfirm
    assert _events(db, "/p/a.png") == ["seen", "favorite", "reconfirm"]
    assert db.get_judgment("/p/a.png").confirmed_at.startswith("2026-01-02T01")

def test_unjudged_item_seen_twice_logs_once(db):
    db.save_media(_media("/p/a.png"))
    db.mark_seen("/p/a.png", now=T0); db.mark_seen("/p/a.png", now=T0 + timedelta(days=3))
    assert _events(db, "/p/a.png") == ["seen"]

def test_score_shown_recorded(db):
    db.save_media(_media("/p/a.png")); db.set_verdict("/p/a.png", "favorite", score_shown=0.82)
    assert db.list_judgment_events("/p/a.png")[0]["score_shown"] == pytest.approx(0.82)

def test_missing_media_returns_none(db):
    assert db.set_verdict("/nope.png", "favorite") is None and db.mark_seen("/nope.png") is None

def test_sticky_category_added_on_verdict_only(db):
    db.save_media(_media("/p/a.png")); cid = db.create_category("Sci-Fi").id
    db.mark_seen("/p/a.png")
    assert db.get_judgment("/p/a.png").categories == []
    s = db.set_verdict("/p/a.png", "pass", sticky_category_id=cid)
    assert [c.name for c in s.categories] == ["Sci-Fi"]
    assert _events(db, "/p/a.png")[-1] == "category_on"

def test_path_with_spaces(db):
    db.save_media(_media("/p/my pics/a b.png"))
    assert db.set_verdict("/p/my pics/a b.png", "favorite").is_favorite is True
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_judgments_db.py -v`
Expected: FAIL (`AttributeError: ... has no attribute 'set_verdict'`).

- [ ] **Step 3: Implement** the methods and rules above in `database_sqlite.py`; reasons are stored as a JSON array sorted by `REASONS.index`. `test_sticky_category_added_on_verdict_only` needs `create_category` — implement the minimal `create_category` from Task 5's interface here (Task 5 adds its validation and the rest).

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `pytest tests/test_judgments_db.py tests/test_media_hidden.py -v && make quality test`
Expected: all PASS (existing favorite tests still green).

- [ ] **Step 5: Commit**

```bash
git add metascan/core/database_sqlite.py tests/test_judgments_db.py
git commit -m "feat(taste): verdict/reason/seen operations with event log; legacy favorite routes through them"
```

---

### Task 3: Generation lineage (writer id)

**Files:**
- Modify: `metascan/core/database_sqlite.py` — `create_t2i_image` (~3800), `create_i2v_video` (~3642)
- Modify: `metascan/core/t2i_runner.py` — `_Step` (~233), `_random_step` (~874), the fallback branches (~549, ~824), the ingest call at ~1393
- Test: `tests/test_judgments_lineage.py` (DB part) and an added case in the existing T2I runner tests (find the file that covers `_random_step` with `grep -l "_random_step\|random batch" tests/`)

**Interfaces:**
- Produces: `create_t2i_image(..., writer_id: Optional[str] = None, variant_group: Optional[str] = None, variant_index: Optional[int] = None) -> int`; same three keyword args on `create_i2v_video`. `_Step.writer_id: Optional[str] = None`.
- Values in this project: a drawn Random step whose prompt the VLM wrote ⇒ `WRITER_BASE`; a step that used `fallback_prompt` ⇒ `WRITER_FALLBACK`; a supplied step (Manual, or Random batch 1 from the boxes) ⇒ `None` (unknown until project 3 captures drafts). I2V passes nothing yet. `variant_group`/`variant_index` stay null until project 3.

- [ ] **Step 1: Write the failing tests**

```python
def test_create_t2i_image_stores_lineage(db):
    db.save_media(_media("/p/g.png"))
    i = db.create_t2i_image(file_path="/p/g.png", writer_id="base", variant_group="vg1", variant_index=2)
    with db._get_connection() as conn:
        r = conn.execute("SELECT writer_id, variant_group, variant_index FROM t2i_images WHERE id=?", (i,)).fetchone()
    assert tuple(r) == ("base", "vg1", 2)
```

Runner case: a Random batch with the fake llama server writing the prompt ⇒ the ingested `t2i_images.writer_id == "base"`; with the VLM marked not installed ⇒ `"fallback"`; a supplied first step ⇒ `None`.

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_judgments_lineage.py -v` (plus the runner test file)
Expected: FAIL (`unexpected keyword argument 'writer_id'`).

- [ ] **Step 3: Implement** the keyword args (INSERT gains three columns) and thread `_Step.writer_id` into the ingest call.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `make quality test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/database_sqlite.py metascan/core/t2i_runner.py tests/
git commit -m "feat(taste): record which writer produced each T2I prompt"
```

---

### Task 4: Tombstones on every delete path

**Files:**
- Modify: `metascan/core/database_sqlite.py` — `delete_media` (~4241), `delete_media_batch` (~4257), `_purge_media_rows` (~2953), `delete_t2i_image` (~3970), `delete_i2v_video` (~3782)
- Test: `tests/test_tombstones_db.py`

**Interfaces:**
- Consumes: Task 1–2 tables/methods; Task 3's `writer_id` keyword on `create_t2i_image`.
- Produces:
  - `DatabaseManager.set_embedding_lookup(fn: Optional[Callable[[str], Optional[Tuple[np.ndarray, str]]]]) -> None` — `fn(native_path)` returns `(vector, model_key)` or `None`. Default: no lookup.
  - `DatabaseManager.list_tombstones() -> List[Dict[str, Any]]` (`embedding` returned as `bytes`, `lineage` parsed JSON).
  - private `_write_tombstones(conn, posix_paths: List[str], now: str) -> None`.

Rules:
- Write a tombstone for a path **only if** it has a `media_judgments` row, a `media_categories` row, **or** a `t2i_images`/`i2v_videos` row (an `i2v_videos` match is on `file_path`, not `source_path`). Copy the judgment fields (or the `is_favorite` flag with `passed=0`, `reasons='[]'` when there's no judgment row). `lineage` = `{"t2i": {...}}` or `{"i2v": {...}}`: the row as a dict **minus `form_state`**; `null` when neither row exists.
- `categories`: JSON array of the item's category names (sorted), read before the cascade removes the links.
- Embedding: call the lookup with the native path; store `vector.astype(np.float32).tobytes()` and `model_key`; any exception or `None` ⇒ both columns null (never block a delete).
- Call `_write_tombstones` in the same transaction, **before** the `DELETE FROM media`: in `delete_media`, `delete_media_batch`, and in `_purge_media_rows` for only the paths it actually deletes (not the released ones).
- In `delete_t2i_image` and `delete_i2v_video`, call `_purge_media_rows` **before** deleting the `t2i_images`/`i2v_videos` row, so the tombstone can still read the lineage (neither table has an FK, so the order is free). Exactly one tombstone per delete.

- [ ] **Step 1: Write the failing tests** in `tests/test_tombstones_db.py` (same fixture/helper; create T2I/I2V rows with `db.create_t2i_image(...)` / `db.create_i2v_video(...)` using synthetic values):

```python
def test_untouched_item_leaves_no_tombstone(db):
    db.save_media(_media("/p/a.png")); db.delete_media(Path("/p/a.png"))
    assert db.list_tombstones() == []

def test_judged_item_tombstone_has_judgment_and_embedding(db):
    db.save_media(_media("/p/a.png")); db.set_verdict("/p/a.png", "pass"); db.set_reason("/p/a.png", "composition", True)
    vec = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (vec, "clip-medium"))
    db.delete_media(Path("/p/a.png"))
    [t] = db.list_tombstones()
    assert (t["passed"], json.loads(t["reasons"]), t["embedding_model"]) == (1, ["composition"], "clip-medium")
    assert np.array_equal(np.frombuffer(t["embedding"], dtype=np.float32), vec)
    assert db.list_judgment_events("/p/a.png")  # event log survives the delete

def test_lookup_failure_does_not_block_delete(db):
    db.save_media(_media("/p/a.png")); db.set_verdict("/p/a.png", "favorite")
    db.set_embedding_lookup(lambda p: (_ for _ in ()).throw(RuntimeError("boom")))
    assert db.delete_media(Path("/p/a.png")) is True
    assert db.list_tombstones()[0]["embedding"] is None

def test_t2i_delete_writes_one_tombstone_with_lineage(db):
    db.save_media(_media("/p/gen.png"))
    image_id = db.create_t2i_image(file_path="/p/gen.png", batch_id="b1", model="krea2",
                                   prompt_used="synthetic prompt", writer_id="base", form_state={"x": 1})
    db.delete_t2i_image(image_id)
    [t] = db.list_tombstones()
    assert t["lineage"]["t2i"]["writer_id"] == "base" and "form_state" not in t["lineage"]["t2i"]

def test_i2v_delete_writes_one_tombstone(db): ...        # same shape via create_i2v_video / delete_i2v_video
def test_batch_delete_tombstones_only_judged(db): ...    # 3 items, 1 judged -> 1 tombstone via delete_media_batch
def test_storyboard_purge_released_path_has_no_tombstone(db): ...  # path still referenced by a beat_images row is released, not tombstoned

def test_categorized_only_item_tombstoned_with_names(db):
    db.save_media(_media("/p/a.png")); cid = db.create_category("Anime").id
    db.add_media_categories(["/p/a.png"], cid, "bulk")
    db.delete_media(Path("/p/a.png"))
    assert json.loads(db.list_tombstones()[0]["categories"]) == ["Anime"]
```

(The `...` tests follow the same pattern; each asserts the exact tombstone count.)

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_tombstones_db.py -v`
Expected: FAIL (`set_embedding_lookup` missing).

- [ ] **Step 3: Implement** the rules above.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `pytest tests/test_tombstones_db.py tests/test_i2v_db.py tests/test_storyboard_db.py -v && make quality test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/database_sqlite.py tests/test_tombstones_db.py
git commit -m "feat(taste): tombstone judged and generated media on every delete path"
```

> Task 4's last test uses `add_media_categories` from Task 5. Mark it `@pytest.mark.skip(reason="Task 5")` here and unskip it in Task 5.

---

### Task 5: Category operations

**Files:**
- Modify: `metascan/core/database_sqlite.py`
- Test: `tests/test_categories_db.py`

**Interfaces:**
- Consumes: Task 1 tables, `CategoryRef`, `CATEGORY_SOURCES`, `CATEGORY_NAME_MAX`.
- Produces (`DatabaseManager`):
  - `create_category(name: str) -> CategoryRef` — trims; empty or longer than 40 ⇒ `ValueError("invalid_name")`; case-insensitive duplicate ⇒ `ValueError("duplicate_name")`.
  - `list_categories() -> List[Dict[str, Any]]` — `{id, name, item_count, created_at}`, sorted by name case-insensitively.
  - `rename_category(category_id: int, name: str) -> Optional[CategoryRef]` — same validation; `None` if the id is unknown.
  - `delete_category(category_id: int) -> bool` — logs `category_off` (with the name) for every linked item, then deletes (links cascade).
  - `add_media_categories(file_paths: List[str], category_id: int, source: str) -> int` — returns links newly created; skips paths with no media row and links that already exist; logs `category_on` per new link; unknown category ⇒ `ValueError("unknown_category")`; source not in `CATEGORY_SOURCES` ⇒ `ValueError("invalid_source")`.
  - `remove_media_categories(file_paths: List[str], category_id: int) -> int` — returns links removed; logs `category_off` per removed link.
  - `get_media_categories(file_path: str) -> List[CategoryRef]` (used by `get_judgment` and tombstones).

- [ ] **Step 1: Write the failing tests** in `tests/test_categories_db.py`:

```python
def test_create_trims_and_rejects_case_duplicate(db):
    assert db.create_category("  Sci-Fi ").name == "Sci-Fi"
    with pytest.raises(ValueError, match="duplicate_name"): db.create_category("sci-fi")
    with pytest.raises(ValueError, match="invalid_name"): db.create_category("   ")
    with pytest.raises(ValueError, match="invalid_name"): db.create_category("x" * 41)

def test_add_is_idempotent_and_skips_unknown_media(db):
    db.save_media(_media("/p/a.png")); cid = db.create_category("Anime").id
    assert db.add_media_categories(["/p/a.png", "/p/missing.png"], cid, "bulk") == 1
    assert db.add_media_categories(["/p/a.png"], cid, "viewer") == 0
    assert db.list_categories()[0]["item_count"] == 1

def test_remove_logs_event(db):
    db.save_media(_media("/p/a.png")); cid = db.create_category("Anime").id
    db.add_media_categories(["/p/a.png"], cid, "viewer")
    assert db.remove_media_categories(["/p/a.png"], cid) == 1
    assert [e["event"] for e in db.list_judgment_events("/p/a.png")] == ["category_on", "category_off"]

def test_delete_category_keeps_judgments(db):
    db.save_media(_media("/p/a.png")); cid = db.create_category("Anime").id
    db.set_verdict("/p/a.png", "favorite", sticky_category_id=cid)
    assert db.delete_category(cid) is True
    s = db.get_judgment("/p/a.png")
    assert (s.verdict, s.categories) == ("favorite", [])

def test_invalid_source_and_unknown_category(db):
    db.save_media(_media("/p/a.png")); cid = db.create_category("Anime").id
    with pytest.raises(ValueError, match="invalid_source"): db.add_media_categories(["/p/a.png"], cid, "magic")
    with pytest.raises(ValueError, match="unknown_category"): db.add_media_categories(["/p/a.png"], 999, "bulk")

def test_categories_cascade_on_media_delete(db):
    db.save_media(_media("/p/a.png")); cid = db.create_category("Anime").id
    db.add_media_categories(["/p/a.png"], cid, "bulk"); db.delete_media(Path("/p/a.png"))
    assert db.list_categories()[0]["item_count"] == 0
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_categories_db.py -v`
Expected: FAIL (`add_media_categories` missing).

- [ ] **Step 3: Implement** the methods; unskip Task 4's categorized-tombstone test.

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `make quality test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add metascan/core/database_sqlite.py tests/test_categories_db.py tests/test_tombstones_db.py
git commit -m "feat(taste): user categories with item links and event logging"
```

---

### Task 6: Judgments and categories API, embedding lookup wiring, docs

**Files:**
- Create: `backend/services/judgments_service.py`, `backend/api/judgments.py`, `backend/services/categories_service.py`, `backend/api/categories.py`
- Modify: `backend/main.py` (register both routers; in `lifespan`, install the embedding lookup)
- Modify: `CLAUDE.md` (one bullet under Key Technical Decisions; add `categories` to the WS channel list)
- Test: `tests/test_judgments_api.py`, `tests/test_categories_api.py` (pattern: `tests/test_folders_api.py`)

**Interfaces:**
- Consumes: Task 2/4/5 DB methods.
- Produces:
  - `JudgmentsService(db)` with `async get(path)`, `async mark_seen(path)`, `async set_verdict(path, verdict, score_shown)`, `async set_reason(path, reason, on, score_shown)` — each `asyncio.to_thread`.
  - `faiss_embedding_lookup(native_path: str) -> Optional[Tuple[np.ndarray, str]]` in `judgments_service.py`: uses `backend.api.similarity._get_faiss_manager()`; returns `None` if the index isn't loaded; tries the native path then `to_posix_path(native_path)` as the key; model key from the index meta written by `FaissIndexManager.create(embedding_dim, model_key)`.
  - Routes (prefix `/api`):
    - `GET /judgments/{file_path:path}` → `JudgmentState.to_dict()`; 404 `Media not found`.
    - `POST /judgments/seen` body `{"file_path": str}` → state; 404 as above.
    - `PATCH /judgments/{file_path:path}` body: either `{"verdict": "favorite"|"pass"|null, "sticky_category_id"?: int}` or `{"reason": str, "on": bool}`, plus optional `"score_shown": float`. Both or neither of `verdict`/`reason` ⇒ 422. `ValueError("unknown_reason")` ⇒ 422 `{code:"unknown_reason"}`; `ValueError("no_verdict")` ⇒ 409 `{code:"no_verdict"}`; `ValueError("unknown_category")` ⇒ 404 `{code:"unknown_category"}`. A verdict that added the sticky link broadcasts `categories_changed`.
  - Response shape (frontend relies on it): `{file_path, verdict, reasons, seen_at, confirmed_at, is_favorite, categories: [{id, name}]}`.
  - `CategoriesService(db)` async wrappers for every Task 5 method.
  - Category routes (prefix `/api`), each mutation broadcasting `ws_manager.broadcast_sync("categories", "categories_changed", {})`:
    - `GET /categories` → `list_categories()`.
    - `POST /categories` `{name}` → `{id, name}`; `invalid_name` ⇒ 422, `duplicate_name` ⇒ 409 (`detail.code`).
    - `PATCH /categories/{id}` `{name}` → `{id, name}`; unknown ⇒ 404; same validation codes.
    - `DELETE /categories/{id}` → `{status: "deleted"}`; unknown ⇒ 404.
    - `POST /categories/{id}/items` `{file_paths: [str], source: "viewer"|"bulk"}` → `{added: int}`; any other source ⇒ 422 (`generation` and `sticky` are server-internal); unknown id ⇒ 404.
    - `DELETE /categories/{id}/items` body `{file_paths: [str]}` → `{status: "deleted", removed: int}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_judgments_api.py`:

```python
def test_patch_verdict_roundtrip(client, seeded):        # seeded media "/p/a.png"
    r = client.patch("/api/judgments//p/a.png", json={"verdict": "pass"})
    assert r.status_code == 200 and r.json()["verdict"] == "pass" and r.json()["is_favorite"] is False

def test_reason_without_verdict_is_409(client, seeded):
    r = client.patch("/api/judgments//p/a.png", json={"reason": "light", "on": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_verdict"

def test_both_verdict_and_reason_is_422(client, seeded):
    assert client.patch("/api/judgments//p/a.png", json={"verdict": "pass", "reason": "light", "on": True}).status_code == 422

def test_seen_and_get(client, seeded):
    assert client.post("/api/judgments/seen", json={"file_path": "/p/a.png"}).json()["seen_at"]
    assert client.get("/api/judgments//p/a.png").json()["seen_at"]

def test_unknown_media_404(client):
    assert client.get("/api/judgments//nope.png").status_code == 404

def test_sticky_category_via_verdict(client, seeded):
    cid = client.post("/api/categories", json={"name": "Anime"}).json()["id"]
    r = client.patch("/api/judgments//p/a.png", json={"verdict": "favorite", "sticky_category_id": cid})
    assert r.json()["categories"] == [{"id": cid, "name": "Anime"}]

def test_legacy_media_patch_still_works(client, seeded):
    client.patch("/api/judgments//p/a.png", json={"verdict": "pass"})
    client.patch("/api/media//p/a.png", json={"is_favorite": False})
    assert client.get("/api/judgments//p/a.png").json()["verdict"] == "pass"
```

And in `tests/test_categories_api.py`:

```python
def test_category_crud_and_items(client, seeded):
    cid = client.post("/api/categories", json={"name": "Sci-Fi"}).json()["id"]
    assert client.post("/api/categories", json={"name": "sci-fi"}).status_code == 409
    assert client.post(f"/api/categories/{cid}/items", json={"file_paths": ["/p/a.png"], "source": "bulk"}).json() == {"added": 1}
    assert client.get("/api/categories").json()[0]["item_count"] == 1
    r = client.request("DELETE", f"/api/categories/{cid}/items", json={"file_paths": ["/p/a.png"]})
    assert r.json() == {"status": "deleted", "removed": 1}
    assert client.delete(f"/api/categories/{cid}").json() == {"status": "deleted"}

def test_internal_sources_rejected(client, seeded):
    cid = client.post("/api/categories", json={"name": "A"}).json()["id"]
    assert client.post(f"/api/categories/{cid}/items", json={"file_paths": ["/p/a.png"], "source": "generation"}).status_code == 422

def test_mutation_broadcasts(client, seeded, monkeypatch):
    sent = []
    monkeypatch.setattr("backend.api.categories.ws_manager.broadcast_sync", lambda *a: sent.append(a))
    client.post("/api/categories", json={"name": "A"})
    assert sent == [("categories", "categories_changed", {})]
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_judgments_api.py tests/test_categories_api.py -v`
Expected: FAIL (404 for the unregistered routes).

- [ ] **Step 3: Implement** the service, router, registration and lifespan install (`db.set_embedding_lookup(faiss_embedding_lookup)` after the DB is available; wrap in try/except like the other lifespan steps). Add a CLAUDE.md bullet: judgments live in `media_judgments` + append-only `judgment_events`, `media.is_favorite` stays authoritative for favorite, and every media-row delete writes a `media_tombstones` row first (choke points: `delete_media`, `delete_media_batch`, `_purge_media_rows`; T2I/I2V deletes purge before dropping their lineage row).

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `make quality test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/ tests/test_judgments_api.py tests/test_categories_api.py CLAUDE.md
git commit -m "feat(taste): judgments and categories APIs; FAISS-backed tombstone embeddings"
```

---

### Task 7: Bulk category CLI

**Files:**
- Create: `scripts/assign_category.py`
- Test: `tests/test_assign_category_cli.py`

**Interfaces:**
- Consumes: Task 6 category routes and `GET /api/media?include_hidden=true` (summary rows carry `file_path`).
- Produces:
  - `is_under(path: str, folder: str) -> bool`: compares by path **components** after turning `\\` into `/` and dropping a trailing slash; case-insensitive when either side starts with a drive letter (`C:`), otherwise case-sensitive.
  - `main(argv: Optional[List[str]] = None, client: Optional[httpx.Client] = None) -> int` (exit code), so tests can inject an `httpx.Client(transport=httpx.MockTransport(...))`.
  - CLI: `python scripts/assign_category.py FOLDER --category NAME [--remove] [--dry-run] [--no-create] [--server http://localhost:8700]`. Sends `Authorization: Bearer $METASCAN_API_KEY` when that env var is set.

Behavior: list media, keep paths under FOLDER (always recursive), find the category by case-insensitive name (create it unless `--no-create`; with `--no-create` and no match ⇒ exit 2 with a message). Add (source `bulk`) or remove in chunks of 500. Print one summary line: `<n> items under <folder>; added <a>` (or `removed <r>`); with `--dry-run`, print `<n> items under <folder> (dry run, nothing changed)` and make no writes. Server unreachable ⇒ exit 1 with a one-line message, no traceback. Standalone: imports only the stdlib and `httpx` (already a dependency), nothing from `metascan`/`backend`, so it can be deleted without touching anything else.

- [ ] **Step 1: Write the failing tests** in `tests/test_assign_category_cli.py` (import the module with `importlib.util.spec_from_file_location`):

```python
@pytest.mark.parametrize("path,folder,expected", [
    ("/lib/scifi/a.png", "/lib/sci", False),
    ("/lib/sci/deep/a.png", "/lib/sci/", True),
    ("C:\\Lib\\Sci\\a.png", "c:/lib/sci", True),
    ("/Lib/Sci/a.png", "/lib/sci", False),
])
def test_is_under(path, folder, expected):
    assert cli.is_under(path, folder) is expected

def test_dry_run_makes_no_writes(capsys):
    calls = []
    client = _mock_client(media=["/lib/sci/a.png", "/lib/other/b.png"], categories=[], record=calls)
    assert cli.main(["/lib/sci", "--category", "Sci-Fi", "--dry-run"], client=client) == 0
    assert all(c.method == "GET" for c in calls)
    assert "1 items under /lib/sci (dry run, nothing changed)" in capsys.readouterr().out

def test_creates_category_and_adds_in_chunks(capsys):
    calls = []
    media = [f"/lib/sci/{i}.png" for i in range(1200)]
    client = _mock_client(media=media, categories=[], record=calls)
    assert cli.main(["/lib/sci", "--category", "Sci-Fi"], client=client) == 0
    posts = [c for c in calls if c.method == "POST" and c.url.path.endswith("/items")]
    assert [len(json.loads(c.content)["file_paths"]) for c in posts] == [500, 500, 200]
    assert all(json.loads(c.content)["source"] == "bulk" for c in posts)

def test_no_create_unknown_category_exits_2():
    client = _mock_client(media=[], categories=[], record=[])
    assert cli.main(["/lib", "--category", "Nope", "--no-create"], client=client) == 2
```

`_mock_client` is a small helper in the test file: an `httpx.MockTransport` that serves `GET /api/media`, `GET/POST /api/categories`, and `POST/DELETE /api/categories/{id}/items`, appending each request to `record`.

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests/test_assign_category_cli.py -v`
Expected: FAIL (script missing).

- [ ] **Step 3: Implement** `scripts/assign_category.py` (with an `if __name__ == "__main__": sys.exit(main())` guard).

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `make quality test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/assign_category.py tests/test_assign_category_cli.py
git commit -m "feat(taste): standalone CLI to bulk-assign a category to a folder tree"
```

---

### Task 8: Media viewer side rail, keys and category picker

**Files:**
- Create: `frontend/src/types/judgments.ts`, `frontend/src/api/judgments.ts`, `frontend/src/api/categories.ts`, `frontend/src/stores/categories.ts`, `frontend/src/components/viewer/RatingRail.vue`, `frontend/src/components/viewer/CategoryPicker.vue`
- Modify: `frontend/src/components/viewer/MediaViewer.vue`, `frontend/src/components/viewer/VideoPlayer.vue`, `frontend/src/stores/media.ts`, `frontend/src/types/websocket.ts` (add the `categories` channel), `frontend/src/App.vue` (always-on `categories` WS bridge calling `categoriesStore.load()`, next to the folders bridge)

**Interfaces:**
- Consumes: Task 6 routes and response shapes.
- Produces:
  - `types/judgments.ts`: `export const REASONS = ['realism','composition','emotion','light','concept','motion'] as const`; `export type Reason = typeof REASONS[number]`; `export type Verdict = 'favorite' | 'pass'`; `export interface CategoryRef { id: number; name: string }`; `export interface Category extends CategoryRef { item_count: number; created_at: string }`; `export interface JudgmentState { file_path: string; verdict: Verdict | null; reasons: Reason[]; seen_at: string | null; confirmed_at: string | null; is_favorite: boolean; categories: CategoryRef[] }`; `export const SEEN_DWELL_MS = 1000`.
  - `api/judgments.ts`: `fetchJudgment(path): Promise<JudgmentState>`, `markSeen(path)`, `setVerdict(path, verdict: Verdict | null, opts?: { scoreShown?: number; stickyCategoryId?: number })`, `setReason(path, reason: Reason, on: boolean, scoreShown?: number)` — paths through `encodeURIComponent` like `api/media.ts`.
  - `stores/media.ts`: `applyFavoriteFlag(filePath: string, isFavorite: boolean): void` — mirrors onto `allMedia`, `selectedMedia` and `favoritePaths` the same way `toggleFavorite` does today.
  - `api/categories.ts`: `fetchCategories(): Promise<Category[]>`, `createCategory(name): Promise<CategoryRef>`, `renameCategory(id, name)`, `deleteCategory(id)`, `addItems(id, filePaths, source: 'viewer' | 'bulk')`, `removeItems(id, filePaths)` (uses `del(path, body)`).
  - `stores/categories.ts` (`useCategoriesStore`): `categories: Ref<Category[]>`, `stickyId: Ref<number | null>`, `load()`, `create(name)`, `rename(id, name)`, `remove(id)`, `setSticky(id | null)`. Holds no per-item state.
  - `RatingRail.vue` props: `{ judgment: JudgmentState | null; hint: string | null; sticky: CategoryRef | null }` — display only, no API calls. Shows the sticky category at the top ("📌 Sci-Fi") and the item's categories as chips under the reasons.
  - `CategoryPicker.vue` props `{ assigned: number[] }`, emits `toggle(id)`, `create(name)`, `sticky(id)`, `rename(id, name)`, `delete(id)`, `close()`.
  - `VideoPlayer.vue`: new emit `play: []` fired on the `<video>` `play` event.

Behavior:
- Layout: the rail (170 px, matching `.superpowers/brainstorm/*/content/viewer-rating.html` option B) sits right of `.media-galleria` inside `.viewer-body`, only when `allowDestructive`. It shows a verdict pill (★ Favorite / ✕ Pass / none), a "loved for…" or "missed on…" header, six chips labelled with their key number and lit in green (favorite) or red (pass), and a "● seen" marker.
- Keys (ignored with Ctrl/Meta/Alt held and in inputs, as today): `F` ⇒ `setVerdict(favorite ? null : 'favorite')`; `X` ⇒ `setVerdict(pass ? null : 'pass')`; `1`–`6` ⇒ with no verdict, show hint "Press F or X first" for 1.5 s; otherwise `setReason(REASONS[n-1], !on)`. The header ☆ button uses the same path as `F`. Each verdict response calls `mediaStore.applyFavoriteFlag`.
- Seen: on `current` change, fetch the judgment and start a `SEEN_DWELL_MS` timer for images (cleared on change); for videos call `markSeen` on the player's `play` emit (once per visit).
- Stale responses: tag every request with the file path it was for and drop a response whose path is no longer `current.file_path`. Send writes for one item one after another (promise chain), so a fast `F` then `2` reaches the server in order.
- Verdicts send `stickyCategoryId` when a sticky category is set.
- `C` opens `CategoryPicker` inside the rail: a text input with focus and the filtered list below. ↑/↓ moves the highlight; Enter on a highlighted category toggles it on the current item (`addItems(..., 'viewer')` / `removeItems`), then refetches the judgment; Enter with a typed name that matches nothing creates the category and adds it; Shift+Enter sets the highlighted category as sticky, or clears sticky if it already is; Esc closes the picker and **only** the picker (the viewer's handler already ignores keys from inputs; stop the event inside the picker). Rename (✎) and delete (🗑, with `window.confirm` naming the item count) are mouse buttons on each row. The viewer's own keys are inactive while the picker is open.
- Help overlay: add rows `X — Pass`, `1–6 — Toggle reason (realism, composition, emotion, light, concept, motion)`, and `C — Categories (Shift+Enter: sticky)`, shown only when `allowDestructive`.

- [ ] **Step 1: Implement** the files above.

- [ ] **Step 2: Type-check and build**

Run: `cd frontend && npm run build`
Expected: `vue-tsc` passes with no errors; Vite build succeeds.

- [ ] **Step 3: Manual verification in the running app** (backend `python run_server.py`, frontend `npm run dev`; use the `run` skill or the Browser pane). On a scratch library of synthetic images, check:
  1. `F`, `→` still favorite and advance exactly as before; the grid star updates.
  2. `X` on a favorite flips to Pass; the chips turn red and clear.
  3. `2` with no verdict shows the hint; after `F`, `2` lights "composition".
  4. Hold `→` while pressing `F` rapidly across 10 items: each item's rail shows only its own state (Review Focus #5); `GET /api/judgments/...` per item agrees with what the rail showed.
  5. Opening an image, waiting >1 s ⇒ `seen_at` set; leaving within 0.5 s ⇒ not set. A video ⇒ set on play.
  6. Storyboard candidate picker (`allowDestructive=false`): no rail, `F`/`X`/digits do nothing.
  7. Delete a favorited item with Ctrl+D ⇒ one row in `media_tombstones`.
  8. `C`, type `Sci`, Enter ⇒ chip appears; Enter again ⇒ removed. Type a new name + Enter ⇒ created and added. Esc closes only the picker.
  9. Shift+Enter on "Sci-Fi" ⇒ 📌 shows; `F` on the next three items ⇒ each gets Sci-Fi; only seeing an item (no verdict) does not.
  10. With two tabs open, create a category in one ⇒ it appears in the other's picker (WS).

- [ ] **Step 4: Run the full suite**

Run: `make quality test`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(taste): rating side rail, keys and category picker in the media viewer"
```

---

### Task 9: Category picker in the T2I and I2V dialogs

**Files:**
- Create: `frontend/src/components/generation/CategorySelect.vue`
- Modify: `frontend/src/components/dialogs/T2IDialog.vue`, `frontend/src/components/dialogs/I2VDialog.vue`, `frontend/src/api/t2i.ts`, `frontend/src/api/i2v.ts`
- Modify: `backend/api/t2i.py`, `backend/api/i2v.py` (request models), `metascan/core/t2i_runner.py`, `metascan/core/i2v_runner.py`
- Test: the existing T2I/I2V runner and API test files (`tests/test_i2v_runner.py`, `tests/test_i2v_api.py`, and the T2I equivalents found with `ls tests | grep t2i`)

**Interfaces:**
- Consumes: Task 5 `add_media_categories`; Task 8 `useCategoriesStore`.
- Produces:
  - Request field `category_ids: List[int] = []` on the T2I batch request and the I2V generate request. Unknown ids ⇒ 404 `{code: "unknown_category"}` at request time (validated before anything is queued).
  - Runners keep `category_ids` on the batch/job and, right after `create_t2i_image` (~`t2i_runner.py:1393`) / `create_i2v_video` (~`i2v_runner.py:383`), call `db.add_media_categories([file_path], cid, "generation")` for each id (via `asyncio.to_thread`), then broadcast `categories_changed`. A category deleted mid-batch is skipped with a log warning, never failing the ingest. Confirm the media row exists at that point; if ingest creates it later, move the call after the media upsert.
  - `CategorySelect.vue`: `v-model: number[]`; a compact multi-select listing `useCategoriesStore().categories` with an inline "new category" entry.
  - Dialog state: the selection is **session-local** (not in `form_state`, so selecting a tile never changes it — the T2I autosave-drift rule), persisted in localStorage as `metascan.t2i.categories.v1` / `metascan.i2v.categories.v1`; ids that no longer exist are dropped on load.
- Placement: T2I — in the settings row with Model / Workflow / Size / Aspect (`T2IDialog.vue` ~line 837); I2V — next to its model/preset settings.

- [ ] **Step 1: Write the failing tests**

```python
def test_t2i_batch_tags_generated_images(...):   # fake comfy + fake llama, as the existing runner tests do
    cid = db.create_category("Anime").id
    run a 2-image Manual batch with category_ids=[cid]
    assert {c.name for c in db.get_media_categories(each_output)} == {"Anime"}
    assert source of each link == "generation"

def test_t2i_unknown_category_rejected_before_queueing(client):
    r = client.post(<t2i batch route>, json={..., "category_ids": [999]})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "unknown_category"
    assert no generation_jobs rows were created

def test_i2v_generate_tags_video(...): same shape as the T2I case
```

(Write them in the style and with the fixtures of the existing runner/API tests; the assertions above are the required ones.)

- [ ] **Step 2: Run them to make sure they fail**

Run: `pytest tests -k "category" -v`
Expected: FAIL (`category_ids` not accepted).

- [ ] **Step 3: Implement** the backend field, validation and tagging, then the frontend select, API field and placement.

- [ ] **Step 4: Verify**

Run: `make quality test && cd frontend && npm run build`
Expected: all PASS; type-check clean. Manually: pick "Anime" in T2I, generate 2 images ⇒ both show the Anime chip in the viewer rail; reopen the dialog ⇒ still selected; selecting an older tile does not change it.

- [ ] **Step 5: Commit**

```bash
git add backend metascan frontend/src tests
git commit -m "feat(taste): tag T2I and I2V generations with the dialog's categories"
```
