# Taste Learning — Project 3: Writer Variants, Picker and Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ordinary T2I use produce prompt-level preference data and measurably better prompts without training: a dedicated prompt-writer model (Qwen3-VL-8B) separate from the tagging/chat model; N prompt variants per caption, each rendered with the same seeds; optional taste guidance from the owner's own favorites; full lineage (writer, variant, the writer's draft vs the owner's edit) on every T2I and I2V output; and a Models-tab dashboard of favorite rate per writer and the diversity trend.

**Architecture:** A role-aware VLM registry runs up to two `VlmClient` instances (`writer`, `general`) and collapses to one when both roles name the same model. The T2I prompt path composes the existing `META_*` style guide with an optional *angle* (one per variant) and optional *taste examples* retrieved from the owner's favorites by CLIP text→image search. The T2I runner turns each caption into N variant steps that share one seed list. Drafts travel client → request → `_job_meta` → write-once columns. Stats come from SQLite plus the taste worker (which already loads CLIP vectors).

**Tech Stack:** Python 3.11, FastAPI, SQLite, llama-server (existing), numpy/faiss (existing, worker only); Vue 3 / Pinia / TS. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-22-taste-learning-design.md` — project 3, the "Model roles" part of project 4 (pulled forward here, see below), "Success measure", "Guarding against taste collapse".

## Global Constraints

- Python 3.11 (`venv-311`); `make VENV_DIR=venv-311 quality test` before every commit; `cd frontend && npm run build` for frontend tasks. Known pre-existing failure: `tests/test_t2i_api.py::TestAppWiring::test_create_app_registers_the_t2i_routes`.
- **Pulled forward from project 4 (planner's call):** the separate *prompt writer* vs *tagging/chat* model roles. Reason: the spec's success baseline is "base Qwen with variants, before the first adapter", and adapters sit on Qwen3-VL-8B — so the baseline must be measured with the 8B writer or the project 4 comparison is meaningless.
- Model roles: config `models.writer_vlm` (default `"qwen3vl-8b"`) and `models.general_vlm` (default: the existing hardware recommendation). Writer role serves T2I prompt writing, T2I variants and I2V prompt writing. General serves tagging, retag, chat, prompt playground and storyboard. Same model id in both → one llama-server, one client. Two ids → two clients, each with its own port, stderr drainer and supervision (`.claude/rules/vlm-llama.md` rules apply per instance). `comfy.unload_vlm_during_generation` shuts **both** down; each restarts lazily. Never use `qwen38-27b` as the writer (reject in config/API with 422 `writer_model_not_allowed`).
- Writer ids (extend `metascan/core/judgments.py`): `base` (8B, no guidance), `base+taste` (8B with taste examples), `fallback`, `manual` (typed from scratch, no draft). Adapters (`adapter:<id>`) arrive in project 4. Every T2I and I2V output records one.
- Variants: `variants ∈ {1,2,3,4}`, default 1. Each variant of a caption is its own step with `variant_group` = one id per caption, `variant_index` 0..N-1, and **the same seed list** as its siblings (so images differ by prompt, not seed). Images requested = `batch_size × variants × count_per_batch`. Variant k (k ≥ 1) gets angle `k-1` from `T2I_VARIANT_ANGLES` in `data/meta_prompt.yml` (hot-reloaded; default list: composition, light, emotion, realism); variant 0 gets no angle. I2V: lineage and writer picker only, **no variants** (planner's call: a 15 s MiniMax clip takes ~4 min; multiplying that is the owner's call later).
- Taste guidance: up to `TASTE_EXAMPLES_K = 3` favorite prompts, each truncated to `TASTE_EXAMPLE_MAX_CHARS = 600`, retrieved by CLIP text embedding of the resolved caption against the FAISS index, restricted to favorited **images** whose prompt is known (`t2i_images.prompt_used`, else the library metadata prompt) and whose metadata model family matches the target `MODEL_PROFILES` entry when known. Inserted as a `EXAMPLES:` block after the style guide. No favorites, no index, or CLIP unavailable → no block, writer id stays `base`, and a step warning says why.
- Drafts and edits: new write-once columns `t2i_images.draft_prompt TEXT`, `t2i_images.edited INTEGER` and the same on `i2v_videos`. `edited = 1` when the rendered prompt differs from the draft after whitespace normalization. Writer id stays the drafting writer when edited. Typed-from-scratch prompts: `writer_id = manual`, `draft_prompt = NULL`, `edited = 0`. The draft is captured in the Pinia store at Generate-Prompt time — **never from a watcher**, never written into the form (`.claude/rules/t2i.md` autosave-drift and `firstBatchSource` rules stay intact).
- **Privacy:** never log prompt text, draft text, examples or captions at INFO or above; tests use synthetic prompts only.
- Success dashboard: favorite rate among **seen** new generations (T2I images with a `seen_at` or verdict) per writer per 14-day window, plus diversity = mean pairwise cosine distance between favorited image vectors per window (computed by the taste worker, stored per window). No `PRAGMA user_version` bump (new columns via `_idempotent_add_column`, new tables via `CREATE TABLE IF NOT EXISTS`).

## Review Focus

1. **Writer and general set to the same model** → exactly one llama-server; unload-for-ComfyUI shuts down each distinct instance once; the T2I Random-batch "don't unload while prompting" guard covers the writer instance — tests in Task 1.
2. **Variants keep the T2I batch contract**: `batch_step` "on show" semantics, the Random single-batch lock (409), the batch-1 rule (`firstBatchSource` from the boxes), cancel, and the hand-back all behave with variants > 1; sibling variants share seeds — tests in Task 4.
3. **Edited vs draft**: generate drafts → edit the Prompt box → Generate records `edited = 1` with the original draft; select a tile then generate does not mix another image's draft in — tests in Task 4 (backend) and live check in Task 5.
4. **No favorites / no index / CLIP not loaded** → taste guidance silently degrades to unguided `base` with a warning, never an error or a hang (bounded CLIP call timeout) — test in Task 3.
5. **Prompt text never logged** at INFO+ in the new paths — test in Task 3 (caplog over a guided write).

---

### Task 1: Writer and general model roles (backend)

**Files:**
- Create: `backend/services/vlm_roles.py`
- Modify: `backend/api/vlm.py` (role-aware getters; `/active` takes `role`), `backend/main.py` (construct per role; preload both roles; shutdown both), `metascan/core/vlm_select.py` (`pick_vlm_model(vlm, role=...)`), the consumers' wiring (`T2iRunner`, `I2vRunner` get the writer getter; tag pump, retag, chat, prompt playground, storyboard get the general getter), `metascan/core/t2i_runner.py` `_unload_vlm` (unload every distinct instance), `backend/config.py` (read `models.writer_vlm` / `models.general_vlm`), `backend/api/models.py` (status rows expose roles)
- Test: `tests/test_vlm_roles.py`

**Interfaces:**
- Produces:
  - `Role = Literal["writer", "general"]`
  - `class VlmRoles` with `client(role: Role) -> VlmClient`, `model_id(role: Role) -> str`, `distinct_clients() -> List[VlmClient]`, `async set_model(role: Role, model_id: str) -> None` (persists to config; rebuilds clients so equal ids share one), `async shutdown_all() -> None`.
  - `get_vlm_client(role: Role = "general")` keeps its name; `get_writer_vlm()` convenience.
  - `GET /api/vlm/roles` → `{"writer": {model_id, state}, "general": {model_id, state}, "shared": bool}`; `POST /api/vlm/roles` `{role, model_id}` → same; 422 `writer_model_not_allowed` for `qwen38-27b` as writer; 404 unknown model; 409 `model_not_installed`.
- [ ] **Step 1: Write the failing tests** (spawn overridden — `VlmClient(spawn_override=...)` — no real llama-server):

```python
def test_same_model_shares_one_client(roles):              # writer=general=qwen3vl-8b → client("writer") is client("general"); len(distinct_clients()) == 1
def test_two_models_two_clients(roles)                      # writer 8b, general 27b → two clients, distinct ports
def test_27b_rejected_as_writer(client)                     # POST /api/vlm/roles {"role":"writer","model_id":"qwen38-27b"} → 422 writer_model_not_allowed
def test_t2i_uses_writer_tagging_uses_general(app_wiring)   # runner get_vlm returns writer client; tag pump/chat return general
async def test_unload_shuts_each_distinct_client_once(roles)
async def test_unload_guard_covers_writer(roles, t2i_case)  # Random batch still prompting → writer not shut down
def test_roles_persist_to_config(tmp_config)
```

- [ ] **Step 2: Run to fail** — `venv-311/bin/pytest tests/test_vlm_roles.py -v`.
- [ ] **Step 3: Implement.** Keep `generate_text`/`chat_stream` request shapes unchanged; semaphores sized from each role's `parallel_slots`.
- [ ] **Step 4: Pass** — `make VENV_DIR=venv-311 quality test`.
- [ ] **Step 5: Commit** — `feat(taste): separate prompt-writer and tagging/chat model roles`.

---

### Task 2: Models tab role pickers

**Files:**
- Modify: `frontend/src/components/dialogs/ConfigModelsTab.vue`, `frontend/src/stores/models.ts`, `frontend/src/api/vlm.ts`

**Interfaces:**
- Consumes: Task 1 `/api/vlm/roles`.
- Behavior: the "Tagging (VLM)" group gains two selects above the rows — **Prompt writer** (installed models except `qwen38-27b`; note "fine-tunes build on this model") and **Tagging & chat** (all installed) — each showing state (ready / loading / stopped). A line "Sharing one model" when equal. Changing a select calls `POST /api/vlm/roles`. The existing Load/Unload row buttons keep working for the general role.
- [ ] **Step 1: Implement.** **Step 2:** `npm run build`. **Step 3:** live check in an isolated temp git worktree (own gitignored `config.json`/`data/`; never the main checkout's data) with spawn of real llama-server skipped if no weights are present — verify the selects round-trip config and the 27B is not offered as writer. **Step 4: Commit** — `feat(taste): prompt-writer and tagging/chat pickers in the Models tab`.

---

### Task 3: Variant angles and taste-guided composition

**Files:**
- Create: `metascan/core/taste_examples.py`, `backend/services/taste_examples_service.py`
- Modify: `metascan/core/t2i_prompt.py` (`compose_t2i_prompts` gains `angle: Optional[str] = None`, `examples: Sequence[str] = ()`), `data/meta_prompt.yml` (`T2I_VARIANT_ANGLES`, `T2I_EXAMPLES_PREAMBLE`), `metascan/core/judgments.py` (`WRITER_BASE_TASTE = "base+taste"`), `metascan/core/database_sqlite.py` (`favorite_prompt_candidates(paths) -> Dict[str, str]`)
- Test: `tests/test_taste_examples.py`, extend `tests/test_t2i_prompt.py`

**Interfaces:**
- Produces:
  - `variant_angles(store) -> List[str]` (parsed list; empty list when the key is missing).
  - `compose_t2i_prompts(..., angle=None, examples=())` — byte-identical to today when both are empty; otherwise appends `ANGLE: lead with <angle>` to the user turn and an `EXAMPLES:` block (preamble + numbered examples) to the system prompt.
  - `async TasteExamplesService.examples_for(caption: str, target_model: str, k: int = 3) -> Tuple[List[str], Optional[str]]` → (examples, warning). Uses the inference client's `encode_text` (timeout 10 s) and the **in-memory** FAISS manager only (`similarity._faiss_manager`, never load under a request); searches top 200 by cosine, keeps favorited non-video paths with a known prompt and a matching model family, returns the best k truncated to 600 chars.
- [ ] **Step 1: Write the failing tests:**

```python
def test_compose_unchanged_without_angle_or_examples(store)   # byte-identical to the current output
def test_compose_with_angle_and_examples(store)               # contains "ANGLE: lead with light" and 2 numbered examples
def test_variant_angles_hot_reload(tmp_yaml)
async def test_examples_from_favorites_only(svc_fake_index)   # non-favorites, videos, unknown-prompt paths excluded; k respected; truncated
async def test_examples_model_family_filter(svc_fake_index)
async def test_no_index_or_clip_degrades(svc_no_index)        # → ([], "taste guidance unavailable: ..."), no exception
async def test_clip_timeout_degrades(svc_slow_clip)
async def test_no_prompt_text_logged(svc_fake_index, caplog)  # caplog at INFO contains none of the synthetic prompt strings
```

- [ ] **Step 2: Run to fail.** **Step 3: Implement.** **Step 4: Pass** — `make VENV_DIR=venv-311 quality test`. **Step 5: Commit** — `feat(taste): variant angles and taste-guided prompt composition`.

---

### Task 4: T2I drafts API, variant steps and lineage

**Files:**
- Modify: `metascan/core/t2i_runner.py` (`BatchRequest`, `_Step`, `_plan_batch`, `_random_step`, `_supplied_step`, `_write_prompt`, `_register_job`, `_insert_images`, `generate_prompt`), `backend/api/t2i.py` (`BatchBody`, `/prompt` request/response), `metascan/core/database_sqlite.py` (`create_t2i_image` + `draft_prompt`, `edited` columns; same columns + kwargs on `create_i2v_video`), `metascan/core/judgments.py` (`normalize_prompt(text) -> str`)
- Test: `tests/test_t2i_variants.py`, extend `tests/test_t2i_runner.py`, `tests/test_t2i_api.py`

**Interfaces:**
- Produces:
  - `POST /api/t2i/prompt` body gains `variants: int = 1`, `writer: "base" | "base+taste" = "base"`; response gains `drafts: [{prompt, negative, writer_id, variant_index, angle}]` (length = variants) while keeping the existing top-level `prompt`/`negative` = `drafts[0]` for compatibility; `warnings` includes a taste-guidance warning when it degraded.
  - `BatchBody` gains `variants: int = 1` (1–4, else 422), `writer: "base" | "base+taste" = "base"`, and for Manual / a supplied Random first batch `prompts: Optional[List[{prompt: str, negative: Optional[str], draft: Optional[str], writer_id: Optional[str], variant_index: int}]]` (length = variants when present). When `prompts` is absent, the existing `prompt`/`negative` fields still work as a single supplied prompt (`writer_id = manual`, no draft).
  - `_Step` gains `variant_group: str`, `variant_index: int`, `draft: Optional[str]`. `_plan_batch`: captions = `batch_size` (Random) or 1 (Manual); each caption expands to `variants` steps that share that caption's `count_per_batch` seeds; `total_steps = captions × variants`; `rendering_step`, `step_seeds`, the queue size and the window keep working (per-step image count is still `count_per_batch`).
  - Random writing: one VLM call per variant (variant 0 no angle, variant k angle k-1), the same examples for all variants of a caption (retrieved once per caption when `writer = base+taste`); `writer_id` per step = `base`, `base+taste` (only when examples were actually used) or `fallback`.
  - Ingest: `create_t2i_image(..., writer_id, variant_group, variant_index, draft_prompt, edited)`; `edited = int(draft is not None and normalize_prompt(draft) != normalize_prompt(prompt_used))`.
- [ ] **Step 1: Write the failing tests** (fake comfy + fake llama like the existing runner tests; synthetic captions):

```python
async def test_random_variants_share_seeds(case)        # batch_size 2, variants 3, count 2 → 12 images, 6 steps, each group's 3 steps have identical seed lists
async def test_variant_angles_in_prompts(case)          # fake llama records user turns: variant 0 no ANGLE, variants 1-2 carry angles 0-1
async def test_variant_lineage_recorded(case)           # t2i_images rows: same variant_group per caption, variant_index 0..2, writer_id "base"
async def test_taste_writer_recorded_only_when_used(case_no_favorites)   # writer "base+taste" requested, no examples → writer_id "base" + warning
async def test_manual_prompts_with_drafts(case)         # prompts=[{draft:"a", prompt:"a"}, {draft:"b", prompt:"b edited"}] → edited 0 / 1, drafts stored
async def test_typed_prompt_is_manual(case)             # legacy prompt field only → writer_id "manual", draft NULL, edited 0
async def test_batch_step_on_show_with_variants(case)   # frames: batch_step advances per step as before; GET /batches step is the step on show
async def test_random_lock_and_cancel_with_variants(case)
def test_variants_out_of_range_422(client)
def test_prompt_endpoint_returns_drafts(client)         # variants 3 → 3 drafts; top-level prompt == drafts[0].prompt
```

- [ ] **Step 2: Run to fail.** **Step 3: Implement.** Update `.claude/rules/t2i.md` with the variants/draft rules (one paragraph). **Step 4: Pass** — `make VENV_DIR=venv-311 quality test`. **Step 5: Commit** — `feat(taste): T2I prompt variants with shared seeds and draft/edit lineage`.

---

### Task 5: T2I dialog — Writer, Variants, draft switcher

**Files:**
- Modify: `frontend/src/components/dialogs/T2IDialog.vue`, `frontend/src/stores/t2i.ts`, `frontend/src/api/t2i.ts`, `frontend/src/types/t2i.ts`, `frontend/src/components/generation/ThumbTile.vue` (variant label)

**Interfaces:**
- Consumes: Task 4 API.
- Behavior (mockup: `.superpowers/brainstorm/6418-1792679425/content/writer-picker.html`):
  - Next to **Generate Prompt**: **Writer** select (`Base`, `Base + taste`; later adapters) and **Variants** select (1–4). Both session-local, persisted in localStorage (`metascan.t2i.writer.v1`, `metascan.t2i.variants.v1`), never in `form_state`.
  - Generate Prompt stores the returned `drafts` in the **store** (not the form) keyed by the request; fills the Prompt box with draft 0 exactly as today; the Prompt label shows `variant k of N` with ◀ ▶ buttons that swap the Prompt (and Negative) box to another draft — a user action, not a watcher. Edits to the box apply to the currently shown variant; switching away keeps that variant's edited text in the store.
  - Generate sends `variants`, `writer`, and `prompts[]` (each variant's current text + its draft + writer_id) when drafts exist and match the current caption; otherwise it sends the legacy single prompt (→ `manual`). Selecting a tile or rolling a caption discards held drafts (dice already empties Prompt/Negative).
  - Tiles show `v2 · 0.82` style labels when `variant_index` is present (score badge from project 2 kept).
- [ ] **Step 1: Implement.** **Step 2:** `npm run build`. **Step 3:** live check in an isolated temp worktree (fake or real llama-server; seed data directly if needed): drafts → switch → edit one → Generate → DB rows show the right drafts/edited flags; tile select discards drafts. **Step 4: Commit** — `feat(taste): writer and variants controls with a draft switcher in T2I`.

---

### Task 6: I2V writer lineage and picker

**Files:**
- Modify: `backend/api/i2v.py` (`/prompt` gains `writer`, response gains `writer_id`; `GenerateRequest` gains `writer_id`, `draft_prompt`), `metascan/core/i2v_runner.py` (taste examples for I2V are **not** used — I2V system prompt differs; `writer` is `base` only for now; pass lineage into `create_i2v_video`), `frontend/src/components/dialogs/I2VDialog.vue`, `frontend/src/stores/i2v.ts`, `frontend/src/api/i2v.ts`
- Test: extend `tests/test_i2v_runner.py`, `tests/test_i2v_api.py`

**Interfaces:**
- `POST /api/i2v/prompt` → `{prompt, warnings, writer_id}` (`base` or `fallback`). The dialog keeps the returned draft in the store and sends `writer_id` + `draft_prompt` on generate; a prompt typed without a draft → `manual`. A **Writer** select shows `Base` only (adapters later), consistent with T2I placement.
- [ ] **Step 1: Write the failing tests** (`test_i2v_records_writer_and_edit`, `test_i2v_typed_prompt_is_manual`, `test_i2v_prompt_returns_writer_id`). **Step 2: Run to fail.** **Step 3: Implement.** **Step 4: Pass** + `npm run build`. **Step 5: Commit** — `feat(taste): I2V writer lineage and draft/edit capture`.

---

### Task 7: Writer stats and diversity dashboard

**Files:**
- Modify: `metascan/core/database_sqlite.py` (`writer_window_stats(window_days: int = 14, windows: int = 6) -> List[Dict]`), `metascan/workers/taste_worker.py` + `metascan/core/taste_runner.py` (train mode also computes `diversity` per 14-day window over favorited image vectors; stored in a new `taste_diversity(window_start TEXT PRIMARY KEY, n INTEGER, mean_distance REAL, computed_at TEXT)` table via the runner), `backend/api/taste.py` (`GET /api/taste/writer-stats`), `frontend/src/components/dialogs/ConfigModelsTab.vue` (Writers section), `frontend/src/api/taste.ts`, `frontend/src/stores/taste.ts`
- Test: `tests/test_writer_stats.py`, extend `tests/test_taste_runner.py`

**Interfaces:**
- `writer_window_stats` → `[{writer_id, window_start, n_seen, n_fav, rate}]` over T2I images created in each window, counting an image as seen when it has `seen_at` or a verdict; `rate = n_fav / n_seen` (None when `n_seen = 0`). I2V rows counted separately with `kind: "i2v"`.
- `GET /api/taste/writer-stats` → `{"windows": [...], "diversity": [{window_start, n, mean_distance}], "collapse_warning": bool}` — `collapse_warning(diversity, windows) -> bool` (pure function in `metascan/core/taste_labels.py`): latest diversity < 0.75 × median of the previous up-to-5 windows **and** the latest overall favorite rate > the previous window's.
- Models tab **Writers** section: a small table per writer (last 6 windows: rate with `n_seen`), the current writer baseline highlighted, and the diversity row; a one-line warning when the latest diversity drops >25% below the 6-window median while the favorite rate rises (the spec's collapse signal). Use the `dataviz` skill conventions for any chart; a plain table is acceptable.
- [ ] **Step 1: Write the failing tests** (`test_rates_per_writer_and_window`, `test_unseen_excluded`, `test_diversity_computed_on_train`, `test_collapse_flag_condition`). **Step 2: Run to fail.** **Step 3: Implement.** **Step 4: Pass** + `npm run build`. **Step 5: Commit** — `feat(taste): favorite rate per writer and diversity trend`.
