"""Pytest configuration for metascan tests."""

import os

# Prevent OpenMP duplicate library crash on macOS when both
# torch and faiss-cpu link libomp.dylib
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
