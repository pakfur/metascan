# System Requirements for Metascan

## Operating System Support
- macOS 10.14+ (Mojave or later)
- Windows 10/11
- Linux (Ubuntu 20.04+, Fedora 34+, or similar)

## Python Requirements
- Python 3.11 or higher
- pip package manager
- venv module (usually included with Python)

## System Dependencies

### Required
- **Python 3.11+**: Core runtime environment

### Optional (for enhanced functionality)
- **FFmpeg**: For video thumbnail generation
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: Download from https://ffmpeg.org/download.html
  
- **exiftool**: For video metadata extraction
  - macOS: `brew install exiftool`
  - Ubuntu/Debian: `sudo apt install libimage-exiftool-perl`
  - Windows: Download from https://exiftool.org/

## Python Virtual Environment Setup

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate
# On Windows:
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies (optional)
pip install -r requirements-dev.txt

# Setup NLTK data
python setup_nltk.py
```

## Disk Space Requirements
- Application: ~100 MB
- Virtual environment: ~500 MB
- Thumbnail cache: Variable (1-5 GB depending on media library size)
- Database: Variable (10-100 MB depending on media library size)

## Memory Requirements
- Minimum: 2 GB RAM
- Recommended: 4 GB RAM or more for large media libraries

## Display Requirements
- Minimum resolution: 1280x720
- Recommended: 1920x1080 or higher
- Color depth: 24-bit or higher

## Qt Platform Notes
- The application uses PyQt6 which includes its own Qt runtime
- No additional Qt installation is required
- Graphics drivers should be up to date for best performance