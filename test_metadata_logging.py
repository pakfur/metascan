#!/usr/bin/env python3
"""
Test script to demonstrate metadata logging functionality.
"""

from pathlib import Path
from metascan.extractors import MetadataExtractorManager
from metascan.utils.metadata_logger import MetadataLogAnalyzer

def test_metadata_logging():
    """Test the metadata logging system."""
    
    print("Testing Metadata Logging System")
    print("=" * 50)
    
    # Create extractor manager with logging enabled
    manager = MetadataExtractorManager(enable_logging=True)
    
    # Find some test files (you'll need to adjust this path)
    test_dir = Path(".")  # Current directory
    test_files = list(test_dir.glob("*.png")) + list(test_dir.glob("*.jpg"))
    
    if not test_files:
        print("No image files found in current directory for testing.")
        print("Place some PNG or JPG files in the current directory and run again.")
        return
    
    print(f"Found {len(test_files)} test files")
    print()
    
    # Process each file
    for file_path in test_files[:5]:  # Limit to 5 files for testing
        print(f"Processing: {file_path.name}")
        try:
            metadata = manager.extract_metadata(file_path)
            if metadata:
                print(f"  ✓ Extracted {metadata.get('source', 'unknown')} metadata")
            else:
                print(f"  - No metadata found")
        except Exception as e:
            print(f"  ✗ Error: {e}")
    
    print()
    print("-" * 50)
    print("Analyzing Results")
    print("-" * 50)
    
    # Analyze the logs
    analyzer = MetadataLogAnalyzer()
    
    # Print overall statistics
    result = analyzer.analyze_all()
    
    if result['total_errors'] == 0:
        print("No errors were logged during processing.")
    else:
        print(f"Total Errors: {result['total_errors']}")
        print(f"Files with Errors: {result['unique_files_with_errors']}")
        
        if result.get('most_common_error'):
            error_type, count = result['most_common_error']
            print(f"Most Common Error: {error_type} ({count} occurrences)")
    
    print()
    print("Log files created:")
    print(f"  - Text log: logs/metadata_extraction_report.txt")
    print(f"  - CSV log: logs/metadata_extraction_errors.csv")
    print()
    print("Use the CLI tool for detailed analysis:")
    print("  python -m metascan.utils.metadata_log_cli --help")


if __name__ == "__main__":
    test_metadata_logging()