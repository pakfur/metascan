# Storyboard Panel Generator — Design

**Date:** 2026-08-01
**Status:** Approved design, pending implementation plans

---

## 1. Concept

A storyboard is a hierarchy: **Storyboard → Scenes → Panels (one per shot) → candidate Images.**

The user writes a scene in loose screenplay form — subject descriptions with
names, a location, mood and lighting notes, and one or two sentences per shot
describing the keyframe. Metascan turns that into structured panel data,
synthesizes a text-to-image prompt per panel, generates several candidate
images per panel through ComfyUI, and presents a browsable storyboard where
the user drills Storyboard → Scene → Panel and picks the keeper image for
each panel.

Subject identity preservation is a *best-effort* goal, not a requirement.
Subject descriptions carried verbatim into every prompt, optional per-subject
LoRAs, and optional reference-image conditioning get identity most of the way
there; pixel-exact character consistency is explicitly out of scope.

Generated media is ordinary metascan media — indexed, thumbnailed, searchable
by CLIP and pHash — so the library tooling applies to storyboard output for
free.

---

## 2. What already exists

The feature leans heavily on infrastructure metascan already has.

| Capability | Existing implementation |
|---|---|
| LLM text generation | `VlmClient.generate_text()` over the supervised `llama-server` subprocess (`metascan/core/vlm_client.py`) |
| Per-target-model prompt dialects | `metascan/core/meta_prompt_templates.py` (`TargetModel`, `Architecture`, `ExtraOption`), plus the hand-written dialect rules in `docs/meta-prompts.md` |
| Prompt persistence + REST surface | `saved_prompts` table and `/api/prompt/*` — built deliberately decoupled from its UI |
| Structured LLM output | GBNF grammar pattern from `metascan/core/vlm_prompts.py:TAGGING_GRAMMAR` |
| Subprocess job queue + progress | `metascan/core/upscale_queue_process.py` |
| Multiplexed progress transport | `backend/ws/manager.py`, `{channel, event, data}` envelope |
| Named collections of media | `folders` / `folder_items` tables with cross-tab WS sync |
| Media ingest, thumbnails, viewer, grid | `metascan/core/scanner.py`, `metascan/cache/`, `MediaViewer`, `ThumbnailGrid` |
| Client-side routing (installed, **unwired**) | `vue-router@5` is in `frontend/package.json` but is not referenced by `main.ts` or `App.vue` |

**The one thing that does not exist is the ability to submit a job to
ComfyUI.** Every ComfyUI touchpoint in the codebase today
(`metascan/extractors/comfyui*.py`) is a read-only metadata extractor.
Building the driver is the bulk of the new backend risk, and it is why the
work is phased as it is.

Note also that `docs/future_ideas.md` describes a ComfyUI integration running
the *opposite* direction (custom nodes `FN-1`..`FN-4` pulling *from* metascan).
That is a separate, complementary integration. This design pushes *to*
ComfyUI. Neither supersedes the other.

---

## 3. Phasing

Three sub-projects, one design (this document), three implementation plans.

**Phase A — ComfyUI driver.**
`metascan/core/comfy_client.py`, `backend/api/comfy.py`, tables
`workflow_presets` and `generation_jobs`. Register a workflow preset, resolve
its bindings, submit with parameter overrides, stream progress, retrieve
outputs, ingest into the media DB. Contains no storyboard concepts and is
independently useful — it is the same primitive any future "regenerate this
image" action would call. Fully exercisable via `curl` on completion.

**Phase B — Storyboard domain.**
`metascan/core/storyboard_brief.py`, `backend/api/storyboard.py`, and the five
storyboard tables. Freeform-text parse, deterministic brief composition,
per-panel prompt synthesis, and panel-level job orchestration on top of
Phase A. Headless; fully exercisable via `curl` on completion.

**Phase C — Authoring / review UI.**
The Vue authoring view, three-level navigation, candidate picker, and the
`storyboard` WS channel consumer.

**Deferred, explicitly:** contact-sheet / PDF export, text-to-video and
animatics, multi-storyboard projects, collaborative editing, multi-reference
conditioning (more than one reference-conditioned subject per panel).

---

## 4. Data model

Seven new tables. All follow the existing conventions in
`metascan/core/database_sqlite.py`: `CREATE TABLE IF NOT EXISTS` inside
`_init_database`, synchronous access under the module `threading.Lock`,
wrapped by `asyncio.to_thread` in the service layer.

### 4.1 Phase A tables

```sql
CREATE TABLE IF NOT EXISTS workflow_presets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL CHECK(kind IN ('t2i','ref')),
    workflow_json TEXT NOT NULL,   -- API-format graph, stored verbatim
    bindings      TEXT NOT NULL,   -- resolved MS_* -> {node_id, widget} map (JSON)
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generation_jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    preset_id       INTEGER NOT NULL REFERENCES workflow_presets(id),
    panel_id        INTEGER REFERENCES panels(id) ON DELETE CASCADE,
    state           TEXT NOT NULL
                    CHECK(state IN ('queued','running','done','failed','cancelled')),
    comfy_prompt_id TEXT,
    params          TEXT NOT NULL,  -- the override set applied (JSON)
    error           TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    finished_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_generation_jobs_state ON generation_jobs(state);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_panel ON generation_jobs(panel_id);
```

`generation_jobs.panel_id` is nullable so Phase A stands alone: a job may be
submitted with no storyboard context at all.

### 4.2 Phase B tables

```sql
CREATE TABLE IF NOT EXISTS storyboards (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    source_text   TEXT,            -- original pasted text, provenance only
    aspect_ratio  TEXT NOT NULL DEFAULT '16:9',
    style_block   TEXT,            -- global look, appended to every prompt
    negative      TEXT,            -- storyboard-level negative prompt
    target_model  TEXT NOT NULL,   -- meta_prompt_templates.TargetModel
    architecture  TEXT NOT NULL,   -- meta_prompt_templates.Architecture
    preset_id     INTEGER REFERENCES workflow_presets(id),
    base_seed     INTEGER NOT NULL,
    batch_size    INTEGER NOT NULL DEFAULT 4,
    folder_id     INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS storyboard_subjects (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    storyboard_id  INTEGER NOT NULL REFERENCES storyboards(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,     -- "MAYA"
    description    TEXT NOT NULL,     -- injected verbatim into every brief
    lora_name      TEXT,
    lora_strength  REAL DEFAULT 0.8,
    reference_path TEXT REFERENCES media(file_path) ON DELETE SET NULL,
    sort_order     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS scenes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    storyboard_id INTEGER NOT NULL REFERENCES storyboards(id) ON DELETE CASCADE,
    sort_order    INTEGER NOT NULL DEFAULT 0,
    name          TEXT NOT NULL,
    location      TEXT,
    time_of_day   TEXT,
    mood          TEXT,
    lighting      TEXT,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS panels (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    scene_id           INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
    sort_order         INTEGER NOT NULL DEFAULT 0,
    shot_size          TEXT,   -- ECU|CU|MCU|MS|MLS|WS|EWS
    angle              TEXT,   -- eye|low|high|overhead|dutch|ots|pov
    lens               TEXT,   -- wide|normal|tele|macro
    action             TEXT NOT NULL,
    subject_ids        TEXT NOT NULL DEFAULT '[]',  -- ORDERED JSON array
    notes              TEXT,
    brief              TEXT,
    prompt             TEXT,
    prompt_locked      INTEGER NOT NULL DEFAULT 0,
    negative           TEXT,   -- overrides storyboards.negative when non-null
    selected_image_id  INTEGER REFERENCES panel_images(id) ON DELETE SET NULL,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS panel_images (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    panel_id        INTEGER NOT NULL REFERENCES panels(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL REFERENCES media(file_path) ON DELETE CASCADE,
    seed            INTEGER,
    variant_index   INTEGER NOT NULL DEFAULT 0,
    prompt_used     TEXT,
    preset_id       INTEGER REFERENCES workflow_presets(id),
    comfy_prompt_id TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_panel_images_panel ON panel_images(panel_id);
```

### 4.3 Design notes on the schema

**`prompt_locked`.** Once the user hand-edits a panel's prompt, re-synthesis
skips that panel. Without the flag, editing structure anywhere silently
destroys hand-tuned prompts — the single most annoying failure mode this
tool could have.

**`selected_image_id` uses `ON DELETE SET NULL`, `panel_images.file_path`
uses `ON DELETE CASCADE`.** Deleting a generated file on disk removes its
candidate row and nulls any panel that had selected it. The panel itself,
and its prompt, survive. These cascades depend on `PRAGMA foreign_keys = ON`,
which `DatabaseManager._get_connection` already sets on every connection.

`panels` forward-references `panel_images` while `panel_images` back-references
`panels`. SQLite resolves foreign keys at use time rather than at
`CREATE TABLE` time, so the circular reference is legal regardless of creation
order.

**`subject_ids` is ordered.** Index `0` is the panel's *primary subject* —
the only one that receives reference-image conditioning (see §7).

### 4.4 Hiding storyboard media from the default grid

A storyboard of 2 scenes × 6 panels × 4 candidates is 48 images. Left
visible, every run floods the library grid.

Add a `hidden INTEGER NOT NULL DEFAULT 0` column to `media`. The default
grid query gains `WHERE hidden = 0`; a filter toggle reveals hidden media.

**Per the covering-index rule in `CLAUDE.md`, `hidden` must be added to both
`idx_media_summary_added` and `idx_media_summary_modified`.** A `WHERE`-clause
column that is absent from the covering index forces SQLite back to the main
table, whose `data` JSON blob has hundreds of MB of overflow pages on large
libraries — the exact regression those indexes exist to prevent.
`_init_database` already rebuilds any index whose stored DDL is missing a
currently-required column, so extending the required-column list is
sufficient. `PRAGMA user_version` currently stands at 2; the `hidden` column
backfill (existing rows → `0`) takes version 3.

The alternative — deriving hidden-ness by joining `folder_items` — was
rejected for the same performance reason.

**Hidden lifecycle.** Every image ingested from a storyboard job is written
with `hidden = 1`. Selecting a candidate as a panel's keeper sets that file's
`hidden = 0`; selecting a different candidate re-hides the previous keeper.
The result is that the library grid shows exactly the storyboard images the
user has chosen, while every reject remains searchable, comparable, and
reachable through the storyboard's folder.

Each storyboard additionally auto-creates a `folders` row (`kind='manual'`)
and adds every generated image to it via `folder_items`, so storyboard output
is browsable through the existing folder UI. `storyboards.folder_id` holds
the link; `ON DELETE SET NULL` means deleting the folder does not delete the
storyboard.

---

## 5. Phase A — the ComfyUI driver

### 5.1 Configuration

```jsonc
"comfy": {
  "base_url": "http://127.0.0.1:8188",
  "in_flight": 2,
  "unload_vlm_during_generation": true,
  "output_root": "data/storyboards",
  "request_timeout_s": 30
}
```

### 5.2 The node-title binding contract

A workflow is registered by uploading its **API-format** JSON. Metascan walks
the graph looking for `_meta.title` values matching the `MS_*` convention and
records the resolved `{node_id, widget}` pairs in
`workflow_presets.bindings`.

| Title | Widgets injected | Required |
|---|---|---|
| `MS_POSITIVE` | `text` | yes |
| `MS_NEGATIVE` | `text` | no |
| `MS_SEED` | `seed`, else `noise_seed` | yes |
| `MS_LATENT` | `width`, `height`, `batch_size` | yes |
| `MS_SAVE` | *(none — identifies the output node whose images to collect)* | yes |
| `MS_LORA` | `lora_name`, `strength_model`, `strength_clip` | no |
| `MS_REF_IMAGE` | `image` | required when `kind='ref'` |

Title matching is exact and case-sensitive. Registration **fails loudly**,
listing every missing required title, rather than accepting a partially bound
preset. This is the one point where a bad setup must be caught — after this,
sixty jobs are already in flight.

Titles were chosen over node-id mapping because they survive re-saving the
workflow in ComfyUI (which renumbers nodes), require no mapping UI, and are
self-documenting inside ComfyUI itself. Because the workflow JSON is stored
alongside the bindings, the two can never drift apart.

Injection is a deep copy of the stored `workflow_json` with the bound widget
values overwritten — the stored preset is never mutated.

**Dimensions.** `MS_LATENT`'s `width` / `height` are not stored per preset —
they are derived from `storyboards.aspect_ratio` and `storyboards.architecture`
by a pure `bucket_dims(aspect_ratio, architecture) -> (w, h)` helper that
snaps to the architecture's supported resolutions (SDXL: 1024×1024,
1216×832, 832×1216, 1344×768, 768×1344; Flux and later architectures: nearest
multiple of 16 at a comparable pixel budget). Supported aspect ratios in v1
are `1:1`, `4:3`, `16:9`, `2.39:1`, and `9:16`. Unsupported combinations fail
at storyboard save time, not at generation time.

### 5.3 Client architecture

`ComfyClient` is an asyncio supervisor modeled directly on `InferenceClient`
(`metascan/core/inference_client.py`), constructed in the FastAPI `lifespan`
and installed as a singleton.

- **One persistent WebSocket** to `/ws?clientId=<uuid>` for the whole
  application, not one per job. Auto-reconnecting, with a `prompt_id → job`
  map maintained in memory and reconciled against `generation_jobs` on
  reconnect.
- Consumed events: `status`, `execution_start`, `executing`, `progress`,
  `executed`, `execution_cached`, `execution_error`.
- `execution_error` carries the failing node's `class_type` and the exception
  text; both go into `generation_jobs.error` verbatim. This is what turns
  "checkpoint not found" into an actionable message instead of a silent hang.
- Job state transitions are re-broadcast on the metascan `storyboard` WS
  channel as `{channel:"storyboard", event:"job_update", data:{…}}`.

### 5.4 Submission and queue discipline

Metascan maintains its **own** queue and holds at most `in_flight` jobs
inside ComfyUI at any time. Dumping all 48 jobs into ComfyUI's FIFO would
make "reroll panel 4 right now" impossible to prioritize and cancellation
coarse.

- Submit: `POST /prompt` with `{prompt: <mutated graph>, client_id}` →
  `prompt_id`.
- Cancel pending: `POST /queue` with `{delete: [prompt_id, …]}`.
- Cancel running: `POST /interrupt`.
- A reroll requested by the user is inserted at the head of the metascan-side
  queue.

### 5.5 Output retrieval and ingest

On job completion:

1. `GET /history/{prompt_id}` → `outputs[<MS_SAVE node id>].images[]`.
2. For each entry, `GET /view?filename=…&subfolder=…&type=output` to fetch
   bytes. Fetching over HTTP rather than reading the filesystem means a
   remote or containerized ComfyUI works unchanged, and eliminates any
   watcher race.
3. Write into `output_root/<storyboard-slug>/<scene-order>/<panel-order>/`.
4. Ingest through the scanner's single-file path — metadata extraction,
   thumbnail generation, media row insert — then insert the `panel_images`
   row and add the file to the storyboard's folder.

`MediaScanner._process_media_file` is currently private. Phase A promotes a
public `ingest_file(path) -> Optional[Media]` wrapper rather than reaching
into the private method from new code.

### 5.6 Reference-image upload

Reference images are uploaded with `POST /upload/image` (multipart), which
returns `{name, subfolder, type}` for injection into `MS_REF_IMAGE`. Uploads
are cached by content hash per subject, so a subject's reference is uploaded
once per run, not once per panel.

### 5.7 VRAM contention

`llama-server` (Qwen3-VL) and ComfyUI (SDXL/Flux) compete for the same GPU.
The design resolves this by **phase separation** rather than by arbitration:

1. **Synthesis phase** — VLM loaded, ComfyUI idle. All prompts for the run
   are synthesized up front.
2. **Generation phase** — if `unload_vlm_during_generation` is true, the VLM
   is stopped; ComfyUI runs alone.

This works cleanly because **rerolling a panel with its existing prompt needs
no VLM at all.** Only an explicit "re-synthesize this panel" does. The common
iteration loop therefore never pays a model-swap cost, and the expensive path
is the one the user explicitly asked for.

---

## 6. Phase B — prompt synthesis

Three stages, each independently testable.

### 6.1 Parse (freeform → structure)

A single `VlmClient.generate_text` call constrained by a GBNF grammar
produces `{subjects[], scenes[{panels[]}]}` JSON, which is written to the
storyboard tables. The original text is retained in
`storyboards.source_text` as provenance only.

**After the parse, the structure is the source of truth.** Re-parsing is a
destructive, explicitly-confirmed action, because panel identity is what
anchors generated images, selections, and hand-edited prompts.

Grammar authoring must heed the warning in `CLAUDE.md`: `\-` is not a valid
GBNF escape, and an invalid grammar crashes `llama-server` with SIGSEGV
inside `llama_grammar_init_impl`, producing an infinite respawn loop.

### 6.2 Brief composition (deterministic)

`metascan/core/storyboard_brief.py` exposes a **pure function** with no I/O,
following the precedent of `metascan/core/photo_exif.py`. Given a storyboard,
scene, panel, and its subjects, it returns a compact brief:

```
SHOT: extreme close-up, eye level, 2.39:1
SUBJECT Maya: late 20s, shaved head, oil-stained flight jacket, red scarf
ACTION: her hand rests on the hull seam
LOCATION: salvage yard, twisted hulls
LIGHT/MOOD: dusk, amber haze, long shadows, tense
STYLE: graphite storyboard sketch, loose gesture lines, monochrome
```

Being pure and I/O-free, the composer carries the bulk of the domain logic
into tests that need no model and no server.

### 6.3 Render (brief → target-model dialect)

**One `generate_text` call per panel.** Per-panel calls keep each request's
context small enough for the existing `--ctx-size / parallel_slots` budget,
make single-panel re-synthesis cheap, and contain the blast radius of a
malformed response to one panel.

Dialect rules come from the existing `meta_prompt_templates` `TargetModel` /
`Architecture` enums. Concurrency is `Semaphore(spec.parallel_slots)` keyed
off the active model's registry entry, per the existing project rule.

A whole-scene single call was rejected: context grows with panel count, one
bad JSON parse loses every prompt in the scene, and rerolling one panel
either re-runs the scene or drifts from it.

### 6.4 Fallback with no VLM

If no VLM is active, **the brief is used as the prompt.** This is degraded on
natural-language models like Flux and perfectly serviceable on SDXL/Pony
tag-style models. The UI labels which of the two a panel's prompt is, so the
user is never guessing.

---

## 7. Consistency mechanics

Ranked by leverage. All five are cheap; the first two are free.

1. **Verbatim subject blocks.** The render instruction requires subject
   descriptors to be carried through *unchanged* rather than paraphrased.
   Paraphrase drift — "red scarf" → "crimson wrap" → "scarlet shawl" — is the
   main reason panel-to-panel identity decays, and preventing it costs
   nothing.
2. **Style block concatenated post-synthesis**, outside the LLM call, so the
   global look cannot drift through paraphrase.
3. **Deterministic seeds:**
   `panel_seed = base_seed + sort_order * 1000 + variant_index`.
   Reproducible across runs; a reroll simply advances `variant_index`.
4. **Optional per-subject LoRA**, injected via `MS_LORA` when the panel's
   primary subject names one.
5. **Optional reference image** on the primary subject, via the `kind='ref'`
   preset and `MS_REF_IMAGE`.

**Constraint — one reference-conditioned subject per panel.** A panel with
two subjects would need two IPAdapter conditionings, which is a workflow
authoring problem metascan cannot paper over. `panels.subject_ids[0]` is the
primary subject and the only one receiving reference conditioning; other
subjects contribute their text description only. This keeps the `'ref'`
preset to exactly one `MS_REF_IMAGE` node and hand-authorable. Multi-reference
conditioning is deferred.

---

## 8. Phase C — frontend

### 8.1 Routing

Phase C activates the already-installed but unwired `vue-router`:

- `/` — the existing library shell, behaviorally unchanged. `App.vue`'s
  current `isMobile` and viewer-swap logic moves into this route component
  as-is.
- `/storyboard/:id?` — the authoring view.

Deep links matter here specifically: `/storyboard/7` is how a reload after a
fifteen-minute generation run returns the user where they were.

### 8.2 Layout

```
┌─ Storyboard: "Salvage Yard Sequence" ────── [Generate all] [⚙] ─┐
│ ┌ Scene 1 ─────────┐ ┌ Scene 2 ─────────┐  ← scene strip        │
│ │ [▣][▣][▣][▣]     │ │ [▣][▣][ ][ ]     │    (keeper thumbs)    │
│ │ Salvage Yard·Dusk│ │ Ridge·Night      │                       │
│ └──────────────────┘ └──────────────────┘                       │
├─────────────────────────────────────────────────────────────────┤
│ Scene 1 · Salvage Yard · Dusk        mood: tense, amber haze     │
│ ┌ P1 ────┐ ┌ P2 ────┐ ┌ P3 ────┐ ┌ P4 ────┐   ← panel grid      │
│ │  ▣     │ │  ▣     │ │ ⟳ 2/4  │ │  ⚠     │                     │
│ │ WIDE   │ │ ECU    │ │ MS/OTS │ │ WIDE   │                     │
│ │ Maya   │ │ Maya   │ │ Drifter│ │ Maya   │                     │
│ └────────┘ └────────┘ └────────┘ └────────┘                     │
├─────────────────────────────────────────────────────────────────┤
│ Panel 2 · ECU · eye level · Maya          [Reroll] [Re-synth]    │
│ action  [ her hand rests on the hull seam            ]           │
│ prompt  [ A weathered hand in extreme close-up …  ] 🔓 edited    │
│ candidates:  [▣ ✓] [▣] [▣] [▣]      ← click to select keeper     │
└─────────────────────────────────────────────────────────────────┘
```

A panel tile shows its keeper if selected, a progress indicator while
generating, or an error badge if its job failed.

### 8.3 Reuse and new code

- **Reused unchanged:** `MediaViewer` for full-screen candidate inspection,
  `ThumbnailCard` for candidate tiles, the `dialog-overlay` / `dialog-card`
  styling conventions, and the `PromptPlayground.vue` patterns for the prompt
  editor (including `AbortController` cancellation).
- **New:** `stores/storyboard.ts` following the optimistic-update-with-rollback
  pattern established in `stores/folders.ts`; `api/storyboard.ts` and
  `api/comfy.ts` typed fetchers; an import dialog for pasting scene text; a
  workflow-preset registration dialog.
- **WS:** a new `storyboard` channel on the existing multiplexed `/ws`,
  carrying `job_update`, `panel_images_changed`, `synthesis_progress`.

### 8.4 Mobile

The storyboard view is **not mounted on mobile**, consistent with how
filters, the metadata panel, and all management dialogs already behave.

---

## 9. Error handling

- **ComfyUI unreachable** — endpoints return 503 with the configured
  `base_url` in the message; the UI surfaces "ComfyUI not reachable at
  `<url>`" rather than a generic failure.
- **Preset registration with missing titles** — 400 listing every missing
  required `MS_*` title.
- **Per-panel job failure** — each panel is an independent `generation_jobs`
  row. A failure marks that panel failed with ComfyUI's error text; sibling
  panels continue. "Retry failed panels" re-queues only the failures.
- **Malformed synthesis response** — affects one panel; that panel falls back
  to its brief and is flagged in the UI.
- **VLM unavailable** — synthesis falls back to briefs across the board
  (§6.4); generation is unaffected.
- **Cancellation** — clears the metascan-side queue and issues
  `/queue {delete:[…]}` plus `/interrupt`. Already-generated candidates are
  kept.

---

## 10. Testing

No real ComfyUI, CLIP, or VLM in the test suite.

- **`tests/_fake_comfy_server.py`** — a fixture speaking `/prompt`,
  `/history`, `/view`, `/upload/image`, and the `/ws` event sequence,
  mirroring the existing `tests/_fake_llama_server.py` precedent.
- **Binding resolution** — pure unit tests over fixture workflow JSON,
  including the missing-required-title failure path and the seed /
  noise_seed widget variance.
- **Brief composition** — pure unit tests; no server, no model.
- **Seed derivation** — unit tests asserting reproducibility and
  variant-index advancement.
- **`bucket_dims`** — pure unit tests over every supported aspect-ratio ×
  architecture pair, plus the unsupported-combination failure.
- **DB CRUD** — temp-DB tests following `tests/test_folders_db.py`, asserting
  cascade behavior explicitly: deleting a media file removes its
  `panel_images` row and **nulls** any `panels.selected_image_id` referencing
  it, leaving the panel and its prompt intact.
- **API** — `fastapi.testclient.TestClient` with a stubbed comfy client,
  following the `tests/test_lifespan_vlm.py` pattern.
- **Covering-index regression** — assert `hidden` is present in the DDL of
  both `idx_media_summary_added` and `idx_media_summary_modified`.

---

## 11. Decisions and rationale

| Decision | Rationale | Rejected alternative |
|---|---|---|
| Metascan drives ComfyUI | Only shape where in-app progress, per-panel reroll, and the review UX work | ComfyUI nodes pulling from metascan (`FN-*`); watched-folder handoff |
| Node-title binding | Survives node renumbering on re-save; no mapping UI; self-documenting | Explicit node-id mapping UI; heuristic auto-detection |
| Freeform → parse → structure authoritative | Fast authoring, stable panel identity, reroll never re-parses | Structured-form-only; freeform stays authoritative |
| Deterministic brief + one LLM call per panel | Small context, cheap single-panel reroll, usable no-VLM fallback | One call per scene; deterministic-only |
| Metascan fetches and owns output files | Exact file↔panel correlation; works with remote/containerized ComfyUI; no watcher race | ComfyUI writing into a scanned folder |
| Auto-folder + `media.hidden` | Keeps 48 candidates per run out of the default grid while staying searchable | Fully visible; keepers-only ingest |
| Panel-level jobs, resume from failure | One bad panel does not waste the other eleven | Whole-storyboard transaction; fire-and-forget |
| One reference-conditioned subject per panel | Keeps the `'ref'` preset hand-authorable | N-reference conditioning (deferred) |
| Phase separation for VRAM | Common reroll path needs no VLM, so it costs nothing | Runtime arbitration between llama-server and ComfyUI |
