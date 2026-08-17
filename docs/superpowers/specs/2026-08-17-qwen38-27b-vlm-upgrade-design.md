# Qwen3.8-27B VLM Upgrade — Assessment & Migration Plan

**Date:** 2026-08-17
**Status:** DRAFT — awaiting review
**Scope:** Evaluate the two proposed Qwen models against metascan's VLM use
cases; plan the adoption of the suitable one across registry, llama.cpp
pin, hardware gates, download/setup, config UI, prompts, docs, and tests.

---

## 1. Model Assessment

### 1.1 Qwen/Qwen3-30B-A3B — NOT suitable ❌

- **Text-only.** It is the May 2025 Qwen3 MoE language model (30.5B total /
  3.3B active). It accepts no image input, so it cannot serve any metascan
  VLM path: scan-time tagging, subject/scene describe, storyboard reference
  images, or the H3 sound stage. All of these send `image_url` parts.
- **Older than what we run.** The registry's `qwen3vl-30b-a3b` entry is the
  *vision* MoE of the same generation (Qwen3-VL-30B-A3B). Swapping to the
  text-only base would be a strict downgrade plus a loss of vision.
- **No further action.** Rejected.

### 1.2 Qwen/Qwen3.8-27B — SUITABLE as a new top-tier entry ✅ (with gates)

Released open-weight 2026-08-13/14 — the newest open Qwen and the only
self-hostable size in the Qwen3.8 family (the other member is a 2.4T-A95B
API-only flagship). Key properties:

| Property | Value | Metascan impact |
|---|---|---|
| Modality | Native VLM: images **and video** | Covers every existing use case; video understanding is a future capability |
| Architecture | Dense 27B hybrid: 48 Gated DeltaNet linear-attention layers + 16 full-attention layers | Needs llama.cpp ≥ ~b10450 (DeltaNet CUDA fix) |
| Context | 262,144 native (1M extensible) | Vastly above our 32K `--ctx-size`; room to raise it |
| KV cache | ~64 KB/token (only 16 full-attn layers carry KV) — ~8 GB at 128K ctx | Long contexts are cheap; the MoE `--cache-type-k/v q8_0` hack becomes unnecessary for this model |
| Thinking | **On by default**, per-request control via `chat_template_kwargs {"enable_thinking": false}` / `reasoning_effort`; newer builds prefer startup `--reasoning on/off` | Must be OFF for us: llama.cpp grammar enforcement is inactive while thinking is enabled (ggml-org/llama.cpp#20345), and every metascan VLM call is GBNF-constrained |
| Chat template | Jinja embedded in GGUF; "template trap" — some packs ship a corrected `chat_template.jinja`; `--jinja` behavior must be verified on the new binary | llama-server auto-detects; verify no flag needed on b10456 |
| Quant sizes | Q4_K_M ≈ 17 GB disk; +0.9 GB mmproj-F16 | Fits RTX 5090 32 GB comfortably; ~19–20 GB working set |
| Abliterated remix | `chimingw/Qwen3.8-27B-Uncensored-OrcaRouter-GGUF` — OrcaRouter abliteration, vision retained, `AUX/mmproj-Qwen3.8-27B-Uncensored-OrcaRouter-F16.gguf`, Q4_K_M 16.8 GB | Matches the registry's existing all-abliterated convention (NSFW lanes) |
| MTP | Optional `MTP-ONLY` draft GGUF (~3 GB Q8_0) for speculative decoding via `--model-draft … --spec-type draft-mtp` | Optional speedup, future work |

**Conclusion:** Qwen3.8-27B is an *addition* (a new top tier), not a family
replacement. There are no 2B/4B/8B Qwen3.8 open weights, so the Qwen3-VL
ladder stays for `cpu_only` / `cuda_entry` / `cuda_mainstream` tiers. On
`cuda_workstation` hardware the 27B should become the recommended model,
superseding `qwen3vl-30b-a3b` in recommendation (that entry stays available).

### 1.3 Hard gates that must pass before any code lands (Phase 0 spike)

1. **llama.cpp ≥ b10450.** Our pin is b7400 (2025-12-14). Builds before
   ~b10450 produce silent garbage on CUDA for DeltaNet layers — exactly the
   WSL2 + CUDA case we run (documented in ggml-org/llama.cpp discussion
   #27164; fixed around commit `ece963f41`). Latest release is **b10456**
   (2026-08-17).
2. **Vision + grammar verified on real hardware.** mmproj image input AND
   GBNF grammar output must both work with reasoning off, on the 5090 under
   WSL2.
3. **No Qwen3-VL regression on the new binary.** The bump is global — the
   existing 2B/4B/8B/30B-A3B models must still load and tag correctly on
   b10456.
4. **Release asset layout unchanged.** b7400 was chosen as "the last
   release with uniform `.zip` assets across platforms". Verify b10456's
   asset names still match `pick_release_asset()` patterns and that the
   `bin/` flattening + SONAME-symlink extraction in
   `setup_models.py:_ensure_target` still applies. If upstream switched
   formats (e.g. `.tar.gz` on Linux), extend the extractor.

---

## 2. New capabilities & prompt/system changes

- **Reasoning off is mandatory for grammar calls.** All current call sites
  (tagging, describe, story stages, H3 sound) use GBNF grammars → the
  server must run with reasoning disabled. Plan: a `VlmModelSpec` field
  (e.g. `reasoning: str | None = None`) that `_build_command` maps to
  `--reasoning off` (only emitted for specs that set it — the flag doesn't
  exist on models without hybrid reasoning and older Qwen3-VL doesn't need
  it, though b10456 accepts it generally; verify).
- **Thinking for non-grammar calls (future).** `generate_text` without a
  grammar (free-prose describe drafts, future captioning) could opt into
  thinking via per-request `chat_template_kwargs` for higher quality.
  Not in scope for the initial migration — grammar paths dominate.
- **Context raise.** With 64 KB/token KV, `--ctx-size` can go from 32768 to
  65536+ for this model at ~4 GB KV cost, giving headroom for multi-image
  describes and long story-composition inputs. Make ctx-size a
  `VlmModelSpec` field (default 32768) instead of a constant.
- **System prompts largely unchanged.** `data/meta_prompt.yml` prompts are
  plain instruction text with no model-specific tokens, and the chat
  template comes from the GGUF. Expect *better* instruction following
  (fewer camera/lens hallucinations noted in docs/meta-prompts.md); plan a
  prompt re-validation pass using the hot-reloading PromptStore rather than
  pre-emptive rewrites.
- **`_IMAGE_MAX_EDGE = 1024` stays initially.** The constant was tuned to
  Qwen3-VL's vision-token math + our 32K ctx budget. With a bigger ctx on
  the 27B it could rise (e.g. 1536) for describe quality — revisit after
  the ctx-size field lands; keep tagging at 1024 (throughput).
- **Video understanding (future idea, not this migration).** Qwen3.8
  understands video natively; llama.cpp multimodal video support is still
  frame-based and immature. The `VlmClient` image-only guard
  (`_SUPPORTED_IMAGE_EXTS`) stays. Log as a future_ideas.md entry: VLM
  video tagging via sampled frames.
- **MTP speculative decoding (future idea).** The remix ships an MTP draft
  GGUF; llama-server `--model-draft … --spec-type draft-mtp` reportedly
  speeds decoding meaningfully. Optional later spec field
  (`draft_gguf_filename`), not in the initial migration.

---

## 3. Migration Plan

### Phase 0 — Spike (no repo changes; throwaway)

Manual verification on the 5090/WSL2 box:
1. Download llama.cpp b10456 CUDA zip; confirm asset name + archive layout.
2. Download `chimingw/…OrcaRouter-GGUF` Q4_K_M + `AUX/` mmproj.
3. `llama-server --reasoning off -m … --mmproj … --jinja?` → verify:
   coherent text (no DeltaNet garbage), image input works, GBNF
   `TAGGING_GRAMMAR`-style output honored, VRAM ≤ ~20 GB, tokens/s.
4. Load `qwen3vl-4b` on the same binary → verify no regression.
5. Record: exact flags needed (`--jinja` default?, `--reasoning` accepted),
   whether the pack's chat template needs the corrected
   `chat_template.jinja`.

**Abort criteria:** grammar broken with vision, or Qwen3-VL regression with
no workaround → stop and reassess.

### Phase 1 — llama.cpp pin bump (b7400 → b10456)

- `metascan/utils/llama_server.py:29` `LLAMA_CPP_RELEASE = "b10456"` +
  comment rewrite (why: Qwen3.8 DeltaNet CUDA fix ~b10450).
- Verify/adjust `pick_release_asset()` patterns and
  `setup_models.py:_ensure_target` extraction against the new archive
  layout (symlinked SONAME chains, `bin/` flattening).
- `scripts/build_llama_server.sh` picks the pin up automatically; confirm
  the tag builds with existing CMake flags.
- Existing local override at `data/bin/local/llama-server` (user's CUDA
  build) will mask the bundled binary — **rebuild it from b10456** or the
  new model silently emits garbage through the stale local build. Document
  prominently.
- Tests: `tests/test_llama_server_paths.py` (pin + asset names).
- Docs: `docs/build-llama-server.md`, CLAUDE.md release-zip bullet.

### Phase 2 — Registry & spec generalization

- `VlmModelSpec` gains fields:
  - `kv_cache_quant: str | None = None` — replaces the
    `if spec.model_id == "qwen3vl-30b-a3b"` branch in
    `vlm_client._build_command` (set `"q8_0"` on the 30B-A3B entry).
  - `ctx_size: int = 32768` — `_build_command` uses it; 27B entry sets
    65536.
  - `reasoning: str | None = None` — emits `--reasoning <val>`; 27B entry
    sets `"off"`.
  - `mmproj_repo_filename` already exists; it must tolerate a subdir path
    (`"AUX/mmproj-Qwen3.8-27B-Uncensored-OrcaRouter-F16.gguf"`) — verify
    the HF downloader passes it through as the repo path and renames on
    write to the flat local `mmproj_filename`.
- Add entry:
  ```python
  "qwen38-27b": VlmModelSpec(
      model_id="qwen38-27b",
      display_name="Qwen3.8 27B (Abliterated)",
      hf_repo="chimingw/Qwen3.8-27B-Uncensored-OrcaRouter-GGUF",
      gguf_filename="Qwen3.8-27B-Uncensored-OrcaRouter-Q4_K_M.gguf",  # verify exact name
      mmproj_filename="mmproj-qwen38-27b-F16.gguf",
      mmproj_repo_filename="AUX/mmproj-Qwen3.8-27B-Uncensored-OrcaRouter-F16.gguf",
      quant="Q4_K_M",
      approx_vram_gb=20.0,
      min_vram_gb=18.0,
      parallel_slots=4,
      ctx_size=65536,
      reasoning="off",
  )
  ```
  (Exact filenames verified during Phase 0.)
- Module docstring: no longer "Qwen3-VL … variants" — now "Qwen VLM
  variants".

### Phase 3 — Selection & hardware gating

- Kill the `"qwen3vl-"` prefix filters; test **registry membership**
  instead (`mid in REGISTRY`). Sites: `metascan/core/vlm_select.py:12-19`,
  `backend/services/scan_dispatch.py:26,46`, `backend/api/vlm.py:83`,
  `backend/main.py:253` (preload), `ConfigModelsTab.vue:404,417`
  (frontend needs the VLM id set or a `is_vlm` row flag from
  `/api/models/status` — prefer the flag; no hardcoded id list in TS).
- `hardware.py:feature_gates` VLM block: replace per-id `elif` VRAM
  thresholds with spec-driven logic (`available = vram >= spec.min_vram_gb`
  with headroom rule; `recommended` = highest-quality available for the
  tier). New ladder: `cuda_workstation` ≥ ~20 GB recommends `qwen38-27b`;
  30B-A3B stays available. Update docstring at `hardware.py:261`.
- `backend/api/vlm.py:153` hardcoded `"qwen3vl-4b"` fallback → derive from
  gates (`recommended_vlm_model_id()`), keep `"qwen3vl-4b"` only as final
  fallback constant if gates are unavailable.

### Phase 4 — Download / setup / config

- `setup_models.py:resolve_qwen3vl_targets` → rename to
  `resolve_vlm_targets` (keep a thin alias for the CLI), driven purely by
  the spec's repo/filenames (it already is — verify mmproj subdir case).
  Update `--qwen3vl` argparse help; consider adding `--vlm` alias.
- **Fix the dead `qwen3vl_repos` override** (inventory §7): either wire
  `backend/config.py:get_models_config()` → download path via
  `resolve_repo()`, or delete the override and its doc claim. Recommend:
  wire it, renamed `models.vlm_repos.<id>` (keep reading the old key for
  compat).
- `backend/api/models.py:_vlm_status_rows` group label `"Tagging
  (Qwen3-VL)"` → `"Tagging (Qwen VLM)"`; rows themselves are
  registry-driven and pick the new entry up automatically. Download stage
  label stays `downloading (n/3)`.

### Phase 5 — Config/Models UI

- Mostly automatic once the row exists. Confirm: VRAM chip renders 20 GB,
  gate chips show recommended-on-workstation, Load/Unload buttons appear
  (after prefix-filter fix in Phase 3), download button fetches 3 targets.
- Tier banner text unchanged.

### Phase 6 — Prompt & quality validation

- Run the existing tagging vocabulary + describe + storyboard compose
  against a sample set with `qwen38-27b`; compare against `qwen3vl-8b`
  and `-30b-a3b` output. Iterate `data/meta_prompt.yml` via hot-reload
  where the new model's style differs (expect: fewer invented camera
  specifics; watch for verbosity changes inside grammar bounds).
- Re-validate `docs/meta-prompts.md` prose notes.

### Phase 7 — Docs & tests

- Docs: CLAUDE.md (VLM bullets: registry, llama.cpp pin, reasoning-off
  rule, grammar/thinking interaction gotcha), `docs/architecture.md`,
  `docs/build-llama-server.md`, `docs/hardware-detection.md`,
  `docs/configuration.md` (vlm_repos), `docs/future_ideas.md` (video
  tagging, MTP).
- Tests to update/add (inventory §11): `test_vlm_models.py` (now five
  entries + new fields), `test_hardware_vlm_gates.py` (spec-driven
  thresholds + workstation recommends 27B), `test_llama_server_paths.py`
  (new pin/assets), `test_models_vlm_rows.py` (5 rows),
  `test_setup_models_qwen3vl.py` (27B targets + mmproj subdir rename),
  `test_vlm_client_*` (`--reasoning`/`--cache-type` flag emission from
  spec fields, ctx_size), `test_scanner_vlm_routing.py` (new recommended
  ids).

### Explicitly out of scope (future ideas)

- MTP speculative decoding, video-frame tagging, per-request thinking for
  non-grammar calls, raising `_IMAGE_MAX_EDGE`.

---

## 4. Open decisions for review

1. **Remix choice:** OrcaRouter abliterated (matches convention, NSFW lanes
   work) vs official `unsloth`/`ggml-org` GGUF (safer provenance, will
   refuse NSFW). Recommendation: OrcaRouter, consistent with the existing
   all-abliterated registry; the `vlm_repos` override lets anyone swap.
2. **Quant:** Q4_K_M (16.8 GB) recommended; Q5_K_M (19.5 GB) fits the 5090
   too and the remix author suggests it for tool/structured use — could be
   the pinned quant instead at +3 GB. Recommendation: start Q4_K_M, revisit
   after Phase 6 quality checks.
3. **Replace vs. add:** keep `qwen3vl-30b-a3b` in the registry (users may
   have it downloaded) but flip the workstation recommendation to
   `qwen38-27b`. Alternative: deprecate the 30B-A3B row. Recommendation:
   keep + flip recommendation.

## Sources

- https://huggingface.co/Qwen/Qwen3-30B-A3B (text-only, May 2025)
- https://huggingface.co/Qwen/Qwen3.8-27B (model card)
- https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF · https://huggingface.co/unsloth/Qwen3.8-27B-GGUF
- https://huggingface.co/chimingw/Qwen3.8-27B-Uncensored-OrcaRouter-GGUF (abliterated + mmproj + MTP)
- https://github.com/ggml-org/llama.cpp/discussions/27164 (DeltaNet CUDA garbage-output fix ≈ b10450)
- https://github.com/ggml-org/llama.cpp/issues/20345 (grammar inactive while thinking enabled)
- https://unsloth.ai/docs/models/qwen3.8 (quant/VRAM table, reasoning_effort)
- https://dev.to/purpledoubled/run-qwen-38-27b-locally-real-gguf-sizes-the-kv-cache-trick-and-the-template-trap-114j (KV cache math, template trap)

---

## Validation results (2026-08-17, RTX 5090 32 GB / WSL2, llama.cpp b10456)

Spike (Task 3) — all four gates passed on the local b10456 CUDA build:
coherent text (no DeltaNet corruption), GBNF grammar enforced exactly with
`--reasoning off` (finish_reason=stop, no think block), vision OK,
vision+grammar tagging OK. Qwen3-VL 30B-A3B regression-checked clean on the
same binary. GGUF sha256 verified against the repo's SHA256SUMS. VRAM:
~21.7 GB for Q4_K_M + mmproj at `--ctx-size 65536 --parallel 4`.

Task 10 — direct `VlmClient` runs through the real code paths (live
prompts + grammars from `meta_prompt.yml`), 8 library thumbnails:

- **Tagging quality:** `qwen38-27b` is clearly more accurate than
  `qwen3vl-30b-a3b`. The 30B MoE misread one bedroom scene as a
  bathroom/shower scene (invented towels, tile) and labeled adult women
  "mother/daughter/child" on three images; the 27B read all eight scenes
  correctly, picked up finer details (whiteboard, screen reflection,
  tongue-out expression), and correctly emitted an `nsfw` tag where
  warranted.
- **Tagging throughput:** 30B-A3B is ~5× faster (~0.5 s/image vs
  ~2.6 s/image) thanks to its 3.3B active params. Trade-off: the
  workstation recommendation now favors quality (27B); users who scan very
  large libraries can still Load the 30B from the Models tab for speed.
- **Describe / story / sound paths:** subject describe
  (`SUBJECT_DESCRIBE_GRAMMAR`), story outline (`OUTLINE_GRAMMAR` +
  validator), and H3 sound (`SOUND_GRAMMAR` + validator) all parsed
  cleanly on the first attempt. Outline quality was strong (coherent
  noir logline, distinct subject voices, well-formed 5-beat arc). No
  `meta_prompt.yml` changes were needed.
- **Load times:** ~15 s cold load for the 27B; ~4.5 s swap to the 30B.
- **Deferred:** the full in-app storyboard compose/compile pass (running
  server + DB) was deliberately deferred to post-merge to avoid
  restarting the live backend; the API surfaces exercised here are the
  same ones the app calls. The b10456 local binary was promoted to
  `data/bin/local` (previous build kept at `data/bin/local.pre-b10456`).
