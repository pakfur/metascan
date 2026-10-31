"""The Import and Hygiene panels wrapping today's scan (spec 6.2, 9.4)."""

import asyncio
import threading
from pathlib import Path

import pytest

from backend import dependencies
from backend.api import scan as scan_api
from backend.services import taste_snapshot as ts
from backend.services.background.hygiene import HygieneProcess
from backend.services.background.idle import IdleTracker
from backend.services.background.import_process import ImportProcess
from backend.services.background.protocol import State
from backend.services.background.registry import ProcessRegistry
from tests.test_background_services import Stub
from tests.test_taste_snapshot_scan import (  # noqa: F401
    _media,
    _pending,
    _seed,
    _wire,
    db,
)


@pytest.fixture
def reg(tmp_path):
    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    for pid in ("embedding", "tagging"):
        registry.register(Stub(pid))
    process = ImportProcess(registry, scan_api.scan_status, scan_api.request_cancel)
    registry.register(process)
    registry.register(
        HygieneProcess(
            registry,
            IdleTracker(),
            get_db=lambda: None,
            get_config=lambda: {},
            get_data_dir=lambda db: None,
        )
    )
    dependencies.set_registry(registry)
    dependencies.set_import_process(process)
    yield registry
    dependencies.set_registry(None)
    dependencies.set_import_process(None)
    scan_api._cancel_requested = False
    scan_api._scan_error = None


def test_idle_import_cannot_be_stopped_or_started(reg):
    st = reg.status("import")
    assert st.state == State.IDLE
    assert (st.capabilities.can_start, st.capabilities.can_pause) == (False, False)
    assert st.capabilities.can_stop is False


def test_hygiene_waits_for_a_quiet_moment(reg):
    st = reg.status("hygiene")
    assert (st.order, st.state) == (80, State.IDLE)
    assert st.activity == "Waiting for a quiet moment"
    assert (st.capabilities.can_start, st.capabilities.can_pause) == (True, True)
    assert st.capabilities.can_stop is False


def test_a_running_scan_shows_progress_with_a_bare_file_name(monkeypatch, db, reg):
    _seed(db)
    seen = {}

    class Scanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, path, subfolders, progress_callback, *a):
            progress_callback(3, 10, "/secret/dir/pic.png")
            seen["status"] = reg.status("import")
            seen["raw"] = scan_api.scan_status()
            return 0

    _wire(monkeypatch, db, [], scanner_cls=Scanner)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    st = seen["status"]
    assert st.state == State.RUNNING
    assert (st.progress.done, st.progress.total) == (3, 10)
    assert "pic.png" in st.activity and "/secret" not in st.activity
    assert st.capabilities.can_stop is True
    assert seen["raw"]["running"] and seen["raw"]["file"] == "pic.png"
    assert reg.status("import").state == State.IDLE


def test_stop_during_a_full_clean_is_a_cancel_that_keeps_everything(
    monkeypatch, db, reg
):
    _seed(db)
    inside, release = threading.Event(), threading.Event()

    class Scanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            db.save_media(_media("/lib/fav.png"))
            inside.set()
            release.wait(5)
            return 1

    _wire(monkeypatch, db, [], scanner_cls=Scanner)

    async def go():
        task = asyncio.create_task(
            scan_api._run_scan(full_cleanup=False, full_clean=True)
        )
        await asyncio.to_thread(inside.wait, 5)
        st = await reg.command("import", "stop")
        winding = reg.status("import")
        release.set()
        await task
        return st, winding

    st, winding = asyncio.run(go())
    assert winding.state == State.RUNNING and winding.activity.startswith("Stopping")
    assert reg.status("import").state == State.STOPPED
    assert db.list_tombstones() == []
    assert _pending(db) is not None
    assert ts.full_clean_marker_exists(Path(db.db_path))


def test_the_full_clean_hold_is_dropped_however_the_run_ends(monkeypatch, db, reg):
    _seed(db)
    held_during = []

    class Scanner:
        outcome = None

        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            held_during.append(
                {
                    pid: reg.status(pid).state
                    for pid in ("embedding", "tagging", "hygiene")
                }
            )
            if Scanner.outcome == "shutdown":
                raise asyncio.CancelledError()
            if Scanner.outcome == "error":
                raise RuntimeError("/private/path boom")
            return 0

    _wire(monkeypatch, db, [], scanner_cls=Scanner)
    for outcome in (None, "error", "shutdown"):
        Scanner.outcome = outcome
        try:
            asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
        except asyncio.CancelledError:
            assert outcome == "shutdown"
        assert reg._holds == {}
        if outcome == "shutdown":  # deletes neither the pending file nor the marker
            assert _pending(db) is not None
            assert ts.full_clean_marker_exists(Path(db.db_path))
    assert all(
        s == {"embedding": State.HELD, "tagging": State.HELD, "hygiene": State.HELD}
        for s in held_during
    )


def test_embedding_is_released_before_the_post_walk_build(monkeypatch, db, reg):
    _seed(db)
    _wire(monkeypatch, db, [])
    monkeypatch.setattr(scan_api, "load_app_config", lambda: {"similarity": {}})
    monkeypatch.setattr(db, "get_unembedded_file_paths", lambda: ["/lib/x.png"])
    at_build = {}

    async def build_index(rebuild, by_owner):
        at_build.update({k: set(v) for k, v in reg._holds.items()})

    monkeypatch.setattr(scan_api.similarity_api, "build_index", build_index)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert set(at_build) == {"tagging", "hygiene"}


def test_a_scan_without_a_registry_still_runs(monkeypatch, db):
    _seed(db)
    _wire(monkeypatch, db, ["/lib/fav.png"])
    dependencies.set_registry(None)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert db.get_judgment("/lib/fav.png") is not None


def test_a_failure_shows_the_type_only_and_the_next_scan_clears_it(
    monkeypatch, db, reg
):
    class Boom:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            raise RuntimeError("/private/path.png is broken")

    _wire(monkeypatch, db, [], scanner_cls=Boom)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    st = reg.status("import")
    assert st.state == State.FAILED and st.error == "RuntimeError"

    _wire(monkeypatch, db, [])
    scan_api._scan_error = None  # what start_scan does
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert reg.status("import").state == State.IDLE


def test_the_owner_starting_a_scan_clears_a_stop(monkeypatch, db, reg):
    _wire(monkeypatch, db, [])
    scan_api._scan_task = None

    async def go():
        scan_api._scan_active = True  # something to stop
        try:
            await reg.command("import", "stop")
        finally:
            scan_api._scan_active = False
        assert reg.status("import").state == State.STOPPED
        monkeypatch.setattr(scan_api, "_run_scan", lambda *a: asyncio.sleep(0))
        await scan_api.start_scan(scan_api.ScanRequest())
        assert scan_api._scan_task is not None
        await scan_api._scan_task
        return reg.status("import").state

    assert asyncio.run(go()) == State.IDLE
