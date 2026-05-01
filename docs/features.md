# Features

[← Back to README](../README.md)

## Media Browsing
- Virtual-scrolling thumbnail grid for large collections (12,000+ items)
- Three-panel resizable layout: filters, thumbnails, metadata
- Thumbnail size presets (S/M/L)
- Sorting by file name, date added, or date modified
- Favorites system with star toggle
- Dark/light mode following system preference

## Media Viewer
- Full-screen overlay with image zoom (mouse wheel) and pan (click-drag)
- Video player with play/pause, seek bar, volume, playback speed (0.25x-2x)
- Frame-by-frame stepping (comma/period keys)
- Previous/next navigation with keyboard arrows
- Per-file playback speed persistence
- Keyboard shortcuts help overlay

## Slideshow
- Ordered or random (shuffled) playback
- Configurable image duration (3-30 seconds)
- Fade transition with adjustable duration
- Auto-hide controls after 3 seconds
- Video support (no auto-advance, manual navigation)

## Similarity & Content Search
- CLIP-powered text-to-image content search (type a description, find matching media)
- FAISS-based visual similarity search (right-click any thumbnail, "Find Similar")
- Similarity banner with adjustable threshold slider
- Multiple CLIP model sizes (Small/Medium/Large)
- Device selection (Auto/CPU/CUDA)
- Embedding index build/rebuild with live progress

## Hardware Detection & Feature Gating
- Auto-detects CPU / RAM / CUDA / MPS / Vulkan / glibc / NLTK at startup
- Classifies the host into one of five tiers (CPU only, Apple Silicon, CUDA entry/mainstream/workstation)
- Per-model availability + recommendation chips in the Models config tab — green "recommended" picks the right CLIP/upscaler for your tier; red "unsupported" explains *why* on hover (insufficient VRAM, no real Vulkan device, glibc too old, etc.)
- Surfaces actionable warnings: WSL2 without GPU Vulkan blocks RIFE, Linux glibc < 2.29 breaks `rife-ncnn-vulkan`, NLTK ≥ 3.8.2 needs `punkt_tab` instead of legacy `punkt`
- Shared device picker (CUDA → MPS → CPU) so Apple Silicon Macs now use MPS for CLIP inference automatically

See [Hardware Detection](hardware-detection.md) for the full probe / tier / gate reference.

## Duplicate Detection
- pHash-based perceptual duplicate grouping
- Side-by-side comparison with thumbnails
- Checkbox selection for batch deletion
- Files moved to trash (recoverable)

## Media Upscaling
- Real-ESRGAN upscaling (2x/4x) with General and Anime models
- GFPGAN face enhancement
- RIFE video frame interpolation (2x/4x/8x FPS multiplier)
- 1-4 concurrent workers
- Queue management with pause/resume, clear completed
- Real-time progress tracking via WebSocket

## Scanning & Indexing
- Multi-phase scan with real-time progress (file-by-file WebSocket updates)
- Stale entry cleanup for deleted files
- Automatic thumbnail generation
- pHash computation during scan
- File system watcher with auto-refresh

## Metadata Extraction
- ComfyUI workflow extraction with enhanced parsing
- SwarmUI parameter parsing
- Fooocus metadata support
- Extracts: prompt, negative prompt, model, sampler, scheduler, steps, CFG scale, seed, LoRAs
- Custom prompt tokenization with NLTK for searchable keywords

## Filtering
- Filter by directory path (with subfolder support)
- Filter by AI source, model, LoRA, file extension, tags, prompt keywords
- Collapsible filter sections with item counts
- AND logic between filter types, OR within same type
- Favorites-only toggle

## Folders (Static & Smart)
- **Static folders** — manually curated collections; right-click items to add/remove
- **Smart folders** — saved rules over fields like tags, model, source, modified/added date, favorite status. Evaluated client-side and re-resolved as the library changes
- Cross-tab sync over WebSocket: creating, renaming, or moving items broadcasts to every connected browser

## Context Menu
- Open (full-screen viewer)
- Find Similar (enter similarity mode)
- Upscale (open upscale dialog)
- Delete (move to trash with confirmation)

## Keyboard Shortcuts

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
