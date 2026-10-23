"""Pytest configuration for metascan tests."""

import os

# Prevent OpenMP duplicate library crash on macOS when both
# torch and faiss-cpu link libomp.dylib
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
# Keep the suite from writing logs/server.log whenever a test runs the
# app's lifespan (see metascan/utils/log_files.py::install_server_log).
os.environ.setdefault("METASCAN_LOG_FILE", "0")


import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from metascan.utils import app_paths  # noqa: E402

# The developer's real runtime state lives next to the source tree. Nothing in
# the suite may touch it: it holds the live SQLite library and config.json
# (which carries secrets).
_REAL_BASE = Path(app_paths.__file__).resolve().parent.parent.parent
_GUARDED_NAMES = ("config.json",)
_GUARDED_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")


def _snapshot_real_state() -> dict:
    """stat() (never open) the real DB / config files; {} entries if absent."""
    snap: dict = {}
    data = _REAL_BASE / "data"
    candidates = [_REAL_BASE / n for n in _GUARDED_NAMES]
    if data.is_dir():
        candidates += [
            p
            for p in data.iterdir()
            if p.is_file() and p.name.endswith(_GUARDED_SUFFIXES)
        ]
    for p in candidates:
        try:
            st = p.stat()
            snap[str(p)] = (st.st_ino, st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            snap[str(p)] = None
    return snap


@pytest.fixture(scope="session", autouse=True)
def _guard_real_data_dir():
    """Fail the run if any test modified the real data/ DB or config.json.

    Tests that start the app lifespan must use ``isolated_app_paths``.
    """
    before = _snapshot_real_state()
    yield
    after = _snapshot_real_state()
    changed = sorted(
        k for k in set(before) | set(after) if before.get(k) != after.get(k)
    )
    assert not changed, (
        "Test suite touched the real data dir / config (use the "
        f"isolated_app_paths fixture): {changed}"
    )


@pytest.fixture(autouse=True)
def isolated_app_paths(tmp_path, monkeypatch):
    """Autouse: redirect the app's base path (data dir, config.json, thumbnails, models,
    logs, taste work dir...) to a temp dir.

    Every path helper derives from ``app_paths.get_base_path`` (resolved as a
    module global at call time), so patching it covers modules that imported
    ``get_data_dir`` by name. The temp base has no config.json
    or config_example.json, so config loads as {} (never the real one).
    """
    base = tmp_path / "app"
    base.mkdir()
    monkeypatch.setattr(app_paths, "get_base_path", lambda: base)
    # Singletons that cache a DB / thumbnail cache from an earlier test.
    import backend.dependencies as deps

    monkeypatch.setattr(deps, "_db_singleton", None)
    monkeypatch.setattr(deps, "_thumbnail_cache_singleton", None)
    yield base
