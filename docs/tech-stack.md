# Tech Stack

[← Back to README](../README.md)

## Backend
- **Python 3.11** - Core application language
- **FastAPI** - Async REST API and WebSocket server
- **Uvicorn** - ASGI server
- **SQLite** - Local database with WAL mode for concurrency
- **NLTK** - Prompt tokenization and keyword extraction

## Frontend
- **Vue 3** - Composition API with `<script setup>` syntax
- **TypeScript** - Type-safe frontend code
- **Vite** - Build tool with hot module replacement
- **Pinia** - State management (media, filters, settings, scan, similarity, upscale, folders, models stores)
- **PrimeVue** - UI component library (Aura theme)
- **MapLibre GL** - Map rendering for the location metadata panel

## AI / Media Processing
- **open_clip_torch** - CLIP embeddings for content search and similarity
- **faiss-cpu** - Vector similarity index for fast nearest-neighbor search
- **imagehash** - Perceptual hashing for duplicate detection
- **Real-ESRGAN** - AI image/video upscaling
- **GFPGAN** - Face enhancement
- **RIFE** - Video frame interpolation
- **Pillow** - Image processing and thumbnail generation
- **pillow-heif** - HEIC/HEIF decoding
- **ffmpeg-python** - Video processing and thumbnail extraction

## Infrastructure
- **Watchdog** - File system monitoring for real-time updates
- **send2trash** - Safe file deletion (recoverable)
- **portalocker** - Cross-platform file locking for queue safety
- **orjson** - Fast JSON parsing for media deserialization

## Development
- **pytest** - Unit testing
- **black** - Code formatting
- **flake8** - Linting
- **mypy** - Static type checking
- **vue-tsc** - TypeScript checking for Vue
