# Taste learning — design

Date: 2026-10-22
Status: approved layout, awaiting document review

## Intent

Prompts produced by the current pipeline (caption → Qwen rewrite in the
model's `META_*` style → deterministic lint) are syntactically correct but the
outputs rate "mid to poor": they lack authentic realism, composition, or
emotional weight. The owner currently hand-tunes prompts to get what they want.

The goal is for metascan to **learn one person's taste from how they already use
it** — generate, favorite, give a quick reason — and use that to:

1. produce outputs closer to what the person likes, and
2. (later) propose things they like but would not have thought of.

It must keep learning as the library grows and taste drifts, and it must work
for any metascan user against their own library, prompts and ratings.

Core belief behind the design: the cheapest big win is **better selection and
better data capture**, not cleverer prompt wording. Krea2 renders an image in
~8 s on the target box (RTX 5090 32 GB, 128 GB RAM), so generating many
candidates and ranking them is affordable; a trained prompt writer comes last
because it is the most data-hungry and easiest to get wrong.

## Goals

- Rating is fast, keyboard-only, and lives in the media viewer. Today's flow
  (`F` favorite, `←`/`→` navigate) is unchanged.
- Ordinary use doubles as training data. There is **no separate
  "build a dataset" mode.**
- A taste scorer that retrains itself after rating activity, in minutes.
- Prompt-writer fine-tunes (Qwen LoRA adapters) produced on demand, overnight,
  as **separate named files**; the user picks the active one, keeps several, and
  deletes bad ones. Base Qwen is always available.
- A measurable success signal: **a higher favorite rate on new generations than
  before.**

## Non-goals (this design)

- Tiered ratings (S/A–D). Binary favorite stays; it is enough once "seen" and
  "pass" are captured. Revisit only if data shows binary is the bottleneck.
- Video taste scoring and video-prompt adapter training. **Video rating is
  captured** (same viewer UI, same tables) so data accumulates, but nothing
  trains on it yet.
- Dream runs (overnight exploration batches). Planned as project 5; the data
  model must not preclude it, but it is not specified here.
- Fine-tuning the image/video generators themselves (Krea2 / MiniMax LoRAs).
- Cloud training, sharing adapters between users, or any remote service.

## Anti-goals (failure even if it technically works)

- Rating that is slow, needs the mouse, or forces moving between keyboard and
  mouse. With ~8 000 images and ~5 000 videos to get through, any friction kills
  it.
- Breaking the existing viewer keys (`F`, arrows, Space, `,` `.`, `M`, `↑` `↓`,
  `H`/`?`, Ctrl+D).
- **Taste collapse**: scorer and writer reinforce each other until everything
  looks the same.
- Prompt text leaving the machine or being read by anyone but the owner. All
  learning runs locally; developers test with their own synthetic prompts.
- A fine-tune that overwrites the base model or cannot be removed.

## Constraints

- Runs inside metascan (FastAPI + Vue 3), not a separate tool. Metascan, ComfyUI
  and llama-server all run on the same PC (Windows, RTX 5090). Training will
  likely run under WSL2 — builder to confirm the toolchain.
- Follow CLAUDE.md and `.claude/rules/` (t2i.md, i2v.md, vlm-llama.md,
  frontend.md, hardware.md). In particular: DB access sync behind
  `asyncio.to_thread`; heavy AI work in subprocesses; one-shot migrations gated on
  `PRAGMA user_version` (currently 4); covering indexes must include every
  summary SELECT column; DELETE endpoints return `{status:"deleted"}`.
- Training needs a CUDA GPU with enough memory; expose it as a feature gate in
  `metascan/core/hardware.py` so users without one still get projects 1–3.
- The developer (and Claude) never reads the owner's prompts. Test fixtures and
  the scorecard briefs are developer-written.

## The arc (five projects, each useful alone, built in order)

### 1. Rating capture

**Media viewer side rail** (chosen over a bottom strip). A fixed column on the
right of `MediaViewer.vue`, shrinking the media slightly. Shows verdict, reason
chips, and (once project 2 exists) the taste score and batch position
("batch 5 of 8"). The rail is always visible; `H` keeps toggling the
shortcut help.

Keys:

| Key | Action |
|---|---|
| `F` | Favorite toggle (unchanged) |
| `X` | Pass toggle: "I looked and it's a no." Mutually exclusive with favorite. |
| `1`–`6` | Toggle reason: realism, composition, emotion, light, concept, motion. Meaning follows the verdict: favorite → "loved for", pass → "missed on". |
| `←` `→` | Prev/next (unchanged). |
| `R` | Review mode: navigation order comes from the scorer's least-certain items instead of library order (inert until project 2 ships). |

Rules:

- **Seen**: an item counts as seen after ~1 s on screen in the viewer (image) or
  once playback starts (video). Scrolling the grid does not count. Unfavorited +
  seen = an implicit negative; unseen = no signal.
- Reasons need a verdict. Pressing `1`–`6` with no verdict shows a brief hint
  and does nothing *(my call)*.
- Every judgment change records whether a taste score was visible at that
  moment and its value, so training can down-weight possibly-anchored ratings
  *(my call; the owner chose to show scores on tiles immediately)*.
- Applies to images and videos alike.

**Data captured** (names indicative; builder finalises):

- `judgment_events` — append-only log: file_path, event (`seen`, `favorite`,
  `unfavorite`, `pass`, `unpass`, `reason_on`, `reason_off`), reason, score_shown
  (nullable), created_at. Timestamps let older judgments decay.
- Current state per item: seen_at, verdict (favorite/pass/none), reasons. Keep
  `media.is_favorite` as the source of truth for favorite so every existing
  consumer (smart folders, filters, grids) keeps working; verdict=pass and
  reasons live alongside. Do not widen the `/api/media` summary unless needed —
  if you do, extend both covering indexes.
- **Generation lineage**: `t2i_images` and `i2v_videos` gain `writer_id`,
  `variant_group` and `variant_index` (see project 3). A batch's siblings are
  already linked by `batch_id`.
- **Tombstones**: deleting a media item that has any judgment or generation
  lineage first writes a small record — file_path, CLIP embedding vector,
  final judgment, generation facts (caption, prompt, model, seed, writer,
  batch/variant). The file goes; the lesson stays. `delete_media` and
  `delete_media_batch` currently hard-delete the row, and `t2i_images` rows are
  pruned by JOIN, so the tombstone must be written before either.
- Existing library: the ~13 000 existing items are bootstrapped by the owner
  rating them in the viewer. Existing `is_favorite=1` rows count as favorites
  with an unknown timestamp (treat as the migration time).

**Categories** (owner-approved). A category is a user-created label
("Anime", "Sci-Fi", "Erotic", "Landscape") that partitions training data so
an adapter can be trained per category. An item may have several categories.

- Stored as a user-managed list plus a many-to-many item↔category link that
  records how the link was made (`viewer`, `sticky`, `generation`, `bulk`).
  Judgment events log category changes; tombstones keep the item's category
  names.
- **Viewer**: `C` opens a type-to-pick list in the side rail. ↑/↓ moves,
  Enter adds or removes the highlighted category on the current item, a new
  name plus Enter creates it and adds it, Shift+Enter makes it the
  **sticky category**, Esc closes. While a sticky category is set (shown at
  the top of the rail), every favorite/pass verdict also adds it to that item.
  Sticky lasts until cleared or the page reloads *(my call)*. Rename and
  delete live in the same list (rare, mouse is fine). Deleting a category
  asks for confirmation and removes its links.
- **Generation**: a Category picker in the T2I and I2V dialogs. Everything a
  batch produces gets those categories (`generation`).
- **Bulk (existing library)**: an external CLI script,
  `scripts/assign_category.py`, adds or removes a category for every library
  item under a filesystem folder, recursively, through the metascan API. It
  has a dry run. It is deliberately standalone so it can be kept or thrown
  away.
- Cross-tab sync on a `categories` WS channel, like folders.

### 2. Taste scorer

- Input: the CLIP image embeddings metascan already computes (FAISS index under
  `embedding_manager`). Images only for now; videos get no score.
- Labels: favorite = positive, pass or seen-and-not-favorited = negative, unseen
  excluded. Batch siblings add pairwise "this beat that" comparisons (same
  caption/prompt, different outcome) — the strongest signal available.
- Weighting: time decay on judgment age (half-life configurable; default
  180 days *(my call)*) and a down-weight for judgments made with a score
  visible. **The clock starts at the rating time, not the import or
  generation time**: what fades is the owner's opinion, not the image. The
  clock restarts whenever the judgment is reconfirmed. Reconfirming means
  changing it or re-pressing it, or viewing an already-judged item for the
  seen threshold without changing it ("saw it again, still agree")
  *(my call)*. Existing favorites migrated in project 1 start at the
  migration time.
- Model: small and fast — logistic regression or a small MLP over the embedding
  with a pairwise term. Optional per-reason heads predicting the 1–6 chips
  (useful later for critique). Exact model is the builder's call; it must
  retrain in minutes on CPU in a subprocess.
- Retrains automatically after enough new judgments or when idle; keeps
  versions; writes scores to a per-item table keyed by scorer version.
- Category-aware: the item's categories are an input, because what makes a
  great landscape differs from a great anime frame (exact mechanism is the
  builder's call).
- Drives: score shown on result tiles **immediately** (owner's decision),
  score + batch position in the viewer rail, best-first sort option, review
  mode (`R`).

### 3. Writer variants + picker (no training yet)

- **Variants**: for one caption, the writer drafts N different prompts;
  each renders "Count per Batch" seeds. Today Count per Batch only varies the
  seed, so siblings share a prompt and cannot show which wording worked. With
  variants, favoriting one variant's image over another's is a direct
  preference between prompts. Must fit the existing Random/Manual batch
  mechanics, `batch_step` semantics, and the autosave-drift rules in
  `.claude/rules/t2i.md`; the result pane shows a ◀ ▶ "variant k of N" on the
  Prompt box. The grid labels each tile with its variant and taste score.
- **Writer picker**: a "Writer" select next to **Generate Prompt** in
  `T2IDialog.vue`, and the same control in `I2VDialog.vue`. Options: base Qwen
  plus each adapter of matching kind, each showing date and favorite rate
  ("untested" until it has a scorecard). With no adapters, only base Qwen.
  I2V lists only i2v-kind adapters, so it shows base Qwen alone until video
  training exists *(my call)*.
- **Taste-guided writing without training**: when base Qwen writes, it is given
  guidance distilled from the owner's favorites near this caption (retrieve
  favorited images by caption-text ↔ image CLIP similarity, then use their
  prompts/structure). This runs locally only. Raw few-shot vs summarised
  "style notes" is the builder's call; measure both against the baseline if
  cheap.
- Every generated item records which writer produced its prompt
  (`base`, adapter id, or `manual` when the user typed the prompt from
  scratch).
- **Hand edits are kept as lineage** *(my call)*: when the user edits a
  writer-drafted prompt before generating, store both the writer's draft and
  the edited prompt that was rendered (writer stays the drafting writer, with
  an `edited` flag). "Owner's edit of draft X" is a direct writer-training
  signal. Capture the draft at Generate-Prompt time in the store, never via a
  watcher that writes the form (autosave-drift rule in `.claude/rules/t2i.md`).

### 4. Adapter training + management

**Model roles (owner's decision).** The prompt writer and the
tagging/chat model are selected **separately** in the Models settings tab, so
the user never switches models when moving between tasks:

- **Prompt writer model**: Qwen3-VL-8B (`qwen3vl-8b`), the base for every
  adapter. Used by T2I/I2V Generate Prompt and variants.
- **Tagging / chat model**: the owner's choice, e.g. `qwen38-27b`. Used by VLM
  tagging, the chat window and any other VLM consumer.

Each consumer routes to its role's model. If the two roles name the same
model, one llama-server serves both. Otherwise, whether to run two
llama-server instances side by side (~24 GB together at the shipped quants)
or swap on demand is the builder's call. Constraints: VRAM must still be freed
for ComfyUI per `comfy.unload_vlm_during_generation`, the user never switches
manually, and the existing `vlm-llama.md` supervision and stderr-drain rules
apply to each instance.

- An adapter is a **LoRA file on top of unchanged base Qwen3-VL-8B**
  (the writer role's model; never the 27B).
  llama-server loads several via repeated `--lora` with
  `--lora-init-without-apply`, and each completion request picks one with the
  per-request `lora: [{id, scale}]` field (verified against llama.cpp server
  docs). Adding or deleting an adapter restarts llama-server, which
  `vlm_client.py` already supervises.
- Training data:
  - **Preference pairs** from variant groups: same caption, the variant whose
    images were favorited vs one whose images were passed/seen-unfavorited →
    DPO-style pairs.
  - **Hindsight pairs** from the whole library: Qwen-VL writes a short brief for
    each favorited generated image; the pair "brief → the prompt that produced
    it" teaches the writer to expand briefs the owner's way. This lets the
    existing 13 000 items contribute before variant data exists. Sources are
    `t2i_images.prompt_used` **and** prompts parsed from library metadata of
    images metascan did not generate, but only where the metadata identifies a
    model family in `t2i_models.MODEL_PROFILES`; everything else is excluded
    *(my call)*.
  - **Edit pairs**: writer draft vs the owner's edited version (project 3).
  - **One adapter per kind, conditioned on target model** *(my call)*: every
    training example carries the target model's `META_*` system prompt exactly
    as at inference, so one t2i adapter serves Krea2, Qwen-Image, SDXL and
    Z-Image without learning a blend of tag-soup and prose. The registry
    records per-target-model example counts; the picker greys out an adapter
    for a target model it saw too few examples of.
- **Category scope** (owner-approved). Each run picks a scope:
  - a single category: trains only on that category's items, and the
    category goes in the adapter name (`taste-scifi-v2`);
  - **All, category-aware**: trains on everything, with the item's
    categories written into each example, so shared lessons carry across and
    small categories still benefit.

  The registry records the scope. The T2I/I2V writer menu shows the adapters
  matching the dialog's selected category, plus the "all" adapters. The
  training window's dataset panel shows per-category counts and readiness,
  so a category too thin to train alone is obvious.
- Training: on demand from the Models settings tab (`ConfigModelsTab.vue`),
  meant to run overnight on the 5090. Train on HF-format weights with
  LoRA/QLoRA (Unsloth/TRL/PEFT), convert with llama.cpp's LoRA→GGUF converter,
  register. Requires HF base weights matching the GGUF base (~16 GB download,
  surfaced through the existing Models tab download flow). Text side only;
  vision tower untouched.
- Metascan says when there is "enough new data since the last adapter"
  (threshold is the builder's call).
- Registry per adapter: name (renameable), kind (`t2i`/`i2v`), created_at,
  base model, data cutoff, pair counts, status (`training`, `untested`,
  `ready`, `failed`), scorecard, file path. Actions: set active, rename, delete
  (removes the file; generations keep the writer id and remembered name).
- **Training window** (owner-approved; mockup
  `.superpowers/brainstorm/*/content/training-window.html`). Opened from the
  Models tab ("Train prompt writer"). **Closable**: the run is a server-side
  job, so closing the window, reloading or closing the browser never stops it.
  While it runs, a header chip shows name, % and ETA and reopens the window,
  and GPU consumers (T2I/I2V Generate, the writer, VLM tagging) are disabled
  with a "GPU busy: training" message. Rating stays available. Contents:
  - *Dataset panel*: counts per example kind (variant pairs, edit pairs,
    hindsight briefs) with "+N new since last adapter", split by target model,
    ratings date span and decay setting, holdout %, readiness hint, and a
    "Preview 10 examples" button (local only).
  - *Run panel*: name; Effort preset Quick / Standard / Thorough; Start from
    **Fresh on all data** (default) or Continue <adapter>; "After training,
    render scorecard briefs (blind)" checkbox; a collapsed Advanced section;
    progress bar, step count, live **holdout agreement** (how often the
    adapter prefers the prompt the owner actually favorited, on held-out
    pairs) next to base Qwen's figure, a loss sparkline, time since the last
    checkpoint.
  - *Controls*: **Start**; **Pause** (stop, keep checkpoint); **Resume**
    (continue a paused or crashed run from its last checkpoint); **Cancel**
    (stop and discard, with confirmation). Pause and Cancel confirm. Only one
    run trains at a time; paused runs are stored checkpoints.
  - *Adapter list*: base + every adapter with status (`ready`, `training`,
    `paused`, `failed` with reason and log link), scorecard favorite rate,
    diversity trend on hover; actions set active, rename, delete, resume.
  - *Hyperparameters* *(my call)*: the Effort preset derives steps from
    dataset size (as epochs) and sets learning rate and LoRA rank. The
    resolved values are **always shown read-only** under the preset ("≈3 000
    steps · lr 1e-4 · rank 16"). **Advanced** allows overriding learning
    rate, epochs/steps and rank within clamped safe ranges, with a warning
    when overridden. Every adapter records its full hyperparameters and the
    adapter list shows them, so runs can be compared.
- **Scorecard**: a fixed set of developer-written test briefs (shipped in
  `data/`, ~40, never the owner's prompts; owner may add their own). Running a
  scorecard generates the briefs with the adapter and with base Qwen, mixes the
  results, and hides the writer in the viewer until rated (blind). Reported:
  favorite rate per writer, plus the scorer's mean as a cheap proxy before the
  owner rates.

### 5. Dream runs (later, not specified)

Overnight exploration batches with deliberate departures from the owner's
usual themes, rated the next morning. Also the main guard against taste
collapse. Keep the batch/variant/writer lineage general enough to tag a
generation as `dream`.

## Success measure

**Favorite rate among seen new generations**, tracked per writer and per
two-week window, shown in the Models tab. Baseline = base Qwen (with variants)
in the period after projects 1 and 3 ship and before the first adapter.
Honest expectation: the scorer (2) mostly saves rating time and orders results;
gains in favorite rate come from taste-guided writing (3) and adapters (4).

## Guarding against taste collapse

- Time decay so old taste fades rather than locks in.
- Score-visible flag on judgments.
- Track diversity of favorited outputs over time (e.g. mean pairwise CLIP
  distance per window) and show it next to favorite rate; a falling diversity
  with a rising favorite rate is the warning sign. Exact metric is the
  builder's call.
- Dream runs (project 5).

## Left to the builder

- Table/column names and the exact split between the event log and current
  state.
- Scorer model family, retrain triggers, thresholds.
- Training toolchain on Windows (WSL2 vs native) and hyperparameters.
- "Enough new data" threshold for suggesting a new adapter.
- How variants slot into the Random batch state machine without breaking the
  rules in `.claude/rules/t2i.md`.
- Whether `SlideshowViewer` also gets the rating keys (default: no).
- Exact visual styling of the rail (mockups:
  `.superpowers/brainstorm/*/content/viewer-rating.html`, `writer-picker.html`).
