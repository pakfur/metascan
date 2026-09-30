---
paths:
  - "metascan/core/hardware.py"
  - "metascan/core/embedding_manager.py"
  - "backend/api/models.py"
  - "frontend/src/types/hardware.ts"
  - "frontend/src/stores/models.ts"
  - "frontend/src/components/dialogs/ConfigModelsTab.vue"
  - "tests/test_hardware*.py"
  - "tests/test_models_hardware_api.py"
  - "tests/test_embedding_device.py"
---

# Hardware tiers, feature gates and the torch device picker

Moved out of the root CLAUDE.md so it loads only when you work with the files above;
the root CLAUDE.md still applies.

- **Hardware tier + per-model gates.** `metascan/core/hardware.py` runs probes once (`@lru_cache(maxsize=1)` on `detect_hardware()`) for CPU/RAM/CUDA/MPS/Vulkan/glibc/NLTK and classifies hosts into 5 tiers: `cpu_only`, `apple_silicon`, `cuda_entry` (<6 GB VRAM), `cuda_mainstream` (6–12 GB), `cuda_workstation` (≥12 GB). CUDA always wins over MPS. `feature_gates(report)` returns `{model_id: Gate(available, recommended, reason)}` per CLIP/Real-ESRGAN/GFPGAN/RIFE/NLTK model. Auto-warnings populate `report.warnings` for WSL2-without-real-Vulkan and Linux glibc < 2.29 (the latter blocks `rife-ncnn-vulkan`). RIFE is gated unavailable when only `llvmpipe` (software Vulkan) is detected. NLTK ≥ 3.8.2 forces `punkt_tab` over legacy `punkt` (CVE-2024-39705). Both `/api/models/hardware` (returns `{tier, report, ...legacy fields}`) and `/api/models/status` (adds `tier` + `gates`) consume the cached report. The frontend `useModelsStore` exposes `tier`, `gates`, `gateFor(id)`; `ConfigModelsTab.vue` renders a tier banner + per-row recommended/unsupported chips with reason tooltips.
- **Shared torch device picker.** `select_torch_device(preference="auto")` in `hardware.py` is the single source of truth for CUDA → MPS (Darwin only) → CPU precedence. `EmbeddingManager._resolve_device` delegates to it; new PyTorch paths (Real-ESRGAN, GFPGAN if/when wired) should do the same. Explicit preferences (`"cpu"`, `"cuda"`, `"mps"`) are returned verbatim — only `"auto"` triggers detection. **Apple Silicon previously fell through to CPU** for CLIP because the old `_resolve_device` only checked `cuda.is_available()`; the shared picker fixes that gap.

## Common tasks

### Adding a hardware probe / tier rule / feature gate
1. **New probe:** add a `_<thing>()` helper in `metascan/core/hardware.py` that returns `Optional[<value>]` and never raises (catch-and-log at DEBUG). Add a field to the `HardwareReport` dataclass with a safe default. Wire it into `detect_hardware()`. Frontend `HardwareReport` interface in `frontend/src/types/hardware.ts` gets the matching field.
2. **New auto-warning:** append to `report.warnings` inside `detect_hardware()` after probes run. Match the spec wording exactly — frontend renders the strings verbatim in `ConfigModelsTab.vue`'s warning banner.
3. **New tier:** extend the `Tier` enum **and** the TS `Tier` union in `frontend/src/types/hardware.ts`, plus `TIER_LABEL` / `TIER_COLOR` maps. Update `classify_tier()` precedence carefully — CUDA must still win over MPS.
4. **New gate / new model id:** add a key to `feature_gates()`'s returned dict; the model id must match the row id used by `_clip_status_rows` / `_upscale_status_rows` / `_nltk_status_rows` in `backend/api/models.py`. The frontend `gateChip()` / `gateChipClass()` helpers in `ConfigModelsTab.vue` will pick it up automatically.
5. **Tests:** `tests/test_hardware.py` covers probe + tier + gate logic in isolation; `tests/test_models_hardware_api.py` covers the HTTP envelope. Both patch `detect_hardware` (or `backend.api.models.detect_hardware`) to inject a fake `HardwareReport` — never rely on the host's real hardware in tests. Call `detect_hardware.cache_clear()` in any test that mutates env vars before invoking the real probe.
6. **VLM model gate.** Qwen3-VL gates live alongside CLIP gates; their
   `recommended` decision is what `backend/services/scan_dispatch.py:should_tag_with_vlm`
   reads to choose between VLM and CLIP tagging on a scan. Per-model VRAM
   floors come from `metascan/core/vlm_models.REGISTRY`'s `min_vram_gb` field
   (single source of truth — `feature_gates` reads from there).
