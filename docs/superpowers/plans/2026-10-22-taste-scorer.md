# Taste Learning — Project 2: Taste Scorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Learn the owner's image taste from project 1's judgments and show it: a score per image that retrains itself in minutes, drives score badges on T2I result tiles and in the viewer rail, a "Taste" best-first library sort, and a review mode (`R`) that shows the items the scorer is least sure about.

**Architecture:** Pure numpy model code (`taste_model.py`) trained in a one-shot subprocess (`taste_worker.py`, the embedding-worker pattern: JSON task file in, JSON result file out) that reads CLIP vectors straight from the on-disk FAISS index. The server builds labels from SQLite, launches the worker through `TasteRunner`, stores the model row and per-image scores, and broadcasts on a new `taste` WS channel. An asyncio loop retrains automatically after enough new judgments and a quiet period. The frontend caches the score map in a Pinia store.

**Tech Stack:** Python 3.11, numpy, faiss-cpu (already deps — **no new dependencies**), FastAPI, SQLite; Vue 3 / Pinia / TS.

**Spec:** `docs/superpowers/specs/2026-10-22-taste-learning-design.md` — project 2 section, "Guarding against taste collapse", and the owner's decisions (scores shown on tiles immediately; video training on hold).

## Global Constraints

- Python 3.11 only (`venv-311`); `make VENV_DIR=venv-311 quality test` before every commit; `cd frontend && npm run build` for frontend tasks. Known pre-existing failure: `tests/test_t2i_api.py::TestAppWiring::test_create_app_registers_the_t2i_routes`.
- **Images only.** Videos are never labels and never get a score (owner: video training on hold). Video judgments keep being recorded by project 1.
- No new Python or npm dependencies. Training runs on CPU in a subprocess; the FastAPI process never imports faiss for training and never runs the fit itself.
- DB access synchronous in `DatabaseManager`, wrapped by a service with `asyncio.to_thread`. Never touch the `/api/media` summary SELECT or its covering indexes — scores travel on their own endpoint.
- No `PRAGMA user_version` bump: this plan only adds tables (`CREATE TABLE IF NOT EXISTS`), no backfill.
- Labels: favorite → 1; pass → 0; seen and not favorited → 0; unseen and no verdict → excluded. Tombstoned items count, using the tombstone's stored embedding when its `embedding_model` matches the index.
- Weighting: `weight = 0.5 ** (age_days / half_life_days)` with `age_days` measured from `COALESCE(confirmed_at, judged_at, seen_at)` (spec: the clock starts at rating time and restarts on reconfirm); `half_life_days` default **180**. A row whose most recent verdict event carried a non-NULL `score_shown` is multiplied by `SCORE_SHOWN_WEIGHT = 0.5`.
- Pairs: within one T2I `batch_id`, every (favorited image, non-favorited labeled image) is a pair with weight `min(w_a, w_b)`.
- Minimum to train: `MIN_POSITIVES = 20` and `MIN_NEGATIVES = 20` among image labels; below that the status says how many more are needed and no model is produced.
- Auto-retrain: when `≥ 25` judgment events of kinds favorite/unfavorite/pass/unpass/reason_on/reason_off/category_on/category_off have been logged since the active model's `labels_as_of`, **and** no such event for `120 s`; checked every `30 s`. Config: `config.json` → `"taste": {"half_life_days": 180, "auto_retrain": true}` (missing keys = defaults).
- A model is tied to the CLIP `model_key` and dim of the index it was trained on. If the index's current `model_key` differs, the model is **stale**: no scores are served, status says "retrain needed", auto-retrain treats it as due.
- Score display: two decimals, `0.00`–`1.00`. When a score is visible, every verdict/reason write sends it as `score_shown` (project 1 already stores it).
- New WS channel `taste`: `training_started {}`, `training_complete {model_id, holdout_auc, n_pos, n_neg}`, `training_failed {error}`, `scores_updated {model_id}`. Add it to CLAUDE.md's channel list.
- Never read the owner's real DB, caption CSVs or prompts; all tests use synthetic data (FAISS test vectors dim ≥ 32, normalized — CLAUDE.md).

**Deferred to project 3 (planner's call):** the favorite-rate-per-writer dashboard and the diversity trend from the spec's "Success measure" / "taste collapse" sections — both compare writers, which do not exist until project 3. This plan records the scorer's own metrics (counts, holdout AUC) only.

## Review Focus

1. **CLIP model switched** (index dim/model differs from the active scorer) → no crash, no wrong scores served, status "retrain needed" — tests in Task 3 and Task 4.
2. **Fresh library with a handful of ratings** → no model, clear "need N more favorites / M more passes" status, no worker crash — tests in Task 2 and Task 4.
3. **Rating while training runs** → events logged after the training snapshot count toward the *next* retrain (`labels_as_of` is the snapshot time, not the completion time) — test in Task 4.
4. **Videos** never appear in labels or scores, even when judged and present in the FAISS index — tests in Task 1 and Task 3.
5. **Two train requests at once** (manual click during an auto-retrain) → exactly one worker; the second request gets 409 `already_training` — test in Task 4.

---

### Task 1: Tables, settings and training labels

**Files:**
- Create: `metascan/core/taste_labels.py`
- Modify: `metascan/core/database_sqlite.py` (tables; `taste_training_rows`; model/score CRUD)
- Test: `tests/test_taste_labels.py`

**Interfaces:**
- Produces (`taste_labels.py`):
  - constants `DEFAULT_HALF_LIFE_DAYS = 180.0`, `SCORE_SHOWN_WEIGHT = 0.5`, `MIN_POSITIVES = 20`, `MIN_NEGATIVES = 20`, `RETRAIN_MIN_EVENTS = 25`, `RETRAIN_IDLE_S = 120`, `RETRAIN_CHECK_S = 30`, `TRAINING_EVENT_KINDS: Tuple[str, ...]` (the 8 kinds above).
  - `decay_weight(rated_at: str, now: datetime, half_life_days: float) -> float`
  - `@dataclass LabelRow(key: str, file_path: str, label: int, weight: float, categories: List[str], batch_id: Optional[str], embedding: Optional[bytes], embedding_model: Optional[str])` — `key` is the file path for live media and `"tombstone:<id>"` for tombstones; `embedding` is set only for tombstones.
  - `@dataclass LabelSet(rows: List[LabelRow], pairs: List[Tuple[int, int, float]], n_pos: int, n_neg: int, as_of: str)` — pairs are `(index_of_positive, index_of_negative, weight)` into `rows`.
  - `build_label_set(raw: Dict[str, Any], now: datetime, half_life_days: float) -> LabelSet` — pure: turns the DB rows into labels, weights, pairs.
  - `missing_for_training(n_pos: int, n_neg: int) -> Optional[Dict[str, int]]` → `None` when trainable, else `{"positives": k, "negatives": m}` still needed.
- Produces (`DatabaseManager`):
  - `taste_training_raw() -> Dict[str, Any]` — `{"media": [...], "tombstones": [...]}`: image rows only (`is_video` false) with `file_path, is_favorite, passed, seen_at, judged_at, confirmed_at, last_verdict_score_shown, categories (names), t2i_batch_id`; tombstones whose path is an image extension with their judgment fields, embedding, embedding_model, categories and lineage `batch_id`.
  - `count_training_events_since(since: Optional[str]) -> Tuple[int, Optional[str]]` → (count, ISO time of the newest such event).
  - Tables: `taste_models(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, labels_as_of TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('active','retired','failed')), embedding_model TEXT NOT NULL, dim INTEGER NOT NULL, categories TEXT NOT NULL, n_pos INTEGER NOT NULL, n_neg INTEGER NOT NULL, n_pairs INTEGER NOT NULL, holdout_auc REAL, half_life_days REAL NOT NULL, params_path TEXT NOT NULL, error TEXT)` and `taste_scores(file_path TEXT PRIMARY KEY REFERENCES media(file_path) ON DELETE CASCADE, model_id INTEGER NOT NULL, score REAL NOT NULL)`.
  - `record_taste_model(row: Dict[str, Any]) -> int` (inserts as `active`, retires the previous active one), `get_active_taste_model() -> Optional[Dict[str, Any]]`, `list_taste_models(limit: int = 10) -> List[Dict[str, Any]]`, `replace_taste_scores(model_id: int, scores: Dict[str, float]) -> int` (one transaction: delete all, insert; skips paths with no media row), `get_taste_scores(file_paths: Optional[List[str]] = None) -> Tuple[Optional[int], Dict[str, float]]`.

- [ ] **Step 1: Write the failing tests** (`_media()` helper as in `tests/test_judgments_db.py`; `T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)`):

```python
def test_decay_halves_at_half_life():
    assert decay_weight("2025-07-05T00:00:00+00:00", T0, 180) == pytest.approx(0.5, rel=1e-3)

def test_labels_from_verdicts_and_seen(db):
    for p in ("/l/fav.png", "/l/pass.png", "/l/seen.png", "/l/unseen.png", "/l/vid.mp4"):
        db.save_media(_media(p))
    db.set_verdict("/l/fav.png", "favorite", now=T0); db.set_verdict("/l/pass.png", "pass", now=T0)
    db.mark_seen("/l/seen.png", now=T0); db.set_verdict("/l/vid.mp4", "favorite", now=T0)
    ls = build_label_set(db.taste_training_raw(), T0, 180)
    assert {r.file_path: r.label for r in ls.rows} == {"/l/fav.png": 1, "/l/pass.png": 0, "/l/seen.png": 0}

def test_score_shown_halves_weight(db): ...      # verdict with score_shown=0.8 → weight 0.5 at age 0
def test_reconfirm_resets_clock(db): ...          # judged 360 d ago, reconfirmed today → weight 1.0
def test_batch_pairs(db): ...                     # 3 images in batch "b1": 1 fav, 1 pass, 1 seen → pairs [(fav,pass),(fav,seen)]
def test_tombstone_rows_carry_embedding(db): ...  # judged image deleted → row key "tombstone:<id>", embedding bytes present
def test_missing_for_training():
    assert missing_for_training(5, 30) == {"positives": 15, "negatives": 0}
    assert missing_for_training(20, 20) is None

def test_event_count_since(db): ...                # seen events don't count; favorite/pass/reason/category do
def test_replace_scores_and_active_model(db): ...   # record two models → first retired; scores replaced; unknown paths skipped
```

- [ ] **Step 2: Run them to make sure they fail** — `venv-311/bin/pytest tests/test_taste_labels.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement.** `t2i_batch_id` comes from `t2i_images.batch_id` (LEFT JOIN on `file_path`); `last_verdict_score_shown` is the `score_shown` of the newest `favorite|unfavorite|pass|unpass` event for the path (NULL if none). Image vs video uses `media.is_video` (tombstones: the file extension against the scanner's image extensions).
- [ ] **Step 4: Run the tests to make sure they pass** — `make VENV_DIR=venv-311 quality test`.
- [ ] **Step 5: Commit** — `feat(taste): scorer tables and training labels from judgments`.

---

### Task 2: The model (pure numpy)

**Files:**
- Create: `metascan/core/taste_model.py`
- Test: `tests/test_taste_model.py`

**Interfaces:**
- Produces:
  - `@dataclass TasteModelParams(w: np.ndarray, b: float, categories: List[str], dim: int, embedding_model: str)` with `save(path: Path) -> None` / `@classmethod load(path: Path)` (`.npz`).
  - `features(embeddings: np.ndarray, row_categories: List[List[str]], categories: List[str]) -> np.ndarray` — L2-normalize each embedding, append a multi-hot vector over `categories` (unknown names ignored).
  - `fit(X: np.ndarray, y: np.ndarray, weights: np.ndarray, pairs: List[Tuple[int, int, float]], *, l2: float = 1e-3, pair_weight: float = 1.0, steps: int = 400, lr: float = 0.05, seed: int = 0) -> Tuple[np.ndarray, float]` — weighted logistic loss + `pair_weight` × weighted pairwise logistic loss on `(s_pos - s_neg)` + `l2·‖w‖²`, full-batch Adam, deterministic.
  - `predict(params: TasteModelParams, X: np.ndarray) -> np.ndarray` — probabilities in [0, 1].
  - `split_holdout(n: int, fraction: float = 0.15, seed: int = 0) -> Tuple[np.ndarray, np.ndarray]` (train idx, holdout idx); pairs touching a holdout row are dropped from training.
  - `auc(y: np.ndarray, scores: np.ndarray) -> Optional[float]` — rank-based (Mann–Whitney), `None` if one class is absent.

- [ ] **Step 1: Write the failing tests** (synthetic, dim 32):

```python
def test_learns_separable_direction():
    rng = np.random.default_rng(0); d = rng.normal(size=32); d /= np.linalg.norm(d)
    X = rng.normal(size=(400, 32)); y = (X @ d > 0).astype(float)
    w, b = fit(features(X, [[]]*400, []), y, np.ones(400), [])
    p = predict(TasteModelParams(w, b, [], 32, "m"), features(X, [[]]*400, []))
    assert auc(y, p) > 0.95

def test_pairs_move_ranking():          # two identical-label clusters; pairs say A > B → mean score A > mean score B
def test_weights_matter():               # contradicting duplicates; heavier-weighted label wins
def test_category_feature_used():        # same embeddings, label depends only on category → AUC > 0.9
def test_deterministic():                # fit twice with seed 0 → identical w
def test_auc_none_when_one_class(): assert auc(np.ones(5), np.arange(5.0)) is None
def test_save_load_roundtrip(tmp_path)
def test_holdout_split_disjoint_and_sized()
```

- [ ] **Step 2: Run to fail** — `venv-311/bin/pytest tests/test_taste_model.py -v`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Pass** — `make VENV_DIR=venv-311 quality test`.
- [ ] **Step 5: Commit** — `feat(taste): numpy taste model with pairwise term and holdout AUC`.

---

### Task 3: Worker subprocess and runner

**Files:**
- Create: `metascan/workers/taste_worker.py`, `metascan/core/taste_runner.py`
- Test: `tests/test_taste_runner.py`

**Interfaces:**
- Consumes: Task 1 (`taste_training_raw`, `build_label_set`, `record_taste_model`, `replace_taste_scores`, `get_active_taste_model`), Task 2 (all), `FaissIndexManager` on-disk layout (`faiss_index.bin`, `id_mapping.json`, `index_meta.json` with `model_key`, under `EmbeddingQueue.index_dir`).
- Produces:
  - Worker CLI: `python -m metascan.workers.taste_worker <task.json>`; task `{"mode": "train"|"score", "index_dir": str, "labels_path": str|null, "model_path": str|null, "out_dir": str, "image_paths": [str]}`; writes `<out_dir>/taste_result.json`: train → `{"status": "ok", "params_path", "holdout_auc", "n_pos", "n_neg", "n_pairs", "dim", "embedding_model", "categories", "scores": {path: score}}`; score → `{"status": "ok", "scores": {...}}`; any failure → `{"status": "error", "error": str}` and exit code 1. It reads vectors with `faiss.read_index` + `reconstruct` for the needed paths only (native and posix keys), uses tombstone embeddings from the labels file when present and their model matches, skips rows with no vector, and scores exactly `image_paths`.
  - `class TasteRunner(db, index_dir: Path, work_dir: Path, python: str = sys.executable)`:
    - `async train(half_life_days: float, now: Optional[datetime] = None) -> Dict[str, Any]` — builds labels (to_thread), checks `missing_for_training` (returns `{"status": "not_enough_data", "missing": {...}}` without spawning), writes the task, spawns the worker with `asyncio.create_subprocess_exec` (stderr drained to the logger line by line — CLAUDE.md's stderr rule), reads the result, records the model with `labels_as_of = LabelSet.as_of` (the snapshot time), replaces scores, prunes params files beyond the newest 5. Returns `{"status": "ok", "model_id", ...metrics}` or `{"status": "error", "error"}` (and records a `failed` model row with the error).
    - `async rescore() -> Optional[int]` — score-only run with the active model for all current image paths; no-op (None) when there's no active model or it is stale.
    - `is_stale(model: Dict[str, Any]) -> bool` — compares `model["embedding_model"]` and `dim` with `index_meta.json`.
    - `training: bool` property; a second `train()` while one runs raises `RuntimeError("already_training")`.

- [ ] **Step 1: Write the failing tests** — build a real small FAISS index on disk in `tmp_path` via `FaissIndexManager.create(32, "small")` + `add_batch` + `save` with synthetic normalized vectors whose favorite/pass labels follow a hidden direction; 30 favorites, 30 passes, one judged video path also present in the index, one tombstone.

```python
async def test_train_end_to_end(runner, db):
    r = await runner.train(180)
    assert r["status"] == "ok" and r["holdout_auc"] > 0.8
    model_id, scores = db.get_taste_scores()
    assert model_id == r["model_id"] and "/l/vid.mp4" not in scores and len(scores) == 60

async def test_not_enough_data_spawns_nothing(runner_few, monkeypatch): ...   # spawn patched to fail if called
async def test_worker_error_recorded(runner, monkeypatch): ...                # corrupt index file → status error, failed row, previous active model kept
async def test_stale_after_model_switch(runner, db, tmp_path): ...           # rewrite index_meta model_key → is_stale True, rescore() returns None
async def test_second_train_while_running_raises(runner): ...
async def test_rescore_scores_new_images(runner, db): ...                    # add 5 vectors+media after training → rescore covers them
```

- [ ] **Step 2: Run to fail** — `venv-311/bin/pytest tests/test_taste_runner.py -v`.
- [ ] **Step 3: Implement.** The worker must not import torch/open_clip.
- [ ] **Step 4: Pass** — `make VENV_DIR=venv-311 quality test`.
- [ ] **Step 5: Commit** — `feat(taste): taste worker subprocess and runner`.

---

### Task 4: API, auto-retrain loop, WS channel, batch position

**Files:**
- Create: `backend/services/taste_service.py`, `backend/api/taste.py`
- Modify: `backend/main.py` (router; lifespan: construct the runner + retrain loop, shut it down cleanly; trigger `rescore()` after the embedding queue's completion callback), `backend/api/judgments.py` + `backend/services/judgments_service.py` (add `batch`), `metascan/core/database_sqlite.py` (`get_t2i_batch_position`), `CLAUDE.md`
- Test: `tests/test_taste_api.py`, `tests/test_taste_retrain_loop.py`

**Interfaces:**
- Consumes: Task 3 `TasteRunner`.
- Produces:
  - Routes (prefix `/api`):
    - `GET /taste/status` → `{"active_model": {id, created_at, n_pos, n_neg, n_pairs, holdout_auc, embedding_model, half_life_days} | null, "stale": bool, "training": bool, "new_events": int, "missing": {"positives", "negatives"} | null, "auto_retrain": bool, "last_error": str | null}`.
    - `POST /taste/train` → 202 `{"status": "started"}`; 409 `{code: "already_training"}`; 409 `{code: "not_enough_data", missing}`. Runs as a background task; result goes out on WS.
    - `GET /taste/scores` → `{"model_id": int | null, "scores": {path: score}}` (empty map when stale or no model). `POST /taste/scores` `{file_paths}` → same shape, subset.
    - `GET /taste/review-queue?limit=200` → `{"paths": [str]}`: images with no verdict and no `seen_at`, ordered by `abs(score - 0.5)` ascending, then path; empty when no model.
  - `GET /api/judgments/{path}` gains `"batch": {"index": int, "size": int} | null` — 1-based position by `t2i_images.id` within the item's `batch_id`; null for non-T2I items. `DatabaseManager.get_t2i_batch_position(file_path: str) -> Optional[Tuple[int, int]]`.
  - `TasteService.retrain_loop()` — every `RETRAIN_CHECK_S`: if `auto_retrain` and not training and (stale or `new_events ≥ RETRAIN_MIN_EVENTS`) and the newest event is older than `RETRAIN_IDLE_S` and labels are sufficient → `train()`. Swallows and logs exceptions per tick; cancelled cleanly on shutdown.
  - WS `taste` events as in Global Constraints; `scores_updated` after every successful train or rescore.

- [ ] **Step 1: Write the failing tests** (app setup as `tests/test_folders_api.py`; runner replaced by a fake with controllable results for API tests; the loop tested with a fake clock/short constants via monkeypatch):

```python
def test_status_without_model(client): assert client.get("/api/taste/status").json()["active_model"] is None
def test_train_conflict(client, fake_runner): fake_runner.training = True; assert client.post("/api/taste/train").json()["detail"]["code"] == "already_training"
def test_not_enough_data_409(client): ...
def test_scores_empty_when_stale(client, fake_runner): ...
def test_review_queue_order(client, db): ...      # unseen items, closest to 0.5 first; judged/seen excluded
def test_judgment_has_batch_position(client, db): ...   # 3 t2i images in "b1" → middle one {"index": 2, "size": 3}; library image → null
async def test_loop_waits_for_quiet(service, fake_runner): ...       # 30 events, last one 10 s ago → no train; 130 s → train
async def test_events_during_training_count_next(service, db): ...   # events logged after labels_as_of → new_events > 0 after completion
async def test_loop_retrains_when_stale(service, fake_runner): ...
```

- [ ] **Step 2: Run to fail** — `venv-311/bin/pytest tests/test_taste_api.py tests/test_taste_retrain_loop.py -v`.
- [ ] **Step 3: Implement**; CLAUDE.md: add the `taste` channel and a "Taste scorer" bullet (worker pattern, labels/weights, stale rule, auto-retrain constants).
- [ ] **Step 4: Pass** — `make VENV_DIR=venv-311 quality test`.
- [ ] **Step 5: Commit** — `feat(taste): scorer API, auto-retrain loop and taste WS channel`.

---

### Task 5: Frontend store, Models tab section, Taste sort

**Files:**
- Create: `frontend/src/types/taste.ts`, `frontend/src/api/taste.ts`, `frontend/src/stores/taste.ts`
- Modify: `frontend/src/components/dialogs/ConfigModelsTab.vue`, `frontend/src/components/layout/ViewMenubar.vue`, `frontend/src/stores/media.ts`, `frontend/src/types/websocket.ts`, `frontend/src/App.vue` (always-on `taste` bridge)

**Interfaces:**
- Consumes: Task 4 routes/WS.
- Produces:
  - `useTasteStore`: `scores: Ref<Map<string, number>>`, `modelId`, `status`, `load()`, `loadStatus()`, `train()`, `scoreOf(path): number | null`, `reviewQueue(limit?): Promise<string[]>`. `scores_updated` → `load()`; `training_*` → `loadStatus()`.
  - Sort option `{ label: 'Taste', value: 'taste' }` in `ViewMenubar`, listed only when the store has scores (kept if currently selected, like Relevance); `mediaStore` sorts `taste` client-side by `scoreOf` descending, unscored items last in date-added order.
  - Models tab "Taste scorer" section: active model line (trained date, `n_pos`/`n_neg`, holdout AUC to 2 decimals), "retrain needed" when stale, "Need N more favorites / M more passes" when `missing`, "N new ratings since last training", a **Retrain now** button (disabled while training or when `missing`), training spinner, last error.

- [ ] **Step 1: Implement.**
- [ ] **Step 2: Build** — `cd frontend && npm run build` → vue-tsc clean.
- [ ] **Step 3: Headless check** against a throwaway backend with synthetic data only: Retrain now → status updates via WS; Taste sort orders by score.
- [ ] **Step 4: Commit** — `feat(taste): taste store, Models tab scorer section and Taste sort`.

---

### Task 6: Viewer rail score, batch position, `score_shown`, review mode

**Files:**
- Modify: `frontend/src/components/viewer/MediaViewer.vue`, `frontend/src/components/viewer/RatingRail.vue`, `frontend/src/types/judgments.ts` (`batch` field)

**Interfaces:**
- Consumes: Task 5 store; Task 4 `batch` field and review queue.
- Behavior:
  - Rail shows `taste 0.71` under the verdict when `scoreOf(path)` is non-null (images only) and `batch 2 of 8` when `judgment.batch` is set.
  - Every verdict/reason write for an item whose score was visible in the rail at key-press time sends that value as `scoreShown`.
  - `R` (rating enabled only): fetch `reviewQueue()`, resolve paths to `Media` objects from `mediaStore.allMedia`, and switch navigation to that list starting at index 0; the header shows `Review · k / N`. `R` again (or reaching the end) returns to the original list at the item that was current when review started. Empty queue → hint "Nothing to review — rate more or retrain". Add `R — Review least-certain` to the help overlay.
- [ ] **Step 1: Implement.**
- [ ] **Step 2: Build** — `npm run build`.
- [ ] **Step 3: Headless check** (throwaway backend): score and batch label appear; a verdict PATCH carries `score_shown`; R enters/exits review and restores position.
- [ ] **Step 4: Commit** — `feat(taste): scores, batch position and review mode in the viewer`.

---

### Task 7: Score badges on T2I result tiles

**Files:**
- Modify: `frontend/src/components/generation/ThumbTile.vue`, `frontend/src/components/generation/ThumbGrid.vue`, `frontend/src/types/jobs.ts`, `frontend/src/components/dialogs/T2IDialog.vue`

**Interfaces:**
- Consumes: Task 5 `useTasteStore`.
- Behavior: `ThumbItem` gains optional `score?: number | null`; `ThumbTile` renders a small bottom-left badge `0.82` when present; the two highest-scored tiles currently shown get a gold outline. T2I fills `score` from `scoreOf(file_path)`; tiles stay in their existing order (no re-sorting — the T2I grid's paging rules in `.claude/rules/t2i.md` stay intact). New images get a score after the post-embedding `rescore()` broadcasts `scores_updated`. I2V passes nothing (videos have no scores).
- [ ] **Step 1: Implement.**
- [ ] **Step 2: Build** — `npm run build`.
- [ ] **Step 3: Headless check**: badges appear after a `scores_updated`; no badge for unscored tiles.
- [ ] **Step 4: Commit** — `feat(taste): taste score badges on T2I result tiles`.
