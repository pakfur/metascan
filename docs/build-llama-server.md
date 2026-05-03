# Building llama-server From Source

[← Back to README](../README.md)

The Qwen3-VL VLM tagger runs inside a `llama-server` subprocess from
[ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp). Metascan
ships an upstream prebuilt that auto-installs from the Models tab, but
in some cases that prebuilt either doesn't exist or doesn't include the
accelerator your hardware actually has. In those cases you need a
local build.

The single most common case: **Linux with an NVIDIA GPU** (including
WSL2). Upstream `llama.cpp` does not publish a Linux + CUDA prebuilt
for any release tag — only Windows ships CUDA binaries — so a Linux
host with a 4090, 5090, A100, etc. would otherwise run the CPU build
and leave 50–100× perf on the table.

## When to use this

| Platform                 | Bundled prebuilt          | Build from source?              |
|--------------------------|---------------------------|---------------------------------|
| Linux + NVIDIA CUDA      | CPU or Vulkan only        | **Yes** — for CUDA acceleration |
| Linux + AMD ROCm         | CPU or Vulkan only        | **Yes** — for HIP acceleration  |
| Linux + Vulkan           | Vulkan                    | Optional                        |
| Linux CPU only           | CPU                       | No                              |
| WSL2 + NVIDIA CUDA       | CPU only (Vulkan rare)    | **Yes** — for CUDA acceleration |
| macOS arm64 (M1/M2/M3)   | Metal-bundled             | No                              |
| macOS Intel (x86_64)     | none                      | **Yes** — for any inference     |
| Windows + NVIDIA CUDA    | CUDA (cu12.4)             | No                              |
| Windows + Vulkan         | Vulkan                    | No                              |

## Prerequisites

The script preflights everything it needs and prints a single
copy-pasteable install line for whatever's missing — you don't have to
memorize the list. For reference, the full set:

- **`cmake`** ≥ 3.18, **`git`**, and a C/C++ toolchain (`build-essential`
  on Debian/Ubuntu, `@development-tools` on Fedora, `base-devel` on
  Arch, Xcode CLT on macOS).
- **`libcurl` development headers** — llama.cpp's `common` library
  link-checks against curl. apt: `libcurl4-openssl-dev`, dnf:
  `libcurl-devel`, brew: `curl`.
- **`pkg-config`** — used by the cmake configure step to find curl etc.
- **CUDA**: NVIDIA CUDA Toolkit 12.x with `nvcc` on `PATH`. For
  Blackwell cards (RTX 50-series, sm_120) you need **CUDA ≥ 12.8**;
  Ubuntu's `nvidia-cuda-toolkit` apt package historically ships an
  older toolkit that won't natively target sm_120, so install from
  [NVIDIA's repo](https://developer.nvidia.com/cuda-downloads) for
  Blackwell. The script detects this and warns.
- **ROCm**: ROCm 6.x with `hipcc` on `PATH`.
- **Vulkan**: `libvulkan-dev` + `glslc` (shaderc) plus a working ICD
  (verify with `vulkaninfo --summary`).
- **Metal** is on by default on macOS arm64; nothing extra to install.

A Python 3.11+ interpreter (the project's `venv` is fine) is needed at
build-script time so the script can read the pinned llama.cpp release
tag from `metascan.utils.llama_server.LLAMA_CPP_RELEASE`. Activate the
venv first: `source venv/bin/activate`.

## Run the build

```bash
# Auto-detect platform + accelerator:
./scripts/build_llama_server.sh

# Or pin the accelerator explicitly:
./scripts/build_llama_server.sh --accel cuda
./scripts/build_llama_server.sh --accel metal
./scripts/build_llama_server.sh --accel vulkan
./scripts/build_llama_server.sh --accel hip
./scripts/build_llama_server.sh --accel cpu
```

The script:

1. Reads the pinned release tag (currently `b7400`) from the runtime so
   the build matches what the rest of metascan expects.
2. Clones `ggml-org/llama.cpp` at that tag into a temp directory.
3. Configures cmake with the appropriate accelerator flag.
4. Compiles (5–15 min depending on hardware).
5. Installs the `llama-server` binary plus its sister shared libraries
   (`libllama.so`, `libmtmd.so`, `libggml*.so`, …) into
   `data/bin/local/`. macOS dylibs and Linux SONAME symlink chains are
   preserved.

## Two different "CUDA versions" — read this if you're confused

`nvidia-smi` and `nvcc --version` report **different things** and getting
them mixed up is the single most common source of "the build script
yelled at me" reports on Ubuntu/WSL2:

| Source                | Reports                                              | What it means                                                       |
|-----------------------|------------------------------------------------------|---------------------------------------------------------------------|
| `nvidia-smi`          | `CUDA Version: 13.1`                                 | The maximum CUDA your **driver** can *run*. Property of the driver. |
| `nvcc --version`      | `release 12.0`                                       | The version of the CUDA **toolkit/compiler** that will *build* code.|

`nvidia-smi`'s number is a **ceiling** — "I can execute kernels compiled
against any CUDA up to this version." It says nothing about which
toolkit (if any) is installed.

`nvcc --version`'s number is the version of the SDK that's actually
going to compile your code. That's the only number that decides what
GPU archs the build can natively target.

When the two disagree on a Blackwell card (compute capability 12.0+,
i.e. RTX 50-series), it's almost always because Ubuntu's
`nvidia-cuda-toolkit` apt package shipped you an older toolkit (often
12.0). The driver is fine; the compiler is too old to know `sm_120`
exists, so the build silently falls back to PTX for an older arch and
JITs at first kernel launch — works, but you lose native codegen.

## Upgrading the CUDA toolkit on Ubuntu/WSL2

For WSL2, install NVIDIA's repo and a Blackwell-aware toolkit:

```bash
# 1. Add NVIDIA's apt repo (one-time).
wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update

# 2. Install a Blackwell-aware toolkit (12.8+; pick the latest minor).
sudo apt-get install -y cuda-toolkit-12-9

# 3. Remove the Ubuntu-shipped one so it doesn't shadow nvcc.
sudo apt-get remove -y nvidia-cuda-toolkit || true

# 4. Put the new nvcc on PATH (add to ~/.bashrc to make permanent).
export PATH="/usr/local/cuda-12.9/bin:${PATH}"
export LD_LIBRARY_PATH="/usr/local/cuda-12.9/lib64:${LD_LIBRARY_PATH:-}"

# 5. Verify — should now report 12.9.
nvcc --version | grep release
```

For non-WSL Linux, swap `wsl-ubuntu` for the matching distro (e.g.
`ubuntu2404`, `debian12`) — pick the path from the dropdowns at
https://developer.nvidia.com/cuda-downloads.

After upgrading, re-run `./scripts/build_llama_server.sh --accel cuda`.
The Blackwell warning should disappear and the produced binary will
natively target `sm_120`.

## How the override works

`metascan.utils.llama_server.binary_path()` returns
`data/bin/local/<name>` if it exists, otherwise the bundled
`data/bin/<name>`. The local-override directory is checked first by
both:

- the **VLM status row** in `/api/models/status` (so the row shows
  `available` once you've built locally even if you've never clicked
  the Models-tab Download button), and
- the **download flow** in `_download_vlm` (so clicking Download for a
  Qwen3-VL row only fetches the GGUF + mmproj weights, not the bundled
  binary that the local override has already replaced).

`data/` is gitignored, so your build doesn't leak into commits.

## Verify it's loaded

After restarting the backend and clicking **Activate** on a Qwen3-VL
row, the server log should show the accelerator backend loading:

```
llama-server: load_backend: loaded CUDA backend from /…/data/bin/local/libggml-cuda.so
llama-server: ggml_cuda_init: found 1 CUDA devices: ... NVIDIA GeForce RTX 5090
```

Plus a noticeable speedup: per-image tagging on an 8B Q5 model goes
from ~20 s on CPU to ~1 s on a recent CUDA card.

If you still see `load_backend: loaded CPU backend …` after the
rebuild, double-check that:

- `data/bin/local/llama-server --version` reports the same release
  number you intended,
- the cmake configure step printed `-- GGML_CUDA: ON` (or your chosen
  accel flag),
- you restarted the backend (Ctrl+C then `python run_server.py`); a
  running process will keep using the old binary it already spawned.

## Reverting

To go back to the bundled binary, just delete `data/bin/local/`:

```bash
rm -rf data/bin/local
```

The runtime falls through to the bundled binary on the next request.
