#!/usr/bin/env python3
"""
Validation script to check that all media files in configured directories
are properly indexed in the SQLite database.

Usage:
    python validate_scan.py [--config CONFIG_FILE] [--verbose]
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Set, List

# Add the project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from metascan.core.database_sqlite import DatabaseManager


class ScanValidator:
    """Validates that all media files are properly indexed in the database"""
    
    SUPPORTED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.mp4'}
    
    def __init__(self, config_file: str, verbose: bool = False):
        self.config_file = config_file
        self.verbose = verbose
        
        # Initialize database manager
        db_path = Path(project_root) / 'data'
        self.db_manager = DatabaseManager(db_path)
        
        # Load configuration
        self.directories = self._load_config()
        
    def _load_config(self) -> List[dict]:
        """Load directory configuration"""
        if not os.path.exists(self.config_file):
            print(f"ERROR: Configuration file not found: {self.config_file}", file=sys.stderr)
            sys.exit(1)
            
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
                directories = config.get('directories', [])
                
            if not directories:
                print("ERROR: No directories configured", file=sys.stderr)
                sys.exit(1)
                
            if self.verbose:
                print(f"Loaded {len(directories)} configured directories")
                
            return directories
        except Exception as e:
            print(f"ERROR: Failed to load configuration: {e}", file=sys.stderr)
            sys.exit(1)
    
    def _find_media_files(self) -> Set[Path]:
        """Find all media files in configured directories"""
        media_files = set()
        
        for dir_info in self.directories:
            dir_path = Path(dir_info['filepath'])
            if not dir_path.exists():
                print(f"WARNING: Directory does not exist: {dir_path}", file=sys.stderr)
                continue
                
            recursive = dir_info.get('search_subfolders', True)
            
            if self.verbose:
                print(f"Scanning directory: {dir_path} (recursive: {recursive})")
            
            # Find media files
            if recursive:
                for ext in self.SUPPORTED_EXTENSIONS:
                    media_files.update(dir_path.rglob(f"*{ext}"))
                    media_files.update(dir_path.rglob(f"*{ext.upper()}"))
            else:
                for ext in self.SUPPORTED_EXTENSIONS:
                    media_files.update(dir_path.glob(f"*{ext}"))
                    media_files.update(dir_path.glob(f"*{ext.upper()}"))
        
        if self.verbose:
            print(f"Found {len(media_files)} media files in filesystem")
            
        return media_files
    
    def _get_database_files(self) -> Set[str]:
        """Get all file paths from the media table"""
        try:
            with self.db_manager._get_connection() as conn:
                rows = conn.execute("SELECT file_path FROM media")
                db_files = {row['file_path'] for row in rows}
                
            if self.verbose:
                print(f"Found {len(db_files)} files in media table")
                
            return db_files
        except Exception as e:
            print(f"ERROR: Failed to query database: {e}", file=sys.stderr)
            sys.exit(1)
    
    def _get_indexed_files(self) -> Set[str]:
        """Get all file paths from the indices table"""
        try:
            with self.db_manager._get_connection() as conn:
                rows = conn.execute("SELECT DISTINCT file_path FROM indices")
                indexed_files = {row['file_path'] for row in rows}
                
            if self.verbose:
                print(f"Found {len(indexed_files)} files in indices table")
                
            return indexed_files
        except Exception as e:
            print(f"ERROR: Failed to query indices: {e}", file=sys.stderr)
            sys.exit(1)
    
    def validate(self) -> int:
        """
        Validate that all media files are properly indexed.
        
        Returns:
            Number of missing files found
        """
        if self.verbose:
            print("Starting validation...")
            print("=" * 50)
        
        # Find all media files in filesystem
        filesystem_files = self._find_media_files()
        
        # Get files from database tables
        media_table_files = self._get_database_files()
        indices_table_files = self._get_indexed_files()
        
        # Convert filesystem paths to strings for comparison
        filesystem_paths = {str(path) for path in filesystem_files}
        
        # Find missing files
        missing_from_media = filesystem_paths - media_table_files
        missing_from_indices = filesystem_paths - indices_table_files
        
        # Files missing from either table are problematic
        all_missing = missing_from_media | missing_from_indices
        
        # Output missing files
        if all_missing:
            if self.verbose:
                print("\nMissing files:")
                print("-" * 30)
                
            for missing_file in sorted(all_missing):
                # Determine which table(s) are missing the file
                missing_from = []
                if missing_file in missing_from_media:
                    missing_from.append("media")
                if missing_file in missing_from_indices:
                    missing_from.append("indices")
                
                if self.verbose:
                    print(f"{missing_file} (missing from: {', '.join(missing_from)})")
                else:
                    print(missing_file)
        
        # Summary
        if self.verbose or all_missing:
            print("\n" + "=" * 50)
            
        print(f"SUMMARY: {len(all_missing)} missing files found")
        
        if self.verbose:
            print(f"  Total filesystem files: {len(filesystem_files)}")
            print(f"  Files in media table: {len(media_table_files)}")
            print(f"  Files in indices table: {len(indices_table_files)}")
            print(f"  Missing from media table: {len(missing_from_media)}")
            print(f"  Missing from indices table: {len(missing_from_indices)}")
            
            if all_missing:
                coverage_pct = ((len(filesystem_files) - len(all_missing)) / len(filesystem_files)) * 100
                print(f"  Database coverage: {coverage_pct:.1f}%")
        
        return len(all_missing)


def main():
    parser = argparse.ArgumentParser(
        description="Validate that all media files are properly indexed in the database"
    )
    parser.add_argument(
        "--config", 
        default="config.json",
        help="Path to configuration file (default: config.json)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )
    
    args = parser.parse_args()
    
    # Create validator and run validation
    validator = ScanValidator(args.config, args.verbose)
    missing_count = validator.validate()
    
    # Exit with appropriate code
    sys.exit(0 if missing_count == 0 else 1)


if __name__ == "__main__":
    main()