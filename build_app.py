#!/usr/bin/env python3
"""
Build script for creating PyInstaller distribution of MetaScan.
This script handles the building process and ensures all required files are included.
"""

import os
import sys
import shutil
import subprocess
import json
from pathlib import Path


def create_distribution_config():
    """Create a distribution-specific config file from config_example.json."""
    # Use config_example.json as the source
    config_example_path = Path("config_example.json")
    
    if config_example_path.exists():
        # Just copy config_example.json as config_dist.json
        with open(config_example_path, 'r') as f:
            dist_config = json.load(f)
    else:
        # Fallback to hardcoded config if config_example.json doesn't exist
        dist_config = {
            "directories": [],
            "filler_words": [
                "<break>",
                "BREAK",
                "background",
                "soft",
                "aesthetic",
                "high",
                "lighting",
                "shot",
                "beautiful",
                "lovely",
                "nice",
                "awesome",
                "amazing",
                "perfect",
                "gorgeous",
                "fantastic",
                "cool",
                "nice looking",
                "superb",
                "wonderful",
                "pretty",
                "great",
                "incredible",
                "stunning",
                "epic",
                "very",
                "really",
                "extremely",
                "highly",
                "best"
            ],
        "tokenization": {
            "heuristics": {
                "comma_density": {
                    "threshold": 0.2,
                    "weight": 0.6
                },
                "stopword_frequency": {
                    "threshold": 0.15,
                    "weight": 0.4
                }
            },
            "classification_threshold": 0.5
        }
    }
    
    # Write the distribution config file
    with open('config_dist.json', 'w') as f:
        json.dump(dist_config, f, indent=2)
    
    print("Created distribution config file: config_dist.json")


def clean_build_dirs():
    """Clean up previous build artifacts."""
    dirs_to_clean = ['build', 'dist']
    for dir_name in dirs_to_clean:
        if os.path.exists(dir_name):
            print(f"Cleaning {dir_name}...")
            shutil.rmtree(dir_name)
    
    # Also clean up the distribution config file
    if os.path.exists('config_dist.json'):
        os.remove('config_dist.json')


def check_requirements():
    """Check if required files exist."""
    required_files = ['main.py', 'icon.png', 'metascan.spec']
    missing_files = []
    
    for file in required_files:
        if not os.path.exists(file):
            missing_files.append(file)
    
    if missing_files:
        print(f"ERROR: Missing required files: {', '.join(missing_files)}")
        return False
    
    return True


def build_app():
    """Build the application using PyInstaller."""
    print("Building MetaScan application...")
    
    # Create distribution config before building
    create_distribution_config()
    
    # Run PyInstaller with the spec file
    result = subprocess.run(
        [sys.executable, '-m', 'PyInstaller', 'metascan.spec', '--clean'],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        print("Build failed!")
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        return False
    
    print("Build completed successfully!")
    return True


def post_build_checks():
    """Perform post-build verification."""
    if sys.platform == 'darwin':
        app_path = Path('dist/MetaScan.app')
        if app_path.exists():
            print(f"✅ macOS app bundle created: {app_path}")
            
            # Check if required files are bundled
            contents_path = app_path / 'Contents'
            if contents_path.exists():
                print("  App bundle structure verified")
            
            return True
    else:
        # For Windows/Linux, check for the executable
        exe_name = 'MetaScan.exe' if sys.platform == 'win32' else 'MetaScan'
        exe_path = Path('dist') / exe_name
        if exe_path.exists():
            print(f"✅ Executable created: {exe_path}")
            return True
    
    print("❌ Build verification failed - executable not found")
    return False


def main():
    """Main build process."""
    print("=" * 60)
    print("MetaScan PyInstaller Build Script")
    print("=" * 60)
    
    # Change to script directory
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Check requirements
    if not check_requirements():
        sys.exit(1)
    
    # Clean previous builds
    clean_build_dirs()
    
    # Build the application
    if not build_app():
        sys.exit(1)
    
    # Verify build
    if not post_build_checks():
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("Build completed successfully!")
    if sys.platform == 'darwin':
        print("You can find the app bundle at: dist/MetaScan.app")
        print("To run: open dist/MetaScan.app")
    else:
        exe_name = 'MetaScan.exe' if sys.platform == 'win32' else 'MetaScan'
        print(f"You can find the executable at: dist/{exe_name}")
        print(f"To run: ./dist/{exe_name}")
    print("=" * 60)


if __name__ == "__main__":
    main()