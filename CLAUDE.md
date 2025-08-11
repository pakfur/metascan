# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Setup and Installation
```bash
# Set up virtual environment and dependencies
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python setup_nltk.py  # Required for first-time setup

# Development installation (editable)
pip install -e .
```

### Running the Application
```bash
# Run from source
python main.py

# Run after installation
metascan
```

### Testing
```bash
# Run all tests
pytest

# Run specific test files
pytest tests/test_prompt_tokenizer.py
pytest test_components.py
pytest test_metadata_logging.py

# Run with coverage
pytest --cov=metascan
```

### Code Quality
```bash
# Format code
black metascan/ tests/

# Type checking
mypy metascan/

# Run both formatting and type checking
black metascan/ tests/ && mypy metascan/
```

### Building Distributable
```bash
# Build application bundle/executable
python build_app.py

# Manual PyInstaller build
python -m PyInstaller metascan.spec --clean
```

### Debugging and Analysis
```bash
# Analyze metadata extraction errors
python -m metascan.utils.metadata_log_cli analyze-all
python -m metascan.utils.metadata_log_cli retest /path/to/file.png --verbose

# Show extraction statistics
python -m metascan.utils.metadata_log_cli stats
```

## Architecture Overview

### Core Components

**Main Application Structure:**
- `main.py` - Entry point that delegates to UI main window
- `metascan/` - Main package with modular components
- PyQt6-based desktop application with SQLite database backend

**Key Modules:**

1. **Core (`metascan/core/`)**
   - `scanner.py` - Main media scanning and processing logic
   - `database_sqlite.py` - SQLite database operations and queries
   - `media.py` - Media file models and data structures
   - `prompt_tokenizer.py` - NLTK-based prompt analysis and tokenization

2. **Extractors (`metascan/extractors/`)**
   - `base.py` - Abstract base class for metadata extractors
   - `comfyui.py`, `swarmui.py`, `fooocus.py` - AI tool-specific metadata parsers
   - Plugin-based architecture for adding new metadata extractors

3. **UI (`metascan/ui/`)**
   - `main_window.py` - Main application window with threaded scanning
   - `thumbnail_view.py` / `virtual_thumbnail_view.py` - Virtualized thumbnail grid
   - `filters_panel.py` - Dynamic filtering interface
   - `metadata_panel.py` - Metadata display and editing
   - `media_viewer.py` - Full-size media viewer with favorites support

4. **Utilities (`metascan/utils/`)**
   - `app_paths.py` - Cross-platform path management
   - `metadata_logger.py` - Metadata extraction logging and analysis
   - `metadata_log_cli.py` - CLI for debugging extraction issues

### Data Flow

1. **Scanning:** Scanner finds media files → Extractors parse metadata → Database stores results
2. **UI Updates:** Database changes trigger filter/thumbnail updates via Qt signals
3. **Filtering:** FiltersPanel queries database → ThumbnailView displays filtered results
4. **Caching:** Thumbnails cached to `~/.metascan/thumbnails/` for performance

### Configuration

- **Runtime Config:** `config.json` in project root (dev) or `~/.metascan/config.json` (bundled)
- **Database:** SQLite at `./data/metascan.db` (dev) or `~/.metascan/data/metascan.db` (bundled)
- **Distribution:** `build_app.py` creates `config_dist.json` for PyInstaller builds

### Key Dependencies

- **PyQt6** - GUI framework with Material design theme support
- **SQLite** - Local database (replaced LevelDB for macOS compatibility)
- **NLTK** - Prompt tokenization and analysis
- **Pillow + ffmpeg-python** - Image/video processing and thumbnail generation
- **Watchdog** - File system monitoring for real-time updates

### Testing Strategy

- **pytest** framework with specific test modules for components
- Test files include: `test_prompt_tokenizer.py`, `test_components.py`, `test_metadata_logging.py`
- Manual testing utilities: `validate_scan.py`

### Build System

- **PyInstaller** with custom spec file (`metascan.spec`)
- **Runtime hooks** handle NLTK data and bundled environment setup
- **Cross-platform** builds for macOS (.app bundle), Windows (.exe), Linux (executable)

## Development Notes

- **Threaded Operations:** Scanner runs in separate thread with progress reporting via Qt signals
- **Virtual Thumbnails:** Large collections use virtualized views for performance
- **Metadata Logging:** All extraction attempts logged to `logs/` for debugging
- **Plugin Architecture:** New AI tools can be supported by adding extractors to `metascan/extractors/`
- **Qt Material Theme:** Uses qt-material library for modern UI styling