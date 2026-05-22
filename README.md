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

Metascan is an open-source browser for AI-generated images and videos that doubles as a general-purpose photo viewer. It automatically extracts metadata from generators like ComfyUI, SwarmUI, and Fooocus — prompts, models, samplers, LoRAs — alongside the camera EXIF and GPS coordinates that come with regular photos, so a single library can mix synthetic and real media without losing context. The built-in viewer handles full-screen images with zoom and pan, plays video with speed and frame-step controls, and runs an ordered or shuffled slideshow with fade transitions.

A FastAPI backend handles scanning, the SQLite database, CLIP/FAISS similarity search, duplicate detection, and Real-ESRGAN/GFPGAN/RIFE upscaling, while a Vue 3 SPA provides the UI. The two halves talk over REST and a multiplexed WebSocket, which means the heavy GPU work can stay on a desktop tower while you browse from any laptop, tablet, or phone on the network. The stack is cross-platform — Linux, macOS (Apple Silicon and Intel), and Windows are first-class, and a hardware probe at startup auto-tunes which CLIP model and upscaler are recommended for your machine.

The library organizes itself two ways. Static folders are manually curated collections you fill by right-clicking items; smart folders are saved rule sets — over tags, model, source, dates, favorite status — that re-resolve as content changes, so a folder like "high-step photoreal generations from last month" stays current without maintenance. Folder mutations broadcast over the WebSocket so multiple browser tabs stay in sync.

<div align="left">
  <img src="/assets/screenshot.jpg" alt="Metascan Main Interface" width="600">
  <img src="/assets/screenshot_map.jpg" alt="OpenMap support for photos" width="600">
</div>
<p align="left"><em>Supports AI Generate and Photos</em></p>

## Screenshots

<div align="left">
  <img src="/assets/media_viewer.jpg" alt="Media Viewer" width="256">
</div>
<p align="left"><em>Media viewer</em></p>

<div align="left">
  <img src="/assets/model_management.jpg" alt="Model Management Dialog" width="256">
  <img src="/assets/smartfolder-create.jpg" alt="SmartFolder Creation" width="256">
</div>
<p align="left"><em>Complete Model Management and Smart Folders</em></p>

## Quick Start

> **First time on this machine?** Start with the [First-Time Setup guide](docs/first_time_setup.md) — it walks through Python 3.11, Node.js, FFmpeg, virtualenv creation, and dependency installation per platform. The steps below assume that's already done.

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

## Documentation

Detailed documentation lives in [`docs/`](docs/):

- **[Features](docs/features.md)** — full feature list, including media browsing, viewer, slideshow, similarity search, duplicates, upscaling, filtering, folders, and keyboard shortcuts
- **[Tech Stack](docs/tech-stack.md)** — backend, frontend, AI/media processing, infrastructure, and dev tooling
- **[First-Time Setup](docs/first_time_setup.md)** — step-by-step, per-platform install of Python, Node, FFmpeg, virtualenv, and all dependencies
- **[Installation](docs/installation.md)** — prerequisites, end-user setup, contributor setup, environment variables
- **[Configuration](docs/configuration.md)** — `config.json` reference, including the `similarity`, `ui`, and `models` sections
- **[API Reference](docs/api-reference.md)** — REST endpoints, WebSocket envelope, error shapes
- **[Architecture](docs/architecture.md)** — client–server layout, database schema, backend/frontend layouts, key design decisions
- **[Hardware Detection](docs/hardware-detection.md)** — what gets probed, tier classification, per-model gates, auto-warnings
- **[Building llama-server](docs/build-llama-server.md)** — when and how to build llama.cpp from source for accelerators upstream doesn't ship (notably Linux + CUDA)
- **[Developer Guidelines](docs/developer-guidelines.md)** — build commands, code style, testing, CI

Contributors should also read [`CONTRIBUTING.md`](CONTRIBUTING.md). The canonical, exhaustive rule set for the codebase lives in [`CLAUDE.md`](CLAUDE.md).

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Thanks to the creators of ComfyUI, SwarmUI, and Fooocus for their amazing AI generation tools
- Real-ESRGAN team for the upscaling models
- OpenCLIP for CLIP model implementations
- Built with Vue 3 and FastAPI for modern web architecture
- Powered by SQLite for efficient local data storage
- Uses FFMPEG for robust video processing capabilities
