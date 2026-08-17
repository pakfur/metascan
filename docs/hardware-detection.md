# Hardware Detection & Feature Gating

[← Back to README](../README.md)

Metascan ships a wide range of AI features (CLIP embeddings, Real-ESRGAN, GFPGAN, RIFE, NLTK), and each one has different host requirements. To avoid silent failures and "why is this so slow" surprises, the backend probes the machine on first request and reports a **tier** plus a **gate** for every model. The Models tab in the config dialog renders this as a tier badge and per-row chips.

## What Gets Probed

`metascan/core/hardware.py` runs the following probes once per process (cached via `@lru_cache(maxsize=1)`):

| Probe | What it reads | Why |
|---|---|---|
| Platform / WSL | `platform.system()`, `platform.machine()`, `/proc/version` | Selects MPS path on Apple Silicon, surfaces WSL2 warnings |
| RAM | `psutil.virtual_memory()` (falls back to `/proc/meminfo`) | Gates ViT-H/14 on Apple Silicon (needs ≥ 24 GB unified) |
| CUDA | `torch.cuda.is_available()`, device props | Tier classification, per-model VRAM gates |
| MPS | `torch.backends.mps.is_available()` + `is_built()` | Apple Silicon device selection |
| Vulkan | `vulkaninfo --summary` | Required by RIFE; distinguishes real GPU from `llvmpipe` software fallback |
| glibc | `os.confstr("CS_GNU_LIBC_VERSION")` | `rife-ncnn-vulkan` needs glibc ≥ 2.29 |
| NLTK | `nltk.__version__` | NLTK ≥ 3.8.2 requires `punkt_tab` (CVE-2024-39705 fix) |

All probes are best-effort: a probe that fails leaves its field at the dataclass default, never raises, and never blocks startup.

## Tier Classification

Hosts are bucketed into one of five tiers based on the strongest GPU detected (CUDA always wins over MPS):

| Tier | Trigger | Typical hardware |
|---|---|---|
| `cpu_only` | No CUDA, no MPS | Generic Linux/Windows server, older Intel Macs |
| `apple_silicon` | MPS available on `arm64` Darwin | M1 / M2 / M3 / M4 Macs |
| `cuda_entry` | CUDA present, < 6 GB VRAM | GTX 1060, GTX 1660, RTX 3050 |
| `cuda_mainstream` | CUDA present, 6–12 GB VRAM | RTX 3060, RTX 3070, RTX 4070 |
| `cuda_workstation` | CUDA present, ≥ 12 GB VRAM | RTX 3090, RTX 4090, A4000+ |

## What Each Model Is Gated On

| Model | Gated unavailable when… | Recommended on… |
|---|---|---|
| `clip-small` (ViT-B/32) | Never — runs on anything | All tiers as fallback; default on CPU/Apple Silicon |
| `clip-medium` (ViT-L/14) | CUDA with < 2 GB VRAM | `cuda_entry` (≥ 2 GB) and `cuda_mainstream` |
| `clip-large` (ViT-H/14) | CPU-only, CUDA < 6 GB, or Apple Silicon < 24 GB unified RAM | `cuda_workstation` |
| `resr-x2` | Never (CPU runs at 30–60 s / 1080p) | Any GPU host |
| `resr-x4` | CUDA with < 4 GB VRAM (CPU is "available but slow") | `cuda_mainstream` and `cuda_workstation` |
| `resr-x4-anime` | Never | Any GPU host |
| `gfpgan-v1.4` | CUDA with < 3 GB VRAM | `cuda_mainstream` and `cuda_workstation` |
| `rife` | No Vulkan, or only `llvmpipe` (software) device — typical of WSL2 without GPU drivers | Any host with a real Vulkan device |
| `nltk-punkt` | NLTK ≥ 3.8.2 (replaced by `punkt_tab`) | NLTK < 3.8.2 |
| `nltk-punkt-tab` | NLTK < 3.8.2 (legacy uses `punkt`) | NLTK ≥ 3.8.2 |
| `nltk-stopwords` | Never | Always |
| `qwen3vl-2b` | CUDA < 3 GB VRAM; always available on CPU-only | `cuda_entry` |
| `qwen3vl-4b` | CUDA < 5 GB VRAM; CPU-only with < 16 GB RAM | `cuda_mainstream`; `apple_silicon` |
| `qwen3vl-8b` | CUDA < 10 GB VRAM; CPU-only (needs GPU acceleration) | `cuda_workstation` below the 20 GB `qwen38-27b` floor |
| `qwen3vl-30b-a3b` (MoE) | CUDA < 24 GB VRAM; CPU-only | Never — available from 24 GB up, but `qwen38-27b` is preferred at that tier |
| `qwen38-27b` | CUDA < 20 GB VRAM; CPU-only | `cuda_workstation` ≥ 20 GB VRAM |

`clip-small` / `clip-medium` are also marked "available but not recommended" on CPU-only hosts — they'll run, just very slowly. The chip tooltip surfaces the reason ("ViT-H/14 is too slow on CPU.", "Requires 4 GB VRAM for 1080p; detected 2 GB.", etc.). On Apple Silicon every VLM size is offered as available (opt-in) regardless of unified RAM headroom — there's no RAM-based cutoff for VLM sizes on Mac, unlike CLIP's ViT-H/14 gate.

## Auto-Warnings

The hardware report ships a `warnings` array surfaced as yellow banners in the Models tab:

- **WSL2 without GPU Vulkan** — *"WSL2 detected without GPU Vulkan; RIFE will not work. Install GPU drivers in Windows host."* WSL2 ships an llvmpipe-only Vulkan loader by default; real GPU access requires updated NVIDIA / AMD drivers on the Windows host.
- **Old glibc on Linux** — *"glibc {version} below 2.29; rife-ncnn-vulkan will fail to load."* The bundled `rife-ncnn-vulkan` binary links against glibc 2.29 symbols (Ubuntu 18.04 LTS and older won't work).

## Shared Torch Device Picker

`select_torch_device(preference="auto")` is the single source of truth for "which device should this PyTorch path run on?". `EmbeddingManager._resolve_device` delegates to it, and any future PyTorch path (Real-ESRGAN, GFPGAN if/when wired) should do the same. Precedence: explicit preference (`"cpu"` / `"cuda"` / `"mps"`) is returned verbatim; `"auto"` picks CUDA → MPS (Darwin only) → CPU. **Apple Silicon Macs previously fell through to CPU** for CLIP because the old resolver only checked `cuda.is_available()`; the shared picker fixes that.

## API Surface

| Endpoint | Returns |
|---|---|
| `GET /api/models/hardware` | `{tier, report, ...legacy fields}` — `report` carries every probe field |
| `GET /api/models/status` | Existing model rows + `tier` + `gates: {model_id: {available, recommended, reason}}` |

Hardware detection is per-process and cached for the server lifetime. Plugging in an eGPU at runtime requires a server restart — call `detect_hardware.cache_clear()` if you need to re-probe in tests or scripts.
