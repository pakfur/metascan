# Qwen3-VL Abliterated Tagging — Design Spec

**Date:** 2026-05-02
**Status:** Approved for planning
**Replaces:** Nothing (additive). CLIP tagging stays as fallback.

## 1. Motivation

The current CLIP-retrieval tagger underwhelms on both SFW and NSFW content. Tags
miss subjects that aren't in the fixed vocabulary (oidv7 + imagenet + aesthetics
+ nsfw), and produce false hits because cosine similarity over an
encoder-frozen vocabulary can't reason about composition or context. CLIP can't
honestly tag explicit content either — instruction-tuned VLMs typically refuse,
and the encoder-only pipeline lacks the reasoning step needed for accurate
attribute tagging.

This design replaces the CLIP-retrieval tagger with a generative VLM
(Qwen3-VL Abliterated) on hardware tiers where it is viable, and leaves the
CLIP tagger in place as the fallback for weak hardware. CLIP itself is
**unchanged** for similarity search (image↔image and text↔image) — the new
component only changes the source of `indices` rows with `index_type='tag'`.

The new component is also designed to host future on-demand inference paths:
re-tag a single image, generate captions in styles like SDXL/Flux/Pony, and
extract image prompts. All share the same loaded Qwen3-VL model.

## 2. Goals and non-goals

**Goals:**

- Higher-quality, open-vocabulary tags on VLM-eligible tiers.
- NSFW-honest tagging (the "Abliterated" requirement).
- Preserve CLIP tagging on `cpu_only` and `cuda_entry` (no regression for weak
  hardware).
- Single supervised inference component reusable for future captioning and
  prompt-generation features.
- No additional user-facing service to install (bundled `llama-server` binary).

**Non-goals (explicitly deferred):**

- On-demand caption generation (style-specific: SDXL / Flux / Pony / natural).
  Prompt templates are scaffolded in this design but not wired to UI.
- On-demand prompt extraction.
- Constrained vocabulary mode for VLM tags (free-form is the v1 choice; can be
  added later if tag explosion becomes a problem).
- vLLM, Ollama, or `llama-cpp-python` engines (rejected — see §11).
- Multi-image tag fusion, tag-set diffing UI, captioning beyond tags.

## 3. User stories

1. **First scan, workstation user.** I drop a folder of 50K AI-generated
   images on a 4090. Metascan picks the 8B model automatically, downloads
   weights, and tags my library overnight. Tags are richer and more accurate
   than they used to be — including for explicit content.
2. **First scan, laptop user.** I run metascan on a CPU-only laptop. CLIP
   tagging runs as before; the Models tab tells me Qwen3-VL is available but
   slow at this tier, and lets me opt in to the 2B model if I want.
3. **Apple Silicon power user.** I have a 64 GB M3 Max. The Models tab shows
   all four Qwen3-VL sizes available. I pick 30B-A3B; metascan downloads it,
   restarts the inference server with the new model, and re-tags on demand.
4. **Re-tag a single image.** I right-click a thumbnail, pick "Re-tag with
   Qwen3-VL." Tags update within seconds, replacing the existing CLIP-source
   tags but preserving any `prompt`-source tags from the file's metadata.
5. **Re-tag whole library.** From the Models tab I click "Re-tag library."
   A progress UI shows ETA, can be cancelled, and only re-tags files whose
   current tags came from CLIP (or that have no tags yet).

## 4. Architecture

### 4.1 Component map

```
                  ┌──────────────────────────────────┐
                  │  FastAPI lifespan (backend/main) │
                  └─────────────┬────────────────────┘
                                │  constructs and installs
                ┌───────────────┼─────────────────┐
                ▼                                 ▼
   ┌───────────────────────┐         ┌──────────────────────────┐
   │ InferenceClient (CLIP)│         │ VlmClient (Qwen3-VL) NEW │
   │ existing, unchanged   │         │ supervises llama-server  │
   └───────────────────────┘         └────────────┬─────────────┘
                                                  │ HTTP
                                                  ▼
                                  ┌──────────────────────────────┐
                                  │ llama-server subprocess      │
                                  │ - GGUF: qwen3vl-<size>       │
                                  │ - mmproj: vision projector   │
                                  │ - port: ephemeral            │
                                  │ - --parallel N (per tier)    │
                                  └──────────────────────────────┘
```

### 4.2 The `VlmClient` shape (mirrors `InferenceClient`)

- Asyncio supervisor in `metascan/core/vlm_client.py`.
- Spawns `llama-server` subprocess, polls `GET /health` until ready.
- Drains stderr line-by-line into the server logger (same pattern as
  `InferenceClient._stderr_loop` — do **not** leave stderr piped without a
  drainer; the buffer fills during model load and the worker hangs silently).
- Typed async methods (HTTP POST to `/v1/chat/completions`):
  - `generate_tags(image_path) → list[str]`
  - `generate_caption(image_path, style) → str` (scaffolded, not wired in v1)
  - `generate_prompt(image_path) → str` (scaffolded, not wired in v1)
  - `chat(messages, image=None) → str` (escape hatch)
- Lifecycle: `ensure_started`, `swap_model(new_id)`, `shutdown`.
- Singleton installed via `set_vlm_client(...)` for use by `backend/api/vlm.py`
  and the scanner queue.

### 4.3 Why `llama-server` (not the alternatives)

- **Cross-platform.** Pre-built upstream binaries for CUDA / Metal / Vulkan /
  CPU (`ggerganov/llama.cpp` releases). Single supervisor surface across all
  three CLAUDE.md hardware tiers that matter (CUDA, MPS, CPU).
- **Abliterated weights are GGUF.** Community fine-tuners publish GGUFs; the
  HF safetensors form for these variants is rare or absent.
- **Quantization is essential.** Q4_K_M and Q5_K_M let us hit 2B–30B on the
  hardware bands described in §6. transformers + bitsandbytes is CUDA-only
  and doesn't load community GGUFs.
- **Stable HTTP API.** OpenAI-compatible `/v1/chat/completions` plus llama.cpp
  extensions (grammar / JSON schema). No NDJSON middleware needed.
- **Process isolation.** Like the existing CLIP `inference_worker.py`, a
  crashed VLM does not take down the FastAPI server.

Rejected: `llama-cpp-python` (wheel/CUDA install pain across users), Ollama
(extra service to install, breaks tier-based auto-model-selection), vLLM
(CUDA-server-only), transformers (no GGUF, no abliterated safetensors).

## 5. Components and files

### 5.1 New files

| File | Purpose |
|---|---|
| `metascan/core/vlm_client.py` | `VlmClient` asyncio supervisor for `llama-server`. Mirrors `InferenceClient`. |
| `metascan/core/vlm_prompts.py` | Pure-data prompt templates and JSON-array grammars. Tagging prompt is the only one wired in v1; caption-style stubs ship for the deferred caption feature. |
| `metascan/core/vlm_models.py` | `VlmModelSpec` registry (model_id → hf_repo, GGUF/mmproj filename, quant, parallel_slots, min_vram_gb). |
| `metascan/utils/llama_server.py` | Platform-aware path resolver and downloader for the `llama-server` binary. Picks CUDA / Metal / Vulkan / CPU build from upstream releases. |
| `backend/api/vlm.py` | REST: `POST /api/vlm/tag`, `POST /api/vlm/retag`, `GET /api/vlm/status`, `DELETE /api/vlm/retag/{job_id}`. |
| `frontend/src/api/vlm.ts` | Typed fetchers. |
| `tests/test_vlm_client.py` | Fake `llama-server` (aiohttp) — supervisor lifecycle, health probe, swap, error paths, port allocation. |
| `tests/test_vlm_prompts.py` | Snapshot tests for prompt templates + grammar. |
| `tests/test_vlm_models.py` | Registry sanity. |
| `tests/test_database_vlm_tags.py` | DB merge matrix (§7.4). |
| `tests/test_hardware_vlm_gates.py` | `feature_gates()` per tier × VRAM band per the §6 table. |
| `tests/test_vlm_api.py` | `TestClient` for the new REST endpoints. |
| `tests/test_scanner_vlm_routing.py` | Scanner correctly routes tagging to VLM vs CLIP based on gate. |

### 5.2 Modified files

| File | Change |
|---|---|
| `metascan/workers/embedding_worker.py` | When invoked with `tag_with_vlm=True`, skip CLIP tagging step; emit `needs_vlm_tag` records on stdout NDJSON for the scanner to pick up. CLIP-tagging code path remains intact for `tag_with_vlm=False`. |
| `metascan/core/scanner.py` | Inspects `feature_gates()` to decide `tag_with_vlm`. Reads `needs_vlm_tag` events; feeds them into a `VlmClient` request queue with bounded concurrency. |
| `metascan/core/database_sqlite.py` | `_update_indices` extended to handle `'vlm'` source, plus merged variants `'vlm+prompt'`, `'vlm+clip'`, `'all'`. Merge matrix in §7.4. |
| `metascan/core/hardware.py` | `feature_gates()` adds `qwen3vl-2b`, `qwen3vl-4b`, `qwen3vl-8b`, `qwen3vl-30b-a3b`, plus a `llama-server` binary gate. Tier × VRAM mapping per §6. |
| `backend/api/models.py` | `_vlm_status_rows()` mirrors `_clip_status_rows()`; included in `/api/models/status`. New WS events on `models` channel: `vlm_status`, `vlm_progress`, `vlm_queue_depth`. |
| `backend/main.py` (lifespan) | Constructs `VlmClient`, wires `set_vlm_client()`. Honours `config.models.preload_at_startup` entries like `qwen3vl-8b`. |
| `frontend/src/stores/models.ts` | VLM rows added to model list; `gateFor('qwen3vl-<id>')` works via the existing helper. |
| `frontend/src/components/dialogs/ConfigModelsTab.vue` | New "Tagging Model" section listing Qwen3-VL options with tier/recommended chips, active-tagger indicator, download buttons. "Re-tag library" action with confirm dialog (count + ETA). |
| `frontend/src/components/thumbnails/ThumbnailCard.vue` | Context-menu item "Re-tag this image" gated on `vlm_status.state === 'ready'`. |
| `setup_models.py` | `--qwen3vl <size>` flag downloads the GGUF + mmproj + ensures the right `llama-server` binary is present. |

### 5.3 Unchanged

`data/vocabulary/`, `metascan/core/vocabulary.py`, similarity-search code,
FAISS index lifecycle, prompt-source tag extraction (`prompt_tokenizer.py`).

## 6. Hardware tier mapping

| Tier | Recommended tagger | Available opt-in | Quant default |
|---|---|---|---|
| `cpu_only` | CLIP (fallback) | `qwen3vl-2b`, `qwen3vl-4b` (≥16 GB RAM) | Q4_K_M |
| `apple_silicon` (any) | `qwen3vl-4b` | `qwen3vl-2b`, `qwen3vl-8b`, `qwen3vl-30b-a3b` | Q4_K_M (Q5_K_M for 8B on ≥32 GB) |
| `cuda_entry` (<6 GB) | `qwen3vl-2b` | — | Q4_K_M |
| `cuda_mainstream` 6–10 GB | `qwen3vl-4b` | — | Q4_K_M |
| `cuda_mainstream` 10–12 GB | `qwen3vl-4b` | `qwen3vl-8b` | Q4_K_M |
| `cuda_workstation` 12–24 GB | `qwen3vl-8b` | `qwen3vl-30b-a3b` (≥24 GB only) | Q5_K_M |
| `cuda_workstation` ≥24 GB | `qwen3vl-30b-a3b` | `qwen3vl-8b` | Q4_K_M (KV-cache q8_0 for 30B) |

Notes:

- `cuda_mainstream` stays a single tier in the `Tier` enum (no enum change);
  `feature_gates()` reads `report.cuda.vram_gb` directly to distinguish the
  6–10 vs 10–12 sub-bands.
- `apple_silicon` is a single tier with all four sizes available opt-in,
  per the brainstorming session — Apple Silicon users self-select.
- 30B-A3B is a 17 GB Q4_K_M download. It is **only** offered on
  `cuda_workstation` ≥24 GB and on `apple_silicon`.
- No precision picker is exposed to users.
- `parallel_slots` for `--parallel N`: 2 for entry/mainstream, 4 for
  workstation.

## 7. Data flow

### 7.1 Cold start

```
FastAPI lifespan
  → InferenceClient (CLIP) — existing, unchanged
  → VlmClient (Qwen3-VL) — NEW
      if 'qwen3vl-<id>' in config.models.preload_at_startup:
          spawn llama-server now (blocks lifespan ~10–30s on first start)
      else:
          mark "not started"; first request triggers ensure_started
  → register both clients into backend.api.{similarity, vlm}
```

### 7.2 Batch scan, VLM-eligible tier

```
scanner.py walks media files; for each file:
  embedding_worker (one-shot subprocess — same as today):
    compute CLIP embedding → DB
    compute pHash → DB
    [skip CLIP tagging — VLM is the active tagger]
    emit JSON line: {"event": "needs_vlm_tag", "path": "..."}
  worker exits when batch done.

scanner reads NDJSON stream → maintains a queue:
  for each path (concurrency = parallel_slots):
    VlmClient.generate_tags(path) → POST /v1/chat/completions
    on response: db.add_tag_indices(path, tags, source='vlm')
    broadcast WS embedding channel: {file, tags, progress}
```

### 7.3 Batch scan, weak tier

Existing flow unchanged. `embedding_worker.py` performs CLIP tagging inline.
`VlmClient` is never started.

### 7.4 Tag merge semantics in `_update_indices`

The DB layer arbitrates which tagger "wins" so users on weak hardware can't
silently downgrade VLM tags by triggering a rescan.

| Existing source | Incoming source | Result |
|---|---|---|
| (none) | `vlm` | insert `vlm` |
| (none) | `clip` | insert `clip` |
| `prompt` | `vlm` | upsert to `vlm+prompt` |
| `prompt` | `clip` | upsert to `clip+prompt` (existing behavior) |
| `clip` | `vlm` | replace with `vlm` (CLIP-source overwritten on a VLM-eligible tier) |
| `clip+prompt` | `vlm` | upsert to `vlm+prompt` (CLIP downgraded, prompt preserved) |
| `vlm` | `clip` (rescan on weak tier) | preserve `vlm`, no change |
| `vlm+prompt` | `clip` | preserve, no change |
| `vlm` | `prompt` (rescan, prompt re-extracted) | upsert to `vlm+prompt` |
| `vlm` | `vlm` (re-tag) | replace tag set wholesale |

Asymmetry rationale: the tier gate decides whether VLM tagging happens; once
a file has been VLM-tagged, a later weak-tier rescan must not erase that work.

### 7.5 On-demand re-tag (single image)

```
ThumbnailCard context menu → POST /api/vlm/tag {path}
  → VlmClient.ensure_started → generate_tags(path)
  → db.add_tag_indices(path, tags, source='vlm')
       (replaces source='clip' rows for this path; merges with source='prompt')
  → broadcast WS folders channel: {event: 'tags_changed', file_path}
  → 200 {tags: [...]}
frontend updates the inverted-index filter panel + thumbnail metadata view.
```

### 7.6 On-demand library re-tag

```
POST /api/vlm/retag {scope: 'all' | folder_id | file_paths[]}
  → enqueue job → return 202 {job_id}
  → background task feeds VlmClient one path at a time
  → WS models channel: {event: 'vlm_progress', current, total, eta}
  → cancellable via DELETE /api/vlm/retag/{job_id}
```

Default behavior re-tags only files whose current tag rows are pure `clip` (or
none). Files with `vlm` source are skipped unless the request specifies
`force: true`.

### 7.7 Model swap

```
POST /api/models/vlm/active {model_id: 'qwen3vl-4b'}
  → cancel any in-flight retag job (drain queue if quick, else hard cancel)
  → VlmClient.swap_model('qwen3vl-4b'):
       SIGTERM llama-server → wait 5s → SIGKILL if needed
       update internal state, spawn fresh llama-server with new GGUF
       /health probe until ready
  → broadcast WS models channel: {event: 'vlm_status', state: 'ready', model_id}
```

## 8. Model registry

`metascan/core/vlm_models.py`:

```python
@dataclass(frozen=True)
class VlmModelSpec:
    model_id: str
    display_name: str
    hf_repo: str           # config-overridable via models.qwen3vl_repos
    gguf_filename: str
    mmproj_filename: str
    quant: str
    approx_vram_gb: float
    min_vram_gb: float
    parallel_slots: int

REGISTRY = {
  'qwen3vl-2b':       VlmModelSpec(quant='Q4_K_M', min_vram_gb=3,  parallel_slots=2, ...),
  'qwen3vl-4b':       VlmModelSpec(quant='Q4_K_M', min_vram_gb=5,  parallel_slots=2, ...),
  'qwen3vl-8b':       VlmModelSpec(quant='Q5_K_M', min_vram_gb=9,  parallel_slots=4, ...),
  'qwen3vl-30b-a3b':  VlmModelSpec(quant='Q4_K_M', min_vram_gb=20, parallel_slots=4, ...),
}
```

`hf_repo` defaults to a known abliterated publisher (e.g. `huihui-ai`). The
exact repo / filename strings are verified at implementation time, not pinned
in this spec — abliterated remixes are a moving target. Users can override
with `config.models.qwen3vl_repos.<id>` without code changes.

## 9. `llama-server` binary

`metascan/utils/llama_server.py` resolves the binary path. Build picked at
install time by `setup_models.py` from `detect_hardware()` output. Stored
under `~/.metascan/bin/llama-server[-<accel>]`. Version pinned in a constant;
upgrade is "delete file → re-run setup."

| Platform / accel | Source |
|---|---|
| Linux x64 + CUDA 12 | upstream `ggerganov/llama.cpp` release `linux-cuda` build |
| Linux x64 + Vulkan | upstream `linux-vulkan` |
| Linux x64 CPU (AVX2) | upstream `linux-avx2` |
| macOS arm64 (Metal) | upstream `macos-arm64` |
| Windows x64 + CUDA 12 | upstream `windows-cuda-x64.exe` |
| Windows x64 + Vulkan | upstream `windows-vulkan-x64.exe` |
| Windows x64 CPU (AVX2) | upstream `windows-avx2-x64.exe` |

A `llama-server` gate in `feature_gates()` returns `available=False` with a
"Click to install" reason when the binary is absent. Models tab download
button calls a new endpoint that triggers the binary download.

## 10. Prompts and structured output

`metascan/core/vlm_prompts.py` ships:

- `TAGGING_SYSTEM_PROMPT` — instructs the model to emit a JSON array of 15–25
  descriptive tags covering subject, attributes, style, setting, action, and
  mood. Explicitly asks for accurate tagging of NSFW content (the abliterated
  variant cooperates).
- `TAGGING_USER_PROMPT` — single-image prompt with the image attached.
- `TAGGING_GRAMMAR` — GBNF / JSON schema constraint passed to llama-server's
  `grammar` or `response_format` parameter, forcing valid JSON-array output.
- `CAPTION_STYLE_PROMPTS` — placeholder dict for `sdxl` / `flux` / `pony` /
  `natural`. Stubs only in v1; not exposed to UI.
- `PROMPT_EXTRACTION_PROMPT` — placeholder for the future prompt-extraction
  feature.

Tag normalization (lowercasing, stripping leading/trailing punctuation, dedup)
happens in `VlmClient.generate_tags` before returning.

## 11. Configuration

```jsonc
{
  "models": {
    "preload_at_startup": ["clip-large", "qwen3vl-8b"],
    "huggingface_token": "",
    "qwen3vl_repos": {
      "qwen3vl-8b": "huihui-ai/Qwen3-VL-8B-Instruct-abliterated-GGUF"
    }
  }
}
```

`qwen3vl_repos` is optional and overrides only the HuggingFace repo. The
GGUF filename, mmproj filename, and quant come from the registry — overriding
the repo expects a drop-in remix that publishes files with the same names. If
that becomes restrictive, a future config could expose a richer object schema.
Token (if present) is injected into the `llama-server` subprocess env as
`HF_TOKEN` — mirrors how the CLIP loader handles the same env var.

## 12. Error handling

| Failure | Behavior |
|---|---|
| `llama-server` binary missing | `VlmClient` marks `unavailable`; scanner falls back to CLIP tagging for the active scan; Models tab shows "Install required" with download button. |
| Binary present but ABI mismatch | stderr drainer captures `ggml_init` error → surfaced via `vlm_status` WS event with reason. |
| GGUF / mmproj file missing | Same UX as binary missing — actionable error, never silent. |
| Spawn OOM at load | One retry with `--n-gpu-layers` reduced 25%; if still fails, mark unavailable, recommend smaller model, fall back to CLIP for active scan. |
| Crash mid-batch (segfault / OOM during inference) | Supervisor auto-restarts (1 retry); on second crash, scan finishes remaining files via CLIP tagger; user notified once via toast. |
| HTTP request timeout (60 s default per image) | Item logged with `vlm_tag_failed`; scan continues; failed items collected into a retry batch at scan end. |
| Grammar-constrained output still parses bad | Log, store empty tag list, mark `vlm_attempted=true` so we don't loop on the file forever. |
| Model swap mid-batch | Current request drains (5 s grace); queued requests pause, get re-issued against new model after `/health` ready. |
| Port collision | Ephemeral port picked at spawn (`bind(('localhost', 0))` → close → reuse). |
| HF gated repo (token missing) | `download_error` WS event with `reason='hf_auth_required'`; existing token UI handles it. |
| Disk full during download | Partial file deleted, error surfaced; matches existing CLIP-download UX. |

## 13. Edge cases

- **HEIC / AVIF / WebP**: convert to JPEG before sending. Use the existing
  thumbnail cache (medium tier) as the conversion source — it already produces
  JPEGs and is sized appropriately.
- **Very large images (>50 MP)**: pre-resize to ≤1024 px on the long edge —
  Qwen3-VL's native input. Use the medium-thumbnail cache tier.
- **Animated images / videos**: scanner already produces a representative
  frame. Use that thumbnail.
- **Multi-instance metascan on one host**: each `VlmClient` picks its own
  ephemeral port; no clash.
- **WSL2 with no real Vulkan**: matches existing `wsl2-no-real-vulkan` warning;
  CUDA build still works through WSL2 GPU passthrough.
- **30B-A3B on 24 GB card**: ship `--cache-type-k q8_0 --cache-type-v q8_0`
  (KV-cache quant) by default for that model only — buys back ~30% KV memory
  at negligible quality cost.

## 14. Testing

| File | Coverage |
|---|---|
| `tests/test_vlm_client.py` | Fake `llama-server` (aiohttp): start/stop, `/health` probe, request routing, model swap, supervisor restart on crash, port allocation, error surfaces. |
| `tests/test_vlm_prompts.py` | Snapshot tests for tagging prompt + JSON-array grammar; caption-style prompt stubs. |
| `tests/test_vlm_models.py` | Registry sanity: every id has a spec, quant strings recognized, `min_vram_gb` monotonic. |
| `tests/test_database_vlm_tags.py` | The merge matrix from §7.4 — every (existing × incoming) cell. |
| `tests/test_hardware_vlm_gates.py` | Patches `detect_hardware()` with fake reports for each tier × VRAM band; asserts `recommended` / `available` per the §6 table. |
| `tests/test_vlm_api.py` | `TestClient` for `/api/vlm/{tag,retag,status}` against a stubbed `VlmClient`. |
| `tests/test_scanner_vlm_routing.py` | Scanner sets `tag_with_vlm` correctly per gate; CLIP-tag step skipped when VLM is the active tagger. |

All tests use a stubbed/fake supervisor — no real `llama-server` in CI.
Existing `KMP_DUPLICATE_LIB_OK=TRUE` and FAISS-dim-32 conventions in
`tests/conftest.py` carry over.

## 15. Performance targets (documented, not CI-enforced)

- Cold start ≤ 30 s for 8B Q5 on workstation tier.
- Per-image tag latency targets:
  - 8B on workstation: ≤ 3 s p50, ≤ 8 s p95.
  - 4B on mainstream: ≤ 1.5 s p50.
  - 2B on CPU: ≤ 30 s p50.
- Re-tag library job cancellable within 5 s of the request.

## 16. Migration

- Existing libraries keep their `source='clip'` rows untouched until a re-tag
  is explicitly triggered (scan of changed files → no change to existing
  rows; only files going through the embedding worker on the eligible tier
  get VLM tags written).
- New scans on VLM-eligible tiers write `source='vlm'`.
- The "Re-tag library" action in the Models tab is the user-facing way to
  backfill an entire library to VLM tags.
- No DB schema migration — `indices.source` is already `TEXT` and the new
  values are valid existing-shape strings.

## 17. Out of scope / future work

- Caption generation in styles (SDXL / Flux / Pony / natural) — prompt
  templates are scaffolded but the UI / API surface is deferred.
- Prompt extraction from images — same scaffolding, deferred.
- Constrained-vocabulary tag mode — only if free-form tag explosion proves
  problematic.
- Per-image tag confidence scores (not exposed by the chat-completion path).
- Multi-image / batched-prompt inference (would require a different request
  shape on llama-server).
- Tag deduping / canonicalization across different VLM remixes.

## 18. Open items resolved during brainstorming

1. **Output shape** — Tags only, free-form. Captions deferred to future
   on-demand feature with style picker.
2. **CLIP relationship** — VLM replaces CLIP tagging on eligible tiers; CLIP
   embedding still computed (similarity search needs it); CLIP tagging
   remains the fallback on `cpu_only` and `cuda_entry`.
3. **Engine** — `llama-server` subprocess.
4. **Worker shape** — Long-running `VlmClient` supervising `llama-server`,
   not a one-shot batch worker. Justified by future on-demand and
   batch-captioning use cases sharing the same loaded model.
5. **Apple Silicon banding** — single tier with all four sizes opt-in.
6. **Quant precision picker** — not exposed.
7. **30B-A3B availability** — gated to `cuda_workstation` ≥24 GB and
   `apple_silicon` only.
