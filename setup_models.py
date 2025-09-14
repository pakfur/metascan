#!/usr/bin/env python3
"""
Setup script to download required NLTK data and AI upscaling models
"""
import nltk
import ssl
import os
import sys
from pathlib import Path

# Add project to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from metascan.core.media_upscaler import MediaUpscaler
from metascan.utils.app_paths import get_models_dir

# Handle SSL certificate issues
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

def download_nltk_data():
    """Download required NLTK data packages"""
    print("=" * 60)
    print("Setting up NLTK data packages...")
    print("=" * 60)
    
    # Download required data
    packages = ['stopwords', 'punkt']
    
    for package in packages:
        try:
            nltk.data.find(f'tokenizers/{package}')
            print(f"✓ {package} already downloaded")
        except LookupError:
            print(f"Downloading {package}...")
            nltk.download(package)
            print(f"✓ {package} downloaded successfully")

def download_upscaling_models():
    """Download required AI upscaling models"""
    print("\n" + "=" * 60)
    print("Setting up AI upscaling models...")
    print("=" * 60)
    
    models_dir = get_models_dir()
    print(f"Models directory: {models_dir}")
    
    # Initialize upscaler
    upscaler = MediaUpscaler(
        models_dir=models_dir, 
        device="auto", 
        tile_size=512, 
        debug=False
    )
    
    if upscaler.models_available:
        print("✓ All required models are already available")
        return True
    
    print("Downloading required AI models...")
    print("This may take several minutes depending on your internet connection.")
    print("\nModels to download:")
    print("• RealESRGAN x2 model (~64 MB)")
    print("• RealESRGAN x4 model (~64 MB)")  
    print("• RealESRGAN x4 anime model (~17 MB)")
    print("• GFPGAN face enhancement model (~333 MB)")
    print("• RIFE frame interpolation binary (~437 MB)")
    print("Total download size: ~915 MB\n")
    
    def progress_callback(message: str, progress: float):
        # Simple progress display
        bar_length = 40
        filled_length = int(bar_length * progress / 100)
        bar = "█" * filled_length + "░" * (bar_length - filled_length)
        print(f"\r[{bar}] {progress:6.1f}% - {message}", end="", flush=True)
    
    success = upscaler.setup_models(progress_callback)
    print()  # New line after progress bar
    
    if success:
        print("✓ All AI models downloaded successfully!")
        return True
    else:
        print("✗ Failed to download AI models")
        print("Please check your internet connection and try again.")
        return False

if __name__ == "__main__":
    print("Metascan Setup - Downloading required data and models")
    
    # Download NLTK data
    try:
        download_nltk_data()
        print("✓ NLTK setup complete!")
    except Exception as e:
        print(f"✗ NLTK setup failed: {e}")
        sys.exit(1)
    
    # Download upscaling models
    try:
        models_success = download_upscaling_models()
        if not models_success:
            print("\nWarning: AI model setup failed. Upscaling features will not work.")
            print("You can try running this script again later or download models")
            print("automatically when you first attempt to upscale media.")
    except Exception as e:
        print(f"✗ AI model setup failed: {e}")
        print("Upscaling features will not work until models are downloaded.")
    
    print("\n" + "=" * 60)
    print("Setup complete! You can now run Metascan.")
    print("=" * 60)