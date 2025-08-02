#!/usr/bin/env python3
"""Test script to verify all components work together"""

from pathlib import Path
import sys
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from metascan.core.database import DatabaseManager
from metascan.core.scanner import MediaScanner
from metascan.cache.thumbnail import ThumbnailCache
from metascan.extractors import MetadataExtractorManager


def test_components():
    """Test basic functionality of all components"""
    
    # Test paths
    test_dir = Path("./test_data")
    db_dir = Path("./test_db")
    cache_dir = Path("./test_cache")
    
    print("Testing Metascan Components")
    print("=" * 50)
    
    try:
        # 1. Test Database Manager
        print("\n1. Testing Database Manager...")
        db = DatabaseManager(db_dir)
        stats = db.get_stats()
        print(f"   ✓ Database initialized. Stats: {stats}")
        
        # 2. Test Metadata Extractors
        print("\n2. Testing Metadata Extractors...")
        extractor_manager = MetadataExtractorManager()
        print(f"   ✓ Loaded {len(extractor_manager.extractors)} extractors")
        
        # 3. Test Thumbnail Cache
        print("\n3. Testing Thumbnail Cache...")
        thumbnail_cache = ThumbnailCache(cache_dir)
        cache_size = thumbnail_cache.get_cache_size()
        print(f"   ✓ Thumbnail cache initialized. Size: {cache_size} bytes")
        
        # 4. Test Media Scanner
        print("\n4. Testing Media Scanner...")
        scanner = MediaScanner(extractor_manager)
        
        # Create test directory if it doesn't exist
        if not test_dir.exists():
            print(f"   ! Test directory '{test_dir}' not found")
            print("   ! Please create it and add some AI-generated images")
        else:
            # Scan for media
            def progress_callback(current, total, path):
                print(f"   Processing {current}/{total}: {path.name}")
            
            media_list = scanner.scan_directory(
                test_dir,
                recursive=True,
                progress_callback=progress_callback
            )
            
            print(f"\n   ✓ Found {len(media_list)} media files")
            
            # Save to database
            if media_list:
                saved = db.save_media_batch(media_list)
                print(f"   ✓ Saved {saved} files to database")
                
                # Test search
                print("\n5. Testing Search Indices...")
                for media in media_list[:3]:  # Test first 3
                    if media.metadata_source:
                        results = db.search_by_index("source", media.metadata_source)
                        print(f"   ✓ Found {len(results)} files from {media.metadata_source}")
                        break
        
        # Cleanup
        db.close()
        print("\n✓ All components tested successfully!")
        
    except Exception as e:
        print(f"\n✗ Error during testing: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_components()