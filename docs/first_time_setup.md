# First-Time Setup

[← Back to README](../README.md)

This guide walks you through getting Metascan running from a clean machine, end to end. If you already have Python 3.11, Node.js 20.19+ (or 22.12+), FFmpeg, ExifTool and Git installed, you can skip ahead to [Clone and bootstrap](#3-clone-and-bootstrap).

> **On Windows, read every "Windows" block in each step.** Several steps differ there, and the plain commands install a CPU-only torch and a server that cannot run CLIP search. The Windows steps were last run on 2026-11-01 on Windows 11 with an RTX 5090. [What that run did not cover](#windows-what-is-tested) and [what the installer rebuild must fix](#windows-installer-rebuild-notes) are at the end.

## Why Python 3.11?

Metascan supports **Python 3.11.x and nothing else** — not 3.12, not 3.13 or newer.
`setup.py` declares `python_requires=">=3.11,<3.12"`, CI builds and tests only 3.11,
and `install.sh` refuses to build a virtual environment with any other interpreter.

- **3.13 and newer cannot work.** The pinned `Pillow==10.2.0` publishes no wheel past
  `cp312`, and building its sdist fails outright. Other pinned AI dependencies are in
  the same position.
- **3.12 is untested.** Its wheels resolve, but nothing in CI exercises that
  combination, so it is not a supported configuration.

Stick with 3.11 even if your system ships something newer — installing it alongside
your system Python is safe, and the steps below show how per platform.

## Contents

- [Why Python 3.11?](#why-python-311)
- [1. Install system prerequisites](#1-install-system-prerequisites)
  - [macOS (Apple Silicon and Intel)](#macos-apple-silicon-and-intel)
  - [Windows 10 / 11](#windows-10--11)
  - [Ubuntu / Debian Linux](#ubuntu--debian-linux)
  - [Fedora / RHEL Linux](#fedora--rhel-linux)
  - [Arch Linux](#arch-linux)
- [2. Verify the toolchain](#2-verify-the-toolchain)
- [3. Clone and bootstrap](#3-clone-and-bootstrap)
- [4. Create the Python virtual environment](#4-create-the-python-virtual-environment)
- [5. Install backend dependencies](#5-install-backend-dependencies)
- [6. Download AI models and NLTK data](#6-download-ai-models-and-nltk-data)
- [7. Install frontend dependencies](#7-install-frontend-dependencies)
- [8. First run](#8-first-run)
- [Troubleshooting](#troubleshooting)
- [Windows: what is tested](#windows-what-is-tested)
- [Windows: installer rebuild notes](#windows-installer-rebuild-notes)

---

## 1. Install system prerequisites

You need five things on every platform:

| Tool | Why |
|---|---|
| **Python 3.11** (with `pip` and `venv`) | Backend runtime |
| **Node.js 20.19+ or 22.12+** (with `npm`) | Frontend build/dev server (Vite 8 needs one of these) |
| **FFmpeg** (with `ffprobe`) | Video thumbnail generation, video upscaling |
| **ExifTool** | Video metadata. Without it, every imported video logs an error |
| **Git** | Cloning the repo |

For contributors, you also want a C/C++ toolchain so native Python packages (e.g. `pillow-heif`, `faiss-cpu`) can build from source if no wheel is available.

### macOS (Apple Silicon and Intel)

Install [Homebrew](https://brew.sh/) first, then:

```bash
# Toolchain
xcode-select --install        # provides clang, make, git

# Runtimes
brew install python@3.11 node ffmpeg exiftool
```

Verify Homebrew put `python3.11` on your PATH:

```bash
which python3.11
python3.11 --version          # → Python 3.11.x
```

If `python3.11` is not found, add Homebrew's bin to your shell profile:

```bash
# Apple Silicon
echo 'export PATH="/opt/homebrew/bin:$PATH"' >> ~/.zshrc
# Intel
echo 'export PATH="/usr/local/bin:$PATH"' >> ~/.zshrc
```

### Windows 10 / 11

Run everything on Windows in **PowerShell** unless a step says otherwise. If you have a WSL install of Metascan, stop it first, and see [moving from WSL2](windows-migration.md) to bring your library across.

1. **Python 3.11**, through the Python Install Manager, which is what `py` is on current Windows:
   ```powershell
   winget install --id Python.PythonInstallManager -e   # skip if `py install --help` already works
   py install 3.11
   ```
   This sits beside any other Python and does not change your default. `python` on your PATH may stay a different version, which is fine: everything below goes through `py -3.11` and the virtualenv. If you prefer a classic installer, [python.org's 3.11.9](https://www.python.org/downloads/release/python-3119/) is the last 3.11 release with one.
2. **The other tools**:
   ```powershell
   winget install --id OpenJS.NodeJS.LTS -e
   winget install --id Git.Git -e
   winget install --id Gyan.FFmpeg -e
   winget install --id OliverBetz.ExifTool -e   # shows an administrator prompt
   ```
3. **NVIDIA driver.** Install a current driver if you have an NVIDIA card. You need its CUDA version in step 5. You do **not** need the CUDA Toolkit.
4. **Open a new terminal.** Tools installed above are not on the PATH of a terminal, or a server, that was already open. A server started from an old terminal logs `Failed to extract video metadata ... [WinError 2]` for every video because it cannot find `exiftool`.
5. **Allow the activation script**, if `Get-ExecutionPolicy` says `Restricted`:
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```

You do not need Visual Studio Build Tools. The one package built from source (`basicsr`) is pure Python. Install them (`winget install Microsoft.VisualStudio.2022.BuildTools`, with the "Desktop development with C++" workload) only if `pip` asks for a C++ compiler.

> **WSL2 instead?** Native Windows is now the better fit: a new file reaches the library in seconds, where WSL2 takes up to about 3 minutes on a Windows drive. To stay on WSL2, follow the Ubuntu/Debian instructions. Don't run both at once.

### Ubuntu / Debian Linux

```bash
# Add the deadsnakes PPA to get Python 3.11 on Ubuntu 22.04+ / Debian 12
sudo apt update
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update

# Python 3.11 + venv + dev headers
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# Build toolchain (for native Python wheels)
sudo apt install -y build-essential pkg-config

# FFmpeg + Node.js LTS + Git
sudo apt install -y ffmpeg libimage-exiftool-perl git curl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

### Fedora / RHEL Linux

```bash
# Python 3.11 ships with Fedora 38+; on RHEL/Alma/Rocky use the appstream module
sudo dnf install -y python3.11 python3.11-devel

# Build toolchain, FFmpeg (RPM Fusion), Node.js, Git
sudo dnf install -y @development-tools pkgconf-pkg-config git perl-Image-ExifTool
sudo dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install -y ffmpeg
sudo dnf module install -y nodejs:20/common
```

### Arch Linux

```bash
# AUR has python311 since system Python is rolling
yay -S python311
sudo pacman -S --needed base-devel ffmpeg perl-image-exiftool nodejs npm git
```

## 2. Verify the toolchain

Before going further, sanity-check every tool. All five commands should print a version with no errors:

```bash
python3.11 --version          # → Python 3.11.x
pip --version                 # any recent version
node --version                # → v20.19+ or v22.12+
npm --version                 # → 9.x or higher
ffmpeg -version | head -n 1   # → ffmpeg version 4.x / 5.x / 6.x
git --version
```

On Windows, use this instead, in a new terminal. `python3.11` and `head` are not dependable there, and a bare `pip` belongs to whichever Python is first on PATH, so it tells you nothing yet:

```powershell
py -3.11 --version                    # → Python 3.11.x
node --version                        # → v20.19+ or v22.12+
npm --version
ffmpeg -version | Select-Object -First 1
ffprobe -version | Select-Object -First 1
exiftool -ver
git --version
nvidia-smi                            # NVIDIA only; note the CUDA version at top right
```

If any of these are missing, fix it now — every step below assumes they all work.

## 3. Clone and bootstrap

```bash
git clone https://github.com/pakfur/metascan.git
cd metascan
```

The rest of this guide assumes the `metascan` directory is your working directory.

**Windows:** clone onto an NTFS drive such as `C:`, not exFAT, because SQLite's write-ahead log has not been tested on exFAT. Your media can stay on any drive. Ignore the `venv-win` folder in the repo: it is a broken leftover tracked in git, and `run.bat` uses it, so [don't use `run.bat`](#windows-installer-rebuild-notes).

## 4. Create the Python virtual environment

Always work inside a virtualenv — it isolates Metascan's dependencies from your system Python and avoids permissions issues.

**macOS / Linux / WSL2:**

```bash
python3.11 -m venv venv
source venv/bin/activate
```

**Windows (PowerShell):**

```powershell
py -3.11 -m venv venv
venv\Scripts\Activate.ps1
```

**Windows (Command Prompt):**

```cmd
py -3.11 -m venv venv
venv\Scripts\activate.bat
```

**Windows (Git Bash):**

```bash
py -3.11 -m venv venv
source venv/Scripts/activate
```

Once activated, your prompt is prefixed with `(venv)` and `python --version` should print `Python 3.11.x`. Upgrade pip while you're here:

```bash
python -m pip install --upgrade pip setuptools wheel
```

## 5. Install backend dependencies

With the virtualenv activated:

**Windows with an NVIDIA GPU: install torch first.** The torch on PyPI is CPU-only on Windows, and `pip install` exits cleanly with it. Pick the index from the CUDA version `nvidia-smi` shows:

| `nvidia-smi` CUDA version | Index | Newest torch (2026-11-01) |
|---|---|---|
| 13.2 or higher | `cu132` | 2.15.0 |
| 13.0 or 13.1 | `cu130` | 2.14.1 |
| 12.6 to 12.9 | `cu126` (not for an RTX 50-series card, which needs a `cu128` or newer build) | 2.14.1 |

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu132
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

The last line must print a `+cu...` version, `True` and your GPU's name. Then continue below; `requirements.txt` keeps the CUDA build. Without an NVIDIA GPU, skip this: the CPU build is what you want.

**Every platform:**

```bash
# Production runtime: FastAPI, torch, CLIP, FAISS, Pillow, etc.
pip install -r requirements.txt
```

This pulls in 1.6 GB of packages with CPU torch, or 3.9 GB with CUDA torch on Windows, and takes 5–15 minutes depending on your connection. If a wheel is unavailable for your platform, pip falls back to building from source — that's where the C/C++ toolchain from step 1 comes in.

**Contributors only** — install the dev tools (linters, type checker, test runner):

```bash
pip install -r requirements-dev.txt
```

`requirements-dev.txt` pins `black==25.11.0` to match the production version exactly; CI fails if they drift.

`pip` then warns that `huggingface-hub` wants a newer `click` than the `click==8.2.2` pin. The app's model downloads and CLIP work anyway. On Windows, `make` does not exist: run `pytest`, `black --check`, `mypy` and `flake8` directly (`pip install flake8` first).

## 6. Download AI models and NLTK data

The data for prompt tokenization and the upscaling models are not bundled — fetch them with the helper script:

```bash
python setup_models.py
```

**On Windows, use `python setup_models.py --skip-upscaling`.** A plain run reports success, but its RIFE step fails on Windows and leaves about 860 MB of leftovers in `data/models/` (`rife.zip`, `rife_binary`). Upscaling is due to be replaced by ComfyUI workflows, so there is no reason to download it.

The script downloads:

- **NLTK data** (`punkt`, `stopwords`) — ~5 MB, into NLTK's default folder (`%APPDATA%\nltk_data` on Windows). Only `stopwords` is used at run time.
- **Real-ESRGAN, GFPGAN, RIFE** — ~915 MB total into `data/models/`, used by the upscale queue.

**CLIP weights are not part of this script.** They download the first time Embedding runs, or from **Config → Models**, into the Hugging Face cache (`~/.cache/huggingface/`). A new `config.json` uses the small model (ViT-B-16, about 600 MB of VRAM). `medium` is ViT-L-14 and `large` is ViT-H-14; pick one in **Config → Models**. While it downloads, the panel stays at "Embedding 0 of N".

> **Behind a corporate proxy or air-gapped?** Set `HTTPS_PROXY` and `HTTP_PROXY` before running the script, or pre-populate the cache by copying `~/.cache/huggingface/` and `~/.cache/torch/hub/` from a machine that has internet access.

### Optional: VLM tagging and prompt writing

Tagging with a vision model, and T2I / I2V prompt writing, need a VLM and the `llama-server` binary. Without one, tags come from CLIP and the rest of the app works. Pass a model id: `--vlm` alone is an error.

| Model id | VRAM | Use |
|---|---|---|
| `qwen3vl-2b` / `qwen3vl-4b` | 3.5 / 6 GB | Small cards |
| `qwen3vl-8b` | 9.5 GB | Default prompt writer |
| `qwen3vl-30b-a3b` / `qwen38-27b` | 22 GB | Workstation cards. `qwen38-27b` cannot write prompts |

```bash
python setup_models.py --skip-nltk --skip-upscaling --vlm qwen3vl-8b
```

**Windows with an NVIDIA GPU: add the CUDA runtime.** That command, and the Models tab's Download button, fetch a `llama-server` that cannot see your GPU, because upstream ships the CUDA DLLs in a second archive. Fetch them into the same folder, keeping the release (`b10456`) and CUDA version (`12.4`) the same as in the `llama-server` URL the setup prints:

```powershell
$url = "https://github.com/ggml-org/llama.cpp/releases/download/b10456/cudart-llama-bin-win-cuda-12.4-x64.zip"
Invoke-WebRequest $url -OutFile cudart.zip
Expand-Archive cudart.zip -DestinationPath data\bin -Force
Remove-Item cudart.zip
.\data\bin\llama-server.exe --list-devices
```

The last line must list `CUDA0` and your GPU. If it prints `(none)`, the DLLs are not next to `llama-server.exe`.

Prompt-writer training has its own setup: see [writer training](writer-training.md).

## 7. Install frontend dependencies

The Vue 3 SPA lives under `frontend/`:

```bash
cd frontend
npm install
cd ..
```

`npm install` reads `frontend/package.json` and pulls in Vue, Vite, PrimeVue, Pinia, MapLibre GL, and the rest. Expect about 160 MB in `frontend/node_modules/` and a minute or less on a fast connection. `npm audit` warnings at the end don't stop anything.

**Windows:** also build the UI, so the backend can serve it on its own (re-run this after pulling frontend changes):

```powershell
cd frontend
npm run build
cd ..
```

For contributors, also do a one-time type-check pass to make sure your install is clean:

```bash
cd frontend && npx vue-tsc -b && cd ..
```

## 8. First run

### Windows

**Do not start the backend with `python run_server.py` on Windows, or with `run.bat`.** It looks fine, and imports and thumbnails work, but with its auto-reload on, CLIP search hangs and fails with `NotImplementedError`, and the taste scorer and writer training can't start. Start uvicorn directly, which runs without auto-reload:

```powershell
venv\Scripts\Activate.ps1
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8700
```

Open <http://localhost:8700>. This one terminal serves the built UI. `--host 0.0.0.0` lets other devices on your network connect (Windows Firewall may ask); use `--host 127.0.0.1` to keep it on this PC. To use another port, change `--port`: this command ignores the `METASCAN_PORT` variable.

For frontend work, also run `npm run dev` in `frontend/` and use `http://localhost:5173`. Its proxy always targets port 8700. Backend changes need a manual restart.

### macOS / Linux / WSL2

You need **two terminals**, both with the project root as the working directory.

**Terminal 1 — backend:**

```bash
source venv/bin/activate
python run_server.py
```

The FastAPI server starts on `http://localhost:8700` and serves auto-generated API docs at `http://localhost:8700/docs`.

**Terminal 2 — frontend dev server:**

```bash
cd frontend
npm run dev
```

Vite prints a local URL (usually `http://localhost:5173`). Open it in any modern browser.

### In the app

From the running app:

1. Open **Config** (the gear icon) → **Directories** and add a directory containing your media. On Windows, write a full path such as `D:/Media`.
2. Files in those folders are imported on their own; the activity grid shows the progress. (Config → Maintenance has Full clean & rescan.) On Windows, the Import panel should say `Watching 1 folder live`. Without "live", it has fallen back to slower polling: check `logs\server.log`.
3. Browse the thumbnail grid; double-click for the full-screen viewer; right-click for context actions.

If ComfyUI is running on `127.0.0.1:8188`, Metascan connects to it without any setting.

**Config → Models on Windows** lists both NLTK rows as missing after `setup_models.py`, because that page looks in `~/.metascan/nltk_data` and not where the script puts them. Keyword extraction still works, and `punkt` is not needed with this NLTK.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `python3.11: command not found` | Python 3.11 isn't on your PATH — re-run the install step for your platform. On macOS, check `which python3.11`; on Windows, use `py -3.11`. |
| `WinError 10013` or `10048` starting the server (Windows) | Port 8700 is blocked. A Metascan running in WSL with `networkingMode=mirrored` does this while it runs, and the port can stay blocked after that server stops, with nothing listening in Linux or shown by `netstat`. `wsl --shutdown` cleared it on the test machine, but it stops everything running in WSL. Otherwise, start on another port (`--port 8702`) and open that port's URL. |
| A page answers on port 8700, but shows the wrong library (Windows) | It may be a WSL server: with mirrored networking it answers on Windows' `localhost`. `GET /api/models/hardware` says `"is_wsl": true`. |
| `torch.cuda.is_available()` is `False` (Windows) | PyPI's CPU-only torch was installed. Delete `venv`, recreate it, and install torch first (step 5). |
| CLIP search hangs, then fails; `NotImplementedError` in `logs\server.log` (Windows) | The server was started with `run_server.py` or `run.bat`. Use the uvicorn command in step 8. |
| `llama-server --list-devices` prints `(none)` (Windows) | The CUDA runtime DLLs are missing (step 6). |
| `Failed to extract video metadata ... [WinError 2]` (Windows) | `exiftool` isn't on that server's PATH. Install it, then restart the server from a new terminal. |
| `venv\Scripts\Activate.ps1 cannot be loaded` | Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| `No module named 'faiss.swigfaiss_avx512'` in the log | Harmless. FAISS tries that build, then falls back. |
| `No module named 'torchvision.transforms.functional_tensor'` importing `basicsr` or `realesrgan` directly | Expected. `metascan/core/media_upscaler.py` installs a shim before it imports them. |
| `error: Microsoft Visual C++ 14.0 or greater is required` (Windows) | Install the Visual Studio Build Tools (see the end of step 1 → Windows). |
| `pip install` hangs on `torch` | Network throttle or proxy issue. Try `pip install --no-cache-dir -r requirements.txt`, or pre-download the wheel from [download.pytorch.org](https://download.pytorch.org/whl/torch_stable.html). |
| `pillow-heif` fails to build / segfaults at scan time | Some macOS ARM builds of `libheif` are broken. Metascan auto-disables HEIC if a decode probe fails — scanning still works, HEIC files are just skipped. |
| `npm install` errors on `node-gyp` | Older Node, missing C++ toolchain. Upgrade to Node 20.19+ or 22.12+ and install the platform's build tools. |
| Backend starts but UI is blank / 502 | Vite dev server isn't running. Make sure terminal 2 (`npm run dev`) is active and pointing at port 5173. |
| `RuntimeError: CUDA out of memory` during similarity index build | Pick a smaller CLIP model in **Config → Models**, or set the device to CPU. The hardware probe should already recommend an appropriate tier. |
| Tests fail with `OMP: Error #15` (macOS) | OpenMP duplicate library — already handled by `tests/conftest.py` setting `KMP_DUPLICATE_LIB_OK=TRUE`. If running tests outside pytest, export it manually. |

For deeper issues, check [`docs/installation.md`](installation.md), [`docs/hardware-detection.md`](hardware-detection.md), and the canonical rule set in [`CLAUDE.md`](../CLAUDE.md).

## Windows: what is tested

Run on 2026-11-01: Windows 11 Pro, RTX 5090, driver 610.88, Python 3.11.9, Node 24.15, torch 2.15.0+cu132, in a fresh clone and an empty `data/`, with a small test folder of images and videos.

| Worked | |
|---|---|
| Install order in steps 4 to 7 | Fresh virtualenv, CUDA torch confirmed on the GPU, UI type-check and build |
| Import | Files found and imported in seconds, `Watching 1 folder live` |
| Library | Thumbnails, image and video streaming, and details, for paths with spaces too. The built UI rendered the grid in headless Chrome |
| CLIP | Embedding 6 of 6, text and image search, on CUDA |
| Step 8 | The uvicorn command on port 8700, and the Vite proxy for HTTP, WebSocket and search |
| `llama-server` | Runs and lists the GPU once the runtime DLLs are in place |
| ComfyUI | Connects to a local ComfyUI at startup |

`pytest` on Windows: 5,350 passed, 16 skipped, none failed. On Linux, 5,361 passed, 5 skipped. Seven of the Windows skips test things Windows cannot do, such as deleting an open file. The first Windows run had 189 failures and 21 errors. Most were tests assuming stored paths are native, and three were real bugs, since fixed: training runs dying when the server read `progress.json` during a replace, hygiene treating every library file as new, and moves writing a native path into the stored `media.data` blob. CI still runs Linux only, so Windows can regress unnoticed.

Not tested yet:

- Loading a VLM and tagging (no weights were downloaded, so `--vlm` ran for the binary only), T2I and I2V generation through ComfyUI, writer training, `requirements-train.txt`.
- Upscaling, skipped because it is being replaced. RIFE is known to be broken (below).
- A truly clean machine. Python, Git, Node, FFmpeg and the driver were already there, and the winget commands for Node, Git, FFmpeg and the Install Manager were confirmed to resolve but not run. ExifTool and `py install 3.11` were run.
- `Set-ExecutionPolicy`: the test machine was already `RemoteSigned`.
- The whole-library move in [windows-migration.md](windows-migration.md).

## Windows: installer rebuild notes

Each workaround above exists because of something below. The new installer should remove these, so the steps can shrink.

| Problem | Where | What the rebuild should do |
|---|---|---|
| CLIP search, taste scorer and writer training fail. `reload=True` makes uvicorn pick `SelectorEventLoop`, which cannot spawn subprocesses | `run_server.py`; callers in `inference_client.py`, `taste_runner.py`, `training_manager.py` | Turn reload off on Windows, or set the loop explicitly. Fail loudly: today the search request hangs after the error |
| Plain install gives CPU torch and exits 0 | `requirements.txt` (`torch>=2.0.0`) | Detect the CUDA version, pick the index, and verify `torch.cuda.is_available()`. `install.sh` has the same gap |
| `llama-server` can't see the GPU | `setup_models.py` (`resolve_vlm_targets`), `backend/api/models.py` (`_download_vlm`), `metascan/utils/llama_server.py` | Also fetch `cudart-llama-bin-win-cuda-<ver>-x64.zip`, then check `--list-devices`. Decide between the 12.4 and 13.3 builds for Blackwell |
| `run.bat` is unsafe. `venv-win` is tracked in git, so its `activate.bat` exists but its `python.exe` doesn't: the script skips creating the venv and would run `pip install` into whatever `python` is on PATH (read from the script, not run) | `run.bat`, `venv-win/` | Remove `venv-win` from git. Pick Python with `py -3.11` and refuse other versions. Also fix its "3.11 or 3.12" message |
| `setup_models.py` says "All AI models downloaded successfully" after RIFE failed. The code looks for `rife-ncnn-vulkan` without `.exe`, and leaves `rife.zip` and `rife_binary` behind | `metascan/core/media_upscaler.py` (lines 194, 349, 972) | Moot once upscaling moves to ComfyUI: drop the download and `realesrgan`, `basicsr`, `gfpgan`. Otherwise fix the name |
| CLIP isn't fetched at install, and the first Embedding looks stuck | `setup_models.py` | Download the chosen model, and show progress |
| The default CLIP model is `small` even on a workstation-tier GPU | `config_example.json` | Use the hardware tier's recommendation |
| The Models tab shows NLTK as missing after setup. Script and tab use different folders, and the tab offers `punkt` while NLTK 3.9.1 needs `punkt_tab` | `setup_models.py`, `backend/api/models.py` (`_nltk_data_dir`, `_NLTK_MODELS`) | Use one folder and one list. Only `stopwords` is read at run time |
| `exiftool` missing shows up only as an error per video | `metascan/extractors/comfyui_video.py`; `install.sh` only warns | Check for it at install, including PATH |
| A leftover WSL port block stops the server, and the error doesn't say why | `run_server.py` | Check the port first and explain it |
| `requirements.txt` carries dev tools (`mypy`, `black`), and `requirements-dev.txt` conflicts with `huggingface-hub` on `click` | requirements files | Move them, and loosen the `click` pin |
| A FAISS index built on WSL is keyed by `/mnt/d/...`, so on Windows `has_file` misses every entry and re-embedding a file adds a second row. Search still works. `windows-migration.md` copies that index across and says nothing re-embeds | `metascan/core/embedding_manager.py` (`add`, `has_file`), `faiss_renames.py`, `backend/api/similarity.py` | Key the index by the stored path, or rebuild it as part of the move |
| `make` targets don't exist on Windows, and CI has no Windows job | `Makefile`, `.github/workflows/python-package.yml` | Provide a script, and add a Windows job |
| Other docs repeat the old steps | `windows-migration.md` (`--vlm` with no id, `cu128`, which stops at torch 2.11, `run.bat`), `installation.md`, `writer-training.md`, `SYSTEM_REQUIREMENTS.md`, `install.sh` (Node 20) | Update them, or point them here |
