# First-Time Setup

[← Back to README](../README.md)

This guide walks you through getting Metascan running from a clean machine, end to end. If you already have Python 3.11, Node.js 18+, FFmpeg, and Git installed, you can skip ahead to [Clone and bootstrap](#3-clone-and-bootstrap).

> **Why Python 3.11?** Several AI dependencies (torch, FAISS, open_clip) do not yet have wheels for Python 3.13, and 3.12 is missing `distutils` which some upscaler packages still require. Stick with **3.11** even if your system ships something newer.

## Contents

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

---

## 1. Install system prerequisites

You need four things on every platform:

| Tool | Why |
|---|---|
| **Python 3.11** (with `pip` and `venv`) | Backend runtime |
| **Node.js 18+** (with `npm`) | Frontend build/dev server |
| **FFmpeg** | Video thumbnail generation, video upscaling |
| **Git** | Cloning the repo |

For contributors, you also want a C/C++ toolchain so native Python packages (e.g. `pillow-heif`, `faiss-cpu`) can build from source if no wheel is available.

### macOS (Apple Silicon and Intel)

Install [Homebrew](https://brew.sh/) first, then:

```bash
# Toolchain
xcode-select --install        # provides clang, make, git

# Runtimes
brew install python@3.11 node ffmpeg
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

The smoothest path is the official installers:

1. **Python 3.11** — download from [python.org/downloads/release/python-3119/](https://www.python.org/downloads/release/python-3119/). On the first installer screen, **check "Add python.exe to PATH"**, then choose "Customize installation" and ensure **pip** and **venv** are selected. Do **not** install 3.12 or 3.13.
2. **Node.js LTS** — download from [nodejs.org](https://nodejs.org/). The LTS installer includes `npm` and adds it to PATH.
3. **Git for Windows** — download from [git-scm.com](https://git-scm.com/download/win). Accept the defaults; this also installs Git Bash, which is the recommended shell for the commands in this guide.
4. **FFmpeg** — the easiest way is via [winget](https://learn.microsoft.com/en-us/windows/package-manager/winget/):
   ```powershell
   winget install --id=Gyan.FFmpeg -e
   ```
   Or download a static build from [ffmpeg.org/download.html](https://ffmpeg.org/download.html) and add the `bin` folder to your `PATH` environment variable.
5. **Build tools** (only if you need to compile native extensions):
   ```powershell
   winget install Microsoft.VisualStudio.2022.BuildTools
   ```
   Select the "Desktop development with C++" workload during install.

> **WSL2 users:** follow the Ubuntu/Debian instructions instead — running Metascan inside WSL gives you GPU acceleration on supported NVIDIA cards and avoids most of the native-build pain.

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
sudo apt install -y ffmpeg git curl
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

### Fedora / RHEL Linux

```bash
# Python 3.11 ships with Fedora 38+; on RHEL/Alma/Rocky use the appstream module
sudo dnf install -y python3.11 python3.11-devel

# Build toolchain, FFmpeg (RPM Fusion), Node.js, Git
sudo dnf install -y @development-tools pkgconf-pkg-config git
sudo dnf install -y https://download1.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm
sudo dnf install -y ffmpeg
sudo dnf module install -y nodejs:20/common
```

### Arch Linux

```bash
# AUR has python311 since system Python is rolling
yay -S python311
sudo pacman -S --needed base-devel ffmpeg nodejs npm git
```

## 2. Verify the toolchain

Before going further, sanity-check every tool. All five commands should print a version with no errors:

```bash
python3.11 --version          # → Python 3.11.x
pip --version                 # any recent version
node --version                # → v18.x or higher
npm --version                 # → 9.x or higher
ffmpeg -version | head -n 1   # → ffmpeg version 4.x / 5.x / 6.x
git --version
```

If any of these are missing, fix it now — every step below assumes they all work.

## 3. Clone and bootstrap

```bash
git clone https://github.com/pakfur/metascan.git
cd metascan
```

The rest of this guide assumes the `metascan` directory is your working directory.

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

**Windows (Command Prompt or Git Bash):**

```cmd
py -3.11 -m venv venv
venv\Scripts\activate
```

Once activated, your prompt is prefixed with `(venv)` and `python --version` should print `Python 3.11.x`. Upgrade pip while you're here:

```bash
python -m pip install --upgrade pip setuptools wheel
```

## 5. Install backend dependencies

With the virtualenv activated:

```bash
# Production runtime: FastAPI, torch, CLIP, FAISS, Pillow, etc.
pip install -r requirements.txt
```

This pulls in roughly 1–2 GB of packages (torch alone is ~700 MB) and can take 5–15 minutes depending on your connection. If a wheel is unavailable for your platform, pip falls back to building from source — that's where the C/C++ toolchain from step 1 comes in.

**Contributors only** — install the dev tools (linters, type checker, test runner):

```bash
pip install -r requirements-dev.txt
```

`requirements-dev.txt` pins `black==25.11.0` to match the production version exactly; CI fails if they drift.

## 6. Download AI models and NLTK data

The default models for prompt tokenization, similarity search, and upscaling are not bundled — fetch them with the helper script:

```bash
python setup_models.py
```

This downloads, into the application data directory:

- **NLTK data** (`punkt_tab`, `stopwords`) — ~5 MB, used by the prompt-keyword extractor.
- **CLIP model weights** — selected based on your hardware tier (small / medium / large). Sizes range from ~600 MB (ViT-B/32) to ~3.5 GB (ViT-L/14).
- **Real-ESRGAN, GFPGAN, RIFE** — ~915 MB total, used by the upscale queue.

You can skip this step and let the models download lazily the first time you trigger a similarity search or upscale, but doing it up front gives you a single progress bar to watch instead of a surprise stall later.

> **Behind a corporate proxy or air-gapped?** Set `HTTPS_PROXY` and `HTTP_PROXY` before running the script, or pre-populate the cache by copying `~/.cache/huggingface/` and `~/.cache/torch/hub/` from a machine that has internet access.

## 7. Install frontend dependencies

The Vue 3 SPA lives under `frontend/`:

```bash
cd frontend
npm install
cd ..
```

`npm install` reads `frontend/package.json` and pulls in Vue, Vite, PrimeVue, Pinia, MapLibre GL, and the rest. Expect ~400 MB in `frontend/node_modules/` and 1–3 minutes on a fast connection.

For contributors, also do a one-time type-check pass to make sure your install is clean:

```bash
cd frontend && npx vue-tsc --noEmit && cd ..
```

## 8. First run

You need **two terminals**, both with the project root as the working directory.

**Terminal 1 — backend:**

```bash
# macOS / Linux / WSL2
source venv/bin/activate
python run_server.py

# Windows PowerShell
venv\Scripts\Activate.ps1
python run_server.py
```

The FastAPI server starts on `http://localhost:8700` and serves auto-generated API docs at `http://localhost:8700/docs`.

**Terminal 2 — frontend dev server:**

```bash
cd frontend
npm run dev
```

Vite prints a local URL (usually `http://localhost:5173`). Open it in any modern browser.

From the running app:

1. Click **Config** → **Folders** and add a directory containing your media.
2. Click **Scan** to index files. The scan dialog streams real-time progress over WebSocket.
3. Browse the thumbnail grid; double-click for the full-screen viewer; right-click for context actions.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `python3.11: command not found` | Python 3.11 isn't on your PATH — re-run the install step for your platform. On macOS, check `which python3.11`; on Windows, reinstall and tick "Add to PATH". |
| `error: Microsoft Visual C++ 14.0 or greater is required` (Windows) | Install the Visual Studio Build Tools (see step 1 → Windows → "Build tools"). |
| `pip install` hangs on `torch` | Network throttle or proxy issue. Try `pip install --no-cache-dir -r requirements.txt`, or pre-download the wheel from [download.pytorch.org](https://download.pytorch.org/whl/torch_stable.html). |
| `pillow-heif` fails to build / segfaults at scan time | Some macOS ARM builds of `libheif` are broken. Metascan auto-disables HEIC if a decode probe fails — scanning still works, HEIC files are just skipped. |
| `npm install` errors on `node-gyp` | Older Node, missing C++ toolchain. Upgrade to Node 18+ and install the platform's build tools. |
| Backend starts but UI is blank / 502 | Vite dev server isn't running. Make sure terminal 2 (`npm run dev`) is active and pointing at port 5173. |
| `RuntimeError: CUDA out of memory` during similarity index build | Pick a smaller CLIP model in **Config → Models**, or set the device to CPU. The hardware probe should already recommend an appropriate tier. |
| Tests fail with `OMP: Error #15` (macOS) | OpenMP duplicate library — already handled by `tests/conftest.py` setting `KMP_DUPLICATE_LIB_OK=TRUE`. If running tests outside pytest, export it manually. |

For deeper issues, check [`docs/installation.md`](installation.md), [`docs/hardware-detection.md`](hardware-detection.md), and the canonical rule set in [`CLAUDE.md`](../CLAUDE.md).
