---
paths:
  - "metascan/core/vlm_*.py"
  - "metascan/utils/llama_server.py"
  - "backend/api/vlm.py"
  - "backend/api/chat.py"
  - "backend/services/vlm_tag_pump.py"
  - "backend/services/scan_dispatch.py"
  - "frontend/src/api/vlm.ts"
  - "scripts/build_llama_server.sh"
  - "setup_models.py"
  - "tests/_fake_llama_server.py"
  - "tests/test_vlm_*.py"
  - "tests/test_llama_*.py"
  - "tests/test_setup_models_*.py"
---

# The Qwen-VL tagging pipeline and llama-server

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

- **Qwen3-VL VLM tagging.** A long-running `VlmClient`
  (`metascan/core/vlm_client.py`) supervises a `llama-server` subprocess for
  generative tagging on hardware tiers where it's viable. CLIP tagging
  remains the fallback for `cpu_only` and `cuda_entry`. The DB layer
  arbitrates merging via `_update_indices` and `add_tag_indices` —
  VLM-source tag rows survive CLIP rescans (the demote-on-rescan logic in
  `database_sqlite._update_indices` preserves them). Engine choice rationale
  is in `docs/superpowers/specs/2026-05-02-qwen3vl-tagging-design.md` §11.
  `metascan/core/vlm_models.REGISTRY` now spans two families — four
  Qwen3-VL Abliterated sizes (`qwen3vl-2b/4b/8b/30b-a3b`) plus
  `qwen38-27b` (Qwen3.8 27B Abliterated, dense hybrid Gated DeltaNet) —
  and `qwen38-27b` is the `cuda_workstation` ≥22 GB VRAM recommendation
  (`qwen3vl-8b` remains the pick below that floor).
- **VLM image-only guard.** `VlmClient.generate_tags` short-circuits with
  `[]` for any path whose suffix isn't in `_SUPPORTED_IMAGE_EXTS`. Both
  the scan-time enqueuer (`embedding_worker.py`) and the retag job
  (`backend/api/vlm.py:_run_retag_job`) filter videos upfront so progress
  totals are honest. Use `VlmClient.is_image_path(path)` from new call
  sites instead of duplicating the extension list.
- **VLM image resize.** `_encode_image_b64` decodes via Pillow and
  resizes to `_IMAGE_MAX_EDGE = 1024` before JPEG-encoding. Skipping the
  resize previously blew past the 8K context budget on 2K SDXL renders
  (`HTTP 400 the request exceeds the available context size`). Tagging
  doesn't need fine-print readability — bumping the cap only makes sense
  alongside a `--ctx-size` bump in `_build_command`.
- **GBNF grammar gotcha.** `\-` is not a valid GBNF escape; bad
  grammars crash `llama-server` with SIGSEGV inside
  `llama_grammar_init_impl`, triggering an infinite respawn loop on every
  tag request. Hyphens must be literal (place at the start or end of a
  character class). The tagging grammar lives in
  `metascan/core/vlm_prompts.py:TAGGING_GRAMMAR`.
- **`llama-server` local override.** `binary_path()` returns
  `data/bin/local/<name>` when present, else the bundled
  `data/bin/<name>`. `scripts/build_llama_server.sh` populates the
  override (Linux + NVIDIA CUDA is the most common reason — upstream
  ships no Linux CUDA prebuilt). The override naturally suppresses the
  bundled-asset download because both `_vlm_status_rows` and the
  downloader check `binary_path().exists()`. See
  `docs/build-llama-server.md`.
- **llama.cpp release archive extraction must flatten `bin/`.** The
  release archives ship the binary plus its sister shared libraries
  (`libllama.so`, `libmtmd.so`, `libggml*.so`, …) under `build/bin/`.
  `llama-server`'s `RUNPATH` is `$ORIGIN`, so every `.so` must land in
  the same directory as the binary or it dies at startup with `error
  while loading shared libraries`. From `LLAMA_CPP_RELEASE = "b10456"`
  onward, Linux/macOS assets are `.tar.gz` (Windows stays `.zip`) —
  `setup_models.py` picks `_extract_flat_bin_targz` or
  `_extract_flat_bin_zip` off the URL's suffix in `_ensure_target`.
  Archives also include symlinked SONAME chains (`libllama.so` →
  `libllama.so.0` → `libllama.so.0.0.<build>`) — the zip path preserves
  them via `os.symlink` (zip stores the link target as the file content
  with `S_IFLNK` in `external_attr`), the tar path via
  `TarInfo.issym()`/`.linkname` (tar stores real symlink entries
  natively). Both extractors defer symlink creation until after their
  targets are written.
- **Local llama.cpp builds need explicit RPATH + flat output.**
  `cmake` by default places shared libs alongside their target's
  source dir (`build/tools/mtmd/libmtmd.so`, `build/src/libllama.so`,
  …) and does not bake `$ORIGIN`-relative `RUNPATH` into the build-tree
  binary. Either of those alone is enough to leave the binary unable
  to find its libs after we copy it. The build script forces output
  consolidation (`-DCMAKE_RUNTIME_OUTPUT_DIRECTORY=build/bin
  -DCMAKE_LIBRARY_OUTPUT_DIRECTORY=build/bin`) for both platforms, then
  applies a per-OS rpath strategy. **Linux / ELF:**
  `-DCMAKE_BUILD_RPATH_USE_ORIGIN=ON -DCMAKE_INSTALL_RPATH='$ORIGIN'`.
  **macOS / Mach-O:** `-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON
  -DCMAKE_INSTALL_RPATH='@loader_path'` plus a post-copy
  `install_name_tool -delete_rpath` / `-add_rpath '@loader_path'` /
  `codesign --force --sign -` pass on the binary and every dylib —
  cmake's ELF-only `BUILD_RPATH_USE_ORIGIN` flag is ignored on Mach-O,
  and `CMAKE_INSTALL_RPATH` only fires on `cmake --install` (we just
  `cp`), so without the post-process pass the binary keeps cmake's
  default absolute build-tree rpath and dies the moment the temp dir
  goes away. The verify step (`--version`) now runs **after**
  `rm -rf "${WORK_DIR}"` so any reliance on the build-tree rpath
  surfaces immediately rather than passing verify and failing on first
  user activation.
- **Qwen3.8 reasoning must stay disabled.** `qwen38-27b`'s `extra_args`
  disable thinking at server startup because llama.cpp grammar enforcement
  is inactive while thinking is enabled (ggml-org/llama.cpp#20345) — and
  every metascan VLM call is GBNF-constrained. Never remove those flags
  without moving all call sites off grammars. The model also requires
  llama.cpp >= ~b10450: older CUDA builds load it fine and silently emit
  corrupted tokens (Gated DeltaNet kernel bug). `LLAMA_CPP_RELEASE` is
  pinned accordingly; a stale `data/bin/local/llama-server` built from an
  older tag reproduces the garbage-output failure even with a correct pin.
- **Chat streaming is its own path.** `VlmClient.chat_stream` (used only
  by `backend/api/chat.py`, the Prompt Playground chat window) sends
  `stream: true`, takes a full multi-turn history, attaches the image to
  the first user message only, and sends no grammar. Keep
  `generate_text`'s request shape unchanged for the prompt / T2I / I2V /
  storyboard pipelines rather than folding chat into it.
- **Per-model llama-server flags live in `VlmModelSpec.extra_args`**, and
  the context budget in `VlmModelSpec.ctx_size` — never re-introduce
  model-id string matching in `vlm_client._build_command` or
  `startswith("qwen3vl-")` filters in selection code; use `mid in REGISTRY`.
- **Qwen3-VL pipeline DB writes must run in a worker thread.**
  Both `_run_retag_job` (`backend/api/vlm.py`) and `VlmTagPump.drain_once`
  (`backend/services/vlm_tag_pump.py`) wrap `db.add_tag_indices` with
  `asyncio.to_thread`. WSL2 `/mnt/<drive>` mounts and slow disks
  produce SQLite fsyncs of 50–100 ms+; running that on the event loop
  serializes concurrent tagging tasks and triggers the `heartbeat:
  event loop stalled` warnings.
- **Per-request VLM concurrency must match `parallel_slots`.** Both
  pipelines use a `Semaphore(spec.parallel_slots)` keyed off the
  registry entry for the active model id (`REGISTRY[mid].parallel_slots`).
  Going below it leaves GPU slots idle in `llama-server`'s
  `--parallel`; going above it forces the server to queue requests and
  removes the overlap benefit.
- **VLM status row requires the binary too.** `_vlm_status_rows` in
  `backend/api/models.py` only flips a row to `available` when GGUF +
  mmproj + `binary_path()` are all present. A partial download (weights
  on disk, binary missing) keeps the row at `missing` so the Download
  button stays enabled — `_ensure_target` short-circuits on existing
  files, so retrying only fetches the missing pieces. `size_bytes` is
  reported from whichever weight files exist regardless of binary
  status, so the user still sees partial-download progress.
- **VLM download stage label.** `_download_vlm` broadcasts
  `download_progress` with `stage="downloading (n/3)"` and
  `percent=0.0`. Don't put the GGUF filename in the stage — it's
  several characters longer than the chip can render. The frontend
  `statusLabel` only appends `${pct}%` when `percent > 0`, so keeping
  the percent at 0 avoids the misleading "33%" / "66%" suffix that
  reflects step count, not byte progress.
- **`VlmClient` stderr drainer logs at DEBUG, errors at WARNING.**
  llama-server stderr includes the entire chat-template dump on each
  load (~150 lines) plus per-request slot chatter. Routine lines go to
  DEBUG; lines containing `error`/`failed`/`fatal`/`abort` are
  promoted to WARNING. The 200-line ring buffer (`_stderr_ring`) is
  attached to crash reports by `_wait_exit` so debugging info still
  reaches the user on a real failure.

## Common tasks

### Adding a new VLM caption style
1. Add the style key to `CAPTION_STYLE_PROMPTS` in `metascan/core/vlm_prompts.py`.
2. The style picker in the (future) UI reads keys directly; backend doesn't need a registry change.
3. The style template should be deterministic, single-image, and produce parseable output if the consumer requires structured fields.
4. Wire it into `VlmClient.generate_caption(image_path, style)` once the future captioning feature lands.
