"""Pytest configuration for metascan tests."""

import os

# Prevent OpenMP duplicate library crash on macOS when both
# torch and faiss-cpu link libomp.dylib
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# Keep the suite from writing logs/server.log whenever a test runs the
# app's lifespan (see metascan/utils/log_files.py::install_server_log).
os.environ.setdefault("METASCAN_LOG_FILE", "0")
