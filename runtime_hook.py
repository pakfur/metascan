"""
Runtime hook to set up NLTK data path for bundled application.
"""

import os
import sys
from pathlib import Path


def setup_nltk_data():
    """Configure NLTK to use bundled data."""
    try:
        import nltk
        
        # When frozen, set NLTK data path to user directory
        if getattr(sys, 'frozen', False):
            nltk_data_dir = Path.home() / '.metascan' / 'nltk_data'
            nltk_data_dir.mkdir(parents=True, exist_ok=True)
            
            # Add to NLTK data path
            nltk.data.path.insert(0, str(nltk_data_dir))
            
            # Download punkt if not available
            try:
                nltk.data.find('tokenizers/punkt')
            except LookupError:
                # Create a marker file to indicate download is needed
                marker_file = nltk_data_dir / 'download_needed.txt'
                marker_file.write_text('punkt tokenizer needs to be downloaded on first run')
    except ImportError:
        pass  # NLTK not available


# Run setup when module is imported
setup_nltk_data()