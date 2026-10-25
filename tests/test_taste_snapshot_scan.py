"""Scan-route level: a full clean & rescan keeps ratings, reasons and
categories (final-review item 1), and the T2I / I2V lineage rows. Uses a
real temp DB and a fake scanner that re-saves a chosen set of paths."""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.api import scan as scan_api
from backend.services import taste_snapshot as ts
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


# A seeded rating as (★ favorite, grade): ★ with ✓, what a pre-grades
# favorite became, and ✗ without ★, what an old reject became.
FAVORITE = (True, "pass")
REJECT = (False, "fail")
UNRATED = (False, None)


def _marks(state):
    return (state.favorite, state.grade)


def _favorite(db, path, now):
    """★ + ✓: a favorite that was also graded pass."""
    db.set_favorite_state(path, True, now=now)
    db.set_grade(path, "pass", now=now)


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


class _DirCfg:
    filepath = "/lib"
    search_subfolders = True


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _seed(db):
    for p in ("/lib/fav.png", "/lib/reject.png", "/lib/gone.png"):
        db.save_media(_media(p))
    cat = db.create_category("Landscape")
    _favorite(db, "/lib/fav.png", T0)
    db.set_reason("/lib/fav.png", "light", True, now=T0)
    db.set_grade("/lib/reject.png", "fail", now=T0)
    db.set_reason("/lib/reject.png", "concept", True, now=T0)
    db.add_media_categories(["/lib/reject.png"], cat.id, "viewer")
    db.set_grade("/lib/gone.png", "fail", now=T0)


def _wire(monkeypatch, db, rescanned, scanner_cls=None):
    class FakeScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            for p in rescanned:
                db.save_media(_media(p))
            return len(rescanned)

    events = []

    async def fake_broadcast(channel, event, data=None):
        events.append((channel, event, data))

    monkeypatch.setattr(scan_api, "get_db", lambda: db)
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: object())
    monkeypatch.setattr(scan_api, "Scanner", scanner_cls or FakeScanner)
    monkeypatch.setattr(scan_api, "load_app_config", lambda: {})
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [_DirCfg()])
    monkeypatch.setattr(scan_api.Path, "exists", lambda self: True)
    # "/lib" is not a real folder: report it there and listable (the
    # missing-folder rules have their own tests, test_scan_missing_folders).
    monkeypatch.setattr(scan_api, "_probe_folder", lambda path: scan_api.PRESENT)
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", fake_broadcast)
    return events


def test_full_clean_restores_taste_state_and_tombstones_the_rest(monkeypatch, db):
    _seed(db)
    events_before = db.list_judgment_events()
    _wire(monkeypatch, db, ["/lib/fav.png", "/lib/reject.png", "/lib/new.png"])

    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    fav = db.get_judgment("/lib/fav.png")
    assert (_marks(fav), fav.reasons, fav.confirmed_at) == (
        FAVORITE,
        ["light"],
        T0.isoformat(),
    )
    rejected = db.get_judgment("/lib/reject.png")
    assert (_marks(rejected), rejected.reasons) == (REJECT, ["concept"])
    assert [c.name for c in rejected.categories] == ["Landscape"]
    assert _marks(db.get_judgment("/lib/new.png")) == UNRATED
    # No new events from the restore: it is a put-back, not a re-grade.
    assert db.list_judgment_events() == events_before
    [t] = db.list_tombstones()
    assert (t["file_path"], t["grade"]) == ("/lib/gone.png", "fail")


def _tombstone_marks(db):
    return {
        t["file_path"]: (t["is_favorite"], t["grade"]) for t in db.list_tombstones()
    }


def test_full_clean_tombstones_each_missing_path_with_its_grade(monkeypatch, db):
    """A completed full clean tombstones every path that did not come back
    with its grade as it was: ✓ without ★ keeps ✓, ✗ keeps ✗, and a ★ alone
    stays a ★ with no grade (★ is not a grade)."""
    for p in ("/lib/graded.png", "/lib/failed.png", "/lib/starred.png"):
        db.save_media(_media(p))
    db.set_grade("/lib/graded.png", "pass", now=T0)
    db.set_grade("/lib/failed.png", "fail", now=T0)
    db.set_reason("/lib/failed.png", "light", True, now=T0)
    db.set_favorite_state("/lib/starred.png", True, now=T0)
    _wire(monkeypatch, db, [])

    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    assert _tombstone_marks(db) == {
        "/lib/graded.png": (0, "pass"),
        "/lib/failed.png": (0, "fail"),
        "/lib/starred.png": (1, None),
    }
    [failed] = [t for t in db.list_tombstones() if t["file_path"] == "/lib/failed.png"]
    assert json.loads(failed["reasons"]) == ["light"]
    assert _pending(db) is None


def test_full_clean_tombstones_pre_grades_leftovers_with_their_old_meaning(
    monkeypatch, db
):
    """Leftovers of a full clean interrupted before grades ride along into
    the next one and, when it completes, are tombstoned with their old
    meaning: a reject is ✗, a favorite (with or without a row) ★ + ✓."""
    target = Path(db.db_path) / scan_api.TASTE_SNAPSHOT_FILENAME

    def legacy_row(path, passed):
        return {
            "file_path": path,
            "passed": passed,
            "reasons": "[]",
            "seen_at": "2026-03-01T10:00:00+00:00",
            "judged_at": "2026-03-02T11:00:00+00:00",
            "confirmed_at": "2026-03-02T11:00:00+00:00",
        }

    target.write_text(
        json.dumps(
            {
                "taken_at": "2026-03-10T08:00:00+00:00",
                "judgments": [
                    legacy_row("/old/fav.png", 0),
                    legacy_row("/old/reject.png", 1),
                ],
                "categories": [],
                "favorites": ["/old/bare.png", "/old/fav.png"],
            }
        ),
        encoding="utf-8",
    )
    _wire(monkeypatch, db, [])

    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    assert _tombstone_marks(db) == {
        "/old/bare.png": (1, "pass"),
        "/old/fav.png": (1, "pass"),
        "/old/reject.png": (0, "fail"),
    }
    assert _pending(db) is None


def _pending(db):
    # Path.exists is monkeypatched to True by _wire, so read and catch.
    f = Path(db.db_path) / scan_api.TASTE_SNAPSHOT_FILENAME
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _pending_paths(db):
    snap = _pending(db)
    if snap is None:
        return None
    return sorted(
        {j["file_path"] for j in snap["judgments"]}
        | {lk["file_path"] for lk in snap["categories"]}
        | set(snap["favorites"])
    )


def test_full_clean_completed_deletes_the_pending_file(monkeypatch, db):
    _seed(db)
    _wire(monkeypatch, db, ["/lib/fav.png", "/lib/reject.png"])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert _pending(db) is None
    assert [t["file_path"] for t in db.list_tombstones()] == ["/lib/gone.png"]


def test_full_clean_cancel_keeps_missing_paths_pending(monkeypatch, db):
    """Intentional change: a cancelled full clean no longer tombstones the
    paths that have not been re-imported yet; they stay pending."""
    _seed(db)
    _wire(monkeypatch, db, [])
    scan_api._cancel_requested = True
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False
    assert db.list_tombstones() == []
    assert _pending_paths(db) == ["/lib/fav.png", "/lib/gone.png", "/lib/reject.png"]


def test_full_clean_cancel_mid_scan_restores_what_came_back(monkeypatch, db):
    _seed(db)
    events_before = db.list_judgment_events()

    class CancellingScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            db.save_media(_media("/lib/fav.png"))
            scan_api._cancel_requested = True  # user hits Cancel now
            return 1

    events = _wire(monkeypatch, db, [], scanner_cls=CancellingScanner)
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False

    fav = db.get_judgment("/lib/fav.png")
    assert (_marks(fav), fav.reasons, fav.confirmed_at) == (
        FAVORITE,
        ["light"],
        T0.isoformat(),
    )
    assert db.list_judgment_events() == events_before
    assert db.list_tombstones() == []
    assert _pending_paths(db) == ["/lib/gone.png", "/lib/reject.png"]
    assert len([e for e in events if e[1] == "taste_restored"]) == 1

    # A later ordinary scan brings the rest back: restored, file deleted.
    _wire(monkeypatch, db, ["/lib/reject.png", "/lib/gone.png"])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    rejected = db.get_judgment("/lib/reject.png")
    assert (_marks(rejected), rejected.reasons) == (REJECT, ["concept"])
    assert [c.name for c in rejected.categories] == ["Landscape"]
    assert _marks(db.get_judgment("/lib/gone.png")) == REJECT
    assert db.list_judgment_events() == events_before
    assert db.list_tombstones() == []
    assert _pending(db) is None


def test_full_clean_scanner_error_restores_what_came_back(monkeypatch, db):
    """Intentional change: the paths that did not come back are kept
    pending instead of being tombstoned."""
    _seed(db)

    class HalfScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            db.save_media(_media("/lib/fav.png"))
            raise RuntimeError("boom")

    events = _wire(monkeypatch, db, [], scanner_cls=HalfScanner)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    assert _marks(db.get_judgment("/lib/fav.png")) == FAVORITE
    assert db.list_tombstones() == []
    assert _pending_paths(db) == ["/lib/gone.png", "/lib/reject.png"]
    assert ("scan", "error", {"message": "boom"}) in events
    restored = [e for e in events if e[1] == "taste_restored"]
    assert len(restored) == 1

    _wire(monkeypatch, db, ["/lib/reject.png", "/lib/gone.png"])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert _marks(db.get_judgment("/lib/reject.png")) == REJECT
    assert _pending(db) is None


def test_full_clean_shutdown_leaves_pending_file_untouched(monkeypatch, db):
    _seed(db)

    class ShutdownScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            db.save_media(_media("/lib/fav.png"))
            raise asyncio.CancelledError()

    _wire(monkeypatch, db, [], scanner_cls=ShutdownScanner)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert _pending(db) is not None
    assert _pending_paths(db) == ["/lib/fav.png", "/lib/gone.png", "/lib/reject.png"]
    assert _marks(db.get_judgment("/lib/fav.png")) == UNRATED  # not restored yet
    assert db.list_tombstones() == []

    # Next startup: the pending file is applied.
    from backend.services.taste_snapshot import apply_pending_taste_snapshot

    db.save_media(_media("/lib/reject.png"))
    counts = apply_pending_taste_snapshot(db, Path(db.db_path))
    assert counts["restored"] == 2
    assert _marks(db.get_judgment("/lib/fav.png")) == FAVORITE
    assert db.get_judgment("/lib/reject.png").reasons == ["concept"]
    assert _pending_paths(db) == ["/lib/gone.png"]
    assert db.list_tombstones() == []


def test_new_full_clean_merges_a_pending_file(monkeypatch, db):
    """Leftovers of an interrupted full clean ride along into the next
    one; when that one completes they are tombstoned like any other."""
    _seed(db)
    _wire(monkeypatch, db, ["/lib/fav.png"])
    scan_api._cancel_requested = True
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False
    assert _pending_paths(db) == ["/lib/fav.png", "/lib/gone.png", "/lib/reject.png"]

    seen = {}
    real_truncate = db.truncate_all_data

    def spy_truncate():
        seen["paths"] = _pending_paths(db)
        return real_truncate()

    monkeypatch.setattr(db, "truncate_all_data", spy_truncate)
    # fav.png re-appears now; the other two never do.
    _wire(monkeypatch, db, ["/lib/fav.png"])
    # Simulate fav.png being present before the second full clean (it was
    # re-imported by some earlier scan) so its pending entry is applied.
    db.save_media(_media("/lib/fav.png"))
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    assert seen["paths"] == ["/lib/fav.png", "/lib/gone.png", "/lib/reject.png"]
    assert _marks(db.get_judgment("/lib/fav.png")) == FAVORITE
    assert sorted(t["file_path"] for t in db.list_tombstones()) == [
        "/lib/gone.png",
        "/lib/reject.png",
    ]
    assert _pending(db) is None


def test_full_clean_writes_snapshot_file_before_truncating(monkeypatch, db):
    _seed(db)
    seen = {}
    real_truncate = db.truncate_all_data

    def spy_truncate():
        f = Path(db.db_path) / scan_api.TASTE_SNAPSHOT_FILENAME
        seen["file"] = json.loads(f.read_text(encoding="utf-8"))
        return real_truncate()

    monkeypatch.setattr(db, "truncate_all_data", spy_truncate)
    _wire(monkeypatch, db, ["/lib/fav.png"])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert seen["file"]["favorites"] == ["/lib/fav.png"]
    assert len(seen["file"]["judgments"]) == 3


def test_full_clean_aborts_without_deleting_when_snapshot_fails(monkeypatch, db):
    _seed(db)

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "export_taste_state", boom)
    events = _wire(monkeypatch, db, [])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert _marks(db.get_judgment("/lib/fav.png")) == FAVORITE
    assert any(e[1] == "error" for e in events)


def test_full_clean_aborts_when_pending_file_is_unreadable(monkeypatch, db):
    """A pending file that exists but can't be read may hold valid ratings:
    the full clean must not overwrite it or truncate the DB."""
    _seed(db)
    events = _wire(monkeypatch, db, [])
    target = Path(db.db_path) / scan_api.TASTE_SNAPSHOT_FILENAME
    old = {
        "taken_at": "t",
        "judgments": [
            {
                "file_path": "/lib/old.png",
                "passed": 1,
                "reasons": "[]",
                "seen_at": None,
                "judged_at": "t",
                "confirmed_at": "t",
            }
        ],
        "categories": [],
        "favorites": [],
    }
    target.write_text(json.dumps(old), encoding="utf-8")
    real_read = Path.read_bytes

    def locked(self, *a, **k):
        if self == target:
            raise PermissionError("locked by another process")
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_bytes", locked)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    monkeypatch.setattr(Path, "read_bytes", real_read)

    assert json.loads(target.read_text(encoding="utf-8")) == old
    assert _marks(db.get_judgment("/lib/fav.png")) == FAVORITE  # not truncated
    assert len(db.get_existing_file_paths()) == 3
    errors = [e for e in events if e[1] == "error"]
    assert len(errors) == 1
    msg = errors[0][2]["message"].lower()
    assert "pending ratings file" in msg and "could not be read" in msg
    assert not any(e[1] == "phase_changed" for e in events)


# ----------------------------------------------------------------------
# T2I / I2V lineage outlives the rescan: the listings' lazy prune pauses
# while a full clean runs or has entries pending
# ----------------------------------------------------------------------

T2I_LINEAGE = {
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
}
I2V_LINEAGE = {
    "prompt_used": "The kite climbs into the wind.",
    "writer_id": "base",
    "writer_model": "qwen3-vl-8b",
    "variant_group": "vg-2",
}
SRC = "/lib/src.png"


def _lineage(row, facts):
    return {k: row[k] for k in facts}


def _lineage_rows(db):
    """(t2i_images rows, i2v_videos rows) actually stored."""
    with db._get_connection() as conn:
        return tuple(
            int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("t2i_images", "i2v_videos")
        )


def _wire_prune_guard(db):
    """What the lifespan installs (backend/main.py)."""
    db.set_lineage_prune_guard(ts.lineage_prune_guard(Path(db.db_path)))


def _out(db, name):
    """A generated file's path in a real ComfyUI output folder next to the
    test database (the file itself is not created). The re-ingest tells a
    file gone from a folder that is there (vanished) from one whose folder
    can't be reached (unmounted, keeps the full clean unfinished), so a
    fictitious /lib/t2i/ path would never finish a full clean."""
    path = Path(db.db_path).parent / "comfy_output" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _seed_generations(db, images, clips):
    """Writer-made T2I images, and I2V clips made from SRC."""
    db.save_media(_media(SRC))
    for n, path in enumerate(images):
        db.save_media(_media(path))
        db.create_t2i_image(file_path=path, variant_index=n, **T2I_LINEAGE)
    for n, path in enumerate(clips):
        db.save_media(_media(path))
        db.create_i2v_video(
            source_path=SRC, file_path=path, variant_index=n, **I2V_LINEAGE
        )


def test_full_clean_keeps_the_lineage_of_t2i_images_and_i2v_clips(monkeypatch, db):
    """The T2I and I2V dialogs list while the rescan re-adds the files one by
    one. An image or clip that comes back keeps its lineage row; a graded one
    that vanished is tombstoned WITH its lineage, read from the row the
    listings left alone."""
    back, gone = _out(db, "t2i/back.png"), _out(db, "t2i/gone.png")
    clip_back, clip_gone = _out(db, "i2v/back.mp4"), _out(db, "i2v/gone.mp4")
    _seed_generations(db, [back, gone], [clip_back, clip_gone])
    db.set_grade(back, "fail", now=T0)
    db.set_grade(gone, "pass", now=T0)
    db.set_grade(clip_gone, "fail", now=T0)
    _wire_prune_guard(db)
    seen = {}

    class DialogsOpenScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            seen["empty"] = (db.list_t2i_images(), db.list_i2v_videos(SRC))
            for path in (back, SRC, clip_back):
                db.save_media(_media(path))
            seen["partly"] = (db.list_t2i_images(), db.list_i2v_videos(SRC))
            return 3

    _wire(monkeypatch, db, [], scanner_cls=DialogsOpenScanner)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    assert seen["empty"] == ([], [])
    images, clips = seen["partly"]
    assert [r["file_name"] for r in images] == ["back.png"]
    assert [Path(r["file_path"]).name for r in clips] == ["back.mp4"]

    # What came back keeps its row, lineage and all (and its grade).
    [image] = db.list_t2i_images()
    assert (image["file_name"], image["grade"]) == ("back.png", "fail")
    assert _lineage(image, T2I_LINEAGE) == T2I_LINEAGE
    assert image["variant_index"] == 0
    [clip] = db.list_i2v_videos(SRC)
    assert Path(clip["file_path"]).name == "back.mp4"
    assert _lineage(clip, I2V_LINEAGE) == I2V_LINEAGE

    # What vanished is tombstoned with its grade AND its lineage.
    tombs = {t["file_path"]: t for t in db.list_tombstones()}
    assert sorted(tombs) == [clip_gone, gone]
    assert (tombs[gone]["grade"], tombs[gone]["scorecard_id"]) == ("pass", "card-1")
    assert _lineage(tombs[gone]["lineage"]["t2i"], T2I_LINEAGE) == T2I_LINEAGE
    assert tombs[gone]["lineage"]["t2i"]["variant_index"] == 1
    assert tombs[clip_gone]["grade"] == "fail"
    assert _lineage(tombs[clip_gone]["lineage"]["i2v"], I2V_LINEAGE) == I2V_LINEAGE

    # The full clean is over and nothing is pending: the listings above
    # pruned the rows of what vanished.
    assert _pending(db) is None and not ts.is_full_clean_running()
    assert _lineage_rows(db) == (1, 1)


def test_full_clean_with_nothing_to_snapshot_pauses_the_prune_too(monkeypatch, db):
    """An unrated library still gets a pending file (empty lists) before the
    truncate, and the run is marked as running from before its snapshot to
    after its restore."""
    back, gone = _out(db, "t2i/back.png"), _out(db, "t2i/gone.png")
    _seed_generations(db, [back, gone], [])
    _wire_prune_guard(db)
    seen = {}
    real_export = db.export_taste_state

    def spy_export():
        seen["running at the snapshot"] = ts.is_full_clean_running()
        return real_export()

    monkeypatch.setattr(db, "export_taste_state", spy_export)

    class ListingScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            seen["file"] = _pending(db)
            seen["running"] = ts.is_full_clean_running()
            seen["listed"] = db.list_t2i_images()
            db.save_media(_media(back))
            return 1

    _wire(monkeypatch, db, [], scanner_cls=ListingScanner)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    assert seen["running at the snapshot"] is True
    assert seen["running"] is True
    snap = seen["file"]
    assert (snap["judgments"], snap["categories"], snap["favorites"]) == ([], [], [])
    assert seen["listed"] == []
    # Nothing was rated: no tombstone, the file is gone, the prune is back.
    assert db.list_tombstones() == []
    assert _pending(db) is None and not ts.is_full_clean_running()
    [image] = db.list_t2i_images()
    assert image["file_name"] == "back.png"
    assert _lineage(image, T2I_LINEAGE) == T2I_LINEAGE
    assert _lineage_rows(db) == (1, 0)


def test_a_cancelled_full_clean_keeps_the_lineage_until_one_completes(monkeypatch, db):
    """A cancel leaves a graded image pending: its lineage row outlives every
    listing until a later full clean tombstones it with that lineage."""
    gone = _out(db, "t2i/gone.png")
    _seed_generations(db, [gone], [])
    db.set_grade(gone, "pass", now=T0)
    _wire_prune_guard(db)
    _wire(monkeypatch, db, [])
    scan_api._cancel_requested = True
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False
    assert not ts.is_full_clean_running()
    assert _pending_paths(db) == [gone]
    assert db.list_t2i_images() == []  # the dialog opens: not listed ...
    assert _lineage_rows(db) == (1, 0)  # ... and kept while it is pending

    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    [t] = db.list_tombstones()
    assert (t["file_path"], t["grade"]) == (gone, "pass")
    assert _lineage(t["lineage"]["t2i"], T2I_LINEAGE) == T2I_LINEAGE
    assert _pending(db) is None
    assert db.list_t2i_images() == []
    assert _lineage_rows(db) == (0, 0)  # nothing pending: pruned


def test_a_shutdown_mid_full_clean_keeps_the_prune_paused(monkeypatch, db):
    gone = _out(db, "t2i/gone.png")
    _seed_generations(db, [gone], [])
    db.set_grade(gone, "pass", now=T0)
    _wire_prune_guard(db)

    class ShutdownScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            raise asyncio.CancelledError()

    _wire(monkeypatch, db, [], scanner_cls=ShutdownScanner)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert not ts.is_full_clean_running()  # cleared on the way out ...
    assert _pending_paths(db) == [gone]  # ... but the file says pending
    assert db.list_t2i_images() == []
    assert _lineage_rows(db) == (1, 0)


@pytest.mark.parametrize("ending", ["snapshot fails", "scanner fails", "cancelled"])
def test_the_running_mark_is_cleared_however_a_full_clean_ends(monkeypatch, db, ending):
    _seed(db)
    seen = {}

    class Scanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            seen["running"] = ts.is_full_clean_running()
            if ending == "scanner fails":
                raise RuntimeError("boom")
            scan_api._cancel_requested = True
            return 0

    if ending == "snapshot fails":

        def boom():
            raise RuntimeError("disk full")

        monkeypatch.setattr(db, "export_taste_state", boom)
    _wire(monkeypatch, db, [], scanner_cls=Scanner)
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False
    if ending == "snapshot fails":
        assert "running" not in seen  # aborted before anything was deleted
    else:
        assert seen["running"] is True
    assert not ts.is_full_clean_running()


def test_an_ordinary_scan_never_pauses_the_prune(monkeypatch, db):
    seen = {}

    class Scanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            seen["running"] = ts.is_full_clean_running()
            return 0

    _wire(monkeypatch, db, [], scanner_cls=Scanner)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert seen["running"] is False


# ----------------------------------------------------------------------
# The lasting marker: a full clean stays unfinished, and the prune paused,
# until a scan of every configured folder completes
# ----------------------------------------------------------------------


def _marker_exists(db):
    # Path.exists is monkeypatched to True by _wire.
    return os.path.exists(ts.full_clean_marker_path(Path(db.db_path)))


def test_full_clean_writes_the_marker_before_truncating(monkeypatch, db):
    _seed(db)
    seen = {}
    real_truncate = db.truncate_all_data

    def spy_truncate():
        seen["marker"] = _marker_exists(db)
        seen["pending file"] = _pending(db) is not None
        return real_truncate()

    monkeypatch.setattr(db, "truncate_all_data", spy_truncate)
    _wire(monkeypatch, db, ["/lib/fav.png", "/lib/reject.png", "/lib/gone.png"])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert seen == {"marker": True, "pending file": True}
    assert not _marker_exists(db)  # the run completed


def test_full_clean_aborts_when_the_marker_cannot_be_written(monkeypatch, db):
    _seed(db)

    def disk_full(data_dir):
        raise OSError("disk full")

    monkeypatch.setattr(scan_api, "write_full_clean_marker", disk_full)
    events = _wire(monkeypatch, db, [])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert len(db.get_existing_file_paths()) == 3  # nothing was deleted
    assert _marks(db.get_judgment("/lib/fav.png")) == FAVORITE
    errors = [e for e in events if e[1] == "error"]
    assert len(errors) == 1 and "disk full" in errors[0][2]["message"]
    assert not any(e[1] == "phase_changed" for e in events)
    assert not ts.is_full_clean_running()


def test_a_full_clean_that_aborts_after_its_marker_deletes_nothing(monkeypatch, db):
    """The marker is written first; a snapshot that fails after it stops the
    full clean before the truncate. The marker stays (fail safe) until a scan
    of every folder completes."""
    _seed(db)
    real_export = db.export_taste_state

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "export_taste_state", boom)
    events = _wire(monkeypatch, db, [])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert len(db.get_existing_file_paths()) == 3  # nothing was deleted
    assert any(e[1] == "error" for e in events)
    assert _marker_exists(db)

    monkeypatch.setattr(db, "export_taste_state", real_export)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert not _marker_exists(db)


def test_a_full_clean_that_cannot_write_its_marker_leaves_nothing_pending(
    monkeypatch, db
):
    """From the review's marker_abort_demo: with the marker written after the
    snapshot, a marker failure left a full snapshot of live paths pending,
    and the next apply put back a ★ and a category the user had removed
    since. The marker now comes first, so nothing is written at all."""
    db.save_media(_media("/lib/a.png"))
    db.set_favorite_state("/lib/a.png", True, now=T0)
    cat = db.create_category("Landscape")
    db.add_media_categories(["/lib/a.png"], cat.id, "viewer")

    def disk_full(data_dir):
        raise OSError("disk full")

    monkeypatch.setattr(scan_api, "write_full_clean_marker", disk_full)
    _wire(monkeypatch, db, [])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert _pending(db) is None
    assert len(db.get_existing_file_paths()) == 1

    # The user un-stars the image and removes its category afterwards ...
    db.set_favorite_state("/lib/a.png", False, now=T0)
    db.remove_media_categories(["/lib/a.png"], cat.id)
    # ... and the next start (or the end of the next scan) applies.
    ts.apply_pending_taste_snapshot(db, Path(db.db_path))
    state = db.get_judgment("/lib/a.png")
    assert state.favorite is False
    assert state.categories == []


@pytest.mark.parametrize("later", ["ordinary scan", "full clean"])
def test_a_cancelled_full_clean_keeps_unrated_lineage_until_a_scan_completes(
    monkeypatch, db, later
):
    """The gap the pending file left: every rated path came back before the
    cancel, so nothing is pending, but the unrated T2I images and I2V clips
    were not rescanned yet. Their rows outlive every listing until a scan
    of every folder completes; then the ones still gone are pruned."""
    rated = _out(db, "t2i/rated.png")
    back, gone = _out(db, "t2i/back.png"), _out(db, "t2i/gone.png")
    clip_back, clip_gone = _out(db, "i2v/back.mp4"), _out(db, "i2v/gone.mp4")
    _seed_generations(db, [rated, back, gone], [clip_back, clip_gone])
    db.set_grade(rated, "pass", now=T0)
    _wire_prune_guard(db)

    class CancelAfterTheRatedImage:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            db.save_media(_media(rated))  # the first folder brings it back
            scan_api._cancel_requested = True  # then the user cancels
            return 1

    _wire(monkeypatch, db, [], scanner_cls=CancelAfterTheRatedImage)
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [_DirCfg(), _DirCfg()])
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False

    assert _pending(db) is None  # nothing pending: the rated image is back
    assert db.get_judgment(rated).grade == "pass"
    assert not ts.is_full_clean_running()
    assert [r["file_name"] for r in db.list_t2i_images()] == ["rated.png"]
    assert db.list_i2v_videos(SRC) == []
    assert _lineage_rows(db) == (3, 2)  # the unrated rows outlive the listing
    assert _marker_exists(db)

    # A later scan of every folder completes: what is on disk comes back
    # with its lineage, the marker goes, and the rest is pruned.
    _wire(monkeypatch, db, [rated, SRC, back, clip_back])
    asyncio.run(
        scan_api._run_scan(full_cleanup=False, full_clean=later == "full clean")
    )
    assert not _marker_exists(db)
    images = db.list_t2i_images()
    assert [r["file_name"] for r in images] == ["back.png", "rated.png"]
    assert [_lineage(r, T2I_LINEAGE) for r in images] == [T2I_LINEAGE] * 2
    [clip] = db.list_i2v_videos(SRC)
    assert Path(clip["file_path"]).name == "back.mp4"
    assert _lineage(clip, I2V_LINEAGE) == I2V_LINEAGE
    assert _lineage_rows(db) == (2, 1)


def test_a_shutdown_mid_full_clean_keeps_the_marker_across_a_restart(monkeypatch, db):
    """An unrated library: its pending file is empty, and the next start
    consumes it. The marker still keeps the rows of what was not rescanned."""
    from backend.main import _install_lineage_prune_guard

    back = _out(db, "t2i/back.png")
    clip = _out(db, "i2v/back.mp4")
    _seed_generations(db, [back], [clip])
    _wire_prune_guard(db)

    class ShutdownScanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            raise asyncio.CancelledError()

    _wire(monkeypatch, db, [], scanner_cls=ShutdownScanner)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    # The next start: the same database in a new process, wired by the
    # lifespan, which also applies (here: consumes) the pending file.
    restarted = DatabaseManager(Path(db.db_path))
    _install_lineage_prune_guard(restarted)
    ts.apply_pending_taste_snapshot(restarted, Path(db.db_path))
    assert _pending(restarted) is None
    assert restarted.list_t2i_images() == []
    assert restarted.list_i2v_videos(SRC) == []
    assert _lineage_rows(restarted) == (1, 1)  # nothing pruned
    assert _marker_exists(restarted)


@pytest.mark.parametrize(
    "ending", ["scanner fails", "cancelled", "cancelled in the last folder"]
)
def test_a_failed_or_cancelled_full_clean_keeps_the_marker(monkeypatch, db, ending):
    _seed_generations(db, [_out(db, "t2i/back.png")], [])
    _wire_prune_guard(db)

    class Scanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            if ending == "scanner fails":
                raise RuntimeError("boom")
            scan_api._cancel_requested = True
            return 0

    _wire(monkeypatch, db, [], scanner_cls=Scanner)
    scan_api._cancel_requested = ending == "cancelled"  # before the first folder
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False
    assert _marker_exists(db)
    assert db.list_t2i_images() == []
    assert _lineage_rows(db) == (1, 0)


def test_a_scan_that_misses_a_configured_folder_keeps_the_marker(monkeypatch, db):
    """A folder it could not see (an unmounted drive) was not walked: its
    files may come back, so the full clean is still unfinished."""
    _seed_generations(db, [_out(db, "t2i/back.png")], [])
    ts.write_full_clean_marker(Path(db.db_path))

    class Unmounted:
        filepath = "/unmounted"
        search_subfolders = True

    _wire(monkeypatch, db, [])
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [_DirCfg(), Unmounted()])
    monkeypatch.setattr(scan_api.Path, "exists", lambda self: str(self) != "/unmounted")
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert _marker_exists(db)

    monkeypatch.setattr(scan_api, "get_directories", lambda c: [_DirCfg()])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert not _marker_exists(db)


def test_a_marker_that_cannot_be_removed_keeps_the_prune_paused(
    monkeypatch, db, caplog
):
    back, gone = _out(db, "t2i/back.png"), _out(db, "t2i/gone.png")
    _seed_generations(db, [back, gone], [])
    _wire_prune_guard(db)
    ts.write_full_clean_marker(Path(db.db_path))  # an unfinished full clean
    assert db.delete_media(Path(gone))  # this one is gone for good
    real_unlink = Path.unlink

    def locked(self, *a, **k):
        if self.name == ts.FULL_CLEAN_MARKER_FILENAME:
            raise PermissionError("locked by another process")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", locked)
    _wire(monkeypatch, db, [back])
    with caplog.at_level(logging.ERROR, logger="backend.services.taste_snapshot"):
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert [r for r in caplog.records if "locked by another" in r.getMessage()]
    assert _marker_exists(db)
    assert [r["file_name"] for r in db.list_t2i_images()] == ["back.png"]
    assert _lineage_rows(db) == (2, 0)  # still paused

    # Once it can go, the next completed scan removes it.
    monkeypatch.setattr(Path, "unlink", real_unlink)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert not _marker_exists(db)
    assert [r["file_name"] for r in db.list_t2i_images()] == ["back.png"]
    assert _lineage_rows(db) == (1, 0)


def test_a_scan_with_no_configured_folder_keeps_the_marker(monkeypatch, db):
    """Nothing was walked, so nothing came back: the full clean is still
    unfinished."""
    ts.write_full_clean_marker(Path(db.db_path))
    _wire(monkeypatch, db, [])
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [])
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=False))
    assert _marker_exists(db)


# ----------------------------------------------------------------------
# Generated output lives outside the scan folders: a full clean re-ingests it
# ----------------------------------------------------------------------


def _png(path):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (200, 30, 30)).save(path)
    return path


def test_full_clean_keeps_generated_output_outside_the_scan_folders(
    monkeypatch, tmp_path, db, caplog
):
    """T2I / I2V output enters the library through ComfyClient's ingest, under
    <comfy output root>/t2i and /i2v/<stem>/ -- usually outside every scan
    folder, so the folder walk never brings it back. After the walk a full
    clean re-ingests (Scanner.ingest_file) the generated files still on disk:
    they stay in the library with their lineage and grades. One gone from
    disk is still tombstoned with its lineage. Real files, real Scanner."""
    from metascan.core.scanner import Scanner

    library = tmp_path / "library"
    photo = _png(library / "photo.png")
    out = tmp_path / "comfy_output" / "t2i"
    kept, unrated, gone = (
        _png(out / "kept.png"),
        _png(out / "unrated.png"),
        _png(out / "gone.png"),
    )
    scanner = Scanner(db)
    scanner.scan_directory(str(library))
    for n, path in enumerate((kept, unrated, gone)):  # what ComfyClient does
        assert scanner.ingest_file(path) is not None
        db.create_t2i_image(file_path=str(path), variant_index=n, **T2I_LINEAGE)
    db.set_grade(str(kept), "pass", now=T0)
    db.set_grade(str(gone), "fail", now=T0)
    db.set_grade(str(photo), "pass", now=T0)
    gone.unlink()  # deleted on disk behind the library's back
    _wire_prune_guard(db)

    class Library:
        filepath = str(library)
        search_subfolders = True

    async def quiet(*a, **k):
        pass

    monkeypatch.setattr(scan_api, "get_db", lambda: db)
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: None)
    monkeypatch.setattr(scan_api, "load_app_config", lambda: {})
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [Library()])
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", quiet)
    with caplog.at_level(logging.INFO, logger="backend.api.scan"):
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))

    in_library = db.get_existing_file_paths()
    assert {str(photo), str(kept), str(unrated)} <= in_library
    assert str(gone) not in in_library
    images = db.list_t2i_images()
    assert [r["file_name"] for r in images] == ["unrated.png", "kept.png"]
    assert [_lineage(r, T2I_LINEAGE) for r in images] == [T2I_LINEAGE] * 2
    assert [r["grade"] for r in images] == [None, "pass"]  # ratings put back
    assert db.get_judgment(str(photo)).grade == "pass"
    [t] = db.list_tombstones()
    assert (t["file_path"], t["grade"]) == (str(gone), "fail")
    assert _lineage(t["lineage"]["t2i"], T2I_LINEAGE) == T2I_LINEAGE
    assert _pending(db) is None and not _marker_exists(db)
    assert _lineage_rows(db) == (2, 0)  # the vanished row is pruned now
    # Counts only in the log, never a path.
    logged = [r.getMessage() for r in caplog.records if "re-ingested" in r.getMessage()]
    assert logged and all("kept.png" not in m and "unrated" not in m for m in logged)


def test_full_clean_reingests_i2v_clips_outside_the_scan_folders(
    monkeypatch, tmp_path, db
):
    clip = tmp_path / "comfy_output" / "i2v" / "src" / "clip.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"a clip")
    db.save_media(_media(SRC))
    db.save_media(_media(str(clip)))
    db.create_i2v_video(source_path=SRC, file_path=str(clip), **I2V_LINEAGE)
    db.set_grade(str(clip), "fail", now=T0)
    _wire_prune_guard(db)
    ingested = []

    class Scanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            db.save_media(_media(SRC))  # the source image is in the library
            return 1

        def ingest_file(self, path):
            ingested.append(Path(path).name)
            db.save_media(_media(str(path)))
            return object()

    _wire(monkeypatch, db, [], scanner_cls=Scanner)
    asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    assert ingested == ["clip.mp4"]
    [row] = db.list_i2v_videos(SRC)
    assert _lineage(row, I2V_LINEAGE) == I2V_LINEAGE
    assert row["grade"] == "fail"
    assert db.list_tombstones() == []


# ----------------------------------------------------------------------
# The re-ingest must succeed for the full clean to finish (re-review r1)
# ----------------------------------------------------------------------


def _generated_library(monkeypatch, tmp_path, db, scanner_cls=None):
    """A library folder with one photo, and three T2I images in a ComfyUI
    output folder outside it -- ingested and recorded the way ComfyClient
    does, img0 graded ✓ and img1 ✗. The scan route is wired to the real
    folder (no Path.exists patch) and, unless given another, the real
    Scanner."""
    from metascan.core.scanner import Scanner

    library = tmp_path / "library"
    _png(library / "photo.png")
    out = tmp_path / "comfy_output" / "t2i"
    files = [_png(out / f"img{n}.png") for n in range(3)]
    scanner = Scanner(db)
    scanner.scan_directory(str(library))
    for n, path in enumerate(files):
        assert scanner.ingest_file(path) is not None
        db.create_t2i_image(file_path=str(path), variant_index=n, **T2I_LINEAGE)
    db.set_grade(str(files[0]), "pass", now=T0)
    db.set_grade(str(files[1]), "fail", now=T0)
    _wire_prune_guard(db)

    class Library:
        filepath = str(library)
        search_subfolders = True

    async def quiet(*a, **k):
        pass

    monkeypatch.setattr(scan_api, "get_db", lambda: db)
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: None)
    monkeypatch.setattr(scan_api, "load_app_config", lambda: {})
    monkeypatch.setattr(scan_api, "get_directories", lambda c: [Library()])
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", quiet)
    monkeypatch.setattr(scan_api, "Scanner", scanner_cls or Scanner)
    return files


def _in_library(db, files):
    paths = db.get_existing_file_paths()
    return [f.name for f in files if str(f) in paths]


def _run(full_clean):
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=full_clean))
    finally:
        scan_api._cancel_requested = False


def _unfinished(db, files):
    """The full clean did not finish: marker kept, nothing tombstoned, every
    generated row kept through a listing."""
    assert _marker_exists(db)
    assert db.list_tombstones() == []
    db.list_t2i_images()
    assert _lineage_rows(db) == (len(files), 0)


def _all_back(db, files):
    assert _in_library(db, files) == [f.name for f in files]
    images = db.list_t2i_images()
    assert [_lineage(r, T2I_LINEAGE) for r in images] == [T2I_LINEAGE] * 3
    assert {r["file_name"]: r["grade"] for r in images} == {
        "img0.png": "pass",
        "img1.png": "fail",
        "img2.png": None,
    }
    assert not _marker_exists(db) and _pending(db) is None
    assert db.list_tombstones() == []


def test_an_ordinary_scan_finishes_a_cancelled_full_clean(monkeypatch, tmp_path, db):
    """P1. A cancel during the last folder skips the re-ingest; the marker
    stays. The next ordinary scan sees the marker, re-ingests the generated
    output, puts the pending grades back and clears the marker."""
    from metascan.core.scanner import Scanner

    class CancelInTheLastFolder(Scanner):
        def scan_directory(self, *a, **k):
            n = super().scan_directory(*a, **k)
            scan_api._cancel_requested = True
            return n

    files = _generated_library(monkeypatch, tmp_path, db, CancelInTheLastFolder)
    real_query = db.generated_paths_without_media
    queried = []

    def spy_query():
        queried.append(1)
        return real_query()

    monkeypatch.setattr(db, "generated_paths_without_media", spy_query)
    _run(full_clean=True)
    assert queried == []  # no re-ingest work at all after a cancel
    assert _in_library(db, files) == []
    _unfinished(db, files)
    assert sorted(Path(p).name for p in _pending_paths(db)) == ["img0.png", "img1.png"]

    monkeypatch.setattr(scan_api, "Scanner", Scanner)
    _run(full_clean=False)
    assert queried == [1]
    _all_back(db, files)


def test_a_cancel_during_the_reingest_stops_it(monkeypatch, tmp_path, db):
    """P2. The cancel is checked between files: one re-ingested, then stop."""
    from metascan.core.scanner import Scanner

    reingested = []

    class CancelAfterOneFile(Scanner):
        def ingest_file(self, path):
            media = super().ingest_file(path)
            if "comfy_output" in str(path):
                reingested.append(Path(path).name)
                scan_api._cancel_requested = True
            return media

    files = _generated_library(monkeypatch, tmp_path, db, CancelAfterOneFile)
    _run(full_clean=True)
    assert reingested == ["img0.png"]
    _unfinished(db, files)


@pytest.mark.parametrize("failure", ["raises", "unreadable"])
def test_a_generated_file_that_fails_to_ingest_keeps_the_full_clean_unfinished(
    monkeypatch, tmp_path, db, caplog, failure
):
    """P3. img1 is still on disk but does not come back (the ingest raises,
    or the file is no image any more): the others do, and the full clean
    stays unfinished -- img1 keeps its row and its pending ✗, no tombstone.
    A later scan that can read it finishes the clean."""
    from metascan.core.scanner import Scanner

    class LockedFile(Scanner):
        def ingest_file(self, path):
            if Path(path).name == "img1.png" and "comfy_output" in str(path):
                raise OSError("locked by another process")
            return super().ingest_file(path)

    files = _generated_library(
        monkeypatch, tmp_path, db, LockedFile if failure == "raises" else None
    )
    good = files[1].read_bytes()
    if failure == "unreadable":
        files[1].write_bytes(b"not an image")
    with caplog.at_level(logging.DEBUG, logger="backend.api.scan"):
        _run(full_clean=True)
    assert _in_library(db, files) == ["img0.png", "img2.png"]
    _unfinished(db, files)
    assert [Path(p).name for p in _pending_paths(db)] == ["img1.png"]
    # The scan route's own log: counts and exception types, never a path.
    messages = " ".join(
        r.getMessage() for r in caplog.records if r.name == "backend.api.scan"
    )
    assert "re-ingested" in messages
    assert "img1" not in messages and "locked by another" not in messages

    files[1].write_bytes(good)
    monkeypatch.setattr(scan_api, "Scanner", Scanner)
    _run(full_clean=False)
    _all_back(db, files)


def test_a_failed_reingest_step_keeps_the_full_clean_unfinished(
    monkeypatch, tmp_path, db, caplog
):
    """P4. The step itself fails: nothing came back, so nothing may be
    tombstoned or pruned. The log names the exception type only."""
    files = _generated_library(monkeypatch, tmp_path, db)
    real = db.generated_paths_without_media

    def boom():
        raise RuntimeError("disk I/O error at /secret/path")

    monkeypatch.setattr(db, "generated_paths_without_media", boom)
    with caplog.at_level(logging.DEBUG, logger="backend.api.scan"):
        _run(full_clean=True)
    assert _in_library(db, files) == []
    _unfinished(db, files)
    messages = " ".join(
        r.getMessage() for r in caplog.records if r.name == "backend.api.scan"
    )
    assert "RuntimeError" in messages and "/secret/path" not in messages

    monkeypatch.setattr(db, "generated_paths_without_media", real)
    _run(full_clean=False)
    _all_back(db, files)


def test_an_unmounted_output_folder_keeps_the_full_clean_unfinished(
    monkeypatch, tmp_path, db
):
    """P5. The output folder is missing (an unmounted volume): its files are
    unreachable, not vanished -- nothing is tombstoned, the marker stays.
    Mounted again, the next scan brings them back."""
    files = _generated_library(monkeypatch, tmp_path, db)
    out = files[0].parent
    unmounted = out.parent / "t2i_unmounted"
    out.rename(unmounted)
    _run(full_clean=True)
    _unfinished(db, files)
    assert sorted(Path(p).name for p in _pending_paths(db)) == ["img0.png", "img1.png"]

    unmounted.rename(out)  # mounted again
    _run(full_clean=False)
    _all_back(db, files)


def test_an_ordinary_scan_reingests_nothing_without_an_unfinished_full_clean(
    monkeypatch, tmp_path, db
):
    """Outside a full clean a dangling row means the file left the library:
    an ordinary scan never adds it back, even if a file is at that path."""
    files = _generated_library(monkeypatch, tmp_path, db)
    assert db.delete_media(files[2])  # its row now dangles; the file stays
    _run(full_clean=False)
    assert _in_library(db, files) == ["img0.png", "img1.png"]
    assert [r["file_name"] for r in db.list_t2i_images()] == ["img1.png", "img0.png"]
    assert _lineage_rows(db) == (2, 0)  # pruned as before


def test_a_vanished_generated_file_is_never_ingested(monkeypatch, tmp_path, db):
    """A generated file gone from a folder that is still there is gone: it
    is not handed to the ingest, it does not hold the full clean back, and
    it is tombstoned with its lineage."""
    from metascan.core.scanner import Scanner

    attempted = []

    class Recording(Scanner):
        def ingest_file(self, path):
            if "comfy_output" in str(path):
                attempted.append(Path(path).name)
            return super().ingest_file(path)

    files = _generated_library(monkeypatch, tmp_path, db, Recording)
    files[1].unlink()
    _run(full_clean=True)
    assert attempted == ["img0.png", "img2.png"]
    assert not _marker_exists(db)
    [t] = db.list_tombstones()
    assert (Path(t["file_path"]).name, t["grade"]) == ("img1.png", "fail")
    assert _lineage(t["lineage"]["t2i"], T2I_LINEAGE) == T2I_LINEAGE
