"""
PyInstaller hook for NLTK to ensure punkt tokenizer data is included.
"""

from PyInstaller.utils.hooks import collect_data_files

# Collect NLTK data files
datas = collect_data_files('nltk', includes=['**/*.pickle', '**/*.txt', '**/*.yml'])