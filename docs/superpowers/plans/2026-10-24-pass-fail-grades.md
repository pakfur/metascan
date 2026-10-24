# Pass / Fail Grades Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split today's favorite-as-training-signal into an independent ★ favorite (view only) and a ✓ pass / ✗ fail **grade** that is the only training signal; migrate existing ratings; make deletion keep every graded image's training value; move the training sections into a Config → Training tab.

**Architecture:** A new `grade` column (`'pass'` / `'fail'` / NULL) on `media_judgments` and `media_tombstones`, filled once by a `user_version = 6` migration (with a pre-migration backup and refuse-to-start on failure). A new write API (`set_grade`, favorite-only `set_favorite`) with new event names; every consumer (taste scorer, writer dataset, hindsight briefs, base+taste examples, writer stats, scorecard) switches from favorite / pass / seen to grade one task at a time, while a temporary `set_verdict` shim and a synced legacy `passed` column keep not-yet-switched code and tests valid; the last task removes both. Tombstones gain the brief, library prompt, an archived ≤ 512 px thumbnail and per-CLIP-model vectors, and a background re-embedder fills vectors after a CLIP change.

**Tech Stack:** Python 3.11, FastAPI, SQLite (`metascan/core/database_sqlite.py`), Pillow, the CLIP `InferenceClient`; Vue 3 + Pinia + TypeScript (PrimeVue).

**Spec:** `docs/superpowers/specs/2026-10-24-pass-fail-grades-design.md` (approved 2026-10-24). Read it first; it wins over this plan on intent.

## Global Constraints

- Python 3.11 (`venv-311`). Gates per task: `make VENV_DIR=venv-311 quality test` and `cd frontend && npm run build`. Known pre-existing failure: `tests/test_t2i_api.py::TestAppWiring::test_create_app_registers_the_t2i_routes`.
- **Names:** never reuse `pass`, `passed`, `unpass` or `verdict` for the new ✓. The new marker is `grade` with values `"pass"` / `"fail"` / `None` (`Grade = Literal["pass", "fail"]`, `GRADES = ("pass", "fail")` in `metascan/core/judgments.py`). New events: `grade_pass`, `grade_fail`, `grade_clear`. Historical `pass` / `unpass` events and every existing `passed` column mean **reject** and must never be read as ✓.
- Favorite (`media.is_favorite`) and grade are independent: neither ever sets or clears the other (the one exception is the one-time migration: favorite → ✓).
- Viewing (`seen_at`) is never a training label anywhere.
- One migration only: `PRAGMA user_version` 5 → 6. Additive tables / columns use `CREATE … IF NOT EXISTS` / `_idempotent_add_column`.
- Unchanged values: half-life 180 days, scorer minimum 20 ✓ + 20 ✗, retrain at 25 events, readiness 100 pairs / 200 new, scorecard promotion at 30 judged pairs, base+taste K = 3.
- Copy (exact): `★ Favorite`, `✓ Pass`, `✗ Fail`, `No grade`, reasons header `worked on…` (✓) / `missed on…` (✗), `Press P or X first`, blind chip `(P or X)`, writer menu `NN% pass` and Base `baseline · NN% pass`, `Hindsight briefs (passed)`, score tooltip `chance you'd pass this`, re-embed status `re-embedding deleted images, n / N`.
- Keys in the media viewer: F = ★ toggle, **P** = ✓ toggle, X = ✗ toggle; pressing the key of the grade already set clears it.
- 409 code for a reason without a grade: `no_grade`.
- Archive: `<data dir>/archive/thumbs/`, JPEG quality 85, longest side ≤ 512 px (`ARCHIVE_MAX_PX = 512`), graded **images** only.
- Backup: `<db file>.pre-v6-<YYYYmmdd-HHMMSS>.bak` via the SQLite backup API, before the v6 transaction.
- Never log prompts, briefs, captions or examples at INFO+ (counts only).
- Never read the owner's real `data/` or `config.json` in tests (conftest isolates paths).

## Review Focus

1. **A legacy pending full-clean snapshot present at the first v6 start** (startup applies it after migrations) must restore with old meaning (`passed = 1` → ✗, favorites list → ★ + ✓), never be quarantined for its shape — test in Task 4.
2. **A failed or interrupted migration** (backup refused for disk space, an exception mid-transaction) must leave `user_version = 5` and the data untouched, and the next start retries — test in Task 2.
3. **Fast key sequences on one image** (P then X, X then X, F during a grade change, P on a hidden scorecard image) end in one consistent state with events in order and reasons cleared on a grade switch — test in Task 3.
4. **Deleting a graded image whose file is unreadable or already gone** still deletes and writes the tombstone without a thumbnail — test in Task 9.
5. **A path reused by a new file after deletion** never gives the old tombstone the new file's vector (worker fallback, backfill, re-embed) — tests in Tasks 5 and 10.

---

### Task 1: Config → Training tab (pure move)

**Files:** create `frontend/src/components/dialogs/ConfigTrainingTab.vue`, `frontend/src/components/dialogs/ModelStatusRow.vue` (the row markup both tabs render); modify `ConfigDialog.vue` (`type TabKey = 'directories' | 'models' | 'training' | 'i2v' | 't2i'`, a "Training" tab button right after Models), `ConfigModelsTab.vue`.

**Interfaces:** Produces `ConfigTrainingTab.vue` holding, unchanged in behaviour: the Writer training group (the `writer_training` rows via `ModelStatusRow`, gate reasons + Re-check, adapter list, Train prompt writer), the Taste scorer block, and the Writers block (stats, diversity, collapse, scorecard table), with their store loads and `useWebSocket('taste' | 'training' | 'models')` listeners. Later tasks edit copy in this file.

- [ ] Extract the per-row template of `ConfigModelsTab.vue` into `ModelStatusRow.vue` (props: the model row; emits the existing download / delete / preload actions) and use it in the Models tab — no visual change.
- [ ] Move the three sections and their script state to `ConfigTrainingTab.vue`; remove `'Writer training'` from the Models tab's `groups`; Models keeps hardware, HF token, bulk row, the other groups, preload hint and VLM roles. "Download all missing" still skips `writer_training` rows.
- [ ] Replace in-app copy that points at "Models tab" for these sections with "Training tab" (`grep -rn "Models tab" frontend/src`).
- [ ] Verify: `cd frontend && npm run build` passes; `grep -n "Taste scorer\|writers-block\|Writer training" frontend/src/components/dialogs/ConfigModelsTab.vue` finds nothing.
- [ ] Commit `refactor(taste): move training sections to a Config → Training tab`.

### Task 2: Schema, v6 migration, backup, refuse-to-start, favorite-preserving upsert

**Files:** modify `metascan/core/database_sqlite.py`, `metascan/core/judgments.py`, `backend/main.py` (lifespan), `backend/dependencies.py`; test `tests/test_grades_migration.py` (new), update `tests/test_judgments_db.py` (`user_version == 6`).

**Interfaces:**
- Produces in `metascan/core/judgments.py`: `Grade`, `GRADES`, `EVENT_GRADE_PASS = "grade_pass"`, `EVENT_GRADE_FAIL = "grade_fail"`, `EVENT_GRADE_CLEAR = "grade_clear"`, `LEGACY_REJECT_EVENTS = ("pass", "unpass")`.
- Produces columns: `media_judgments.grade TEXT` (NULL / 'pass' / 'fail'), `media_tombstones.grade TEXT`, and for later tasks (created now, used in Tasks 9–10): `media_tombstones.brief TEXT`, `brief_model TEXT`, `prompt TEXT`, `model_name TEXT`, `thumb_path TEXT` (relative to the data dir); table `tombstone_vectors(tombstone_id INTEGER NOT NULL, model_key TEXT NOT NULL, embedding BLOB NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (tombstone_id, model_key))`; index `idx_media_judgments_grade ON media_judgments(grade)`.
- Produces `class MigrationError(RuntimeError)` (message is owner-facing) and `def backup_before_v6(db_path: Path, now: datetime) -> Optional[Path]` (None when skipped).
- `_insert_tombstone` copies `grade` from the judgment row.

- [ ] Write failing tests (fixture: build a v5 DB with the old schema — favorites with and without a judgment row, rejects with reasons, seen-only rows, tombstones with `is_favorite` / `passed`, legacy `favorite` / `pass` events with `score_shown`):
  - `test_v6_maps_favorites_to_pass_and_rejects_to_fail` — grades as mapped; favorite stays favorite; seen-only → `grade IS NULL`.
  - `test_v6_creates_a_judgment_row_for_a_rowless_favorite` — `grade='pass'`, the three timestamps = migration time.
  - `test_v6_keeps_timestamps_reasons_and_writes_no_events` — `seen_at/judged_at/confirmed_at`, reasons and the event count unchanged.
  - `test_v6_maps_tombstones` and `test_v6_copies_tombstone_embeddings_to_tombstone_vectors` (`model_key = embedding_model`).
  - `test_v6_backs_up_first_and_is_idempotent` — `.pre-v6-*.bak` exists; a second open changes nothing.
  - `test_v6_skips_the_backup_with_nothing_to_migrate` (no judgments, tombstones or favorites).
  - Review Focus 2: `test_v6_refuses_without_disk_space` (patch free space) and `test_v6_failure_mid_transaction_leaves_v5` (inject an exception after the first UPDATE) — `MigrationError`, `user_version == 5`, data untouched; the next open migrates.
  - `test_upsert_preserves_an_existing_favorite` — `save_media(Media(is_favorite=False))` on a favorited path keeps ★; a new row may be inserted as favorite only through the ★ paths (assert `save_media` of a new path never sets ★).
  - `test_lifespan_stops_on_migration_error` — startup raises with the owner-facing message (no per-request 500s).
- [ ] Run `venv-311/bin/pytest tests/test_grades_migration.py -v` — FAIL.
- [ ] Implement: schema additions; the v6 block after v5 (backup with a free-space check of db size × 1.2, then one transaction: grades, rowless favorites, tombstones, embedding copy, `user_version = 6`; one INFO line with counts); `_MEDIA_UPSERT_SQL` keeps `is_favorite` of an existing row (`is_favorite = media.is_favorite`); the lifespan opens the DB eagerly and stops on `MigrationError`. Keep the old `passed` columns for now (Task 12 drops them).
- [ ] Run the new tests and `make VENV_DIR=venv-311 quality test` — PASS (except the known failure).
- [ ] Commit `feat(grades): v6 migration to pass/fail grades with backup`.

### Task 3: Judgment write/read model and API

**Files:** modify `metascan/core/judgments.py` (`JudgmentState`), `metascan/core/database_sqlite.py` (`_read_judgment`, `_apply_verdict` → grade logic, `set_reason`, `mark_seen`, `set_favorite`, `_t2i_image_row`, `list_t2i_images`, `list_i2v_videos`), `backend/services/judgments_service.py`, `backend/api/judgments.py`, `backend/api/media.py`, `backend/services/media_service.py`; frontend minimal wiring: `types/judgments.ts`, `api/judgments.ts`, `MediaViewer.vue`, `RatingRail.vue`, `stores/t2i.ts`, `stores/i2v.ts`, `types/t2i.ts`; tests `tests/test_judgments_db.py`, `tests/test_judgments_api.py`.

**Interfaces:**
- `JudgmentState(file_path, favorite: bool, grade: Optional[Grade], reasons, seen_at, judged_at, confirmed_at, categories)`; `to_dict()` keys exactly `file_path, favorite, grade, reasons, seen_at, judged_at, confirmed_at, categories`.
- `DatabaseManager.set_grade(file_path: str, grade: Optional[Grade], score_shown: Optional[float] = None, now: Optional[datetime] = None, sticky_category_id: Optional[int] = None) -> Optional[JudgmentState]` — same grade again = refresh `confirmed_at` only; a change or clear logs `grade_pass` / `grade_fail` / `grade_clear` (with `score_shown`), clears reasons, sets `judged_at = confirmed_at = now`, `seen_at = COALESCE(seen_at, now)`; sticky category applied on a non-null grade.
- `DatabaseManager.set_favorite(file_path, is_favorite: bool) -> bool` — ★ only, logs `favorite` / `unfavorite`, never touches grade or reasons.
- `set_reason` requires a grade → raises the existing "no verdict" error type, surfaced as 409 `no_grade`.
- `mark_seen` records `seen` the first time only; **no `reconfirm`** any more.
- Interim (removed in Task 12): `set_grade` keeps `media_judgments.passed = (grade == 'fail')`; `set_verdict` becomes a test-only shim — `'favorite'` → favorite on + grade pass, `'pass'` → favorite off + grade fail, `None` → favorite off + grade cleared.
- `JudgmentsService.set_grade(...)`, `JudgmentsService.set_favorite(path, on) -> Optional[JudgmentState]`.
- `PATCH /api/judgments/{path}` body: exactly one of `{grade: "pass"|"fail"|null, score_shown?, sticky_category_id?}`, `{favorite: bool}`, `{reason, on, score_shown?}`; anything else 422 (the old `verdict` field is gone). `GET` returns `favorite` and `grade`.
- Legacy `PATCH /api/media/{path}` `{is_favorite}` → `set_favorite` only, no scorecard hook.
- T2I / I2V list rows gain `grade` (keep `passed` until Task 12).
- Frontend: `type Grade = 'pass' | 'fail'`; `setGrade(path, grade, opts)`, `setFavorite(path, on)` in `api/judgments.ts`; the viewer's F calls `setFavorite`, X toggles grade `fail` (P arrives in Task 11); the T2I star calls `setFavorite` (no `score_shown`, clears nothing else).

- [ ] Failing tests: `test_grade_and_favorite_are_independent` (★ then ✗ keeps ★; ✓ never sets ★); `test_grade_switch_clears_reasons_and_logs_in_order` (✓ + reason, then ✗ → reasons `[]`, events `[grade_pass, reason_on, grade_fail]`); Review Focus 3 `test_rapid_grade_sequences_end_consistent` (P,X,X,F,P → final ★ on, ✓, events in order); `test_reason_without_grade_is_no_grade` (API 409 `no_grade`); `test_viewing_never_reconfirms` (seen twice a day apart → one `seen`, `confirmed_at` unchanged); `test_legacy_favorite_patch_touches_favorite_only` (on a ✗ image: grade stays ✗, reasons kept, scorecard hook not called); `test_patch_rejects_old_verdict_field` (422); `test_sticky_category_applies_on_grade_not_favorite`.
- [ ] Run them — FAIL. Implement. Update existing tests in these two files to the new API; keep other files' tests green through the shim.
- [ ] Gates + `npm run build` — PASS. Commit `feat(grades): independent favorite and pass/fail grade API`.

### Task 4: Full-clean snapshot v2 and legacy translation

**Files:** modify `metascan/core/database_sqlite.py` (`export_taste_state`, `restore_taste_state`, `_has_newer_judgment`, `_put_back_judgment`), `backend/services/taste_snapshot.py` (`_valid_judgment`, `_valid_shape`, load); tests `tests/test_taste_snapshot_db.py`, `tests/test_taste_snapshot_pending.py`.

**Interfaces:** `export_taste_state()` → `{"version": 2, "taken_at", "judgments": [{file_path, grade, reasons, seen_at, judged_at, confirmed_at}], "categories": [...as today], "favorites": [...]}`. `def upgrade_legacy_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]` in `taste_snapshot.py`: a snapshot without `version` → v2 (`passed = 1` → `grade 'fail'`; a path in `favorites` with `passed = 0` → `grade 'pass'`). Validation accepts both shapes. `_has_newer_judgment` = grade, favorite, reasons or `judged_at` present. Restore writes `grade` and ★ (raw UPDATE), no events.

- [ ] Failing tests: Review Focus 1 `test_legacy_pending_snapshot_restores_with_old_meaning` (legacy file on disk at startup apply → ✗ for rejects, ★ + ✓ for favorites, file consumed, nothing quarantined); `test_v2_snapshot_round_trips`; `test_restore_never_overwrites_a_newer_grade`; `test_bad_v2_grade_value_is_quarantined`.
- [ ] Run — FAIL. Implement. Gates — PASS. Commit `feat(grades): versioned full-clean snapshot with legacy translation`.

### Task 5: Taste scorer on grades

**Files:** modify `metascan/core/taste_labels.py`, `metascan/core/database_sqlite.py` (`taste_training_raw`, `taste_label_counts_raw`, `count_training_events_since`, `taste_review_queue`, `taste_models` gains `labels_version INTEGER NOT NULL DEFAULT 1`), `metascan/core/taste_runner.py` + `metascan/workers/taste_worker.py` (`LABELS_VERSION = 2` both; drop the tombstone by-path vector fallback), `backend/services/taste_service.py` (an active model with `labels_version < LABELS_VERSION` is due, scores still served), `ConfigTrainingTab.vue` (Taste scorer copy: "✓ pass / ✗ fail", missing "N more ✓ / ✗"); tests `tests/test_taste_labels.py`, `tests/test_taste_retrain_loop.py`, `tests/test_taste_api.py`, `tests/test_taste_worker.py` (or the file covering `_training_matrix`), fixture `tests/_taste_service_fixtures.py`.

**Interfaces:** `_label_for(row) -> Optional[int]`: grade `'pass'` → 1, `'fail'` → 0, else None. Rated-at = `COALESCE(confirmed_at, judged_at)`. `score_shown` ×0.5 from the last grade event, where for a migrated grade the last legacy `favorite` (for ✓) or `pass` (for ✗) event counts. Batch pairs ✓ vs ✗. `TRAINING_EVENT_KINDS = ("grade_pass", "grade_fail", "grade_clear", "reason_on", "reason_off", "category_on", "category_off")`, images only. Review queue = scored images with `grade IS NULL`.

- [ ] Failing tests: `test_only_grades_are_labels` (★ alone, seen-only, ★ + seen → excluded; ✓ → 1; ✗ → 0; ★ + ✗ → 0); `test_migrated_grade_keeps_historical_score_shown_halving`; `test_favorite_events_do_not_count_toward_retrain` and `test_video_events_do_not_count`; `test_model_with_old_labels_version_is_due`; `test_review_queue_is_ungraded_images`; Review Focus 5 `test_worker_never_borrows_a_vector_by_path` (tombstone without its own vector for the index model + a live file at the same path → the tombstone row has no vector).
- [ ] Run — FAIL. Implement. Gates + build — PASS. Commit `feat(grades): taste scorer learns pass vs fail only`.

### Task 6: Writer dataset, hindsight briefs job, base+taste examples on grades

**Files:** modify `metascan/core/writer_dataset.py`, `metascan/core/database_sqlite.py` (`writer_dataset_raw`, `favorite_prompt_candidates` → `pass_prompt_candidates`), `backend/services/taste_examples_service.py`, `backend/services/hindsight_briefs.py`, `WriterTrainingDialog.vue` (copy `Hindsight briefs (passed)`); tests `tests/test_writer_dataset.py`, `tests/test_writer_dataset_parity.py`, `tests/test_taste_examples.py`, `tests/test_hindsight_briefs.py`.

**Interfaces:** winner variant = at least one image with grade `'pass'` (writer not `fallback`); loser variant = every image grade `'fail'`; other variants neither. An edit pair needs at least one ✓ image of the edited variant. Hindsight target = live ✓ image (tombstoned ✓ arrives in Task 9). `DatabaseManager.pass_prompt_candidates(...)` replaces `favorite_prompt_candidates(...)` with the same parameters and return shape, selecting ✓ images; keep it on covering indexes (extend `idx_media_summary_added`/`_modified` per CLAUDE.md if the SELECT gains a column; use `idx_media_judgments_grade`).

- [ ] Failing tests: `test_loser_needs_every_image_failed` (seen-only and ungraded variants are neither); `test_favorite_alone_makes_no_winner`; `test_edit_counts_only_with_a_pass`; `test_hindsight_uses_passed_images`; `test_examples_are_passed_prompts`; `test_briefs_job_picks_passed_images_without_a_brief`.
- [ ] Run — FAIL. Implement. Gates + build — PASS. Commit `feat(grades): writer training data from pass/fail grades`.

### Task 7: Writer stats, diversity and collapse warning on grades

**Files:** modify `metascan/core/database_sqlite.py` (`generation_outcomes`, `writer_window_stats`), `metascan/core/taste_labels.py` (`_pooled_t2i_rates`, `collapse_warning`), `metascan/core/taste_runner.py` (`_diversity_task`: ✓ T2I generations), `backend/services/taste_service.py` (`writer_stats`), `frontend/src/types/taste.ts`, `ConfigTrainingTab.vue` (Writers block: "pass rate", "graded"); tests `tests/test_writer_stats.py`.

**Interfaces:** `GET /api/taste/writer-stats` windows rows carry `n_graded`, `n_pass`, `rate = n_pass / n_graded` (replacing `n_seen`, `n_fav`); diversity is over ✓ T2I generations; collapse uses the pooled T2I pass rate. Tombstones and the scorecard exclusion work as today.

- [ ] Failing tests: `test_rate_is_pass_over_graded` (seen-only and ★-only excluded from both counts; ★ + ✗ counts as fail); `test_diversity_uses_passed_generations`; `test_collapse_reads_pass_rate`.
- [ ] Run — FAIL. Implement. Gates + build — PASS. Commit `feat(grades): writer stats report pass rates`.

### Task 8: Scorecard on grades

**Files:** modify `backend/services/scorecard.py` (`_judged`, `compute_results`, `scorecard_summary`, promotion), `metascan/core/database_sqlite.py` (`scorecard_image_rows` also returns tombstoned scorecard images from lineage when the path is not live; `scorecard_for_path`; `_t2i_image_row.scorecard_blind`), `metascan/core/scorecard_blind.py`, frontend `types/writers.ts` (`favText` → `passText`), `types/t2i.ts` (`t2iBlind` by grade), `MediaViewer.vue` (`isBlind` by grade), `MetadataPanel.vue` + blind chip copy `(P or X)`, `WriterTrainingDialog.vue`, `ConfigTrainingTab.vue` (scorecard table "pass"); tests `tests/test_scorecard.py`.

**Interfaces:** judged = grade in `GRADES`; results `{adapter_id, n_judged, pass_base, pass_adapter, pass_rate_base, pass_rate_adapter, mean_score_base, mean_score_adapter}`; summary base `pass_rate`; blind = scorecard image AND `grade IS NULL` (★ never reveals); results and promotion count tombstoned scorecard images (grade from the tombstone, writer / variant / brief from its lineage).

- [ ] Failing tests: `test_favorite_neither_judges_nor_reveals`; `test_results_are_pass_rates`; `test_deleted_judged_images_still_count` (judge 30 pairs, delete some → `n_judged` unchanged, still ready); `test_blind_payloads_lift_on_grade_only`.
- [ ] Run — FAIL. Implement. Gates + build — PASS. Commit `feat(grades): scorecard judged by pass/fail, counts deleted images`.

### Task 9: Deleting keeps the signal (brief, library prompt, archived thumbnail)

**Files:** create `metascan/core/training_archive.py`; modify `metascan/core/database_sqlite.py` (`_write_tombstones` / `_insert_tombstone` take thumbnails and copy brief + library prompt; `writer_dataset_raw` includes tombstoned ✓ hindsight; `set_tombstone_brief`), `backend/services/media_service.py` (`delete_media`), `backend/api/duplicates.py`, `backend/services/t2i_service.py`, the I2V delete service, `metascan/core/writer_dataset.py` (tombstoned ✓ hindsight), `backend/services/hindsight_briefs.py` (✓ tombstones with a thumbnail and no brief); tests `tests/test_training_archive.py` (new), `tests/test_tombstones_db.py`, `tests/test_writer_dataset.py`, `tests/test_hindsight_briefs.py`.

**Interfaces:**
- `ARCHIVE_MAX_PX = 512`; `def archive_thumbnail(src: Path, archive_dir: Path) -> Optional[Path]` — EXIF-transposed, RGBA flattened on white, longest side ≤ 512, JPEG q85, file named `<uuid4 hex>.jpg`; returns None (logged, never raises) when the source can't be read.
- `DatabaseManager.delete_media(file_path: Path, thumb_path: Optional[str] = None) -> bool` (and the purge / batch paths take `thumbs: Mapping[str, str]` of posix path → relative thumb path); tombstones store `thumb_path`, `brief` / `brief_model` (from `hindsight_briefs` before the cascade), and for a ✓ library item `prompt` / `model_name`.
- `DatabaseManager.set_tombstone_brief(tombstone_id: int, brief: str, model: str) -> bool`.
- Delete order in every service: (1) if the item is a graded image, `archive_thumbnail` from the live file; (2) trash the file; (3) DB delete with the thumb path. If the trash step fails, remove the archived file and keep the row.

- [ ] Failing tests: `test_archive_is_at_most_512_and_jpeg`; Review Focus 4 `test_delete_with_unreadable_file_still_tombstones` (no thumbnail, tombstone written); `test_thumbnail_is_made_before_trash` (spy order); `test_only_graded_images_are_archived` (ungraded, ★-only, video → no archive); `test_deleted_pass_keeps_its_brief_and_library_prompt`; `test_dataset_uses_tombstoned_pass_hindsight`; `test_briefs_job_briefs_a_deleted_pass_from_its_thumbnail` (fake VLM).
- [ ] Run — FAIL. Implement. Gates + build — PASS. Commit `feat(grades): deleted graded images keep brief, prompt and thumbnail`.

### Task 10: Per-model tombstone vectors and the re-embedder

**Files:** create `backend/services/tombstone_reembed.py`; modify `metascan/core/database_sqlite.py` (`tombstones_needing_vectors`, `add_tombstone_vectors`; `taste_training_raw` reads `tombstone_vectors` for the index's model key; the delete-time capture and `backfill_tombstone_embeddings` write `tombstone_vectors`), `backend/services/judgments_service.py` (backfill skips a path that has a live media row), `backend/services/taste_service.py` (`mark_due(reason: str) -> None`; status gains `reembed: {running, done, total} | null`), `backend/main.py` (construct after the taste service; chain into the embedding-complete hook; notify once at startup), `ConfigTrainingTab.vue` (status line `re-embedding deleted images, n / N`); tests `tests/test_tombstone_reembed.py` (new), `tests/test_tombstone_backfill.py`.

**Interfaces:**
- `DatabaseManager.tombstones_needing_vectors(model_key: str) -> List[Tuple[int, str]]` — graded image tombstones with a `thumb_path` and no `tombstone_vectors` row for `model_key`; `add_tombstone_vectors(rows: List[Tuple[int, str, bytes]]) -> int` (INSERT OR IGNORE).
- `class TombstoneReembedder(db, client_factory, model_key_provider, blocked: Callable[[], bool], on_done: Callable[[int], None])` with `notify() -> None`, `status() -> Optional[Dict[str, int | bool]]`, `aclose() -> None`. It starts CLIP via `client.ensure_started(model_key)` and calls `encode_image(path)` per item; `blocked()` = `generation_busy(t2i_runner, comfy_client) or gpu_reserved()`, checked before start and between items (stop, resume on the next notify); `on_done(n_added)` → `taste_service.mark_due("reembed")` when n > 0. Single-flight; never runs while a T2I batch / ComfyUI job is in flight or training holds the lock.

- [ ] Failing tests (fake inference client): `test_reembeds_graded_tombstones_for_the_current_model`; `test_stops_when_generation_starts_and_resumes_later`; `test_never_runs_while_training_holds_the_lock`; `test_marks_the_scorer_due_when_vectors_were_added`; Review Focus 5 `test_backfill_skips_a_reused_path`; `test_labels_use_only_the_current_models_vectors`.
- [ ] Run — FAIL. Implement. Gates + build — PASS. Commit `feat(grades): per-model tombstone vectors and background re-embed`.

### Task 11: Rating surfaces in the frontend

**Files:** modify `MediaViewer.vue` (P key, help overlay rows F / P / X, `Press P or X first`), `RatingRail.vue` (★ Favorite toggle; ✓ Pass / ✗ Fail / No grade; `worked on…` / `missed on…`), `ThumbnailCard.vue`, generation `ThumbTile.vue`, the I2V tile (read-only ✓ / ✗ corner badge from `grade`), `SlideshowViewer.vue` / `MobileMediaViewer.vue` / `I2VDialog.vue` (★ = favorite only), `types/folders.ts` + `stores/folders.ts` (`RuleField 'grade'`: pass / fail / none; evaluator; `FIELD_DEFS.grade`), `ScanDialog.vue` copy (favorites and grades preserved), score badge tooltip `chance you'd pass this`, review-mode copy; library list payloads carry `grade` (backend: `media_to_summary_dict` / `get_all_media_summaries`, extending both covering indexes with `grade` if it is read from `media_judgments` via the summary query — or a separate grades fetch; pick one and keep `/api/media` fast).

**Interfaces:** consumes Task 3's `setGrade` / `setFavorite`; tiles read `grade` from the payloads.

- [ ] Implement; verify `npm run build`; live check in an isolated worktree (isolated data dir, ports other than 8700 / 5173, invented images): F / P / X / 1–6 keyboard-only in the viewer, badges in the library, T2I and I2V grids, the `grade` smart-folder rule. Stop only processes you started.
- [ ] Commit `feat(grades): pass/fail in the viewer, rail, tiles and smart folders`.

### Task 12: Remove the interim shims, drop legacy columns, guard tests, docs

**Files:** modify `metascan/core/database_sqlite.py` (remove `set_verdict`, stop writing `passed`; the v6 block also drops `media_judgments.passed` and `media_tombstones.passed` with `ALTER TABLE … DROP COLUMN` — if the runtime SQLite is older than 3.35 leave the column unwritten and unread), `metascan/core/judgments.py` (remove `Verdict`), T2I / I2V payloads drop `passed`, tests that still use the shim; docs: `CLAUDE.md` (judgments and tombstones, taste scorer, writer stats, WS notes, `user_version` 6, covering-index note), `.claude/rules/writer-training.md`, `.claude/rules/t2i.md`, `.claude/rules/i2v.md`, `docs/features.md` (rating + shortcut table with F / P / X / 1–6 / C / R), `docs/writer-training.md`, `docs/t2i.md`, `README.md`; test `tests/test_grades_guard.py` (new).

- [ ] Failing guard tests: `test_no_live_code_reads_passed` (grep `metascan/` and `backend/` for `passed` outside the migration and `upgrade_legacy_snapshot`); `test_no_code_treats_pass_event_as_grade` (no reader maps event `"pass"` to ✓ outside the score_shown legacy mapping); `test_v6_drops_legacy_columns` (on a fresh v5 fixture).
- [ ] Run — FAIL. Remove shims, update remaining tests to `set_grade` / `set_favorite`, write the docs. Gates + build — PASS.
- [ ] Commit `refactor(grades): drop legacy verdict shims; docs`.
