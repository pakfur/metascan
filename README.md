# Metascan

**Media browser with metadata extraction and intelligent indexing**

[![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://python.org)
[![PyQt6](https://img.shields.io/badge/PyQt6-GUI-green.svg)](https://www.riverbankcomputing.com/software/pyqt/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python package](https://github.com/pakfur/metascan/actions/workflows/python-package.yml/badge.svg)](https://github.com/pakfur/metascan/actions/workflows/python-package.yml)

## Overview

Metascan is a Open Source, free to use desktop application for browsing, organizing, and analyzing AI-generated images and videos. It automatically extracts metadata from AI generation tools like ComfyUI, SwarmUI, and Fooocus, providing an UI to browse your media collection with filtering capabilities.

<figure>
    <img src="/assets/screenshot.png"
         alt="Metascan Screenshot">
    <figcaption>Screenshot of Metascan showing filters, images and metadata</figcaption>
</figure>

## Quick Start Guide

**First-time setup in 4 easy steps:**

1. **Add Directories**: Click the **Config** button in the toolbar to add folders containing your AI-generated images and videos

2. **Scan Media**: Click the **Scan** button to index your media files and extract metadata (this may take a few minutes for large collections)

3. **Browse Images**: Use the **S/M/L** thumbnail size buttons in the toolbar to adjust thumbnail size for comfortable browsing

4. **Filter Content**: Use the left panel to filter by:
   - **Prompts** - Search text prompts used in generation
   - **Models** - Filter by AI models used
   - **LoRAs** - Filter by specific LoRA models
   - **Custom Tags** - User-defined tags for organization

**Pro Tips**: Double-click any thumbnail to view full-size media with zoom controls. Mark favorites by clicking the star icon (⭐) in the media viewer or pressing "F".

## Features

### Media Support
- Image formats: PNG, JPG, WEBP with embedded metadata
- Video formats: MP4, AVI, MOV with metadata extraction
- Thumbnail generation with FFMPEG integration

### Metadata Extraction
- ComfyUI workflow extraction
- SwarmUI parameter parsing  
- Fooocus metadata support
- Custom prompt tokenization with NLTK

### Filtering & Search
- Filter by prompts, models, LoRAs, and custom tags
- Inverted index for fast search across large collections
- Real-time filter updates
- Favorites for tagging favorite media.

### GUI Interface
- Virtualized thumbnail grid for performance with large collections
- Three-panel layout: filters, thumbnails, metadata
- Resizable panels with persistent layout
- Full-size media viewer with zoom capabilities
- Media deletion with keyboard shortcut (Cmd+D/Ctrl+D)
- Favorites system with star icon in media viewer


## Tech Stack

**Core Technologies:**
- **Python 3.8+** - Core application language
- **PyQt6** - Cross-platform GUI framework
- **SQLite** - Local database for metadata storage
- **NLTK** - Natural language processing for prompt analysis

**Media Processing:**
- **Pillow** - Image processing and thumbnail generation
- **FFMPEG-Python** - Video processing and thumbnail extraction
- **Watchdog** - File system monitoring for real-time updates

**Development Tools:**
- **pytest** - Unit testing framework
- **black** - Code formatting
- **mypy** - Static type checking

## Installation

### Prerequisites

- **Python 3.8 or higher**
- **FFMPEG** - Required for video thumbnail generation
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

4. **Set up NLTK data (first time only):**
   ```bash
   python setup_nltk.py
   ```

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
   
   # Set up NLTK data
   python setup_nltk.py
   ```

2. **Verify development setup:**
   ```bash
   # Run tests
   pytest
   
   # Check code formatting
   black --check metascan/ tests/
   
   # Run type checking
   mypy metascan/
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
   - Click "Scan" to index your media files
   - The scanning process will extract metadata and generate thumbnails
   - Progress is shown in the status bar

3. **Browse and filter:**
   - Use the left panel to filter by prompts, models, LoRAs, and tags
   - View thumbnails in the center panel
   - See detailed metadata in the right panel
   - Double-click thumbnails to view full-size media

### Key Features

- **Filtering:** Click filter items in the left panel to refine your view
- **Favorites:** Mark media as favorites for quick access
  - In media viewer: Click the star icon in the title bar or press `F` key
  - Star icon shows hollow (☆) for non-favorites, filled gold (★) for favorites
  - Use the Favorites filter in the left panel to show only favorite media
- **Search:** Use text filters to search across prompts and metadata
- **Viewer:** Double-click images/videos for full-size viewing with zoom controls
- **Delete Media:** Press `Cmd+D` (macOS) or `Ctrl+D` (Windows/Linux) to delete selected media
  - Works from both the thumbnail view and media viewer
  - Shows confirmation dialog with OK button focused
  - Moves files to system trash/recycle bin (recoverable)
  - Automatically updates database and refreshes views
  - In viewer: navigates to next media (or previous if at end)

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
  "cache_size_mb": 500
}
```

## Testing

Run the test suite:

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=metascan

# Run specific test file
pytest tests/test_prompt_tokenizer.py
```

## Development

### Code Style

The project uses `black` for code formatting and `mypy` for type checking:

```bash
# Format code
black metascan/ tests/

# Type checking
mypy metascan/

# Run both
black metascan/ tests/ && mypy metascan/
```

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
   python -m venv venv
   source venv/bin/activate  # or venv\Scripts\activate on Windows
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   python setup_nltk.py
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

**Testing:**
- Write tests for new features using `pytest`
- Maintain or improve test coverage
- Test UI changes manually across different screen sizes
- Include tests for metadata extraction with sample files

**Commit Guidelines:**
- Use clear, descriptive commit messages
- Reference issues in commits when applicable
- Keep commits atomic and focused on single changes

### Areas for Contribution

**🐛 Bug Reports & Fixes**
- Report bugs with detailed steps to reproduce
- Include system information and error messages
- Fix existing issues marked as "good first issue"

**✨ Feature Requests & Implementation**
- New metadata extractors for additional AI tools
- Additional file format support
- UI/UX improvements and accessibility features
- Performance optimizations for large media collections

**📚 Documentation**
- Improve code documentation and docstrings
- Create tutorials and usage examples
- Translate documentation to other languages

**🧪 Testing & Quality Assurance**
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
   # Run tests
   pytest
   
   # Check code style
   black metascan/ tests/
   mypy metascan/
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

- **Issues:** Use GitHub Issues for bug reports and feature requests
- **Discussions:** Use GitHub Discussions for general questions
- **Code Review:** All contributions go through code review process

Thank you for contributing to Metascan!

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- Thanks to the creators of ComfyUI, SwarmUI, and Fooocus for their amazing AI generation tools
- Built with PyQt6 for cross-platform desktop GUI
- Powered by SQLite for efficient local data storage
- Uses FFMPEG for robust video processing capabilities
