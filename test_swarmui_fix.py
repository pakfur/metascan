#!/usr/bin/env python3

import sys
from pathlib import Path
from metascan.extractors.swarmui import SwarmUIExtractor
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)

def test_swarmui_extraction(file_path):
    """Test SwarmUI metadata extraction on a single file"""
    extractor = SwarmUIExtractor()
    path = Path(file_path)
    
    if not path.exists():
        print(f"File not found: {file_path}")
        return False
    
    print(f"\nTesting: {path.name}")
    print("-" * 50)
    
    if extractor.can_extract(path):
        result = extractor.extract(path)
        if result:
            print("✓ Extraction successful!")
            if 'prompt' in result:
                prompt = result.get('prompt', '')
                print(f"  Prompt: {prompt[:80]}..." if len(prompt) > 80 else f"  Prompt: {prompt}")
            if 'model' in result:
                print(f"  Model: {result['model']}")
            if 'steps' in result:
                print(f"  Steps: {result['steps']}")
            if 'seed' in result:
                print(f"  Seed: {result['seed']}")
            return True
        else:
            print("✗ Extraction failed")
            return False
    else:
        print("✗ Cannot extract SwarmUI metadata")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_swarmui_fix.py <image_file> [image_file2 ...]")
        sys.exit(1)
    
    success_count = 0
    total_count = 0
    
    for file_path in sys.argv[1:]:
        total_count += 1
        if test_swarmui_extraction(file_path):
            success_count += 1
    
    print(f"\n{'='*50}")
    print(f"Results: {success_count}/{total_count} files successfully processed")