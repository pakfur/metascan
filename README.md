# Metascan

**Media browser with metadata extraction and intelligent indexing**

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://python.org)
[![PyQt6](https://img.shields.io/badge/PyQt6-GUI-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python package](https://github.com/pakfur/metascan/actions/workflows/python-package.yml/badge.svg)](https://github.com/pakfur/metascan/actions/workflows/python-package.yml)

# Latest Release v0.2.5

### New Features

## Slideshow
 - New Slideshow feature

## Volume Control
  - Volume slider with real-time adjustment (0-100%)

## Playback Speed Control
  - UI dropdown selector with preset speeds: 0.25x, 0.5x, 0.75x, 1x, 1.25x, 1.5x, 2x
  - Per-file speed persistence - each video remembers its playback speed in the database

## Frame-by-Frame Navigation
  - Previous/Next frame buttons (◀◀ / ▶▶) in the control bar
  - Help overlay (press H or ?) displays all available keyboard shortcuts
  - Shows for 5 seconds with formatted shortcut list

##  Bug Fixes
  - Fixed playback speed dropdown not updating when switching between videos
  - Fixed playback speed changes being ignored when video is paused
  - Fixed playback speed not persisting after app restart
  - Fixed video looping issues: Progress bar now resets correctly and frame navigation works reliably after video loops
  - Switched from Qt's built-in infinite loop to manual loop control for better position tracking

## UI Improvements
  - Reduced internal padding on speed dropdown and mute button for better icon visibility
  - All controls include tooltips showing keyboard shortcuts
  - Control bar now displays: frame nav, play/pause, timeline, time display, speed, volume, and help

  ---
##  Keyboard Shortcuts Quick Reference:

```
  - Space: Play/Pause
  - , / . : Previous/Next Frame
  - ↑ / ↓: Volume Up/Down
  - M: Mute
  - ← / →: Previous/Next Media
  - F: Toggle Favorite
  - Ctrl+D: Delete
  - H or ?: Show Shortcuts
  - Esc: Close Viewer
 ```

## Version v0.2.0

### New Features

#### Queue Pause/Resume
  - The upscaler queue can now be paused and resumed, giving you full control over batch processing
  - Perfect for when you need to free up system resources temporarily

#### Multi-worker Processing
  - Support for 1-4 concurrent upscale workers to maximize hardware utilization
  - Worker count is preserved across application restarts when there are pending tasks

#### Interactive Queue Management
  - Double-click any queue item to open the upscaled file
  - Cmd/Ctrl+double-click to open the file's containing folder
  - Quick access to your upscaled media right from the queue window

#### Frame Interpolation
  - RIFE frame interpolation is now fully functional for video upscaling
  - Smoothly increase frame rates while upscaling (e.g., 24fps → 48fps)
  - Configurable interpolation factors

### Improvements

#### Queue Management
  - Process-safe file locking prevents corruption when multiple instances run
  - Automatic corruption detection and recovery with backup creation
  - Better error handling and recovery from failed tasks

#### Metadata Preservation
  - All AI generation metadata (prompts, models, seeds, LoRAs, etc.) is now preserved correctly after upscaling
  - Only technical properties (dimensions, file size, timestamps) are updated
  - More reliable metadata handling for both images and videos

#### Better User Feedback
  - Improved status messages and progress reporting
  - Clearer error messages when operations fail
  - Enhanced logging for troubleshooting

####  Video Processing
  - Modern FFmpeg parameter usage (fps_mode instead of deprecated vsync)
  - More reliable frame extraction and video compilation
  - Better handling of various video formats and frame rates

#### Bug Fixes

  - Fixed race conditions in queue processing that could cause tasks to be skipped or duplicated
  - Fixed frame interpolation not being applied to videos
  - Fixed metadata being lost after upscaling operations
  - Fixed FFmpeg deprecation warnings
  - Fixed various edge cases in queue state management
  - Removed error-prone metadata copying code

### Dependencies

  New dependencies added:
  - portalocker - Cross-platform file locking for queue safety
  - psutil - Better process management and monitoring
  - Pillow - Image processing utilities

#### Upgrade Notes

  No manual migration required. Existing queue files will be automatically upgraded to the new format. If corruption is detected, a backup will be created before recovery.

  
## Overview

Metascan is an open source desktop application for browsing, organizing, and upscaling AI-generated images and videos. It automatically extracts metadata from AI generation tools like ComfyUI, SwarmUI, and Fooocus, providing a comprehensive interface to manage your media collection with advanced filtering and upscaling capabilities.

<img src="/assets/screenshot.png" alt="Metascan Main Interface" width="600">

## Screenshots

<div align="center">
  <img src="/assets/media_viewer.png" alt="Media Viewer" width="256">
  <img src="/assets/context_menu.png" alt="Context Menu" width="256">
</div>
<p align="center"><em>Media viewer with zoom controls and right-click context menu</em></p>

<div align="center">
  <img src="/assets/upscale.png" alt="Upscale Dialog" width="256">
  <img src="/assets/upscale_queue.png" alt="Upscale Queue" width="256">
</div>
<p align="center"><em>Upscaling configuration dialog and queue management window</em></p>

<div align="center">
  <img src="/assets/upscaling_progress.png" alt="Upscaling Progress" width="256">
</div>
<p align="center"><em>Real-time upscaling progress indicator in toolbar</em></p>

## Quick Start Guide

**First-time setup in 5 easy steps:**

1. **Add Directories**: Click the **Config** button in the toolbar to add folders containing your AI-generated images and videos

2. **Scan Media**: Click the **Scan** button to index your media files and extract metadata (this may take a few minutes for large collections)

3. **Browse Images**: Use the **S/M/L** thumbnail size buttons in the toolbar to adjust thumbnail size for comfortable browsing

4. **Filter Content**: Use the left panel to filter by:
   - **Paths** - Filter by directory and sub-directory
   - **Prompts** - Filter text prompts keywords used in generation
   - **Models** - Filter by AI models used
   - **LoRAs** - Filter by specific LoRA models
   - **File Extension** - Filter by file type

6. **Upscale Media**: Right-click any image or video and select "Upscale" to enhance quality using Real-ESRGAN models

**Pro Tips**: Double-click any thumbnail to view full-size media with zoom controls. Mark favorites by clicking the star icon or pressing "F". Access scanning and theme selection from the **Tools** menu.

## Features

### Media Support
- Image formats: PNG, JPG, WEBP with embedded metadata
- Video formats: MP4, AVI, MOV with metadata extraction
- Thumbnail generation with FFMPEG integration

### Media Upscaling
- Real-ESRGAN powered upscaling for images and videos
- Multiple model options: General, Anime, Face enhancement
- Scale factors: 2x, 4x, 8x upscaling
- Face enhancement option for portrait images
- Frame interpolation for smoother video playback
- Metadata preservation during upscaling
- Process-based queue system for reliable operation
- Real-time progress tracking with cancellation support

### Metadata Extraction
- ComfyUI workflow extraction with enhanced parsing
- SwarmUI parameter parsing  
- Fooocus metadata support with improved detection
- Custom prompt tokenization with NLTK
- Enhanced extractor system for better metadata detection

### Filtering & Search
- Filter by file directories, prompt keywords, models, LoRAs, and file extensions, 
- Inverted index for fast search across large collections
- Real-time filter updates
- Favorites system for organizing preferred media

### User Interface
- Virtualized thumbnail grid for performance with large collections
- Three-panel layout: filters, thumbnails, metadata
- Flexible thumbnail sorting (File Name, Date Added, Date Modified)
- Resizable panels with persistent layout
- Full-size media viewer with zoom capabilities
- Tools menu with centralized access to scanning and themes
- Configurable mouse wheel sensitivity
- Material design theme support with multiple color schemes

### Upscaling Interface
- Dedicated upscaling dialog with model selection
- Queue management window for monitoring operations
- Progress indicators with detailed status information
- Task cancellation and removal capabilities
- Support for batch operations

## Tech Stack

**Core Technologies:**
- **Python 3.8+** - Core application language
- **PyQt6** - Cross-platform GUI framework
- **SQLite** - Local database for metadata storage
- **NLTK** - Natural language processing for prompt analysis

**Media Processing:**
- **Pillow** - Image processing and thumbnail generation
- **FFMPEG-Python** - Video processing and thumbnail extraction
- **Real-ESRGAN** - AI-powered upscaling models
- **Watchdog** - File system monitoring for real-time updates

**Development Tools:**
- **pytest** - Unit testing framework
- **black** - Code formatting
- **mypy** - Static type checking

## Installation 

### Prerequisites

- **Python 3.8 or higher**
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

2. **Set up virtual environment:**
   ```bash
   python -m venv venv
   
   # Activate virtual environment:
   # On macOS/Linux:
   source venv/bin/activate
   # On Windows:
   venv\Scripts\activate
   ```

3. **Install production dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up NLTK data and AI models (first time only):**
   ```bash
   python setup_models.py
   ```
   This will download:
   - NLTK data packages for prompt tokenization (~5 MB)
   - AI upscaling models: RealESRGAN, GFPGAN, RIFE (~915 MB total)
   - Models can be downloaded later when first using upscaling features

5. **Run the application:**
   ```bash
   python main.py
   ```

### Development Setup (Contributors)

For local development with all dev tools:

1. **Follow steps 1-2 above, then:**
   ```bash
   # Install production dependencies
   pip install -r requirements.txt
   
   # Install development dependencies (testing, formatting, type checking)
   pip install -r requirements-dev.txt
   
   # Set up NLTK data and AI models
   python setup_models.py
   ```

2. **Verify development setup:**
   ```bash
   # Run tests
   pytest
   
   # Check code formatting and type checking
   make quality
   ```

### Alternative Installation (Editable)

For development with editable installation:

```bash
pip install -e .
metascan  # Run from anywhere after installation
```

## Usage

### First Launch

1. **Configure scan directories:**
   - Click the "Configure" button in the toolbar
   - Add directories containing your AI-generated media
   - Click "Save" to apply settings

2. **Scan your media:**
   - Use **Tools > Scan** or click "Scan" to index your media files
   - The scanning process will extract metadata and generate thumbnails
   - Progress is shown in the status bar

3. **Browse and filter:**
   - Use the left panel to filter by directory paths, prompt keywords, models, LoRAs, and file extensions
   - View thumbnails in the center panel
   - See detailed metadata in the right panel
   - Double-click thumbnails to view full-size media

### Media Upscaling

1. **Single File Upscaling:**
   - Right-click any image or video in the thumbnail view
   - Select "Upscale" from the context menu
   - Choose upscaling options (model, scale factor, enhancements)
   - Click "Start Upscaling" to begin processing

2. **Queue Management:**
   - Access the upscaling queue via the main menu
   - Monitor progress of active upscaling tasks
   - Cancel or remove tasks as needed
   - View detailed status and error information

3. **Upscaling Options:**
   - **Model Type**: General (photos), Anime (illustrations), Face (portraits)
   - **Scale Factor**: 2x, 4x, or 8x magnification
   - **Face Enhancement**: Improve facial details (images only)
   - **Frame Interpolation**: Smooth video playback (videos only)
   - **Metadata Preservation**: Retain original metadata in upscaled files

### Key Features

- **Sorting:** Organize your media collection with flexible sorting options
  - Access via **View > Sort by** menu with three options:
    - **File Name** - Alphabetical sorting (default)
    - **Date Added** - Sort by creation/scan date
    - **Date Modified** - Sort by file modification date
  - Sort order persists across all operations (filtering, scanning, app restart)
  - Current sort selection shown with checkmark in menu

- **Filtering:** Click filter items in the left panel to refine your view

- **Favorites:** Mark media as favorites for quick access
  - In media viewer: Click the star icon in the title bar or press `F` key
  - Star icon shows hollow for non-favorites, filled gold for favorites
  - Use the Favorites filter in the left panel to show only favorite media

- **Search:** Use text filters to search across prompts and metadata

- **Viewer:** Double-click images/videos for full-size viewing with zoom controls

- **Delete Media:** Press `Cmd+D` (macOS) or `Ctrl+D` (Windows/Linux) to delete selected media
  - Works from both the thumbnail view and media viewer
  - Shows confirmation dialog with OK button focused
  - Moves files to system trash/recycle bin (recoverable)
  - Automatically updates database and refreshes views
  - In viewer: navigates to next media (or previous if at end)

- **Theme Selection:** Access multiple material design themes via **Tools > Themes**

- **Mouse Sensitivity:** Configure scroll wheel sensitivity in the configuration dialog

## Menu Reference

### File Menu
- **Open** - Open selected media in system default application
- **Open Folder** - Open containing folder in file manager
- **Delete file** (Ctrl+D) - Delete selected media file
- **Configuration** - Open settings dialog
- **Exit** (Ctrl+Q) - Close application

### View Menu
- **Refresh** (F5) - Reload media collection
- **Sort by** - Choose sorting method (File Name, Date Added, Date Modified)

### Tools Menu
- **Scan** (Ctrl+S) - Scan directories for new media
- **Themes** - Select application theme

## Configuration

Configuration is stored in `config.json` in the application directory:

```json
{
  "scan_directories": [
    "/path/to/your/ai/images",
    "/path/to/your/ai/videos"
  ],
  "watch_directories": true,
  "thumbnail_size": [300, 300],
  "cache_size_mb": 500,
  "sort_order": "file_name",
  "scroll_wheel_step": 120,
  "theme": "dark_teal.xml"
}
```

### Configuration Options

- **`scan_directories`**: List of directories to scan for media files
- **`watch_directories`**: Enable real-time directory monitoring
- **`thumbnail_size`**: Thumbnail dimensions `[width, height]` in pixels
- **`cache_size_mb`**: Maximum thumbnail cache size in megabytes
- **`sort_order`**: Default thumbnail sorting method
  - `"file_name"` - Alphabetical by filename (default)
  - `"date_added"` - Sort by creation/scan date
  - `"date_modified"` - Sort by file modification date
- **`scroll_wheel_step`**: Mouse wheel scroll sensitivity (pixels per notch)
- **`theme`**: Selected UI theme file

## Development

### Build Commands

The project includes a Makefile for common development tasks:

```bash
# Set up development environment
make setup

# Run the application
make run

# Run tests
make test

# Code quality checks (formatting and type checking)
make quality

# Install package in development mode
make install-dev
```

### Code Style

The project uses `black` for code formatting and `mypy` for type checking:

```bash
# Format code
black metascan/ tests/

# Type checking
mypy metascan/

# Run both (via Makefile)
make quality
```

### Testing

Run the test suite:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=metascan

# Run specific test file
pytest tests/test_prompt_tokenizer.py
```

## Architecture

### Process-Based Upscaling

The application uses a robust process-based architecture for upscaling operations:

- **Worker Processes**: Upscaling runs in isolated subprocess for stability
- **JSON Communication**: Inter-process communication via atomic JSON file operations
- **Queue Management**: Centralized queue with real-time status updates
- **Signal Handling**: Graceful cancellation via SIGTERM signals
- **Progress Tracking**: Real-time progress updates with detailed status information

### Database Structure

Media metadata is stored in a SQLite database with the following key tables:

- **media**: Core media file information and metadata
- **prompts**: Tokenized prompt data for search indexing
- **models**: AI model information extracted from metadata
- **loras**: LoRA model references
- **tags**: User-defined and extracted tags

## Contributing

We welcome contributions to Metascan! Here's how to get started:

### Setting Up Development Environment

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/yourusername/metascan.git
   cd metascan
   ```

2. **Set up development environment:**
   ```bash
   make setup
   ```

3. **Install pre-commit hooks (optional but recommended):**
   ```bash
   pip install pre-commit
   pre-commit install
   ```

### Development Guidelines

**Code Standards:**
- Follow PEP 8 style guidelines
- Use `black` for code formatting
- Add type hints where appropriate
- Write docstrings for public functions and classes
- Maintain mypy type checking compliance

**Testing:**
- Write tests for new features using `pytest`
- Maintain or improve test coverage
- Test UI changes manually across different screen sizes
- Include tests for metadata extraction with sample files
- Test upscaling functionality with various media formats

**Commit Guidelines:**
- Use clear, descriptive commit messages
- Reference issues in commits when applicable
- Keep commits atomic and focused on single changes

### Areas for Contribution

**Bug Reports & Fixes**
- Report bugs with detailed steps to reproduce
- Include system information and error messages
- Fix existing issues marked as "good first issue"

**Feature Requests & Implementation**
- New metadata extractors for additional AI tools
- Additional file format support
- UI/UX improvements and accessibility features
- Performance optimizations for large media collections
- Enhanced upscaling models and options

**Documentation**
- Improve code documentation and docstrings
- Create tutorials and usage examples
- Translate documentation to other languages

**Testing & Quality Assurance**
- Add test coverage for untested code
- Create integration tests
- Test on different operating systems
- Performance testing with large datasets

### Submitting Changes

1. **Create a feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes and test:**
   ```bash
   make test
   make quality
   ```

3. **Commit and push:**
   ```bash
   git add .
   git commit -m "Add your descriptive commit message"
   git push origin feature/your-feature-name
   ```

4. **Create a pull request:**
   - Describe your changes clearly
   - Reference any related issues
   - Include screenshots for UI changes
   - Ensure all tests pass

### Getting Help

- **Issues**: Use GitHub Issues for bug reports and feature requests
- **Discussions**: Use GitHub Discussions for general questions
- **Code Review**: All contributions go through code review process

Thank you for contributing to Metascan!

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Thanks to the creators of ComfyUI, SwarmUI, and Fooocus for their amazing AI generation tools
- Real-ESRGAN team for the upscaling models
- Built with PyQt6 for cross-platform desktop GUI
- Powered by SQLite for efficient local data storage
- Uses FFMPEG for robust video processing capabilities
