"""The T2I / I2V listings never prune lineage until a full clean is finished
(CLAUDE.md, "Full clean & rescan keeps ratings"): while one runs, while its
pending file exists, and while its lasting marker does.

``list_t2i_images`` / ``list_i2v_videos`` delete the lineage rows whose
media row is gone. A full clean empties ``media`` and the rescan re-adds it
file by file, so every lineage row looks dangling until the rescan reaches
its file: one listing mid-rescan (the T2I dialog opening, a WebSocket
reconnect) used to wipe every prompt, draft, writer id, variant group and
scorecard id, and the completed full clean then tombstoned the vanished
images without their lineage. The scan-route cases are in
``test_taste_snapshot_scan.py``."""

import logging
import os
import sqlite3
import stat
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import pytest

from backend.services import taste_snapshot as ts
from metascan.core import database_sqlite
from metascan.core.database_sqlite import TASTE_SNAPSHOT_VERSION, DatabaseManager
from metascan.core.media import Media

T2I = [f"/lib/t2i/out{n}.png" for n in (1, 2)]
SOURCE = "/lib/src.png"
CLIPS = [f"/lib/i2v/clip{n}.mp4" for n in (1, 2)]

# What a writer-made image or clip records, and a full clean must keep.
T2I_LINEAGE: Dict[str, Any] = {
    "batch_id": "b" * 32,
    "model": "krea2",
    "caption": "a red kite over a gray sea",
    "prompt_used": "A red kite flies over a gray sea.",
    "draft_prompt": "A kite over the sea.",
    "edited": 1,
    "writer_id": "base+taste",
    "writer_model": "qwen3-vl-8b",
    "variant_group": "vg-1",
    "scorecard_id": "card-1",
    "resolved_caption": "a red kite over a gray sea",
    "prompt_categories": ["Landscape"],
}
I2V_LINEAGE: Dict[str, Any] = {
    "prompt_used": "The kite climbs into the wind.",
    "draft_prompt": "The kite climbs.",
    "edited": 1,
    "writer_id": "base",
    "writer_model": "qwen3-vl-8b",
    "variant_group": "vg-2",
}


def _media(path: str) -> Media:
    return Media(
        file_path=Path(path),
        file_size=1,
        width=8,
        height=8,
        format="png",
        created_at=datetime.now(),
        modified_at=datetime.now(),
    )


def _seed(db: DatabaseManager) -> None:
    for p in (*T2I, SOURCE, *CLIPS):
        db.save_media(_media(p))
    for n, path in enumerate(T2I):
        db.create_t2i_image(file_path=path, variant_index=n, **T2I_LINEAGE)
    for n, path in enumerate(CLIPS):
        db.create_i2v_video(
            source_path=SOURCE, file_path=path, variant_index=n, **I2V_LINEAGE
        )


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    _seed(mgr)
    yield mgr
    mgr.close()


def _rows(db: DatabaseManager) -> tuple:
    """(t2i_images rows, i2v_videos rows) actually stored."""
    with db._get_connection() as conn:
        return tuple(
            int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("t2i_images", "i2v_videos")
        )


def _list(db: DatabaseManager) -> tuple:
    """What the T2I dialog and the I2V dialog (for SOURCE) list."""
    return db.list_t2i_images(), db.list_i2v_videos(SOURCE)


def _names(db: DatabaseManager) -> tuple:
    images, clips = _list(db)
    return (
        [r["file_name"] for r in images],
        [Path(r["file_path"]).name for r in clips],
    )


def _lineage(row: Dict[str, Any], facts: Dict[str, Any]) -> Dict[str, Any]:
    return {k: row[k] for k in facts}


def _delete_first_files(db: DatabaseManager) -> None:
    """A library delete of one image and one clip: it leaves their lineage
    rows behind for the listing to prune."""
    assert db.delete_media(Path(T2I[0]))
    assert db.delete_media(Path(CLIPS[0]))


def _empty_snapshot() -> Dict[str, Any]:
    """What a full clean of a library with no ratings writes."""
    return {
        "version": TASTE_SNAPSHOT_VERSION,
        "taken_at": "2026-10-25T00:00:00+00:00",
        "judgments": [],
        "categories": [],
        "favorites": [],
    }


# ---- the database hook --------------------------------------------------------


def test_a_paused_prune_keeps_every_row_through_a_full_clean(db):
    db.set_lineage_prune_guard(lambda: True)
    images, clips = _list(db)
    assert [_lineage(r, T2I_LINEAGE) for r in images] == [T2I_LINEAGE] * 2
    assert [_lineage(r, I2V_LINEAGE) for r in clips] == [I2V_LINEAGE] * 2
    assert db.truncate_all_data()

    # Mid-rescan: nothing to list yet (no media row), and nothing deleted.
    assert _list(db) == ([], [])
    assert _rows(db) == (2, 2)

    # The rescan brings the files back: the same rows, lineage and all.
    for p in (*T2I, SOURCE, *CLIPS):
        db.save_media(_media(p))
    assert _list(db) == (images, clips)


def test_a_paused_prune_still_lists_only_rows_whose_media_exists(db):
    db.set_lineage_prune_guard(lambda: True)
    _delete_first_files(db)
    assert _names(db) == (["out2.png"], ["clip2.mp4"])
    assert _rows(db) == (2, 2)


@pytest.mark.parametrize(
    "guard", [None, lambda: False], ids=["no guard", "no full clean"]
)
def test_with_no_full_clean_a_dangling_row_is_still_pruned(
    db, guard: Optional[Callable[[], bool]]
):
    db.set_lineage_prune_guard(guard)
    _delete_first_files(db)
    assert _names(db) == (["out2.png"], ["clip2.mp4"])
    assert _rows(db) == (1, 1)  # pruned for real, not just filtered


def test_the_prune_resumes_once_the_full_clean_is_over(db):
    state = {"paused": True}
    db.set_lineage_prune_guard(lambda: state["paused"])
    _delete_first_files(db)
    _list(db)
    assert _rows(db) == (2, 2)
    state["paused"] = False
    assert _names(db) == (["out2.png"], ["clip2.mp4"])
    assert _rows(db) == (1, 1)


def test_a_guard_that_raises_pauses_the_prune(db, caplog):
    def broken() -> bool:
        raise OSError("cannot stat the pending file")

    db.set_lineage_prune_guard(broken)
    _delete_first_files(db)
    with caplog.at_level(logging.DEBUG, logger="metascan.core.database_sqlite"):
        assert _names(db) == (["out2.png"], ["clip2.mp4"])
    assert _rows(db) == (2, 2)  # never prune when unsure
    logged = [r for r in caplog.records if "cannot stat" in r.getMessage()]
    # One per listing: a WARNING first, then DEBUG (see the next test).
    assert [r.levelno for r in logged] == [logging.WARNING, logging.DEBUG]


def test_a_guard_that_keeps_failing_warns_once_per_interval(db, caplog, monkeypatch):
    def broken() -> bool:
        raise OSError("cannot stat the marker")

    db.set_lineage_prune_guard(broken)
    _delete_first_files(db)

    def warnings() -> int:
        return len(
            [
                r
                for r in caplog.records
                if r.levelno == logging.WARNING and "cannot stat" in r.getMessage()
            ]
        )

    with caplog.at_level(logging.DEBUG, logger="metascan.core.database_sqlite"):
        for _ in range(3):
            _list(db)
        assert warnings() == 1  # every listing asks the guard; one WARNING
        monkeypatch.setattr(database_sqlite, "PRUNE_GUARD_WARN_INTERVAL_S", 0.0)
        db.list_t2i_images()  # the interval has passed: one more
    assert warnings() == 2
    assert _rows(db) == (2, 2)


def test_the_guard_is_read_under_the_db_lock(db):
    """A full clean's truncate takes the DB lock, so it can never land
    between a listing's read of the guard and its delete."""
    held: List[bool] = []

    def guard() -> bool:
        held.append(db.lock.locked())
        return False

    db.set_lineage_prune_guard(guard)
    _delete_first_files(db)
    _list(db)
    assert held == [True, True]


# ---- the guard the lifespan installs ----------------------------------------


def test_no_full_clean_and_no_pending_file_lets_the_prune_run(tmp_path):
    assert ts.lineage_prune_guard(tmp_path)() is False
    assert ts.is_full_clean_running() is False


def test_a_pending_file_pauses_the_prune_even_an_empty_one(tmp_path):
    guard = ts.lineage_prune_guard(tmp_path)
    ts.write_snapshot_file(tmp_path, _empty_snapshot())
    assert ts.pending_snapshot_exists(tmp_path) is True
    assert guard() is True
    ts.snapshot_file_path(tmp_path).unlink()
    assert guard() is False


def test_a_running_full_clean_pauses_the_prune_until_it_ends(tmp_path):
    guard = ts.lineage_prune_guard(tmp_path)
    with ts.full_clean_running():
        assert ts.is_full_clean_running() is True
        assert guard() is True  # no pending file needed
    assert guard() is False
    with pytest.raises(RuntimeError):
        with ts.full_clean_running():
            raise RuntimeError("the scan failed")
    assert ts.is_full_clean_running() is False  # cleared however it ends
    assert guard() is False


def test_the_marker_alone_pauses_the_prune(tmp_path):
    """The lasting marker of an unfinished full clean: it outlives the run
    and the pending file, and only clear_full_clean_marker (a scan of every
    folder completed) removes it."""
    guard = ts.lineage_prune_guard(tmp_path)
    ts.write_full_clean_marker(tmp_path)
    assert ts.full_clean_marker_exists(tmp_path) is True
    assert ts.pending_snapshot_exists(tmp_path) is False
    assert guard() is True
    assert ts.clear_full_clean_marker(tmp_path) is True
    assert ts.full_clean_marker_exists(tmp_path) is False
    assert guard() is False
    assert ts.clear_full_clean_marker(tmp_path) is True  # already gone: fine


def test_the_marker_is_durable_once_written(tmp_path, monkeypatch):
    """Written before the truncate, so its data and its directory entry are
    fsynced before write_full_clean_marker returns."""
    synced: List[bool] = []
    real_fsync = os.fsync

    def spy_fsync(fd: int) -> None:
        synced.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        real_fsync(fd)

    monkeypatch.setattr(ts.os, "fsync", spy_fsync)
    ts.write_full_clean_marker(tmp_path)
    assert synced[0] is False  # the file
    if os.name != "nt":
        assert synced[1] is True  # its directory entry


def test_the_marker_says_how_to_resume_the_prune(tmp_path):
    ts.write_full_clean_marker(tmp_path)
    text = ts.full_clean_marker_path(tmp_path).read_text(encoding="utf-8")
    flat = " ".join(text.split()).lower()
    assert ts.full_clean_marker_path(tmp_path).name == "full_clean_incomplete"
    assert "delete this file" in flat
    # A scan may have completed since with a folder missing: the marker
    # never claims that no scan finished.
    assert "no scan has finished" not in flat
    assert "every configured folder" in flat


def _marker_text(data_dir: Path) -> str:
    return ts.full_clean_marker_path(data_dir).read_text(encoding="utf-8")


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def _noted(data_dir: Path, written: str) -> str:
    """The block the last note left after the marker's text, which it
    leaves as written."""
    text = _marker_text(data_dir)
    assert text.startswith(written)
    return text.removeprefix(written)


def test_the_marker_says_what_holds_the_full_clean_back(tmp_path):
    """Its text says the rule the scan applies: an output folder that is
    missing or can't be read holds the clean back only when it held a
    generated file the scan did not find, and a generated file on disk that
    can't be read or imported holds it back too."""
    ts.write_full_clean_marker(tmp_path)
    flat = _flat(_marker_text(tmp_path))
    assert "neither generated-output folder missing or unreadable" not in flat
    assert (
        "a generated-output folder that is missing or can't be read holds it "
        "back when it held a generated image or clip the scan did not find"
    ) in flat
    assert (
        "generated images and clips that are on disk but can't be read or "
        "imported hold it back too"
    ) in flat


def test_the_marker_names_the_output_roots_the_last_scan_could_not_reach(tmp_path):
    """A scan that could not reach a generated-output root names it in the
    marker, with the way out; the next scan's findings replace that list,
    and with none left the marker reads as written."""
    ts.write_full_clean_marker(tmp_path)
    written = _marker_text(tmp_path)

    ts.note_generated_output(
        tmp_path, {"/mnt/d/comfy/t2i": "missing", "/mnt/e/clips": "can't be read"}
    )
    block = _noted(tmp_path, written)
    assert "/mnt/d/comfy/t2i (missing)" in block
    assert "/mnt/e/clips (can't be read)" in block
    # The block's own advice: the marker's text has the way out too.
    flat = _flat(block)
    assert "reconnect or mount the drive and run a scan" in flat
    assert "recreate it (empty is fine) and run full clean & rescan" in flat

    ts.note_generated_output(tmp_path, {"/mnt/e/clips": "missing"})
    block = _noted(tmp_path, written)
    assert "/mnt/d/comfy/t2i" not in block and "/mnt/e/clips (missing)" in block

    ts.note_generated_output(tmp_path, {})
    assert _marker_text(tmp_path) == written


def test_the_marker_counts_the_generated_files_the_last_scan_could_not_import(
    tmp_path,
):
    """Files on disk the last scan could not read or import: how many, and
    the output folders that hold them (a file under none is said to be
    outside them), with the way out -- never a file's path, which the note
    is not even given. The next scan's findings replace it."""
    ts.write_full_clean_marker(tmp_path)
    written = _marker_text(tmp_path)

    ts.note_generated_output(tmp_path, {}, 3, ["/mnt/d/comfy/t2i", None])
    block = _noted(tmp_path, written)
    flat = _flat(block)
    assert (
        "could not read or import 3 generated images or clips that are on disk"
    ) in flat
    assert "  in /mnt/d/comfy/t2i\n" in block
    assert "  outside the configured output folders\n" in block
    for advice in (
        "a file still on disk is never recorded as deleted",
        "its ratings are kept, pending",
        "the scan's done step lists the files",
        "delete each one, or move it out of the output folder (or repair it), "
        "then run full clean & rescan",
        "a file that only failed this time (a busy disk) comes back by itself "
        "at the next scan",
    ):
        assert advice in flat

    ts.note_generated_output(tmp_path, {"/mnt/e/clips": "missing"}, 1, ["/mnt/e/out"])
    block = _noted(tmp_path, written)
    assert "1 generated image or clip that is on disk" in _flat(block)
    assert "/mnt/d/comfy/t2i" not in block and "outside the configured" not in block
    # Both said, the folders it could not reach first.
    assert block.index("/mnt/e/clips (missing)") < block.index("  in /mnt/e/out\n")

    ts.note_generated_output(tmp_path, {})
    assert _marker_text(tmp_path) == written


def test_the_marker_says_when_the_check_itself_failed(tmp_path):
    """The re-ingest step raised: nothing about the generated output is
    known. The marker says the check failed, naming the error by its type
    alone, and that the next scan tries again; it replaces what an earlier
    scan said."""
    ts.write_full_clean_marker(tmp_path)
    written = _marker_text(tmp_path)
    ts.note_generated_output(tmp_path, {"/mnt/d/comfy/t2i": "missing"})

    ts.note_generated_output(tmp_path, {}, check_failed="OperationalError")
    block = _noted(tmp_path, written)
    flat = _flat(block)
    assert "the last scan could not check the generated output (operationalerror;" in (
        flat
    )
    assert "none of them is recorded as deleted. the next scan tries again." in flat
    assert "/mnt/d/comfy/t2i" not in block

    ts.note_generated_output(tmp_path, {})
    assert _marker_text(tmp_path) == written


# The block the build before this one appended, as it wrote it.
OLDER_BLOCK = """
The last scan could not reach these generated-output folders:
  /mnt/d/comfy/t2i (missing)
Their images and clips may still be there, so their ratings are kept,
pending, and none of them is recorded as deleted. Reconnect or mount the
drive and run a scan. If a folder is gone for good, recreate it (empty is
fine) and run Full clean & rescan.
"""


def test_a_note_replaces_the_block_an_older_build_wrote(tmp_path):
    """A marker left by the build before, whose block named unreachable
    folders only: a note replaces that block too, and one with nothing to
    say leaves the text as written. Every section starts with the same
    words, and the marker's text never starts a line with them."""
    ts.write_full_clean_marker(tmp_path)
    written = _marker_text(tmp_path)
    assert "\n" + ts._LAST_SCAN_COULD_NOT not in written
    marker = ts.full_clean_marker_path(tmp_path)

    marker.write_text(written + OLDER_BLOCK, encoding="utf-8")
    ts.note_generated_output(tmp_path, {}, 2, ["/mnt/e/out"])
    block = _noted(tmp_path, written)
    assert "/mnt/d/comfy/t2i" not in block and "  in /mnt/e/out\n" in block
    assert block.startswith("\n" + ts._LAST_SCAN_COULD_NOT)

    marker.write_text(written + OLDER_BLOCK, encoding="utf-8")
    ts.note_generated_output(tmp_path, {})
    assert _marker_text(tmp_path) == written


def test_noting_the_generated_output_never_creates_the_marker(tmp_path):
    """Deleted by hand while a scan ran (to let the lists prune): noting what
    that scan could not bring back does not bring it back."""
    assert ts.note_generated_output(tmp_path, {"/mnt/d/comfy/t2i": "missing"}) is False
    assert ts.note_generated_output(tmp_path, {}, 1, [None]) is False
    assert ts.note_generated_output(tmp_path, {}, check_failed="OSError") is False
    assert ts.full_clean_marker_exists(tmp_path) is False


def test_the_marker_says_favorites_and_briefs_are_recorded_too(tmp_path):
    """The way out tombstones every path the snapshot has an entry for: a
    ★ alone or a brief alone is recorded as deleted too."""
    ts.write_full_clean_marker(tmp_path)
    flat = " ".join(_marker_text(tmp_path).split()).lower()
    for kept in ("a grade", "a view", "a favorite", "a category", "hindsight brief"):
        assert kept in flat


def test_a_marker_that_cannot_be_removed_keeps_the_prune_paused(
    tmp_path, monkeypatch, caplog
):
    ts.write_full_clean_marker(tmp_path)
    real_unlink = Path.unlink

    def locked(self: Path, *a: Any, **k: Any) -> None:
        if self.name == ts.FULL_CLEAN_MARKER_FILENAME:
            raise PermissionError("locked by another process")
        real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked)
    with caplog.at_level(logging.ERROR, logger="backend.services.taste_snapshot"):
        assert ts.clear_full_clean_marker(tmp_path) is False
    assert ts.full_clean_marker_exists(tmp_path) is True
    assert ts.lineage_prune_guard(tmp_path)() is True  # fail safe
    [logged] = [r for r in caplog.records if "locked by another" in r.getMessage()]
    assert logged.levelno == logging.ERROR


def test_a_pending_file_that_cannot_be_checked_pauses_the_prune(db, tmp_path):
    """Only a missing file means "nothing pending": any other stat error
    (here a data dir that is a file) is raised, and the database counts a
    raising guard as paused."""
    not_a_dir = tmp_path / "data"
    not_a_dir.write_text("", encoding="utf-8")
    with pytest.raises(OSError):
        ts.lineage_prune_guard(not_a_dir)()
    db.set_lineage_prune_guard(ts.lineage_prune_guard(not_a_dir))
    _delete_first_files(db)
    _list(db)
    assert _rows(db) == (2, 2)


def test_the_lifespan_installs_the_guard():
    from fastapi.testclient import TestClient

    from backend.dependencies import get_db
    from backend.main import create_app

    with TestClient(create_app()):
        db = get_db()
        data_dir = Path(db.db_path)
        _seed(db)
        _delete_first_files(db)
        # A full clean is pending (its file outlives the run).
        ts.write_snapshot_file(data_dir, _empty_snapshot())
        assert _names(db) == (["out2.png"], ["clip2.mp4"])
        assert _rows(db) == (2, 2)
        ts.snapshot_file_path(data_dir).unlink()
        # A full clean never followed by a completed scan.
        ts.write_full_clean_marker(data_dir)
        _list(db)
        assert _rows(db) == (2, 2)
        assert ts.clear_full_clean_marker(data_dir)
        with ts.full_clean_running():  # a full clean is running
            _list(db)
            assert _rows(db) == (2, 2)
        _list(db)
        assert _rows(db) == (1, 1)  # none of the three: the prune is back


def test_a_guard_that_cannot_be_installed_falls_back_to_never_pruning(
    db, monkeypatch, caplog
):
    from backend import main as main_mod

    def broken(data_dir: Path) -> Callable[[], bool]:
        raise OSError("data dir unreadable")

    monkeypatch.setattr(main_mod, "lineage_prune_guard", broken)
    with caplog.at_level(logging.ERROR, logger="backend.main"):
        main_mod._install_lineage_prune_guard(db)
    assert [r for r in caplog.records if "lineage prune guard" in r.getMessage()]
    _delete_first_files(db)
    _list(db)
    assert _rows(db) == (2, 2)  # fail safe: nothing is pruned


def test_installing_the_guard_never_stops_the_startup(caplog):
    from backend.main import _install_lineage_prune_guard

    with caplog.at_level(logging.ERROR, logger="backend.main"):
        _install_lineage_prune_guard(object())  # no db_path, no setter
    assert [r for r in caplog.records if "lineage prune guard" in r.getMessage()]


# ---- a paused listing is one query ----------------------------------------------


def _traced_selects(db: DatabaseManager, monkeypatch: Any) -> List[str]:
    """Every SELECT the database runs from now on (a connection trace)."""
    statements: List[str] = []
    real = db._get_connection

    @contextmanager
    def traced() -> Iterator[sqlite3.Connection]:
        with real() as conn:
            conn.set_trace_callback(statements.append)
            yield conn

    monkeypatch.setattr(db, "_get_connection", traced)
    return statements


def _pages(db: DatabaseManager, size: int) -> List[List[int]]:
    pages: List[List[int]] = []
    before: Optional[int] = None
    while True:
        page = db.list_t2i_images(limit=size, before_id=before)
        pages.append([int(r["id"]) for r in page])
        if len(page) < size:
            return pages
        before = int(page[-1]["id"])


def test_a_paused_listing_skips_the_dangling_rows_in_one_query(tmp_path, monkeypatch):
    """Mid full clean nearly every row dangles. The refill loop asked for
    ``limit - len(out)`` rows per query -- one per round trip near a full
    page, all under the DB lock (1.25 s for 50k dangling rows behind 59
    live ones, in the review). Paused, nothing may be pruned, so one query
    with the limit and the cursor answers each page: the same rows and the
    same pages, and every row is still there."""
    db = DatabaseManager(tmp_path / "db")
    # 2,000 old rows whose files did not come back, then 300 newer rows of
    # which every fifth did: 60 live, with dangling rows between them.
    paths = [f"/lib/t2i/old{i}.png" for i in range(2000)] + [
        f"/lib/t2i/new{i}.png" for i in range(300)
    ]
    with db.lock, db._get_connection() as conn:
        conn.executemany(
            "INSERT INTO t2i_images (file_path, prompt_used) VALUES (?, ?)",
            [(p, "a prompt") for p in paths],
        )
        conn.commit()
    live = [f"/lib/t2i/new{i}.png" for i in range(0, 300, 5)]
    for p in live:
        db.save_media(_media(p))
    with db._get_connection() as conn:
        live_ids = [
            int(r[0])
            for r in conn.execute(
                "SELECT id FROM t2i_images WHERE file_path LIKE '/lib/t2i/new%' "
                "ORDER BY id DESC"
            )
            if (int(r[0]) - 2001) % 5 == 0
        ]
    assert len(live_ids) == 60
    expected = [live_ids[0:25], live_ids[25:50], live_ids[50:60]]

    db.set_lineage_prune_guard(lambda: True)
    selects = _traced_selects(db, monkeypatch)
    paused_pages = _pages(db, 25)
    t2i_reads = [s for s in selects if "FROM t2i_images" in s]
    assert paused_pages == expected
    assert len(t2i_reads) == len(paused_pages)  # one query per page
    near_full = db.list_t2i_images(limit=61)  # 60 live, then 2,240 dangling
    assert [int(r["id"]) for r in near_full] == live_ids
    assert len([s for s in selects if "FROM t2i_images" in s]) == len(t2i_reads) + 1
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t2i_images").fetchone()[0] == 2300

    # Unpaused, the same pages -- and now the dangling rows are pruned.
    db.set_lineage_prune_guard(None)
    assert _pages(db, 25) == expected
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM t2i_images").fetchone()[0] == 60


def test_generated_paths_without_media(db):
    """The T2I images and I2V clips whose media row is missing: what a full
    clean re-ingests from disk after its folder walk."""
    _delete_first_files(db)
    assert sorted(db.generated_paths_without_media()) == sorted(
        [os.path.normpath(T2I[0]), os.path.normpath(CLIPS[0])]
    )
    assert db.truncate_all_data()
    assert sorted(db.generated_paths_without_media()) == sorted(
        os.path.normpath(p) for p in (*T2I, *CLIPS)
    )
