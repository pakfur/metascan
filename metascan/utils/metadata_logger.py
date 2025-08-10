"""
Metadata parsing logger and error tracking system.
"""
import csv
import json
import logging
import traceback
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
import hashlib

logger = logging.getLogger(__name__)


class MetadataParsingLogger:
    """Logs metadata parsing attempts, errors, and results for debugging."""
    
    def __init__(self, log_dir: Optional[Path] = None):
        """
        Initialize the metadata parsing logger.
        
        Args:
            log_dir: Directory for log files. Defaults to project root/logs
        """
        if log_dir is None:
            # Default to logs directory in project root
            log_dir = Path(__file__).parent.parent.parent / "logs"
        
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # File paths
        self.text_log_path = self.log_dir / "metadata_extraction_report.txt"
        self.csv_log_path = self.log_dir / "metadata_extraction_errors.csv"
        
        # Initialize CSV file with headers if it doesn't exist
        if not self.csv_log_path.exists():
            self._init_csv_file()
    
    def _init_csv_file(self):
        """Initialize CSV file with headers."""
        with open(self.csv_log_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                'timestamp',
                'file_id',
                'file_path', 
                'file_name',
                'extractor',
                'success',
                'error_type',
                'error_message',
                'raw_metadata',
                'stack_trace'
            ])
    
    def _generate_file_id(self, file_path: Path) -> str:
        """Generate a unique ID for a file based on its path."""
        # Use MD5 hash of the absolute path for consistent IDs
        path_str = str(file_path.absolute())
        return hashlib.md5(path_str.encode()).hexdigest()[:16]
    
    def log_extraction_attempt(
        self,
        file_path: Path,
        extractor_name: str,
        success: bool,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
        raw_data: Optional[Any] = None
    ):
        """
        Log a metadata extraction attempt.
        
        Args:
            file_path: Path to the media file
            extractor_name: Name of the extractor used
            success: Whether extraction succeeded
            metadata: Extracted metadata (if successful)
            error: Exception that occurred (if failed)
            raw_data: Raw data before parsing (for debugging)
        """
        timestamp = datetime.now().isoformat()
        file_id = self._generate_file_id(file_path)
        
        # Log to text file for detailed analysis
        self._log_to_text_file(
            timestamp, file_id, file_path, extractor_name,
            success, metadata, error, raw_data
        )
        
        # Log errors to CSV for programmatic analysis
        if not success and error:
            self._log_to_csv(
                timestamp, file_id, file_path, extractor_name,
                error, raw_data
            )
    
    def _log_to_text_file(
        self,
        timestamp: str,
        file_id: str,
        file_path: Path,
        extractor_name: str,
        success: bool,
        metadata: Optional[Dict[str, Any]],
        error: Optional[Exception],
        raw_data: Optional[Any]
    ):
        """Write detailed log entry to text file."""
        with open(self.text_log_path, 'a', encoding='utf-8') as f:
            f.write(f"\n{'='*80}\n")
            f.write(f"Timestamp: {timestamp}\n")
            f.write(f"File ID: {file_id}\n")
            f.write(f"File Path: {file_path}\n")
            f.write(f"Extractor: {extractor_name}\n")
            f.write(f"Success: {success}\n")
            
            if success and metadata:
                f.write("\nExtracted Metadata:\n")
                f.write(json.dumps(metadata, indent=2, default=str))
                f.write("\n")
            
            if error:
                f.write(f"\nError Type: {type(error).__name__}\n")
                f.write(f"Error Message: {str(error)}\n")
                f.write("\nStack Trace:\n")
                f.write(traceback.format_exc())
            
            if raw_data:
                f.write("\nRaw Data (first 1000 chars):\n")
                raw_str = str(raw_data)[:1000]
                f.write(raw_str)
                if len(str(raw_data)) > 1000:
                    f.write("\n... (truncated)")
                f.write("\n")
    
    def _log_to_csv(
        self,
        timestamp: str,
        file_id: str,
        file_path: Path,
        extractor_name: str,
        error: Exception,
        raw_data: Optional[Any]
    ):
        """Write error entry to CSV file."""
        with open(self.csv_log_path, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            
            # Prepare raw metadata string (truncated for CSV)
            raw_metadata_str = ""
            if raw_data:
                try:
                    if isinstance(raw_data, dict):
                        raw_metadata_str = json.dumps(raw_data, default=str)[:500]
                    else:
                        raw_metadata_str = str(raw_data)[:500]
                except:
                    raw_metadata_str = "Error serializing raw data"
            
            # Get concise stack trace
            stack_trace = traceback.format_exc().replace('\n', ' | ')[:1000]
            
            writer.writerow([
                timestamp,
                file_id,
                str(file_path),
                file_path.name,
                extractor_name,
                False,  # success
                type(error).__name__,
                str(error),
                raw_metadata_str,
                stack_trace
            ])
    
    def get_errors_for_file(self, file_path: Path) -> List[Dict[str, Any]]:
        """Get all logged errors for a specific file."""
        file_id = self._generate_file_id(file_path)
        errors = []
        
        if not self.csv_log_path.exists():
            return errors
        
        with open(self.csv_log_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row['file_id'] == file_id:
                    errors.append(row)
        
        return errors
    
    def get_all_errors(self) -> List[Dict[str, Any]]:
        """Get all logged errors."""
        errors = []
        
        if not self.csv_log_path.exists():
            return errors
        
        with open(self.csv_log_path, 'r', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            errors = list(reader)
        
        return errors
    
    def clear_logs(self):
        """Clear all log files."""
        if self.text_log_path.exists():
            self.text_log_path.unlink()
        if self.csv_log_path.exists():
            self.csv_log_path.unlink()
            self._init_csv_file()


class MetadataLogAnalyzer:
    """Utility class for analyzing metadata parsing logs."""
    
    def __init__(self, log_dir: Optional[Path] = None):
        """Initialize the analyzer with the log directory."""
        self.logger = MetadataParsingLogger(log_dir)
    
    def analyze_file(self, file_path: Path) -> Dict[str, Any]:
        """
        Analyze parsing errors for a specific file.
        
        Args:
            file_path: Path to the media file
            
        Returns:
            Analysis results including error patterns and suggestions
        """
        errors = self.logger.get_errors_for_file(file_path)
        
        if not errors:
            return {
                'file_path': str(file_path),
                'file_id': self.logger._generate_file_id(file_path),
                'error_count': 0,
                'message': 'No errors found for this file'
            }
        
        # Analyze error patterns
        error_types = {}
        extractors_failed = set()
        
        for error in errors:
            error_type = error.get('error_type', 'Unknown')
            error_types[error_type] = error_types.get(error_type, 0) + 1
            extractors_failed.add(error.get('extractor', 'Unknown'))
        
        return {
            'file_path': str(file_path),
            'file_id': self.logger._generate_file_id(file_path),
            'error_count': len(errors),
            'error_types': error_types,
            'extractors_failed': list(extractors_failed),
            'latest_error': errors[-1] if errors else None,
            'errors': errors
        }
    
    def analyze_all(self) -> Dict[str, Any]:
        """
        Analyze all parsing errors in the log.
        
        Returns:
            Comprehensive analysis of all errors
        """
        errors = self.logger.get_all_errors()
        
        if not errors:
            return {
                'total_errors': 0,
                'message': 'No errors found in logs'
            }
        
        # Aggregate analysis
        files_with_errors = set()
        error_types = {}
        extractor_failures = {}
        
        for error in errors:
            files_with_errors.add(error.get('file_path', 'Unknown'))
            
            error_type = error.get('error_type', 'Unknown')
            error_types[error_type] = error_types.get(error_type, 0) + 1
            
            extractor = error.get('extractor', 'Unknown')
            extractor_failures[extractor] = extractor_failures.get(extractor, 0) + 1
        
        # Find most common errors
        most_common_error = max(error_types.items(), key=lambda x: x[1])
        most_failing_extractor = max(extractor_failures.items(), key=lambda x: x[1])
        
        return {
            'total_errors': len(errors),
            'unique_files_with_errors': len(files_with_errors),
            'error_types': error_types,
            'extractor_failures': extractor_failures,
            'most_common_error': most_common_error,
            'most_failing_extractor': most_failing_extractor,
            'recent_errors': errors[-10:] if len(errors) > 10 else errors
        }
    
    def retest_file(self, file_path: Path) -> Dict[str, Any]:
        """
        Re-run metadata extraction on a file for testing.
        
        Args:
            file_path: Path to the media file
            
        Returns:
            Results of the re-test
        """
        from metascan.extractors import MetadataExtractorManager
        
        if not file_path.exists():
            return {
                'success': False,
                'error': f'File not found: {file_path}'
            }
        
        manager = MetadataExtractorManager()
        
        # Enable detailed logging for this test
        original_logger = manager.logger
        manager.logger = self.logger
        
        try:
            metadata = manager.extract_metadata(file_path)
            
            return {
                'success': metadata is not None,
                'file_path': str(file_path),
                'metadata': metadata,
                'message': 'Extraction successful' if metadata else 'No metadata found'
            }
        except Exception as e:
            return {
                'success': False,
                'file_path': str(file_path),
                'error': str(e),
                'error_type': type(e).__name__,
                'stack_trace': traceback.format_exc()
            }
        finally:
            manager.logger = original_logger
    
    def print_report(self, file_path: Optional[Path] = None):
        """
        Print a formatted report to console.
        
        Args:
            file_path: Specific file to analyze, or None for all errors
        """
        if file_path:
            result = self.analyze_file(file_path)
            print(f"\n{'='*60}")
            print(f"Metadata Parsing Analysis for: {result['file_path']}")
            print(f"{'='*60}")
            print(f"File ID: {result['file_id']}")
            print(f"Total Errors: {result['error_count']}")
            
            if result['error_count'] > 0:
                print(f"\nError Types:")
                for error_type, count in result.get('error_types', {}).items():
                    print(f"  - {error_type}: {count}")
                
                print(f"\nFailed Extractors: {', '.join(result.get('extractors_failed', []))}")
                
                if result.get('latest_error'):
                    print(f"\nLatest Error:")
                    error = result['latest_error']
                    print(f"  Timestamp: {error.get('timestamp')}")
                    print(f"  Extractor: {error.get('extractor')}")
                    print(f"  Error: {error.get('error_message')}")
        else:
            result = self.analyze_all()
            print(f"\n{'='*60}")
            print(f"Metadata Parsing Analysis - All Errors")
            print(f"{'='*60}")
            print(f"Total Errors: {result['total_errors']}")
            
            if result['total_errors'] > 0:
                print(f"Unique Files with Errors: {result['unique_files_with_errors']}")
                
                print(f"\nError Type Distribution:")
                for error_type, count in result.get('error_types', {}).items():
                    print(f"  - {error_type}: {count}")
                
                print(f"\nExtractor Failure Count:")
                for extractor, count in result.get('extractor_failures', {}).items():
                    print(f"  - {extractor}: {count}")
                
                most_common = result.get('most_common_error')
                if most_common:
                    print(f"\nMost Common Error: {most_common[0]} ({most_common[1]} occurrences)")
                
                most_failing = result.get('most_failing_extractor')
                if most_failing:
                    print(f"Most Failing Extractor: {most_failing[0]} ({most_failing[1]} failures)")
                
                print(f"\nRecent Errors:")
                for error in result.get('recent_errors', [])[-5:]:
                    print(f"  - {error.get('file_name')} | {error.get('extractor')} | {error.get('error_type')}")
        
        print(f"{'='*60}\n")