# Pass / fail grades: separating favorites from training signals

Date: 2026-10-24. Status: approved by the owner (2026-10-24), with the
Training tab added at their request.

This changes the rating model built in taste-learning projects 1–4
(`docs/superpowers/specs/2026-10-22-taste-learning-design.md`, §1 rating
capture, §2 taste scorer, project 4 data rules) and the code that grew from
it. Where this document and that one disagree, this one wins.

## Why

Today one marker does two jobs. **Favorite** used to mean only "I want to
look at this one more often"; projects 1–4 also made it the main positive
training signal. **Pass** (X) means "not for me" — a reject. On top of that,
several signals are implicit and invisible: merely *viewing* an image for a
second counts as a negative, any edited prompt counts as "my text beats the
writer" even when it was an experiment, and whether a T2I image belongs to a
"caption group" (which decides if its rating forms a training pair) is never
shown. The owner's words: the training flow and normal use "are mixed
together", it is "not clear when scoring matters", "not obvious when editing
a prompt sends a strong signal", and "not clear if an image is a part of a
caption group".

The fix, as the owner decided it:

1. **Two independent markers.** ★ **Favorite** goes back to being a pure
   "look at this again" marker that nothing learns from. A new **grade** —
   ✓ **Pass** ("the prompt did what I wanted") or ✗ **Fail** ("it didn't") —
   becomes the only training signal. Neither marker ever sets the other.
2. **Viewing teaches nothing.** Only explicit grades train anything.
3. **Deleting keeps the signal.** The owner deletes failed images as today;
   deleting a graded image must not lose what it taught.

## Goals

- Favorite and grade are separate, visible, and independent in every rating
  surface, with keyboard-only grading in the viewer.
- Every consumer that learned from favorites / passes / views learns from
  grades instead, and only from grades.
- A one-time migration that turns today's ratings into grades without
  inverting, losing, or re-timing any of them.
- Deleting a graded image keeps its training value for the taste scorer, the
  writer dataset, hindsight briefs and the scorecard — including across a
  future change of CLIP model.
- After the change, everyday use is "safe by construction": nothing done
  while just using the app (viewing, favoriting, organizing, experimenting
  with a prompt) can teach the writer or the scorer something the owner did
  not mean.

## Non-goals

- Compare sessions (a dedicated side-by-side A/B training mode). Discussed
  and wanted later; a separate project.
- Hiding or auto-cleaning failed images. The owner said: "No need to do
  anything with an X image." No hide filter, no cleanup action, no grace
  period.
- Changing the 180-day half-life, the readiness thresholds (100 pairs / 200
  new), the 20 + 20 scorer minimum, or the 30-pair scorecard promotion.
- Rating on the mobile viewer or in the slideshow (they keep their ★ only).
- Video-based training. Videos keep collecting grades (the owner wants that
  data for later) but no new consumer reads them.
- Tiered ratings (S/A–D). Undecided by the owner; not part of this.

## Anti-goals (failure even if it "works")

- Any historical rating read with the wrong meaning. "pass" means *reject*
  in every existing column, event and snapshot file; a single place that
  reads an old "pass" as the new ✓ inverts the owner's library.
- Any rating lost or re-timed by the migration (decay clocks restarting).
- A favorite, a view, or a mouse-free browsing session changing any model.
- Breaking keyboard-only rating in the viewer.
- Deleting a graded image silently dropping it from training or from a
  scorecard the owner already judged.

## The model

| Marker | Values | Meaning | Set by | Read by |
|---|---|---|---|---|
| ★ Favorite | on / off | "I want to look at this again" | F in viewers, ★ on tiles, slideshow, mobile | view features only: favorites filter, smart-folder `favorite` rule, ★ display |
| Grade | ✓ pass / ✗ fail / none | "the prompt did / didn't do what I wanted" | P / X in the viewer (and the rail's buttons) | every training and measurement consumer |
| Reasons 1–6 | realism, composition, emotion, light, concept, motion | what worked (✓) or missed (✗) | 1–6 in the viewer | as today (require a grade) |
| Seen | timestamp | the viewer showed it for ≥ 1 s | dwell timer, as today | the rail's "● seen" chip only — never a label |

Rules:

- Favorite and grade are fully independent going forward: ★ alone carries no
  grade; ★ with ✗ is allowed; grading never changes ★.
- Pressing the key of the grade already set clears the grade (toggle, like
  today's verdict keys). Switching ✓ ↔ ✗, or clearing, clears the reasons
  (they described the other grade), as today's verdict change does.
- Reasons need a grade (409 `no_grade`, replacing `no_verdict`); the rail's
  reasons header reads "worked on…" for ✓ and "missed on…" for ✗.
- Sticky categories (Shift+Enter in the category picker) are applied on
  every **grade**, not on favorite. *(My call — sticky exists for "tag
  everything I judge in this run".)*

## Rating surfaces (frontend)

- **MediaViewer keys:** F toggles ★ only; **P** toggles ✓ (P is free today);
  X toggles ✗ (same key as today's reject, so muscle memory carries over);
  1–6 reasons (hint "Press P or X first"); C categories; R review mode;
  everything else unchanged. Help overlay (H / ?) and
  `docs/features.md`'s shortcut table updated.
- **RatingRail:** ★ Favorite as its own toggle; ✓ Pass / ✗ Fail / "No grade"
  replacing ★ Favorite / ✕ Pass / "No verdict"; reasons header as above;
  "● seen" stays.
- **Tiles** (library `ThumbnailCard`, T2I `ThumbTile`, I2V tiles): ★ stays
  and toggles favorite only. *(My call:)* a small read-only ✓ / ✗ corner badge
  shows the grade, so grades are visible in grids — the owner's complaint
  was that signals are invisible. Grading itself happens in the viewer.
- **T2I store:** the star no longer calls the verdict API with
  `score_shown`, and un-starring no longer clears anything but ★.
- **Slideshow F, mobile ★, I2V ★, library ★:** favorite only. They must no
  longer flip a grade, clear reasons, or reach the scorecard.
- **Smart folders:** the `favorite` rule stays; *(my call:)* add a `grade`
  rule (pass / fail / none), so "all ✗" is one folder away when the owner
  deletes failures.
- **Scorecard blinding** (viewer `isBlind`, `t2iBlind`, MetadataPanel note,
  blind chip): a hidden scorecard image is revealed by a grade only; ★ never
  reveals it (otherwise the writer shows before the grade). Copy: "(P or X)".
- **Copy that changes:** writer menu `NN% fav` → `NN% pass`, Base `baseline ·
  NN% pass`; training window and Writers dashboard rates → pass rate, judged
  = graded; "Hindsight briefs (favorites)" → "(passed)"; ScanDialog's
  "Ratings (favorite/pass) … preserved" → favorites and grades; score-badge
  tooltip → "chance you'd pass this"; taste status "needs N more" counts ✓
  and ✗.

## Config → Training tab (owner's request)

The training sections move out of Config → Models into a new **Training**
tab of the Config dialog, placed right after Models *(order: my call)*:

- the **Writer training** group: the "Writer training base" and "llama.cpp
  LoRA converter" rows (download / delete / progress / release note), the
  training check with its reasons and Re-check, the adapter list and the
  **Train prompt writer** button;
- the **Taste scorer** block (status, retrain, missing counts);
- the **Writers** block (pass-rate windows, diversity, collapse warning, the
  scorecard table).

The Models tab keeps model downloads, the hardware and HF token blocks,
preload settings and the VLM roles (writer / general model choice, which is
model configuration, not training). Behaviour does not change by moving:
same stores, same WebSocket listeners, same actions — and "Download all
missing" keeps skipping the writer-training rows. Any copy that says "Models
tab" for these sections (docs, help text, the training window's own
references) says "Training tab". *(My call:)* split the code into a
`ConfigTrainingTab.vue` rather than leave the sections in `ConfigModelsTab.vue`
behind a flag.

## API

- `PATCH /api/judgments/{path}` takes exactly one of `{grade: "pass" |
  "fail" | null, score_shown?}`, `{favorite: bool}`, or `{reason, on}`. The
  old `verdict` field is removed (the app's own frontend is the only client;
  sending it is a 422). Sticky categories ride `grade` as `verdict` did.
- `GET /api/judgments/{path}` returns `favorite` and `grade` (plus reasons,
  seen_at, judged_at, the batch position and the scorecard blind flag, as
  today).
- `POST /api/judgments/seen` unchanged on the wire, but it writes nothing a
  model reads (see "Seen" below).
- Legacy `PATCH /api/media/{path}` `{is_favorite}` stays and touches ★ only:
  no grade change, no reasons cleared, no events other than favorite /
  unfavorite, no scorecard hook.
- T2I / I2V list payloads replace `passed` with `grade` (keep `is_favorite`).

## Storage and names

**Never reuse the words `pass` or `verdict` for the new ✓.** Old data uses
`pass`, `passed` and `unpass` for *reject*; `verdict` is today's
favorite-or-pass and is also used by `training_manager` for run outcomes.

- `media_judgments` gains `grade TEXT` (`'pass'` / `'fail'` / NULL).
  `judged_at` becomes "when the grade was last set"; `confirmed_at` as today.
  The old `passed` column is dropped by the migration (or kept renamed
  `legacy_reject` — builder's choice); after the migration **no live code
  reads it**, and a test guards that.
- `media.is_favorite` stays the authoritative favorite.
- `media_tombstones` gains `grade`; `is_favorite` stays (view info); its
  `passed` column is migrated and dropped (same rule). Tombstones also gain
  what deleted items need for training (see "Deleting keeps the signal").
- New events: `grade_pass`, `grade_fail`, `grade_clear` (with
  `score_shown` where the client sends it). `favorite` / `unfavorite` keep
  being logged but are view events. Historical `pass` / `unpass` events stay
  in the append-only log untouched and keep meaning reject.

## Migration (`PRAGMA user_version` 5 → 6)

One gated, idempotent migration in `_init_database`, after the v5 block:

1. **Back up the database first** *(my call)*: a consistent copy via the
   SQLite backup API to `<db dir>/metascan.db.pre-v6-<timestamp>.bak`,
   logged with its path. Check free disk space (at least the database's
   size plus a margin) before copying. A database with nothing to migrate
   (no judgment rows, tombstones or favorites) skips the backup. The owner
   deletes the file when satisfied.
   **If the backup or the migration fails, the server refuses to start**
   *(my call)* with one clear message naming the cause and what to do (e.g.
   free N GB on the drive). There is no degraded mode: running new code on a
   v5 database would let fresh grades be overwritten when the migration
   later runs — the "rating lost" anti-goal. The database manager is created
   lazily (`backend/dependencies.py`), so the lifespan must open it at
   startup and stop on failure rather than surface 500s per request. Worker
   subprocesses that open their own database manager (e.g. the embedding
   worker) are only started by a running server, so they always see v6.
2. In one transaction:
   - every judgment row with `passed = 1` → `grade = 'fail'`;
   - every `media.is_favorite = 1` → `grade = 'pass'` on its judgment row;
     a favorite with no judgment row (possible — see "Bugs fixed on the
     way") gets one with `grade = 'pass'` and `seen_at` / `judged_at` /
     `confirmed_at` = migration time;
   - every other row → `grade` NULL (seen-only rows stay seen-only);
   - reasons stay where they are; `seen_at`, `judged_at`, `confirmed_at`
     are not touched (decay clocks continue);
   - tombstones the same way (`passed = 1` → fail, `is_favorite = 1` → pass);
   - `passed` columns dropped / renamed as above;
   - `user_version = 6`.
3. **No synthetic events.** (Writing them would become the newest verdict
   event and erase the historical `score_shown` halving, would trip the
   25-event retrain threshold at once, and would be ignored for tombstones.)
   Instead the active taste model trained under the old labels is treated as
   due for retraining (e.g. bump `LABELS_VERSION` in runner and worker and
   treat models of an older labels version as due). Its scores stay served
   until the retrain lands.
4. **Historical `score_shown` keeps working:** for a migrated grade, the
   halving that applied to the old verdict (from its last `favorite` or
   `pass` event) keeps applying; new grade events carry their own.
5. Log one INFO line with the counts (favorites → ✓, rejects → ✗, rows
   created) — counts only.

Readiness and data counts change because losers now need ✗; the owner has
generated no T2I since project 3, so no pairs are lost in practice. The
owner's scorecards migrate exactly: a scorecard image counted as judged only
with an explicit favorite or pass, and those become ✓ / ✗.

### Pending full-clean snapshot

`taste_snapshot_before_full_clean.json` (written before a Full clean &
rescan, applied at startup and after scans, after migrations) has no version
key and old semantics. New snapshots carry `"version": 2` and each
judgment's `grade` (plus the favorites list as today). A version-less file
is legacy: on read, a `passed = 1` judgment restores as ✗, a path in the
favorites list restores ★ and ✓. Validation accepts both shapes, so a legacy
pending file is restored, never quarantined for its shape. `_has_newer_judgment`
counts a grade, a favorite, reasons or `judged_at` as newer data.

## Taste scorer

- Labels: ✓ → 1, ✗ → 0, no grade → excluded (views and favorites count for
  nothing). Within-T2I-batch pairs are ✓ vs ✗. Weight `0.5 ** (age /
  half_life)` from `COALESCE(confirmed_at, judged_at)`; the `score_shown`
  ×0.5 applies to the last grade event as above.
- Minimum stays 20 ✓ + 20 ✗; status reports what is missing in grade terms.
  If the owner's library has fewer than 20 rejects today, the scorer stays
  "missing" until it has 20 ✗ — expected and acceptable.
- **Reconfirm-on-view is removed** *(my call)*: viewing a graded image a day
  later no longer refreshes its decay clock, because viewing must teach
  nothing. Re-grading does.
- `TRAINING_EVENT_KINDS` (what counts toward the 25-event retrain): grade
  events, reasons and categories — not favorites. *(My call:)* count image
  events only (today video events count too, though the scorer is
  images-only).
- Review mode (R) queue: scored images with **no grade**, nearest 0.5 first
  (today it excludes anything seen).
- The score now means "chance you'd pass this". The gold outline on the two
  highest-scored T2I tiles is unchanged mechanically.

## Writer training and measurement

- **Variant preference pairs:** within a caption group, a winner variant has
  at least one ✓ image; a loser variant has every image ✗. Other variants are
  neither.
- **Edit pairs:** an edited render counts only when at least one of its
  images is ✓ (chosen = the edited prompt, rejected = the draft, as today).
- **Hindsight examples:** ✓ images with a known prompt and a brief (live, or
  deleted — see below). The briefs job picks ✓ images without a brief.
- **Base + taste examples** (`favorite_prompt_candidates`): ✓ prompts. Keep
  the query fast; it currently forces `INDEXED BY idx_media_summary_added`,
  which covers `is_favorite` — adjust the query/indexes together and keep
  the covering-index rule in CLAUDE.md.
- **Writer stats** (`generation_outcomes`, `writer_window_stats`): pass rate
  = ✓ / (✓ + ✗) among graded generations per window (T2I and I2V); the
  denominator is graded, not seen. Favorite diversity becomes the diversity
  of ✓ T2I generations; the collapse warning reads the pooled T2I pass rate.
- **Scorecard:** an image is judged when it has a grade; a pair counts when
  both images are graded; results are pass rates (`pass_rate_base`,
  `pass_rate_adapter`, keep `n_judged`, mean score); promotion at 30 judged
  pairs as today. Results and promotion also count **deleted** scorecard
  images (from their tombstones), so deleting after judging changes nothing.
- Adapters trained before this change stay usable; nothing is retrained
  automatically.

## Deleting keeps the signal

Deletion stays exactly as the owner uses it today (file to the OS trash,
media row removed, tombstone written). What changes is what the tombstone
keeps and who reads it. Applies to every delete path (library delete,
duplicates delete, T2I / I2V delete, purge); the stale-file cleanup has no
file left, so it keeps everything except the thumbnail.

1. **Grade and lineage** — the tombstone carries `grade` (and `is_favorite`,
   reasons, categories, timestamps, T2I/I2V lineage as today).
2. **Brief** — a deleted ✓ image keeps its hindsight brief (today
   `hindsight_briefs` cascades away with the media row). Copy it onto the
   tombstone or move briefs to a table that does not cascade — builder's
   choice; the writer dataset reads it for deleted ✓ images.
3. **Library prompt** — for a deleted ✓ library image (no T2I row), the
   tombstone keeps the metadata prompt and model name hindsight needs (today
   only T2I lineage survives).
4. **Archived thumbnail** — for every deleted **graded image** (✓ or ✗;
   images only, not videos), save a JPEG of at most 512 px on the longest
   side to `<data dir>/archive/thumbs/` keyed by tombstone, created from the
   live file **before** it is trashed (the library and duplicates deletes
   trash first today — reorder them). 512 px matches
   `EmbeddingManager.compute_image_embedding`'s own downsizing, so a vector
   computed from the archive is close to the original's. About 50 KB each
   (2,000 deletions ≈ 100 MB); no cap — it is training data like the
   tombstones. A failed archive write is logged and never blocks the delete.
5. **Vectors per CLIP model** — a tombstone's vector must match the current
   index's CLIP model. When the model changes (or a tombstone has no vector),
   re-embed graded tombstones from their archived thumbnails through the live
   inference worker's `encode_image` (no FAISS or DB side effects), in the
   background after an index build, resumable. **The app starts the CLIP
   inference worker itself for this** *(my call)* rather than waiting for a
   search to start it — otherwise, after a CLIP switch, the scorer sits at
   "missing (vectors)" with nothing telling the owner why. It is a GPU user
   like any other: it runs only when idle the way `AutoIndexScheduler` gates
   index builds (no T2I batch or ComfyUI job in flight), never while training
   holds the GPU lock, and it stops when either starts. When it finishes, the
   scorer is due for retraining. While it runs, the taste status says so
   ("re-embedding deleted images, n / N"). Keep vectors keyed by model (one
   per tombstone per model, or replace on change — builder's choice).
6. **Never borrow a vector by path.** The taste worker's fallback that looks
   up a tombstone's vector by its old path can attach a *new* file's vector
   to an old tombstone if the path was reused. Remove it, or make it
   provably safe; when unsure, the row has no vector.
7. **Briefs from the archive** — the briefs job may also write a brief for a
   deleted ✓ image that never got one, from its archived thumbnail.

## Bugs fixed on the way

- **The media upsert can silently clear a favorite.** `_MEDIA_UPSERT_SQL`
  writes `is_favorite = excluded.is_favorite` and `Media` defaults to False,
  so re-ingesting an existing path (e.g. `ingest_file`) resets ★ with no
  event; conversely `save_media(is_favorite=True)` creates a favorite with no
  judgment row. The upsert must never change an existing row's favorite;
  only the ★ paths do.
- **The legacy star judges scorecard images** (it flips pass → favorite,
  clears reasons and fires the scorecard hook). Fixed by "favorite only".

## Left to the builder

Column/table shapes for briefs, library prompts and per-model vectors; drop
vs rename of the old `passed` columns; how "due for retraining" is marked;
the archive's file naming; the badge's exact look; API error codes beyond
those named; ordering of the work.

## Calls I made (the owner should check these)

1. Sticky categories apply on a grade, not on ★.
2. Read-only ✓ / ✗ badges on library, T2I and I2V tiles; grading stays in the
   viewer.
3. A `grade` smart-folder rule.
4. A hidden scorecard image is revealed only by a grade, never by ★.
5. A database backup before the migration (skipped when there is nothing to
   migrate); if the backup or migration fails, the server refuses to start
   with a clear message instead of running half-migrated.
6. Reconfirm-on-view removed; re-grading refreshes the clock.
7. Retrain-event counting is images only.
8. Archived thumbnails for graded images only, 512 px, uncapped; after a CLIP
   switch the app starts CLIP itself (when idle) to re-embed them.
9. Slideshow and mobile keep ★ only.
10. The Training tab sits right after Models, as its own component.

## Testing

- Migration on a v5 fixture DB that has every case: favorites with and
  without judgment rows, rejects with reasons, seen-only rows, tombstones of
  each, legacy events with `score_shown`, a legacy pending snapshot — assert
  the mapping, untouched timestamps, no new events, `user_version = 6`, the
  backup file, and idempotence.
- A legacy (version-less) pending snapshot restores with old semantics; a v2
  one round-trips.
- Each consumer's existing tests restated in grade terms (labels, review
  queue, writer stats, diversity / collapse, dataset winners / losers /
  edits / hindsight, scorecard judged / blind / results / promotion, base +
  taste examples, briefs job), plus: favorites and views change no model
  input; deleted graded images still count everywhere; a vector is never
  attached by path; the upsert preserves ★.
- A guard test that no live code reads the old `passed` column or treats a
  `pass` event as ✓.
- Archive: created before trash, ≤ 512 px, failure doesn't block the delete;
  re-embed fills vectors for the current model only.
- `npm run build` for the frontend (no frontend unit tests exist).

## Docs to update

`CLAUDE.md` (judgments and tombstones, taste scorer, writer stats, WS notes,
`user_version` list with v6, covering-index note), `.claude/rules/writer-training.md`
(data rules, scorecard, hindsight, frontend), `.claude/rules/t2i.md`
(base + taste, `% pass`), `.claude/rules/i2v.md` (the star), `docs/features.md`
(rating and shortcuts), `docs/writer-training.md` ("What it learns from",
scorecard), `docs/t2i.md`, `README.md` rating mentions.
