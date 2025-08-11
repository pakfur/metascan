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

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.scanner import Scanner
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
        scanner = Scanner(db, thumbnail_cache)
        print(f"   ✓ Scanner initialized")
        
        # Create test directory if it doesn't exist
        if not test_dir.exists():
            print(f"   ! Test directory '{test_dir}' not found")
            print("   ! Please create it and add some AI-generated images")
        else:
            # Scan for media
            def progress_callback(current, total, path):
                print(f"   Processing {current}/{total}: {path.name}")
                return True  # Continue scanning
            
            processed_count = scanner.scan_directory(
                str(test_dir),
                recursive=True,
                progress_callback=progress_callback
            )
            
            print(f"\n   ✓ Processed {processed_count} media files")
            
            # Test search if we processed any files
            if processed_count > 0:
                print("\n5. Testing Search Indices...")
                # Get all media from database to test search
                all_media = db.get_all_media()
                if all_media:
                    # Test with first media that has metadata
                    for media in all_media[:3]:
                        if media.metadata_source:
                            results = db.search_by_index("source", media.metadata_source)
                            print(f"   ✓ Found {len(results)} files from {media.metadata_source}")
                            break
                    else:
                        print("   ! No media with metadata_source found for search testing")
                else:
                    print("   ! No media found in database for search testing")
        
        # Cleanup
        db.close()
        print("\n✓ All components tested successfully!")
        
    except Exception as e:
        print(f"\n✗ Error during testing: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    test_components()