# MetaScan Build Instructions

How to create a distributable version of MetaScan using PyInstaller.

## Prerequisites

1. Python 3.11+ with all dependencies installed (`pip install -r requirements.txt`)
2. PyInstaller installed (`pip install pyinstaller`)
3. For macOS: Xcode Command Line Tools (for `iconutil`)

## Building the Application

### Quick Build

Simply run the build script:

```bash
python build_app.py
```

This will:
- Clean previous build artifacts
- Create the application bundle/executable  
- Verify the build completed successfully

### Manual Build

If you prefer to run PyInstaller manually:

```bash
# Clean previous builds
rm -rf build dist

# Run PyInstaller
python -m PyInstaller metascan.spec --clean
```

## Build Outputs

### macOS
- **Location**: `dist/MetaScan.app`
- **Type**: Application bundle
- **Run**: `open dist/MetaScan.app` or double-click in Finder

### Windows/Linux  
- **Location**: `dist/MetaScan.exe` (Windows) or `dist/MetaScan` (Linux)
- **Type**: Executable file
- **Run**: `./dist/MetaScan`

## Application Features

The built application includes:

1. **Database Auto-Creation**: The SQLite database is automatically created in:
   - **Development**: `./data/metascan.db`
   - **Bundled App**: `~/.metascan/data/metascan.db`

2. **Configuration Management**: 
   - **Development**: Uses `./config.json`
   - **Bundled App**: Creates user config at `~/.metascan/config.json` from bundled default

3. **Application Icon**: 
   - Uses `icon.png` converted to platform-appropriate format
   - macOS: Automatically converted to ICNS format

4. **Thumbnail Cache**: 
   - Always stored in `~/.metascan/thumbnails/` for persistent caching

## Troubleshooting

### NLTK Data Issues
If NLTK data is missing, the app will:
- Attempt to download stopwords on first run
- Use empty stopword set if download fails
- Store NLTK data in `~/.metascan/nltk_data/`

### Configuration Issues
- The app automatically copies the bundled config to user directory on first run
- User can modify `~/.metascan/config.json` without affecting future app updates

### Database Location
- In bundled apps, database is always in user directory for persistence
- Directory is created automatically if it doesn't exist

## Build Configuration Files

- **metascan.spec**: PyInstaller specification file
- **build_app.py**: Automated build script
- **hook-nltk.py**: PyInstaller hook for NLTK data
- **runtime_hook.py**: Runtime setup for bundled environment
- **create_icns.sh**: Icon conversion script for macOS