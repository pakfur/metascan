#!/usr/bin/env python3
"""
ComfyUI Extractors Comparison Test

This script compares the metadata extraction capabilities of the enhanced ComfyUI extractor
versus the existing ComfyUI extractor. It focuses on the quantity of metadata extracted
rather than the correctness of the content.

The test:
1. Finds ComfyUI media files from the database
2. Randomly selects test files  
3. Runs both extractors on the same files
4. Compares the amount of metadata extracted
5. Generates a comparison report
"""

import random
import sys
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging
from datetime import datetime
import json

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from metascan.core.database_sqlite import DatabaseManager
from metascan.extractors.comfyui import ComfyUIExtractor
from metascan.extractors.enhanced_comfyui import ComfyUIMetadataExtractor
from metascan.utils.app_paths import get_data_dir

# Configure logging
logging.basicConfig(level=logging.WARNING)  # Reduce noise
logger = logging.getLogger(__name__)


class ExtractionResult:
    """Container for extraction results and metadata analysis."""
    
    def __init__(self, file_path: Path, extractor_name: str, metadata: Optional[Dict[str, Any]]):
        self.file_path = file_path
        self.extractor_name = extractor_name
        self.metadata = metadata
        self.success = metadata is not None
        self.field_count = self._count_fields() if metadata else 0
        self.non_empty_fields = self._count_non_empty_fields() if metadata else 0
        
    def _count_fields(self) -> int:
        """Count total number of fields in the metadata."""
        if not self.metadata:
            return 0
        return len(self.metadata)
    
    def _count_non_empty_fields(self) -> int:
        """Count fields that have meaningful non-empty values."""
        if not self.metadata:
            return 0
            
        count = 0
        for key, value in self.metadata.items():
            if key == "raw_metadata":  # Skip raw metadata in counting
                continue
            if key == "parsing_errors":  # Skip error tracking
                continue
                
            if self._is_meaningful_value(value):
                count += 1
                
        return count
    
    def _is_meaningful_value(self, value: Any) -> bool:
        """Check if a value is meaningful (non-empty, non-null)."""
        if value is None:
            return False
        if isinstance(value, str) and value.strip() == "":
            return False
        if isinstance(value, (list, dict)) and len(value) == 0:
            return False
        return True
    
    def get_field_summary(self) -> Dict[str, Any]:
        """Get a summary of extracted fields."""
        if not self.metadata:
            return {}
            
        summary = {}
        for key, value in self.metadata.items():
            if key in ["raw_metadata", "parsing_errors"]:
                continue
                
            if self._is_meaningful_value(value):
                if isinstance(value, list):
                    summary[key] = f"List[{len(value)} items]"
                elif isinstance(value, dict):
                    summary[key] = f"Dict[{len(value)} keys]"
                elif isinstance(value, str) and len(value) > 100:
                    summary[key] = f"String[{len(value)} chars]"
                else:
                    summary[key] = type(value).__name__
        
        return summary


class ComfyUIExtractorComparison:
    """Main comparison test class."""
    
    def __init__(self):
        self.db_manager = DatabaseManager(get_data_dir())
        self.original_extractor = ComfyUIExtractor()
        self.enhanced_extractor = ComfyUIMetadataExtractor()
        self.failed_extractions = []  # Store failed extractions for output
        
    def find_comfyui_media_files(self, limit: int = 50) -> List[Path]:
        """Find ComfyUI media files from the database."""
        print("🔍 Finding ComfyUI media files from database...")
        
        # Search for files with ComfyUI source
        comfyui_paths = self.db_manager.search_by_index("source", "comfyui")
        
        print(f"Found {len(comfyui_paths)} ComfyUI files in database")
        
        # Convert to Path objects and verify files exist
        valid_files = []
        for path_str in comfyui_paths:
            file_path = Path(path_str)
            if file_path.exists():
                valid_files.append(file_path)
        
        print(f"Found {len(valid_files)} existing ComfyUI files")
        
        # If we don't have enough files, return what we have
        if len(valid_files) <= limit:
            return valid_files
        
        # Randomly sample the requested number
        return random.sample(valid_files, limit)
    
    def extract_with_both(self, file_path: Path) -> tuple[ExtractionResult, ExtractionResult]:
        """Extract metadata using both extractors."""
        
        # Test original extractor
        original_metadata = None
        if self.original_extractor.can_extract(file_path):
            try:
                original_metadata = self.original_extractor.extract(file_path)
            except Exception as e:
                logger.warning(f"Original extractor failed on {file_path}: {e}")
        
        original_result = ExtractionResult(file_path, "Original ComfyUI", original_metadata)
        
        # Test enhanced extractor
        enhanced_metadata = None
        if self.enhanced_extractor.can_extract(file_path):
            try:
                enhanced_metadata = self.enhanced_extractor.extract(file_path)
            except Exception as e:
                logger.warning(f"Enhanced extractor failed on {file_path}: {e}")
        
        enhanced_result = ExtractionResult(file_path, "Enhanced ComfyUI", enhanced_metadata)
        
        return original_result, enhanced_result
    
    def run_comparison(self, num_files: int = 10) -> List[tuple[ExtractionResult, ExtractionResult]]:
        """Run the comparison test on random ComfyUI files."""
        
        print(f"\n🧪 Starting ComfyUI Extractor Comparison Test")
        print(f"Target: {num_files} files")
        print("=" * 60)
        
        # Find test files
        test_files = self.find_comfyui_media_files(limit=num_files * 2)  # Get more than needed
        
        if len(test_files) < num_files:
            print(f"⚠️  Only found {len(test_files)} files, testing all available")
            num_files = len(test_files)
        else:
            test_files = random.sample(test_files, num_files)
        
        results = []
        
        # Test each file
        for i, file_path in enumerate(test_files, 1):
            print(f"\n📁 Testing file {i}/{num_files}: {file_path.name}")
            
            original_result, enhanced_result = self.extract_with_both(file_path)
            results.append((original_result, enhanced_result))
            
            # Track failed extractions for worse performer
            self._track_failed_extraction(original_result, enhanced_result, file_path)
            
            # Print quick summary
            print(f"  Original: {'✓' if original_result.success else '✗'} "
                  f"({original_result.non_empty_fields} fields)")
            print(f"  Enhanced: {'✓' if enhanced_result.success else '✗'} "
                  f"({enhanced_result.non_empty_fields} fields)")
            
            if enhanced_result.non_empty_fields > original_result.non_empty_fields:
                print(f"  🎯 Enhanced found {enhanced_result.non_empty_fields - original_result.non_empty_fields} more fields")
            elif original_result.non_empty_fields > enhanced_result.non_empty_fields:
                print(f"  ⚠️  Original found {original_result.non_empty_fields - enhanced_result.non_empty_fields} more fields")
            else:
                print(f"  ⚖️  Both found same number of fields")
        
        return results
    
    def generate_report(self, results: List[tuple[ExtractionResult, ExtractionResult]]) -> None:
        """Generate a comprehensive comparison report."""
        
        print(f"\n📊 ComfyUI Extractor Comparison Report")
        print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
        
        total_tests = len(results)
        original_successes = sum(1 for orig, _ in results if orig.success)
        enhanced_successes = sum(1 for _, enh in results if enh.success)
        
        # Overall statistics
        print(f"\n📈 Overall Statistics:")
        print(f"  Total files tested: {total_tests}")
        print(f"  Original extractor successes: {original_successes}/{total_tests} ({100*original_successes/total_tests:.1f}%)")
        print(f"  Enhanced extractor successes: {enhanced_successes}/{total_tests} ({100*enhanced_successes/total_tests:.1f}%)")
        
        # Field count comparison
        orig_total_fields = sum(orig.non_empty_fields for orig, _ in results)
        enh_total_fields = sum(enh.non_empty_fields for _, enh in results)
        
        print(f"\n🎯 Metadata Field Count Comparison:")
        print(f"  Original extractor total fields: {orig_total_fields}")
        print(f"  Enhanced extractor total fields: {enh_total_fields}")
        print(f"  Enhanced advantage: {enh_total_fields - orig_total_fields} more fields ({100*(enh_total_fields - orig_total_fields)/max(orig_total_fields, 1):.1f}% more)")
        
        if orig_total_fields > 0:
            print(f"  Average fields per file (Original): {orig_total_fields/total_tests:.1f}")
        if enh_total_fields > 0:
            print(f"  Average fields per file (Enhanced): {enh_total_fields/total_tests:.1f}")
        
        # Win/Loss/Tie breakdown
        enhanced_wins = 0
        original_wins = 0
        ties = 0
        
        for orig, enh in results:
            if enh.non_empty_fields > orig.non_empty_fields:
                enhanced_wins += 1
            elif orig.non_empty_fields > enh.non_empty_fields:
                original_wins += 1
            else:
                ties += 1
        
        print(f"\n🏆 Head-to-Head Results:")
        print(f"  Enhanced extractor wins: {enhanced_wins}/{total_tests} ({100*enhanced_wins/total_tests:.1f}%)")
        print(f"  Original extractor wins: {original_wins}/{total_tests} ({100*original_wins/total_tests:.1f}%)")
        print(f"  Ties: {ties}/{total_tests} ({100*ties/total_tests:.1f}%)")
        
        # Detailed file-by-file results
        print(f"\n📋 Detailed Results:")
        print(f"{'File Name':<30} {'Original':<10} {'Enhanced':<10} {'Difference':<12} {'Winner'}")
        print("-" * 80)
        
        for orig, enh in results:
            file_name = orig.file_path.name[:29]  # Truncate long names
            orig_count = orig.non_empty_fields
            enh_count = enh.non_empty_fields
            diff = enh_count - orig_count
            
            if diff > 0:
                winner = "Enhanced ✓"
                diff_str = f"+{diff}"
            elif diff < 0:
                winner = "Original ✓"
                diff_str = str(diff)
            else:
                winner = "Tie ⚖️"
                diff_str = "0"
            
            print(f"{file_name:<30} {orig_count:<10} {enh_count:<10} {diff_str:<12} {winner}")
        
        # Sample extracted fields comparison for first few files
        print(f"\n🔍 Sample Field Extraction (first 3 files):")
        for i, (orig, enh) in enumerate(results[:3]):
            print(f"\n  File {i+1}: {orig.file_path.name}")
            
            orig_fields = set(orig.get_field_summary().keys()) if orig.success else set()
            enh_fields = set(enh.get_field_summary().keys()) if enh.success else set()
            
            print(f"    Original fields: {', '.join(sorted(orig_fields)) if orig_fields else 'None'}")
            print(f"    Enhanced fields:  {', '.join(sorted(enh_fields)) if enh_fields else 'None'}")
            
            # Fields only in enhanced
            only_enhanced = enh_fields - orig_fields
            if only_enhanced:
                print(f"    Enhanced-only:    {', '.join(sorted(only_enhanced))}")
            
            # Fields only in original
            only_original = orig_fields - enh_fields
            if only_original:
                print(f"    Original-only:    {', '.join(sorted(only_original))}")
        
        # Write failed extractions to output file
        self._write_failed_extractions()
        
        print(f"\n" + "=" * 80)
    
    def _track_failed_extraction(self, original_result: ExtractionResult, 
                               enhanced_result: ExtractionResult, file_path: Path) -> None:
        """Track failed extractions from the worse-performing extractor."""
        
        # Determine which extractor performed worse
        orig_fields = original_result.non_empty_fields
        enh_fields = enhanced_result.non_empty_fields
        
        # If enhanced performed worse (fewer fields), track its extraction
        if enh_fields < orig_fields and enhanced_result.metadata:
            self.failed_extractions.append({
                "file_path": str(file_path),
                "worse_extractor": "Enhanced ComfyUI",
                "fields_extracted": enh_fields,
                "better_extractor_fields": orig_fields,
                "metadata": enhanced_result.metadata
            })
        
        # If original performed worse (fewer fields), track its extraction  
        elif orig_fields < enh_fields and original_result.metadata:
            self.failed_extractions.append({
                "file_path": str(file_path),
                "worse_extractor": "Original ComfyUI", 
                "fields_extracted": orig_fields,
                "better_extractor_fields": enh_fields,
                "metadata": original_result.metadata
            })
        
        # If an extractor failed completely (no metadata), track it
        if not enhanced_result.success and original_result.success:
            self.failed_extractions.append({
                "file_path": str(file_path),
                "worse_extractor": "Enhanced ComfyUI",
                "fields_extracted": 0,
                "better_extractor_fields": orig_fields,
                "metadata": None,
                "extraction_failed": True
            })
        elif not original_result.success and enhanced_result.success:
            self.failed_extractions.append({
                "file_path": str(file_path),
                "worse_extractor": "Original ComfyUI",
                "fields_extracted": 0,
                "better_extractor_fields": enh_fields,
                "metadata": None,
                "extraction_failed": True
            })
    
    def _write_failed_extractions(self) -> None:
        """Write failed extractions to output file for analysis."""
        if not self.failed_extractions:
            print("No failed extractions to write.")
            return
            
        output_file = Path("test_comfyui_extractors.out")
        
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write("ComfyUI Extractor Comparison - Failed Extractions Analysis\n")
                f.write("=" * 70 + "\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Total failed extractions: {len(self.failed_extractions)}\n\n")
                
                for i, failure in enumerate(self.failed_extractions, 1):
                    f.write(f"FAILURE #{i}\n")
                    f.write(f"File: {failure['file_path']}\n")
                    f.write(f"Worse Extractor: {failure['worse_extractor']}\n")
                    f.write(f"Fields Extracted: {failure['fields_extracted']}\n")
                    f.write(f"Better Extractor Fields: {failure['better_extractor_fields']}\n")
                    
                    if failure.get('extraction_failed'):
                        f.write("Status: EXTRACTION FAILED COMPLETELY\n")
                    
                    f.write("Metadata JSON:\n")
                    if failure['metadata'] is None:
                        f.write("null\n")
                    else:
                        # Pretty print the metadata JSON
                        metadata_json = json.dumps(failure['metadata'], indent=2, ensure_ascii=False)
                        f.write(metadata_json + "\n")
                    
                    f.write("\n" + "-" * 50 + "\n\n")
            
            print(f"📝 Failed extractions written to: {output_file}")
            print(f"   Total failures recorded: {len(self.failed_extractions)}")
            
        except Exception as e:
            logger.error(f"Failed to write failed extractions: {e}")


def main():
    """Main test function."""
    
    # Set random seed for reproducible results
    random.seed(42)
    
    try:
        comparison = ComfyUIExtractorComparison()
        
        # Run the comparison test
        results = comparison.run_comparison(num_files=10)
        
        # Generate the report
        comparison.generate_report(results)
        
        print(f"\n✅ Comparison test completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())