"""The hygiene checks and the mass-removal guard (spec 10, Task 19).

Real temp folders and a real temp DB. The checks delete rows by themselves, so
most of these tests are about what must NOT be deleted."""

import os
import shutil
import time
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from backend.api import scan as scan_api
from backend.services import taste_snapshot
from backend.services.background import hygiene_checks as hc
from backend.services.background.hygiene import (
    CheckResult,
    HygieneContext,
    HygieneProcess,
    MassRemovalBlocked,
)
from backend.services.background.hygiene_checks import (
    ChangedFiles,
    Finish,
    MissingDerived,
    MissingFiles,
    NewFiles,
    default_checks,
)
from backend.services.background.idle import IdleTracker
from backend.services.background.protocol import State
from backend.services.background.registry import ProcessRegistry
from metascan.cache.thumbnail import ThumbnailCache
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.move_detector import MOVE_GRACE_S
from metascan.core.scanner import Scanner
from metascan.utils.path_utils import to_posix_path

NOW = 1000.0


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture(autouse=True)
def _wired(monkeypatch, db):
    monkeypatch.setattr(scan_api, "get_db", lambda: db)
    monkeypatch.setattr(scan_api, "_scan_task", None)
    monkeypatch.setattr(scan_api, "_scan_active", False)
    monkeypatch.setattr(hc, "_vlm_tier", lambda: False)


class FakeImport:
    def __init__(self, vanished_at=NOW - 10 * MOVE_GRACE_S):
        self.vanished_at = vanished_at  # None: Import never saw them vanish
        self.enqueued = []
        self.settling = set()
        self.bad = set()
        self.halted = False  # the owner paused or stopped Import

    def owner_halted(self):
        return self.halted

    def vanished_since(self, path):
        return self.vanished_at

    def is_settling(self, path):
        return path in self.settling

    def accepts_unknown(self):
        return True

    def gave_up_on(self, path):
        return path in self.bad

    async def enqueue_unknown(self, paths):
        self.enqueued.extend(paths)
        return len(paths)

    async def apply_moves(self, pairs):
        return []


def _media(path) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


def _rows(db, folder: Path, present: int, missing: int, prefix="f"):
    """``present`` rows with a file and ``missing`` rows without."""
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(present + missing):
        p = folder / f"{prefix}{i}.png"
        if i < present:
            p.write_bytes(b"x")
        db.save_media(_media(p))
    return [folder / f"{prefix}{i}.png" for i in range(present, present + missing)]


def _ctx(
    db, folders, tmp_path, *, imp="default", confirmed=None, stop=lambda: False, **cfg
):
    config = {
        "directories": [
            {"filepath": str(f), "search_subfolders": True} for f in folders
        ]
    }
    config.update(cfg)
    return HygieneContext(
        db=db,
        config=config,
        data_dir=scan_api._data_dir(db),
        import_process=FakeImport() if imp == "default" else imp,
        should_stop=stop,
        report=lambda *a: None,
        clock=lambda: NOW,
        confirmed={str(c): n for c, n in (confirmed or {}).items()},
    )


def _has(db, path) -> bool:
    return to_posix_path(path) in db.all_media_paths()


def _tombstone_grade(db, path):
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT grade FROM media_tombstones WHERE file_path = ?",
            (to_posix_path(path),),
        ).fetchone()
    return None if row is None else row[0]


# ------------------------------------------------------------ missing files


async def test_missing_file_is_tombstoned_with_its_grade(db, tmp_path):
    folder = tmp_path / "lib"
    [gone] = _rows(db, folder, present=3, missing=1)
    db.set_grade(gone, "pass")
    ctx = _ctx(db, [folder], tmp_path)
    result = await MissingFiles().run(ctx)
    assert result.cleaned == 1
    assert not _has(db, gone)
    assert _tombstone_grade(db, gone) == "pass"


async def test_nothing_is_deleted_while_the_owner_has_import_halted(db, tmp_path):
    folder = tmp_path / "lib"
    [gone] = _rows(db, folder, present=3, missing=1)
    imp = FakeImport()
    imp.halted = True
    result = await MissingFiles().run(_ctx(db, [folder], tmp_path, imp=imp))
    assert result.cleaned == 0 and _has(db, gone)
    imp.halted = False  # Play: the next pass cleans as before
    result = await MissingFiles().run(_ctx(db, [folder], tmp_path, imp=imp))
    assert result.cleaned == 1 and not _has(db, gone)


async def test_real_import_stopped_by_the_owner_deletes_nothing_until_play(
    db, tmp_path
):
    folder = tmp_path / "lib"
    [gone] = _rows(db, folder, present=3, missing=1)
    imp, registry = _real_import(tmp_path, db)
    try:
        registry._intent["import"] = "stopped"
        assert imp.owner_halted()
        ctx = _ctx(db, [folder], tmp_path, imp=imp)
        assert (await MissingFiles().run(ctx)).cleaned == 0 and _has(db, gone)
        registry._intent["import"] = "none"  # Play
        assert not imp.owner_halted()
    finally:
        await registry.aclose()


async def test_vanished_within_move_grace_is_not_cleaned(db, tmp_path):
    folder = tmp_path / "lib"
    [gone] = _rows(db, folder, present=3, missing=1)
    imp = FakeImport(vanished_at=NOW - MOVE_GRACE_S + 5)
    result = await MissingFiles().run(_ctx(db, [folder], tmp_path, imp=imp))
    assert result.cleaned == 0 and _has(db, gone)
    imp.vanished_at = NOW - MOVE_GRACE_S
    assert (
        await MissingFiles().run(_ctx(db, [folder], tmp_path, imp=imp))
    ).cleaned == 1


async def test_a_file_import_never_saw_vanish_waits_for_the_next_pass(db, tmp_path):
    folder = tmp_path / "lib"
    [gone] = _rows(db, folder, present=3, missing=1)
    check = MissingFiles()
    ctx = _ctx(db, [folder], tmp_path, imp=FakeImport(vanished_at=None))
    assert (await check.run(ctx)).cleaned == 0
    assert _has(db, gone)
    later = _ctx(db, [folder], tmp_path, imp=FakeImport(vanished_at=None))
    later.clock = lambda: NOW + hc.WATCH_INTERVAL_S
    assert (await check.run(later)).cleaned == 1


async def test_nothing_under_an_unavailable_folder_is_cleaned(db, tmp_path):
    folder = tmp_path / "drive"
    gone = _rows(db, folder, present=0, missing=3)
    # The folder is empty: the mount point of a drive that is not mounted.
    ctx = _ctx(db, [folder], tmp_path)
    result = await MissingFiles().run(ctx)
    assert result.cleaned == 0
    assert all(_has(db, p) for p in gone)
    assert ctx.shared["missing_folders"] == [str(folder)]
    # A folder that is missing altogether likewise.
    folder.rmdir()
    ctx = _ctx(db, [folder], tmp_path)
    assert (await MissingFiles().run(ctx)).cleaned == 0
    assert all(_has(db, p) for p in gone)


@pytest.mark.parametrize(
    "present,missing,blocked",
    [(299, 101, True), (0, 100, False), (309, 101, False)],
    ids=["101 of 400", "100 of 100", "101 of 410"],
)
async def test_guard_needs_both_25_percent_and_100_rows(
    db, tmp_path, present, missing, blocked
):
    folder = tmp_path / "lib"
    gone = _rows(db, folder, present=present, missing=missing)
    if not present:
        (folder / "keep.txt").write_bytes(b"x")  # not an unmounted mount point
    ctx = _ctx(db, [folder], tmp_path)
    result = await MissingFiles().run(ctx)
    if blocked:
        assert result.cleaned == 0
        assert ctx.shared["blocked"] == [(str(folder), missing)]
        assert all(_has(db, p) for p in gone)
    else:
        assert result.cleaned == missing
        assert not ctx.shared.get("blocked")
        assert not any(_has(db, p) for p in gone)


async def test_guard_blocks_one_folder_and_cleans_the_others(db, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    gone_a = _rows(db, a, present=299, missing=101)
    gone_b = _rows(db, b, present=7, missing=3)
    ctx = _ctx(db, [a, b], tmp_path)
    result = await MissingFiles().run(ctx)
    assert result.cleaned == 3
    assert all(_has(db, p) for p in gone_a)
    assert not any(_has(db, p) for p in gone_b)
    assert ctx.shared["blocked"] == [(str(a), 101)]


async def test_rows_under_no_configured_folder_are_one_guarded_group(db, tmp_path):
    kept = tmp_path / "kept"
    old = tmp_path / "removed_from_config"
    _rows(db, kept, present=2, missing=0)
    gone = _rows(db, old, present=299, missing=101)
    ctx = _ctx(db, [kept], tmp_path)
    assert (await MissingFiles().run(ctx)).cleaned == 0
    assert ctx.shared["blocked"] == [(hc.NO_FOLDER_LABEL, 101)]
    assert hc.NO_FOLDER_LABEL == "folders no longer in the scan list"
    assert all(_has(db, p) for p in gone)
    # Confirmed, the group is cleaned like any other.
    ctx = _ctx(db, [kept], tmp_path, confirmed={hc.NO_FOLDER_LABEL: 101})
    assert (await MissingFiles().run(ctx)).cleaned == 101


async def test_incomplete_full_clean_adds_but_cleans_nothing(db, tmp_path):
    folder = tmp_path / "lib"
    [gone] = _rows(db, folder, present=2, missing=1)
    new = folder / "new.png"
    new.write_bytes(b"y")
    os.utime(new, (NOW, NOW - 3600))
    taste_snapshot.write_full_clean_marker(scan_api._data_dir(db))
    imp = FakeImport()
    ctx = _ctx(db, [folder], tmp_path, imp=imp)
    assert (await MissingFiles().run(ctx)).cleaned == 0
    assert _has(db, gone) and ctx.shared["cleaning_skipped"]
    await NewFiles().run(ctx)
    assert imp.enqueued == [str(new)]


async def test_the_pending_snapshot_also_stops_cleaning(db, tmp_path):
    folder = tmp_path / "lib"
    [gone] = _rows(db, folder, present=2, missing=1)
    scan_api._data_dir(db).mkdir(parents=True, exist_ok=True)
    taste_snapshot.snapshot_file_path(scan_api._data_dir(db)).write_text("{}")
    assert (await MissingFiles().run(_ctx(db, [folder], tmp_path))).cleaned == 0
    assert _has(db, gone)


async def test_a_running_scan_or_a_taken_gate_skips_the_delete(
    db, tmp_path, monkeypatch
):
    folder = tmp_path / "lib"
    [gone] = _rows(db, folder, present=2, missing=1)
    monkeypatch.setattr(scan_api, "_scan_active", True)
    assert (await MissingFiles().run(_ctx(db, [folder], tmp_path))).cleaned == 0
    monkeypatch.setattr(scan_api, "_scan_active", False)
    assert scan_api.move_gate.acquire(blocking=False)
    try:
        ctx = _ctx(db, [folder], tmp_path)
        assert (await MissingFiles().run(ctx)).cleaned == 0
        assert ctx.shared["interrupted"]  # nothing completes on a skipped batch
    finally:
        scan_api.move_gate.release()
    assert _has(db, gone)
    assert (await MissingFiles().run(_ctx(db, [folder], tmp_path))).cleaned == 1


async def test_a_batch_holds_the_gate_for_at_most_200_rows(db, tmp_path, monkeypatch):
    folder = tmp_path / "lib"
    _rows(db, folder, present=900, missing=250)  # 250 of 1150: 21.7%
    sizes = []
    real = db.delete_media_batch

    def spy(paths, *a, **k):
        assert scan_api.move_gate.locked()  # held for the delete
        sizes.append(len(paths))
        return real(paths, *a, **k)

    monkeypatch.setattr(db, "delete_media_batch", spy)
    assert (await MissingFiles().run(_ctx(db, [folder], tmp_path))).cleaned == 250
    assert sizes == [200, 50]
    assert not scan_api.move_gate.locked()


async def test_a_row_under_an_unreachable_output_root_is_left_alone(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=2, missing=0)
    root = tmp_path / "unmounted" / "t2i"
    stuck = _rows(db, root / "2026-01-01", present=0, missing=1, prefix="g")
    shutil.rmtree(tmp_path / "unmounted")  # the drive is gone
    ctx = _ctx(db, [folder], tmp_path, t2i={"output_root": str(root)})
    assert (await MissingFiles().run(ctx)).cleaned == 0
    assert _has(db, stuck[0]) and ctx.shared["output_unreachable"]


async def test_a_row_under_a_reachable_output_root_is_cleaned(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=2, missing=0)
    root = tmp_path / "out" / "t2i"
    stuck = _rows(db, root / "2026-01-01", present=1, missing=1, prefix="g")
    ctx = _ctx(db, [folder], tmp_path, t2i={"output_root": str(root)})
    assert (await MissingFiles().run(ctx)).cleaned == 1
    assert not _has(db, stuck[0])


async def test_a_stop_between_chunks_cleans_nothing_and_latches(db, tmp_path):
    folder = tmp_path / "lib"
    [gone] = _rows(db, folder, present=2, missing=1)
    ctx = _ctx(db, [folder], tmp_path, stop=lambda: True)
    assert (await MissingFiles().run(ctx)).cleaned == 0
    assert _has(db, gone) and ctx.shared["interrupted"]


# ----------------------------------------------------------- the guard, Play


class Rig:
    def __init__(self, tmp_path, db, folders):
        self.clock = lambda: NOW
        self.registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
        self.proc = HygieneProcess(
            self.registry,
            IdleTracker(self.clock),
            get_db=lambda: db,
            get_config=lambda: {
                "directories": [
                    {"filepath": str(f), "search_subfolders": True} for f in folders
                ]
            },
            get_data_dir=scan_api._data_dir,
            import_process=lambda: FakeImport(),
            checks=[MissingFiles()],
            clock=self.clock,
        )
        self.registry.register(self.proc)

    async def play(self):
        await self.registry.command("hygiene", "start")
        await self.proc.wait_pass()

    def status(self):
        return self.registry.status("hygiene")

    async def aclose(self):
        await self.proc.aclose()
        await self.registry.aclose()


async def test_play_confirms_that_folder_only(db, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    gone_a = _rows(db, a, present=299, missing=101)
    gone_b = _rows(db, b, present=299, missing=101)
    rig = Rig(tmp_path, db, [a, b])
    try:
        await rig.play()
        st = rig.status()
        assert st.state == State.FAILED
        assert (
            st.activity
            == f"101 files missing from {a} and 1 more: press play to confirm"
        )
        assert all(_has(db, p) for p in gone_a + gone_b)

        await rig.play()  # Play: a is confirmed, b is not
        assert not any(_has(db, p) for p in gone_a)
        assert all(_has(db, p) for p in gone_b)
        st = rig.status()
        assert st.state == State.FAILED
        assert st.activity == f"101 files missing from {b}: press play to confirm"

        # The confirmation lapsed with that pass: nothing more goes unasked.
        assert rig.proc._confirmed == {}
    finally:
        await rig.aclose()


async def test_the_message_uses_thousands_separators(db):
    msg = MassRemovalBlocked("D:\\Media", 1240).message()
    assert msg == "1,240 files missing from D:\\Media: press play to confirm"


async def test_a_blocked_folder_that_recovers_is_not_confirmed_later(db, tmp_path):
    a = tmp_path / "a"
    gone = _rows(db, a, present=299, missing=101)
    rig = Rig(tmp_path, db, [a])
    try:
        await rig.play()
        assert rig.proc._blocked_folders == [str(a)]
        for p in gone:  # the files reappear
            p.write_bytes(b"x")
        await rig.play()
        assert rig.proc._blocked_folders == []
        assert rig.status().state == State.IDLE
        for p in gone:  # and go again: blocked once more, no stale confirmation
            p.unlink()
        await rig.play()
        assert rig.status().state == State.FAILED
    finally:
        await rig.aclose()


async def test_in_sync_shows_the_last_run_and_what_it_cleaned(db, tmp_path):
    a = tmp_path / "a"
    _rows(db, a, present=9, missing=1)
    rig = Rig(tmp_path, db, [a])
    try:
        await rig.play()
        text = rig.status().activity
        assert text.startswith("In sync, last run ") and text.endswith("cleaned 1 file")
    finally:
        await rig.aclose()


# ------------------------------------------------------------ the marker


async def _finish(db, tmp_path, folder, *, interrupt=False):
    ctx = _ctx(db, [folder], tmp_path)
    await NewFiles().run(ctx)
    if interrupt:
        ctx.shared["interrupted"] = True
    await Finish().run(ctx)
    return ctx


async def test_clean_pass_removes_the_marker_like_an_ordinary_scan(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=2, missing=0)
    data_dir = scan_api._data_dir(db)
    taste_snapshot.write_full_clean_marker(data_dir)
    await _finish(db, tmp_path, folder)
    assert not taste_snapshot.full_clean_marker_exists(data_dir)


async def test_cancelled_pass_keeps_the_marker(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=2, missing=0)
    data_dir = scan_api._data_dir(db)
    taste_snapshot.write_full_clean_marker(data_dir)
    await _finish(db, tmp_path, folder, interrupt=True)
    assert taste_snapshot.full_clean_marker_exists(data_dir)


async def test_finish_runs_on_an_interrupted_pass_with_cancelled_true(
    db, tmp_path, monkeypatch
):
    seen = []
    monkeypatch.setattr(scan_api, "finish_ordinary_pass", lambda rep: seen.append(rep))
    rig_folder = tmp_path / "lib"
    _rows(db, rig_folder, present=1, missing=0)
    stop = {"now": False}

    class Stopper:
        name = "stopper"

        async def run(self, ctx):
            stop["now"] = True
            return CheckResult()

    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    proc = HygieneProcess(
        registry,
        IdleTracker(lambda: NOW),
        get_db=lambda: db,
        get_config=lambda: {"directories": []},
        get_data_dir=scan_api._data_dir,
        checks=[Stopper(), Finish()],
        clock=lambda: NOW,
    )
    proc.should_stop = lambda: stop["now"]  # type: ignore[method-assign]
    registry.register(proc)
    try:
        await registry.command("hygiene", "start")
        await proc.wait_pass()
    finally:
        await proc.aclose()
        await registry.aclose()
    assert len(seen) == 1 and seen[0].cancelled is True
    assert seen[0].completed is False


async def test_an_unreachable_output_root_keeps_the_marker(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=2, missing=0)
    root = tmp_path / "unmounted" / "t2i"
    data_dir = scan_api._data_dir(db)
    taste_snapshot.write_full_clean_marker(data_dir)
    # The marker pauses MissingFiles, so ask it past the pause the way Finish
    # sees it: a row left alone under the lost root.
    ctx = _ctx(db, [folder], tmp_path, t2i={"output_root": str(root)})
    await NewFiles().run(ctx)
    ctx.shared["output_unreachable"] = True
    await Finish().run(ctx)
    assert taste_snapshot.full_clean_marker_exists(data_dir)


async def test_an_unavailable_folder_keeps_the_marker(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=2, missing=0)
    lost = tmp_path / "lost"  # not there
    data_dir = scan_api._data_dir(db)
    taste_snapshot.write_full_clean_marker(data_dir)
    ctx = _ctx(db, [folder, lost], tmp_path)
    await NewFiles().run(ctx)
    assert ctx.shared["missing_folders"] == [str(lost)]
    assert ctx.shared["walked_folders"] == 1
    await Finish().run(ctx)
    assert taste_snapshot.full_clean_marker_exists(data_dir)


# ------------------------------------------------------------ new files


async def test_new_files_go_through_imports_path_not_a_second_one(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=2, missing=0)
    old = folder / "old_new.png"
    old.write_bytes(b"y")
    os.utime(old, (NOW, NOW - 3600))
    fresh = folder / "being_written.png"
    fresh.write_bytes(b"y")  # modified just now: Import will settle it
    settling = folder / "settling.png"
    settling.write_bytes(b"y")
    os.utime(settling, (NOW, NOW - 3600))
    imp = FakeImport()
    imp.settling.add(str(settling))
    ctx = _ctx(db, [folder], tmp_path, imp=imp)
    await NewFiles().run(ctx)
    assert imp.enqueued == [str(old)]
    assert not _has(db, old)  # hygiene ingests nothing itself


async def test_new_files_under_an_output_root_go_to_import(db, tmp_path):
    folder = tmp_path / "lib"
    folder.mkdir()
    out = folder / "out" / "t2i"
    out.mkdir(parents=True)
    f = out / "g.png"
    f.write_bytes(b"y")
    os.utime(f, (NOW, NOW - 3600))
    imp = FakeImport()
    await NewFiles().run(
        _ctx(db, [folder], tmp_path, imp=imp, t2i={"output_root": str(out)})
    )
    assert imp.enqueued == [str(f)]


async def test_import_enqueue_unknown_and_is_settling(tmp_path):
    from backend.services.background.import_process import ImportProcess

    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    imp = ImportProcess(registry, lambda: {"running": False}, lambda: False)
    assert await imp.enqueue_unknown([str(tmp_path / "a.png")]) == 0  # not watching
    imp._watch_on = True
    assert await imp.enqueue_unknown([str(tmp_path / "a.png")]) == 1  # via _intake
    assert await imp.enqueue_unknown([str(tmp_path / "a.png")]) == 0
    assert imp.is_settling(str(tmp_path / "a.png"))
    assert not imp.is_settling(str(tmp_path / "b.png"))
    await registry.aclose()


# ------------------------------------------------------------ changed files


def _png(path: Path, size=(8, 8), color="red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def _real_scanner(db, tmp_path, with_thumbs=False):
    cache = ThumbnailCache(tmp_path / "thumbs") if with_thumbs else None
    return Scanner(db, cache), cache


async def test_changed_file_reingests_and_keeps_ratings(db, tmp_path):
    folder = tmp_path / "lib"
    p = _png(folder / "a.png")
    scanner, _ = _real_scanner(db, tmp_path)
    scanner.ingest_file(p)
    db.set_grade(p, "pass")
    with db._get_connection() as conn:
        conn.execute("UPDATE media_hashes SET has_embedding = 1")
        conn.commit()
    ctx = _ctx(db, [folder], tmp_path)
    ctx.scanner = scanner
    assert (await ChangedFiles().run(ctx)).changed == 0  # nothing differs

    _png(p, size=(40, 40), color="blue")
    os.utime(p, (NOW, time.time() + 100))
    imp = FakeImport()
    ctx = _ctx(db, [folder], tmp_path, imp=imp)
    ctx.scanner = scanner
    assert (await ChangedFiles().run(ctx)).changed == 1
    with db._get_connection() as conn:
        size = conn.execute("SELECT file_size FROM media").fetchone()[0]
        emb = conn.execute("SELECT has_embedding FROM media_hashes").fetchone()[0]
    assert size == p.stat().st_size and emb == 0
    with db._get_connection() as conn:
        grade = conn.execute("SELECT grade FROM media_judgments").fetchone()[0]
    assert grade == "pass"


async def test_changed_file_import_has_settling_is_skipped(db, tmp_path):
    folder = tmp_path / "lib"
    p = _png(folder / "a.png")
    scanner, _ = _real_scanner(db, tmp_path)
    scanner.ingest_file(p)
    _png(p, size=(40, 40))
    os.utime(p, (NOW, time.time() + 100))
    imp = FakeImport()
    imp.settling.add(str(p))
    ctx = _ctx(db, [folder], tmp_path, imp=imp)
    ctx.scanner = scanner
    assert (await ChangedFiles().run(ctx)).changed == 0


async def test_mtime_within_two_seconds_is_not_a_change(db, tmp_path):
    folder = tmp_path / "lib"
    p = _png(folder / "a.png")
    scanner, _ = _real_scanner(db, tmp_path)
    scanner.ingest_file(p)
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 1.5))
    ctx = _ctx(db, [folder], tmp_path)
    ctx.scanner = scanner
    assert (await ChangedFiles().run(ctx)).changed == 0


async def test_both_modified_at_shapes_compare(db, tmp_path):
    folder = tmp_path / "lib"
    p = _png(folder / "a.png")
    scanner, _ = _real_scanner(db, tmp_path)
    scanner.ingest_file(p)
    with db._get_connection() as conn:  # the back-filled epoch-float shape
        conn.execute("UPDATE media SET modified_at = ?", (str(p.stat().st_mtime),))
        conn.commit()
    page = db.media_stat_page("", 10)
    assert abs(page[0][2] - p.stat().st_mtime) < 0.001
    ctx = _ctx(db, [folder], tmp_path)
    ctx.scanner = scanner
    assert (await ChangedFiles().run(ctx)).changed == 0


# ------------------------------------------------------------ derived data


class Kick:
    def __init__(self):
        self.calls = []

    async def kick(self, *a, **k):
        self.calls.append((a, k))
        return {"status": "started"}


class TagKick:
    def __init__(self):
        self.calls = 0

    def kick(self, **k):
        self.calls += 1
        return True


async def test_missing_thumbnail_and_phash_regenerate(db, tmp_path):
    folder = tmp_path / "lib"
    p = _png(folder / "a.png")
    scanner, cache = _real_scanner(db, tmp_path, with_thumbs=True)
    scanner.ingest_file(p)
    thumb = cache.get_thumbnail_path(p)
    assert thumb.exists()
    thumb.unlink()
    with db._get_connection() as conn:
        conn.execute("UPDATE media_hashes SET phash = NULL, has_embedding = 1")
        conn.commit()
    ctx = _ctx(db, [folder], tmp_path)
    ctx.thumbnails = cache
    ctx.embedding = Kick()
    result = await MissingDerived().run(ctx)
    assert result.changed == 2
    assert thumb.exists()
    with db._get_connection() as conn:
        phash, emb = conn.execute(
            "SELECT phash, has_embedding FROM media_hashes"
        ).fetchone()
    assert phash and emb == 1  # the embedding mark is not reset


async def test_unembedded_kicks_embedding_and_untagged_enqueues(
    db, tmp_path, monkeypatch
):
    folder = tmp_path / "lib"
    plain = _png(folder / "plain.png")
    gen = _png(folder / "gen.png")
    spent = _png(folder / "spent.png")
    tagged = _png(folder / "tagged.png")
    scanner, cache = _real_scanner(db, tmp_path, with_thumbs=True)
    for p in (plain, gen, spent, tagged):
        scanner.ingest_file(p)
    db.add_tag_indices(tagged, ["cat"], source="vlm")
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO t2i_images(file_path, batch_id) VALUES (?, 'b')",
            (to_posix_path(gen),),
        )
        conn.commit()
    db.mark_tag_exhausted(spent)
    monkeypatch.setattr(hc, "_vlm_tier", lambda: True)
    ctx = _ctx(db, [folder], tmp_path)
    ctx.thumbnails, ctx.embedding, ctx.tagging = cache, Kick(), TagKick()
    await MissingDerived().run(ctx)
    assert ctx.embedding.calls == [((True,), {})]
    assert db.queued_tag_paths([plain, gen, spent, tagged]) == {to_posix_path(plain)}
    assert ctx.tagging.calls == 1


async def test_no_vlm_tier_means_no_tag_queue(db, tmp_path):
    folder = tmp_path / "lib"
    p = _png(folder / "plain.png")
    scanner, cache = _real_scanner(db, tmp_path, with_thumbs=True)
    scanner.ingest_file(p)
    ctx = _ctx(db, [folder], tmp_path)
    ctx.thumbnails, ctx.embedding, ctx.tagging = cache, Kick(), TagKick()
    await MissingDerived().run(ctx)
    assert db.queued_tag_paths([p]) == set() and ctx.tagging.calls == 0


# ------------------------------------------------------------ tag exhaustion


def test_fail_tag_giving_up_records_the_image_as_exhausted(db, tmp_path):
    p = tmp_path / "a.png"
    db.save_media(_media(p))
    db.enqueue_tags([p])
    for _ in range(3):
        db.fail_tag(p, "boom")
    assert db.images_without_vlm_tags() == [to_posix_path(p)]
    assert db.images_without_vlm_tags(for_hygiene=True) == []
    assert db.clear_tag_exhausted([p]) == 1
    assert db.images_without_vlm_tags(for_hygiene=True) == [to_posix_path(p)]


def test_exhausted_rows_go_with_the_media_row(db, tmp_path):
    p = tmp_path / "a.png"
    db.save_media(_media(p))
    db.mark_tag_exhausted(p)
    db.delete_media_batch([p])
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM vlm_tag_exhausted").fetchone()[0] == 0


# ------------------------------------------------------------ the list


async def test_a_new_check_is_one_class(db, tmp_path):
    names = [c.name for c in default_checks()]
    assert names == [
        "missing files",
        "new files",
        "changed files",
        "missing derived data",
        "finish",
    ]
    ran = []

    class Extra:
        name = "extra"

        async def run(self, ctx):
            ran.append(ctx.shared is not None)
            return CheckResult()

    registry = ProcessRegistry(tmp_path / "bg.json", lambda st: None)
    proc = HygieneProcess(
        registry,
        IdleTracker(lambda: NOW),
        get_db=lambda: db,
        get_config=lambda: {"directories": []},
        get_data_dir=lambda d: None,
        checks=[Extra()],
        clock=lambda: NOW,
    )
    registry.register(proc)
    try:
        await registry.command("hygiene", "start")
        await proc.wait_pass()
    finally:
        await proc.aclose()
        await registry.aclose()
    assert ran == [True]
    # The default process registers the five by itself.
    reg2 = ProcessRegistry(tmp_path / "bg2.json", lambda st: None)
    default = HygieneProcess(
        reg2,
        IdleTracker(lambda: NOW),
        get_db=lambda: db,
        get_config=lambda: {},
        get_data_dir=lambda d: None,
    )
    assert [c.name for c in default.checks] == names
    await reg2.aclose()


# ------------------------------------------------- moves, DST, guard, confirm


def _q(db, sql, args=()):
    with db._get_connection() as conn:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]


def _real_import(tmp_path, db, **kw):
    from backend.services.background.import_process import ImportProcess

    async def noop():
        return None

    registry = ProcessRegistry(tmp_path / "bgi.json", lambda st: None)
    imp = ImportProcess(
        registry,
        lambda: {"running": False},
        lambda: False,
        get_db=lambda: db,
        after_move=noop,
        broadcast=lambda *a: None,
        **kw,
    )
    return imp, registry


async def test_a_renamed_folder_keeps_its_ratings_with_import_stopped(db, tmp_path):
    folder = tmp_path / "lib"
    scanner, _ = _real_scanner(db, tmp_path)
    colors = ["red", "green", "blue", "yellow", "white"]
    files = []
    for i, c in enumerate(colors):  # noise: each file has its own size
        img = Image.frombytes(
            "RGB", (20 + 7 * i, 20 + 3 * i), os.urandom(3 * (20 + 7 * i) * (20 + 3 * i))
        )
        (folder / "old").mkdir(parents=True, exist_ok=True)
        img.save(folder / "old" / f"{c}.png")
        files.append(folder / "old" / f"{c}.png")
    keep = _png(folder / "keep.png", size=(30, 31))
    for p in [*files, keep]:
        scanner.ingest_file(p)
    target = files[0]
    db.set_grade(target, "pass")
    db.set_favorite_state(target, True)
    cat = db.create_category("Anime")
    db.add_media_categories([target], cat.id, "viewer")
    db.add_tag_indices(target, ["cat", "dog"], source="clip")

    os.rename(folder / "old", folder / "new")  # the folder is renamed
    imp, registry = _real_import(tmp_path, db)  # never started: Import is stopped
    try:
        ctx = _ctx(db, [folder], tmp_path, imp=imp)
        # Import never saw them vanish, so the plain grace would not allow a
        # delete in this pass either: the matcher must have re-keyed them.
        result = await MissingFiles().run(ctx)
        assert result.cleaned == 0
        await NewFiles().run(ctx)
    finally:
        await registry.aclose()
    moved = folder / "new" / files[0].name
    new = to_posix_path(moved)
    assert _has(db, moved) and not _has(db, target)
    assert _q(db, "SELECT grade FROM media_judgments WHERE file_path=?", (new,)) == [
        ("pass",)
    ]
    assert _q(db, "SELECT is_favorite FROM media WHERE file_path=?", (new,)) == [(1,)]
    assert _q(
        db, "SELECT COUNT(*) FROM media_categories WHERE file_path=?", (new,)
    ) == [(1,)]
    assert _q(
        db,
        "SELECT COUNT(*) FROM indices WHERE file_path=? AND index_type='tag'",
        (new,),
    ) == [(2,)]
    assert _q(db, "SELECT COUNT(*) FROM media_tombstones") == [(0,)]
    assert all(_has(db, folder / "new" / f.name) for f in files)


async def test_an_ambiguous_move_is_not_a_move(db, tmp_path):
    folder = tmp_path / "lib"
    scanner, _ = _real_scanner(db, tmp_path)
    a = _png(folder / "a" / "x.png")
    scanner.ingest_file(a)
    # two identical copies appear: either could be the file
    for name in ("b", "c"):
        shutil.copy2(a, _png(folder / name / "x.png"))
    a.unlink()
    imp, registry = _real_import(tmp_path, db)
    try:
        ctx = _ctx(db, [folder], tmp_path, imp=imp)
        await MissingFiles().run(ctx)
    finally:
        await registry.aclose()
    assert not _has(db, folder / "b" / "x.png") and not _has(db, folder / "c" / "x.png")


def test_a_daylight_saving_hour_is_the_same_time():
    from metascan.core.move_detector import Candidate, match_moves, mtimes_equal

    assert mtimes_equal(1000.0, 1000.0 + 3600.5)
    assert mtimes_equal(1000.0, 1000.0 - 3601.9)
    assert not mtimes_equal(1000.0, 1000.0 + 3603)
    assert not mtimes_equal(1000.0, 1000.0 + 1800)
    old = Candidate("/a/x.png", 5, 1000.0, "ph")
    new = Candidate("/b/x.png", 5, 4600.0, "ph")
    assert match_moves([new], [old]) == [("/a/x.png", "/b/x.png")]


async def test_a_file_an_hour_off_is_not_changed(db, tmp_path):
    folder = tmp_path / "lib"
    p = _png(folder / "a.png")
    scanner, _ = _real_scanner(db, tmp_path)
    scanner.ingest_file(p)
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE media SET modified_at = ?", (str(p.stat().st_mtime + 3600),)
        )
        conn.commit()
    ctx = _ctx(db, [folder], tmp_path)
    ctx.scanner = scanner
    assert (await ChangedFiles().run(ctx)).changed == 0


async def test_a_file_import_gave_up_on_is_not_handed_over_again(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=1, missing=0)
    bad = folder / "bad.png"
    bad.write_bytes(b"not an image")
    os.utime(bad, (NOW, NOW - 3600))
    imp, registry = _real_import(tmp_path, db)
    imp._watch_on = True
    st = bad.stat()
    imp._bad[str(bad)] = (st.st_size, st.st_mtime)
    try:
        assert await imp.enqueue_unknown([str(bad)]) == 0
        ctx = _ctx(db, [folder], tmp_path, imp=imp)
        await NewFiles().run(ctx)
        assert "new_files_pending" not in ctx.shared  # a failed ingest, as in a scan
        bad.write_bytes(b"changed bytes")  # its stamp changed: tried again
        os.utime(bad, (NOW, NOW - 3000))
        assert await imp.enqueue_unknown([str(bad)]) == 1
    finally:
        await registry.aclose()


async def test_import_paused_or_off_takes_nothing_and_keeps_the_marker(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=1, missing=0)
    new = folder / "new.png"
    new.write_bytes(b"y")
    os.utime(new, (NOW, NOW - 3600))
    data_dir = scan_api._data_dir(db)
    taste_snapshot.write_full_clean_marker(data_dir)
    imp = FakeImport()
    ctx = _ctx(db, [folder], tmp_path, imp=imp)
    await NewFiles().run(ctx)
    assert imp.enqueued == [str(new)] and ctx.shared["new_files_pending"]
    await Finish().run(ctx)
    assert taste_snapshot.full_clean_marker_exists(data_dir)  # files not in yet
    off = _ctx(db, [folder], tmp_path, imp=imp, watch_directories=False)
    imp.enqueued.clear()
    await NewFiles().run(off)
    assert imp.enqueued == []


async def test_the_guard_counts_rows_still_in_grace(db, tmp_path):
    folder = tmp_path / "lib"
    gone = _rows(db, folder, present=299, missing=101)
    imp = FakeImport(vanished_at=NOW - 5)  # all inside MOVE_GRACE_S
    ctx = _ctx(db, [folder], tmp_path, imp=imp)
    assert (await MissingFiles().run(ctx)).cleaned == 0
    assert ctx.shared["blocked"] == [(str(folder), 101)]

    # Some past grace: still the whole vanish is judged, never a slice of it.
    class Mixed(FakeImport):
        def vanished_since(self, path):
            return NOW - 1000 if int(Path(path).stem[1:]) % 2 else NOW - 5

    ctx = _ctx(db, [folder], tmp_path, imp=Mixed())
    assert (await MissingFiles().run(ctx)).cleaned == 0
    assert all(_has(db, p) for p in gone)


async def test_the_confirmation_is_for_the_count_shown(db, tmp_path):
    folder = tmp_path / "lib"
    _rows(db, folder, present=299, missing=101)
    # 30 more files vanish after Play showed 101
    for i in range(30):
        (folder / f"f{i}.png").unlink()
    ctx = _ctx(db, [folder], tmp_path, confirmed={str(folder): 101})
    assert (await MissingFiles().run(ctx)).cleaned == 0
    assert ctx.shared["blocked"] == [(str(folder), 131)]
    # within the margin (5% or 10 rows) the confirmation holds
    ctx = _ctx(db, [folder], tmp_path, confirmed={str(folder): 125})
    assert (await MissingFiles().run(ctx)).cleaned == 131


async def test_an_interrupted_pass_keeps_a_confirmation_that_cleaned_nothing(
    db, tmp_path
):
    a = tmp_path / "a"
    _rows(db, a, present=299, missing=101)

    class Interrupt:
        name = "interrupt"

        async def run(self, ctx):
            ctx.shared["interrupted"] = True
            return CheckResult()

    rig = Rig(tmp_path, db, [a])
    rig.proc.checks = [Interrupt()]
    try:
        rig.proc._blocked_counts = [(str(a), 101)]
        rig.proc._blocked_folders = [str(a)]
        await rig.play()
        assert rig.proc._confirmed == {str(a): 101}  # nothing cleaned: kept
        rig.proc.checks = [MissingFiles()]
        # the interrupted pass is still owed: the next tick runs it
        assert rig.proc.tick()
        await rig.proc.wait_pass()
        assert rig.proc._confirmed == {}
        assert not any(_has(db, a / f"f{i}.png") for i in range(299, 400))
    finally:
        await rig.aclose()


async def test_unhashable_files_are_not_retried_until_they_change(
    db, tmp_path, monkeypatch
):
    folder = tmp_path / "lib"
    scanner, cache = _real_scanner(db, tmp_path, with_thumbs=True)
    p = _png(folder / "a.png")
    scanner.ingest_file(p)
    with db._get_connection() as conn:
        conn.execute("UPDATE media_hashes SET phash = NULL")
        conn.commit()
    calls = []
    import metascan.core.phash_utils as pu

    monkeypatch.setattr(pu, "compute_phash_for_file", lambda f: calls.append(f) or None)
    check = MissingDerived()
    for _ in range(2):
        ctx = _ctx(db, [folder], tmp_path)
        ctx.thumbnails, ctx.embedding = cache, Kick()
        await check.run(ctx)
    assert len(calls) == 1
    _png(p, size=(20, 20))
    ctx = _ctx(db, [folder], tmp_path)
    ctx.thumbnails, ctx.embedding = cache, Kick()
    await check.run(ctx)
    assert len(calls) == 2


async def test_a_row_under_a_missing_folder_is_not_a_move_source(db, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    scanner, _ = _real_scanner(db, tmp_path)
    src = _png(a / "x.png", size=(40, 41))
    keep = _png(b / "keep.png", size=(30, 31))
    for p in (src, keep):
        scanner.ingest_file(p)
    db.set_grade(src, "pass")
    old = to_posix_path(src)
    away = tmp_path / "away"
    os.rename(a, away)  # the drive is unmounted
    b.mkdir(exist_ok=True)
    shutil.copy2(away / "x.png", b / "copy.png")  # cp -p: same size and mtime
    imp, registry = _real_import(tmp_path, db)
    try:
        ctx = _ctx(db, [a, b], tmp_path, imp=imp)
        result = await MissingFiles().run(ctx)
        assert result.cleaned == 0
        assert _has(db, src) and not _has(db, b / "copy.png")
        assert _q(
            db, "SELECT grade FROM media_judgments WHERE file_path=?", (old,)
        ) == [("pass",)]
        os.rename(away, a)  # the drive returns
        ctx = _ctx(db, [a, b], tmp_path, imp=imp)
        assert (await MissingFiles().run(ctx)).cleaned == 0
        assert _has(db, src)
        assert _q(
            db, "SELECT grade FROM media_judgments WHERE file_path=?", (old,)
        ) == [("pass",)]
    finally:
        await registry.aclose()
