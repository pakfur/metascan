# Metascan

**AI media browser with metadata extraction, similarity search, and web-based remote access**

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://python.org)
[![Vue 3](https://img.shields.io/badge/Vue-3-42b883.svg)](https://vuejs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python package](https://github.com/pakfur/metascan/actions/workflows/python-package.yml/badge.svg)](https://github.com/pakfur/metascan/actions/workflows/python-package.yml)

# Latest Release v0.3.0

### Web UI Migration

Metascan now features a modern Vue.js web frontend with a FastAPI backend server, enabling remote access from any platform via a browser while keeping heavy AI/GPU processing on a powerful PC.

- **Vue 3 + TypeScript frontend** served by the FastAPI backend
- **FastAPI backend** with REST API (35+ endpoints) and WebSocket for real-time updates
- **Remote access** from Mac, Windows, Linux, or any device with a browser
- **Three-panel responsive layout** with resizable splitter (filters, thumbnails, metadata)
- **Virtual-scrolling thumbnail grid** for smooth browsing of large collections (12,000+ items)
- **Full-screen media viewer** with image zoom/pan, video player (speed control, frame stepping, volume)
- **Slideshow** with ordered/random modes, fade transitions, configurable timing
- **Multi-phase scan dialog** with real-time WebSocket progress (file-by-file updates)
- **CLIP-powered content search** (text-to-image) and FAISS similarity search with threshold slider
- **Duplicate finder** with pHash grouping, thumbnail previews, and batch deletion
- **Upscale queue** with Real-ESRGAN options, GFPGAN face enhancement, RIFE frame interpolation, pause/resume
- **Similarity settings** dialog with CLIP model/device selection, embedding index build with live progress
- **Configuration dialog** for directory management
- **File watcher** integration with auto-refresh via WebSocket
- **Right-click context menu** on thumbnails (Open, Find Similar, Upscale, Delete)
- **Keyboard shortcuts** for fast navigation (Esc, F5, Ctrl+S, Ctrl+D, arrows, Space, etc.)
- **Dark/light mode** via system `prefers-color-scheme`
- **API key authentication** for secure remote access

### Previous Releases

<details>
<summary>v0.2.5 - Slideshow, Volume Control, Frame Stepping</summary>

- Slideshow feature with ordered/random modes and transition effects
- Volume slider with real-time adjustment (0-100%)
- Playback speed presets (0.25x-2x) with per-file persistence
- Frame-by-frame navigation (comma/period keys)
- Keyboard shortcuts help overlay (H or ?)
</details>

<details>
<summary>v0.2.0 - Upscale Queue, Multi-worker, Frame Interpolation</summary>

- Queue pause/resume for batch processing control
- 1-4 concurrent upscale workers
- RIFE frame interpolation for video upscaling
- Process-safe file locking, corruption detection and recovery
- Metadata preservation after upscaling
- Fixed race conditions, FFmpeg deprecation warnings
</details>

## Overview

Metascan is an open source application for browsing, organizing, and upscaling AI-generated images and videos. It automatically extracts metadata from AI generation tools like ComfyUI, SwarmUI, and Fooocus, providing a comprehensive interface to manage your media collection with advanced filtering, similarity search, and upscaling capabilities.

The application uses a client-server architecture: a Python backend handles scanning, AI processing, and database management, while a Vue.js web frontend provides the browsing interface. This enables running the backend on a powerful GPU machine and accessing it remotely from any device.

<img src="/assets/screenshot.jpg" alt="Metascan Main Interface" width="600">

## Screenshots

<div align="center">
  <img src="/assets/media_viewer.jpg" alt="Media Viewer" width="256">
</div>
<p align="center"><em>Media viewer/em></p>

<div align="center">
  <img src="/assets/model_management.jpg" alt="Model Management Dialog" width="256">
  <img src="/assets/smartfolder_create.jpg" alt="SmartFolder Creation" width="256">
</div>
<p align="center"><em>Complete Model Management and Smart Folders</em></p>

## Quick Start

### Running the Application

1. **Start the backend server:**
   ```bash
   source venv/bin/activate
   python run_server.py
   ```
   The API server starts at `http://localhost:8700` with auto-generated docs at `/docs`.

2. **Start the frontend dev server:**
   ```bash
   cd frontend
   npm run dev
   ```
   Open `http://localhost:5173` in your browser.

3. **Add directories:** Click **Config** in the toolbar to add folders containing your AI-generated media.

4. **Scan media:** Click **Scan** to index files and extract metadata.

5. **Browse and filter:** Use the left panel filters, double-click thumbnails to view full-size, right-click for context menu actions.

### Remote Access

To access Metascan from another device on your network:

```bash
# Start the backend (binds to all interfaces by default)
python run_server.py

# Optionally set an API key for security
METASCAN_API_KEY=your-secret-key python run_server.py
```

Then open `http://<server-ip>:8700` from any browser. For the dev frontend, run `cd frontend && npm run dev -- --host` and access port 5173.

## Features

### Media Browsing
- Virtual-scrolling thumbnail grid for large collections (12,000+ items)
- Three-panel resizable layout: filters, thumbnails, metadata
- Thumbnail size presets (S/M/L)
- Sorting by file name, date added, or date modified
- Favorites system with star toggle
- Dark/light mode following system preference

### Media Viewer
- Full-screen overlay with image zoom (mouse wheel) and pan (click-drag)
- Video player with play/pause, seek bar, volume, playback speed (0.25x-2x)
- Frame-by-frame stepping (comma/period keys)
- Previous/next navigation with keyboard arrows
- Per-file playback speed persistence
- Keyboard shortcuts help overlay

### Slideshow
- Ordered or random (shuffled) playback
- Configurable image duration (3-30 seconds)
- Fade transition with adjustable duration
- Auto-hide controls after 3 seconds
- Video support (no auto-advance, manual navigation)

### Similarity & Content Search
- CLIP-powered text-to-image content search (type a description, find matching media)
- FAISS-based visual similarity search (right-click any thumbnail, "Find Similar")
- Similarity banner with adjustable threshold slider
- Multiple CLIP model sizes (Small/Medium/Large)
- Device selection (Auto/CPU/CUDA)
- Embedding index build/rebuild with live progress

### Hardware Detection & Feature Gating
- Auto-detects CPU / RAM / CUDA / MPS / Vulkan / glibc / NLTK at startup
- Classifies the host into one of five tiers (CPU only, Apple Silicon, CUDA entry/mainstream/workstation)
- Per-model availability + recommendation chips in the Models config tab — green "recommended" picks the right CLIP/upscaler for your tier; red "unsupported" explains *why* on hover (insufficient VRAM, no real Vulkan device, glibc too old, etc.)
- Surfaces actionable warnings: WSL2 without GPU Vulkan blocks RIFE, Linux glibc < 2.29 breaks `rife-ncnn-vulkan`, NLTK ≥ 3.8.2 needs `punkt_tab` instead of legacy `punkt`
- Shared device picker (CUDA → MPS → CPU) so Apple Silicon Macs now use MPS for CLIP inference automatically

### Duplicate Detection
- pHash-based perceptual duplicate grouping
- Side-by-side comparison with thumbnails
- Checkbox selection for batch deletion
- Files moved to trash (recoverable)

### Media Upscaling
- Real-ESRGAN upscaling (2x/4x) with General and Anime models
- GFPGAN face enhancement
- RIFE video frame interpolation (2x/4x/8x FPS multiplier)
- 1-4 concurrent workers
- Queue management with pause/resume, clear completed
- Real-time progress tracking via WebSocket

### Scanning & Indexing
- Multi-phase scan with real-time progress (file-by-file WebSocket updates)
- Stale entry cleanup for deleted files
- Automatic thumbnail generation
- pHash computation during scan
- File system watcher with auto-refresh

### Metadata Extraction
- ComfyUI workflow extraction with enhanced parsing
- SwarmUI parameter parsing
- Fooocus metadata support
- Extracts: prompt, negative prompt, model, sampler, scheduler, steps, CFG scale, seed, LoRAs
- Custom prompt tokenization with NLTK for searchable keywords

### Filtering
- Filter by directory path (with subfolder support)
- Filter by AI source, model, LoRA, file extension, tags, prompt keywords
- Collapsible filter sections with item counts
- AND logic between filter types, OR within same type
- Favorites-only toggle

### Context Menu
- Open (full-screen viewer)
- Find Similar (enter similarity mode)
- Upscale (open upscale dialog)
- Delete (move to trash with confirmation)

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| F5 | Refresh media list |
| Ctrl+S | Open scan dialog |
| Ctrl+Shift+S | Open slideshow |
| Ctrl+Shift+D | Open duplicate finder |
| Ctrl+U | Upscale selected media |
| Esc | Close viewer / exit similarity mode |
| Space | Play/Pause (video) |
| Left/Right | Previous/Next media (in viewer) |
| Up/Down | Volume up/down (video) |
| , / . | Previous/Next frame (video) |
| F | Toggle favorite |
| M | Mute/Unmute (video) |
| Ctrl+D | Delete file |
| H or ? | Show shortcuts help (in viewer) |

## Tech Stack

### Backend
- **Python 3.11** - Core application language
- **FastAPI** - Async REST API and WebSocket server
- **Uvicorn** - ASGI server
- **SQLite** - Local database with WAL mode for concurrency
- **NLTK** - Prompt tokenization and keyword extraction

### Frontend
- **Vue 3** - Composition API with `<script setup>` syntax
- **TypeScript** - Type-safe frontend code
- **Vite** - Build tool with hot module replacement
- **Pinia** - State management (media, filters, settings, scan, similarity, upscale stores)
- **PrimeVue** - UI component library (Aura theme)

### AI / Media Processing
- **open_clip_torch** - CLIP embeddings for content search and similarity
- **faiss-cpu** - Vector similarity index for fast nearest-neighbor search
- **imagehash** - Perceptual hashing for duplicate detection
- **Real-ESRGAN** - AI image/video upscaling
- **GFPGAN** - Face enhancement
- **RIFE** - Video frame interpolation
- **Pillow** - Image processing and thumbnail generation
- **ffmpeg-python** - Video processing and thumbnail extraction

### Infrastructure
- **Watchdog** - File system monitoring for real-time updates
- **send2trash** - Safe file deletion (recoverable)
- **portalocker** - Cross-platform file locking for queue safety
- **orjson** - Fast JSON parsing for media deserialization

### Development
- **pytest** - Unit testing
- **black** - Code formatting
- **mypy** - Static type checking
- **vue-tsc** - TypeScript checking for Vue

## Installation

### Prerequisites

- **Python 3.11** (required for package compatibility)
- **Node.js 18+** (for the Vue frontend)
- **FFMPEG** - Required for video thumbnail generation and upscaling
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: Download from [FFmpeg.org](https://ffmpeg.org/download.html)

### Quick Start (End Users)

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd metascan
   ```

2. **Set up Python backend:**
   ```bash
   python3.11 -m venv venv

   # Activate virtual environment:
   # On macOS/Linux:
   source venv/bin/activate
   # On Windows:
   venv\Scripts\activate

   # Install all dependencies (backend + server)
   pip install -r requirements.txt
   ```

3. **Set up NLTK data and AI models (first time only):**
   ```bash
   python setup_models.py
   ```
   This will download:
   - NLTK data packages for prompt tokenization (~5 MB)
   - AI upscaling models: RealESRGAN, GFPGAN, RIFE (~915 MB total)
   - Models can be downloaded later when first using upscaling features

4. **Set up Vue frontend:**
   ```bash
   cd frontend
   npm install
   cd ..
   ```

5. **Run the application:**
   ```bash
   # Terminal 1: Start backend
   source venv/bin/activate
   python run_server.py

   # Terminal 2: Start frontend
   cd frontend
   npm run dev
   ```
   Open `http://localhost:5173` in your browser.

### Development Setup (Contributors)

For local development with all dev tools:

1. **Follow steps 1-4 above, then:**
   ```bash
   # Install development dependencies
   pip install -r requirements-dev.txt
   ```

2. **Verify development setup:**
   ```bash
   # Run Python tests
   pytest

   # Check Python code quality
   make quality

   # Check frontend types
   cd frontend && npx vue-tsc --noEmit
   ```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `METASCAN_HOST` | `0.0.0.0` | Backend server bind address |
| `METASCAN_PORT` | `8700` | Backend server port |
| `METASCAN_API_KEY` | (none) | API key for authenticated access (optional) |
| `METASCAN_CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins |

## Configuration

Configuration is stored in `config.json` in the application directory:

```json
{
  "directories": [
    {
      "filepath": "/path/to/your/ai/images",
      "search_subfolders": true
    }
  ],
  "watch_directories": true,
  "thumbnail_size": [200, 200],
  "cache_size_mb": 500,
  "sort_order": "date_added",
  "theme": "light_blue_500.xml",
  "similarity": {
    "clip_model": "small",
    "device": "auto",
    "phash_threshold": 10,
    "clip_threshold": 0.7,
    "search_results_count": 100,
    "video_keyframes": 4,
    "compute_phash_during_scan": true
  }
}
```

### Configuration Options

- **`directories`**: List of scan directories with subfolder toggle
- **`watch_directories`**: Enable real-time directory monitoring
- **`thumbnail_size`**: Thumbnail dimensions `[width, height]` in pixels
- **`cache_size_mb`**: Maximum thumbnail cache size in megabytes
- **`sort_order`**: Default sorting (`"date_added"`, `"file_name"`, `"date_modified"`)
- **`theme`**: Selected UI theme
- **`similarity.clip_model`**: CLIP model size (`"small"`, `"medium"`, `"large"`)
- **`similarity.device`**: Compute device (`"auto"`, `"cpu"`, `"cuda"`)
- **`similarity.clip_threshold`**: Similarity search threshold (0-1)
- **`similarity.search_results_count`**: Max similarity search results
- **`similarity.compute_phash_during_scan`**: Compute perceptual hashes during scan

## API Reference

The backend exposes a REST API at `http://localhost:8700`. Full interactive documentation is available at `/docs` (Swagger UI) or `/redoc`.

### Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/media` | List all media (with sort/filter params) |
| GET | `/api/media/{path}` | Get single media record |
| DELETE | `/api/media/{path}` | Delete media (move to trash) |
| PATCH | `/api/media/{path}` | Update favorite/playback speed |
| GET | `/api/stream/{path}` | Stream file with Range support |
| GET | `/api/thumbnails/{path}` | Serve cached thumbnail |
| GET | `/api/filters` | Get filter groups with counts |
| POST | `/api/filters/apply` | Apply filters, return matching paths |
| POST | `/api/scan/prepare` | Count files for scan confirmation |
| POST | `/api/scan/start` | Begin scan (progress via WebSocket) |
| POST | `/api/similarity/search` | Find similar media |
| POST | `/api/similarity/content-search` | CLIP text-to-image search |
| POST | `/api/duplicates/find` | Find duplicate groups |
| POST | `/api/upscale` | Submit upscale tasks |
| GET | `/api/upscale/queue` | List queue tasks |
| WS | `/ws` | Multiplexed WebSocket (scan, upscale, embedding, watcher channels) |

## Architecture

### Client-Server Architecture

```
Browser (Vue 3 SPA)           Backend (FastAPI)
  ├── Pinia stores    ←REST→  ├── API routes
  ├── WebSocket       ←WS──→  ├── WebSocket manager
  └── Components              ├── Services (async wrappers)
                              ├── Core modules (scanner, DB, embeddings)
                              ├── Workers (subprocess: upscale, embedding)
                              └── SQLite database
```

- **Frontend** is a thin client handling display and user interaction
- **Backend** handles all heavy processing: scanning, AI embeddings, upscaling
- **WebSocket** provides real-time progress for scans, upscaling, and file watcher events
- **Subprocess workers** isolate long-running AI tasks (embedding generation, upscaling) from the main server process

### Database Structure

Media metadata is stored in a SQLite database with WAL mode:

- **media**: Serialized Media objects as JSON with favorite status and playback speed
- **indices**: Inverted index for fast filtering (source, model, extension, path, tag, prompt, lora)
- **media_hashes**: Perceptual hashes and CLIP embedding status

## Hardware Detection & Feature Gating

Metascan ships a wide range of AI features (CLIP embeddings, Real-ESRGAN, GFPGAN, RIFE, NLTK), and each one has different host requirements. To avoid silent failures and "why is this so slow" surprises, the backend probes the machine on first request and reports a **tier** plus a **gate** for every model. The Models tab in the config dialog renders this as a tier badge and per-row chips.

### What gets probed

`metascan/core/hardware.py` runs the following probes once per process (cached via `@lru_cache`):

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

### Tier classification

Hosts are bucketed into one of five tiers based on the strongest GPU detected (CUDA always wins over MPS):

| Tier | Trigger | Typical hardware |
|---|---|---|
| `cpu_only` | No CUDA, no MPS | Generic Linux/Windows server, older Intel Macs |
| `apple_silicon` | MPS available on `arm64` Darwin | M1 / M2 / M3 / M4 Macs |
| `cuda_entry` | CUDA present, < 6 GB VRAM | GTX 1060, GTX 1660, RTX 3050 |
| `cuda_mainstream` | CUDA present, 6–12 GB VRAM | RTX 3060, RTX 3070, RTX 4070 |
| `cuda_workstation` | CUDA present, ≥ 12 GB VRAM | RTX 3090, RTX 4090, A4000+ |

### What each model is gated on

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

`clip-small` / `clip-medium` are also marked "available but not recommended" on CPU-only hosts — they'll run, just very slowly. The chip tooltip surfaces the reason ("ViT-H/14 is too slow on CPU.", "Requires 4 GB VRAM for 1080p; detected 2 GB.", etc.).

### Auto-warnings

The hardware report ships a `warnings` array surfaced as yellow banners in the Models tab:

- **WSL2 without GPU Vulkan** — *"WSL2 detected without GPU Vulkan; RIFE will not work. Install GPU drivers in Windows host."* WSL2 ships an llvmpipe-only Vulkan loader by default; real GPU access requires updated NVIDIA / AMD drivers on the Windows host.
- **Old glibc on Linux** — *"glibc {version} below 2.29; rife-ncnn-vulkan will fail to load."* The bundled `rife-ncnn-vulkan` binary links against glibc 2.29 symbols (Ubuntu 18.04 LTS and older won't work).

### Shared torch device picker

`select_torch_device(preference="auto")` is the single source of truth for "which device should this PyTorch path run on?". `EmbeddingManager._resolve_device` delegates to it, and any future PyTorch path (Real-ESRGAN, GFPGAN if/when wired) should do the same. Precedence: explicit preference (`"cpu"` / `"cuda"` / `"mps"`) is returned verbatim; `"auto"` picks CUDA → MPS (Darwin only) → CPU. **Apple Silicon Macs previously fell through to CPU** for CLIP because the old resolver only checked `cuda.is_available()`; the shared picker fixes that.

### API surface

| Endpoint | Returns |
|---|---|
| `GET /api/models/hardware` | `{tier, report, ...legacy fields}` — `report` carries every probe field |
| `GET /api/models/status` | Existing model rows + `tier` + `gates: {model_id: {available, recommended, reason}}` |

Hardware detection is per-process and cached for the server lifetime. Plugging in an eGPU at runtime requires a server restart — call `detect_hardware.cache_clear()` if you need to re-probe in tests or scripts.

## Development

### Build Commands

```bash
# Backend
make serve            # Run the FastAPI backend server
python run_server.py  # Same, without make

# Frontend
cd frontend
npm run dev        # Development server with HMR
npm run build      # Production build (vue-tsc + vite)
npx vue-tsc --noEmit  # Type check only

# Quality
make test          # Run Python tests
make quality       # Black formatting + mypy type checking
```

### Code Style

**Python:** `black` for formatting, `mypy` for type checking
**TypeScript/Vue:** `vue-tsc` for type checking, Vite for building

```bash
# Python
black metascan/ tests/ backend/
mypy metascan/

# Frontend
cd frontend && npx vue-tsc --noEmit
```

### Testing

```bash
# Python tests
pytest
pytest --cov=metascan
pytest tests/test_prompt_tokenizer.py

# Frontend type checking
cd frontend && npx vue-tsc --noEmit

# Frontend production build verification
cd frontend && npm run build
```

## Contributing

We welcome contributions to Metascan! Here's how to get started:

### Setting Up Development Environment

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/yourusername/metascan.git
   cd metascan
   ```

2. **Set up both backend and frontend:**
   ```bash
   # Backend
   python3.11 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   python setup_models.py

   # Frontend
   cd frontend && npm install && cd ..
   ```

### Development Guidelines

**Code Standards:**
- Follow PEP 8 style guidelines for Python
- Use TypeScript for all frontend code
- Use `black` for Python formatting, `vue-tsc` for frontend type checking
- Add type hints where appropriate
- Write docstrings for public functions and classes

**Testing:**
- Write tests for new features using `pytest`
- Maintain or improve test coverage
- Verify frontend builds cleanly (`npm run build`)
- Test both backend API endpoints and frontend components

**Commit Guidelines:**
- Use clear, descriptive commit messages with conventional prefixes (`feat:`, `fix:`, `refactor:`)
- Reference issues in commits when applicable
- Keep commits atomic and focused on single changes

### Submitting Changes

1. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes and test:**
   ```bash
   make test
   make quality
   cd frontend && npm run build
   ```

3. **Commit and push:**
   ```bash
   git add .
   git commit -m "feat: add your descriptive commit message"
   git push origin feature/your-feature-name
   ```

4. **Create a pull request** with a clear description, related issues, and screenshots for UI changes.

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Thanks to the creators of ComfyUI, SwarmUI, and Fooocus for their amazing AI generation tools
- Real-ESRGAN team for the upscaling models
- OpenCLIP for CLIP model implementations
- Built with Vue 3 and FastAPI for modern web architecture
- Powered by SQLite for efficient local data storage
- Uses FFMPEG for robust video processing capabilities
