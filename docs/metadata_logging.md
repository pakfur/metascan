# Metadata Parsing Log and Test Utility

## Overview

The metadata parsing log system captures all metadata extraction attempts, including both successes and failures. This allows for debugging parsing issues and improving the metadata extraction subsystem.

## Features

### 1. Automatic Logging
- Logs all metadata extraction attempts during scanning
- Captures successful extractions with metadata details
- Records errors with full stack traces and context
- Stores raw data for debugging parsing issues

### 2. Log Files

Two log files are created in the `logs/` directory:

- **`metadata_extraction_report.txt`**: Detailed human-readable log with full information
- **`metadata_extraction_errors.csv`**: CSV file for programmatic analysis of errors

### 3. CSV Error Format

The CSV file contains the following columns:
- `timestamp`: When the extraction was attempted
- `file_id`: Unique 16-character hash of the file path
- `file_path`: Full path to the media file
- `file_name`: Name of the file
- `extractor`: Which extractor was used
- `success`: Whether extraction succeeded (true/false)
- `error_type`: Type of exception that occurred
- `error_message`: Error message
- `raw_metadata`: First 500 chars of raw metadata (for debugging)
- `stack_trace`: Full stack trace (pipe-separated lines)

## Command-Line Interface

### Installation
The CLI is included with the metascan package.

### Usage

```bash
# Show help
python -m metascan.utils.metadata_log_cli --help

# Analyze all errors
python -m metascan.utils.metadata_log_cli analyze-all

# Analyze errors for a specific file
python -m metascan.utils.metadata_log_cli analyze-file /path/to/image.png

# Re-test metadata extraction on a file
python -m metascan.utils.metadata_log_cli retest /path/to/image.png --verbose

# Show statistics
python -m metascan.utils.metadata_log_cli stats

# Export errors as CSV for further processing
python -m metascan.utils.metadata_log_cli csv > errors_export.csv

# Clear all logs (with confirmation)
python -m metascan.utils.metadata_log_cli clear-logs
```

### Output Formats

Most commands support JSON output with the `--json` flag:

```bash
# Get analysis as JSON
python -m metascan.utils.metadata_log_cli analyze-all --json

# Get retest results as JSON
python -m metascan.utils.metadata_log_cli retest /path/to/file.png --json
```

## Python API

### Basic Usage

```python
from metascan.utils.metadata_logger import MetadataLogAnalyzer

# Create analyzer
analyzer = MetadataLogAnalyzer()

# Analyze all errors
result = analyzer.analyze_all()
print(f"Total errors: {result['total_errors']}")

# Analyze specific file
file_result = analyzer.analyze_file(Path("/path/to/file.png"))
print(f"Errors for file: {file_result['error_count']}")

# Re-test a file
test_result = analyzer.retest_file(Path("/path/to/file.png"))
if test_result['success']:
    print("Extraction successful!")
else:
    print(f"Failed: {test_result['error']}")

# Print formatted report
analyzer.print_report()  # All errors
analyzer.print_report(Path("/path/to/file.png"))  # Specific file
```

### Logging Configuration

The MetadataExtractorManager automatically logs all extraction attempts:

```python
from metascan.extractors import MetadataExtractorManager

# Enable logging (default)
manager = MetadataExtractorManager(enable_logging=True)

# Disable logging if needed
manager = MetadataExtractorManager(enable_logging=False)
```

## Use Cases

### 1. Debugging Parsing Errors
When a file fails to parse:
1. Check the error in the CSV log
2. Use the file ID to find detailed info in the text log
3. Use `retest` command to reproduce the issue
4. Fix the extractor and retest

### 2. Identifying Patterns
Use the `stats` command to identify:
- Most common error types
- Which extractors fail most often
- Files that consistently cause issues

### 3. Testing Fixes
After fixing an extractor:
```bash
# Retest a specific file
python -m metascan.utils.metadata_log_cli retest /path/to/problematic_file.png

# Clear logs and rescan to verify fixes
python -m metascan.utils.metadata_log_cli clear-logs --force
# Then run your normal scanning process
```

### 4. Batch Analysis
Export errors to CSV for batch processing:
```bash
# Export all errors
python -m metascan.utils.metadata_log_cli csv > all_errors.csv

# Process with other tools (pandas, Excel, etc.)
```

## Log File Management

### Location
Logs are stored in:
- Default: `<project_root>/logs/`
- Custom: Can be configured when creating MetadataParsingLogger

### Rotation
Currently, logs append indefinitely. For production use, consider:
- Periodically archiving old logs
- Using `clear-logs` command to reset
- Implementing log rotation (future enhancement)

### Size Monitoring
Use the `stats` command to check log file sizes:
```bash
python -m metascan.utils.metadata_log_cli stats
```

## Troubleshooting

### No Errors Logged
If no errors appear in logs:
- Verify logging is enabled in MetadataExtractorManager
- Check that files are actually being processed
- Ensure the logs directory exists and is writable

### Large Log Files
If log files grow too large:
- Use `clear-logs` to reset
- Archive old logs before clearing
- Consider implementing rotation

### Permission Errors
Ensure the process has write permissions to:
- `<project_root>/logs/` directory
- Log files if they already exist