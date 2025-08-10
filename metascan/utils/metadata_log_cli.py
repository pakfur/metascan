#!/usr/bin/env python3
"""
Command-line interface for metadata parsing log analysis.

Usage:
    python -m metascan.utils.metadata_log_cli --help
    python -m metascan.utils.metadata_log_cli analyze-all
    python -m metascan.utils.metadata_log_cli analyze-file /path/to/file.png
    python -m metascan.utils.metadata_log_cli retest /path/to/file.png
    python -m metascan.utils.metadata_log_cli clear-logs
"""

import argparse
import sys
from pathlib import Path
import json
from typing import Optional

from metascan.utils.metadata_logger import MetadataLogAnalyzer, MetadataParsingLogger


def analyze_all_errors(args):
    """Analyze all errors in the log."""
    analyzer = MetadataLogAnalyzer()
    
    if args.json:
        # Output as JSON
        result = analyzer.analyze_all()
        print(json.dumps(result, indent=2, default=str))
    else:
        # Print formatted report
        analyzer.print_report()


def analyze_file_errors(args):
    """Analyze errors for a specific file."""
    file_path = Path(args.file)
    
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    
    analyzer = MetadataLogAnalyzer()
    
    if args.json:
        # Output as JSON
        result = analyzer.analyze_file(file_path)
        print(json.dumps(result, indent=2, default=str))
    else:
        # Print formatted report
        analyzer.print_report(file_path)


def retest_file(args):
    """Re-run metadata extraction on a file."""
    file_path = Path(args.file)
    
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    
    analyzer = MetadataLogAnalyzer()
    
    print(f"Re-testing metadata extraction for: {file_path}")
    print("-" * 60)
    
    result = analyzer.retest_file(file_path)
    
    if args.json:
        # Output as JSON
        print(json.dumps(result, indent=2, default=str))
    else:
        # Print formatted result
        if result['success']:
            print(f"✓ Extraction successful")
            if result.get('metadata'):
                print("\nExtracted Metadata:")
                print(json.dumps(result['metadata'], indent=2, default=str))
            else:
                print("No metadata found in file")
        else:
            print(f"✗ Extraction failed")
            print(f"Error: {result.get('error', 'Unknown error')}")
            if result.get('stack_trace') and args.verbose:
                print("\nStack Trace:")
                print(result['stack_trace'])


def show_errors_csv(args):
    """Show errors in CSV format for further processing."""
    logger = MetadataParsingLogger()
    errors = logger.get_all_errors()
    
    if args.file_id:
        # Filter by file ID
        errors = [e for e in errors if e.get('file_id') == args.file_id]
    
    if not errors:
        print("No errors found", file=sys.stderr)
        return
    
    # Print CSV header
    if errors:
        headers = errors[0].keys()
        print(','.join(headers))
        
        # Print rows
        for error in errors:
            row = [str(error.get(h, '')) for h in headers]
            # Escape commas and quotes in values
            escaped_row = []
            for v in row:
                if ',' in v or '"' in v:
                    # Escape quotes by doubling them and wrap in quotes
                    escaped_v = '"' + v.replace('"', '""') + '"'
                    escaped_row.append(escaped_v)
                else:
                    escaped_row.append(v)
            row = escaped_row
            print(','.join(row))


def clear_logs(args):
    """Clear all log files."""
    logger = MetadataParsingLogger()
    
    if not args.force:
        response = input("Are you sure you want to clear all metadata parsing logs? (y/N): ")
        if response.lower() != 'y':
            print("Cancelled")
            return
    
    logger.clear_logs()
    print("Metadata parsing logs cleared")


def show_stats(args):
    """Show statistics about the metadata parsing logs."""
    analyzer = MetadataLogAnalyzer()
    result = analyzer.analyze_all()
    
    print("\nMetadata Parsing Log Statistics")
    print("=" * 40)
    
    if result['total_errors'] == 0:
        print("No errors logged")
        return
    
    print(f"Total Errors: {result['total_errors']}")
    print(f"Unique Files with Errors: {result['unique_files_with_errors']}")
    
    if result.get('error_types'):
        print("\nTop Error Types:")
        sorted_errors = sorted(result['error_types'].items(), key=lambda x: x[1], reverse=True)
        for error_type, count in sorted_errors[:5]:
            percentage = (count / result['total_errors']) * 100
            print(f"  {error_type}: {count} ({percentage:.1f}%)")
    
    if result.get('extractor_failures'):
        print("\nExtractor Failure Distribution:")
        for extractor, count in result['extractor_failures'].items():
            percentage = (count / result['total_errors']) * 100
            print(f"  {extractor}: {count} ({percentage:.1f}%)")
    
    # Check log file sizes
    logger = MetadataParsingLogger()
    if logger.text_log_path.exists():
        text_size = logger.text_log_path.stat().st_size / (1024 * 1024)  # MB
        print(f"\nText Log Size: {text_size:.2f} MB")
    if logger.csv_log_path.exists():
        csv_size = logger.csv_log_path.stat().st_size / (1024 * 1024)  # MB
        print(f"CSV Log Size: {csv_size:.2f} MB")


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description='Metadata parsing log analyzer and testing utility',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze all errors
  %(prog)s analyze-all
  
  # Analyze errors for a specific file
  %(prog)s analyze-file /path/to/image.png
  
  # Re-test metadata extraction
  %(prog)s retest /path/to/image.png --verbose
  
  # Show statistics
  %(prog)s stats
  
  # Export errors as CSV
  %(prog)s csv > errors.csv
  
  # Clear all logs
  %(prog)s clear-logs --force
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Analyze all errors
    analyze_all_parser = subparsers.add_parser(
        'analyze-all',
        help='Analyze all errors in the log'
    )
    analyze_all_parser.add_argument(
        '--json',
        action='store_true',
        help='Output results as JSON'
    )
    
    # Analyze file errors
    analyze_file_parser = subparsers.add_parser(
        'analyze-file',
        help='Analyze errors for a specific file'
    )
    analyze_file_parser.add_argument(
        'file',
        help='Path to the media file'
    )
    analyze_file_parser.add_argument(
        '--json',
        action='store_true',
        help='Output results as JSON'
    )
    
    # Retest file
    retest_parser = subparsers.add_parser(
        'retest',
        help='Re-run metadata extraction on a file'
    )
    retest_parser.add_argument(
        'file',
        help='Path to the media file'
    )
    retest_parser.add_argument(
        '--json',
        action='store_true',
        help='Output results as JSON'
    )
    retest_parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show detailed error information'
    )
    
    # Show CSV
    csv_parser = subparsers.add_parser(
        'csv',
        help='Export errors in CSV format'
    )
    csv_parser.add_argument(
        '--file-id',
        help='Filter by file ID'
    )
    
    # Clear logs
    clear_parser = subparsers.add_parser(
        'clear-logs',
        help='Clear all log files'
    )
    clear_parser.add_argument(
        '--force', '-f',
        action='store_true',
        help='Skip confirmation prompt'
    )
    
    # Show statistics
    stats_parser = subparsers.add_parser(
        'stats',
        help='Show statistics about the logs'
    )
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # Execute command
    if args.command == 'analyze-all':
        analyze_all_errors(args)
    elif args.command == 'analyze-file':
        analyze_file_errors(args)
    elif args.command == 'retest':
        retest_file(args)
    elif args.command == 'csv':
        show_errors_csv(args)
    elif args.command == 'clear-logs':
        clear_logs(args)
    elif args.command == 'stats':
        show_stats(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()