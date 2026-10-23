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
    """stat() (never open) the real DB / config / log files.

    Non-recursive: data/*.db*, config.json and the files directly in logs/.
    Absent files map to None so creation is detected too.
    """
    snap: dict = {}
    candidates = [_REAL_BASE / n for n in _GUARDED_NAMES]
    data = _REAL_BASE / "data"
    if data.is_dir():
        candidates += [
            p
            for p in data.iterdir()
            if p.is_file() and p.name.endswith(_GUARDED_SUFFIXES)
        ]
    logs = _REAL_BASE / "logs"
    if logs.is_dir():
        candidates += [p for p in logs.iterdir() if p.is_file()]
    for p in candidates:
        try:
            st = p.stat()
            snap[str(p)] = (st.st_ino, st.st_size, st.st_mtime_ns)
        except FileNotFoundError:
            snap[str(p)] = None
    return snap


def _changed(before: dict, after: dict) -> list:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


@pytest.fixture(scope="session", autouse=True)
def _guard_real_data_dir():
    """Session-level tripwire: fail if the real DB/config/log files changed.

    The per-test check in ``isolated_app_paths`` names the culprit test; this
    one also catches changes made outside any test body (fixtures, import-time
    code, subprocesses).

    Caveat: a dev server (or any other process) running against the same
    checkout legitimately writes data/metascan.db and logs/server.log while the
    suite runs, which trips both checks. Stop it before running the tests.
    """
    before = _snapshot_real_state()
    yield
    changed = _changed(before, _snapshot_real_state())
    assert not changed, (
        "Test suite touched the real data dir / config / logs (use the "
        f"isolated_app_paths fixture): {changed}"
    )


_TRACKED_META_PROMPT = _REAL_BASE / "data" / "meta_prompt.yml"


def _reset_prompt_state(base: Path) -> None:
    """Make prompt-store state per-test and deterministic.

    ``get_prompt_store()`` lazily builds a module singleton from
    ``get_data_dir()/meta_prompt.yml``; without this, whether a test sees a
    store (and which one) depended on which test ran first. Seed the temp data
    dir with the repo's *tracked* ``data/meta_prompt.yml`` (a source file, not
    user data), drop the singleton and every cache derived from it.
    """
    import shutil

    data = base / "data"
    data.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_TRACKED_META_PROMPT, data / "meta_prompt.yml")
    # Import after seeding: meta_prompt_templates builds the store at import.
    from metascan.core import i2v_templates, meta_prompt_templates, prompt_store
    from metascan.core import shot_templates

    prompt_store.reset_singleton_for_tests()
    # The elements cache is cleared by an on_reload listener registered on the
    # *first* singleton at import; a fresh singleton has no such listener.
    meta_prompt_templates._ELEMENTS_CACHE.clear()
    i2v_templates.reload_i2v_templates()
    shot_templates._cache.clear()


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
    _reset_prompt_state(base)
    before = _snapshot_real_state()
    yield base
    prompt_store_mod = sys.modules.get("metascan.core.prompt_store")
    if prompt_store_mod is not None:
        prompt_store_mod.reset_singleton_for_tests()
    changed = _changed(before, _snapshot_real_state())
    assert not changed, f"this test touched the real DB/config/logs: {changed}"
