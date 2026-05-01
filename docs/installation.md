# Installation

[← Back to README](../README.md)

## Prerequisites

- **Python 3.11** (required for package compatibility — 3.13 is not supported)
- **Node.js 18+** (for the Vue frontend)
- **FFMPEG** — required for video thumbnail generation and upscaling
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: Download from [FFmpeg.org](https://ffmpeg.org/download.html)

## Quick Start (End Users)

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

## Development Setup (Contributors)

For local development with all dev tools:

1. **Follow steps 1–4 above, then:**
   ```bash
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

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `METASCAN_HOST` | `0.0.0.0` | Backend server bind address |
| `METASCAN_PORT` | `8700` | Backend server port |
| `METASCAN_API_KEY` | (none) | API key for authenticated access (optional) |
| `METASCAN_CORS_ORIGINS` | `*` | Comma-separated allowed CORS origins |
