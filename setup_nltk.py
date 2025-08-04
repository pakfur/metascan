#!/usr/bin/env python3
"""
Setup script to download required NLTK data
"""
import nltk
import ssl
import os

# Handle SSL certificate issues
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

def download_nltk_data():
    """Download required NLTK data packages"""
    print("Downloading NLTK data packages...")
    
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

if __name__ == "__main__":
    download_nltk_data()
    print("\nNLTK setup complete!")