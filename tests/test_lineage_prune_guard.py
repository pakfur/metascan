"""The T2I / I2V listings never prune lineage while a full clean is pending
(CLAUDE.md, "Full clean & rescan keeps ratings").

``list_t2i_images`` / ``list_i2v_videos`` delete the lineage rows whose
media row is gone. A full clean empties ``media`` and the rescan re-adds it
file by file, so every lineage row looks dangling until the rescan reaches
its file: one listing mid-rescan (the T2I dialog opening, a WebSocket
reconnect) used to wipe every prompt, draft, writer id, variant group and
scorecard id, and the completed full clean then tombstoned the vanished
images without their lineage. The scan-route cases are in
``test_taste_snapshot_scan.py``."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from backend.services import taste_snapshot as ts
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
    assert len(logged) == 2  # one per listing
    assert {r.levelno for r in logged} == {logging.DEBUG}


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
        with ts.full_clean_running():  # a full clean is running
            _list(db)
            assert _rows(db) == (2, 2)
        _list(db)
        assert _rows(db) == (1, 1)  # neither: the prune is back


def test_installing_the_guard_never_stops_the_startup(caplog):
    from backend.main import _install_lineage_prune_guard

    with caplog.at_level(logging.ERROR, logger="backend.main"):
        _install_lineage_prune_guard(object())  # no db_path, no setter
    assert [r for r in caplog.records if "lineage prune guard" in r.getMessage()]
