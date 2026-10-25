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
    test database (the file itself is not created). It lies under no
    configured output root (``_wire``'s config is empty), so once its media
    row is gone the re-ingest counts it gone, never unreachable."""
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
    # A folder's turn asks _probe_folder (test_scan_missing_folders).
    monkeypatch.setattr(
        scan_api,
        "_probe_folder",
        lambda path: scan_api.MISSING if path == "/unmounted" else scan_api.PRESENT,
    )
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


def _capture(monkeypatch):
    """Record every scan event as (event, data)."""
    events = []

    async def broadcast(channel, event, data=None):
        events.append((event, data))

    monkeypatch.setattr(scan_api.ws_manager, "broadcast", broadcast)
    return events


def _marker_text(db):
    return ts.full_clean_marker_path(Path(db.db_path)).read_text(encoding="utf-8")


def _marker_block(db):
    """What the last scan said after the marker's text ("" for nothing)."""
    head, start, rest = _marker_text(db).partition("\n" + ts._LAST_SCAN_COULD_NOT)
    return start + rest


def _flat(text):
    return " ".join(text.split()).lower()


def test_a_failed_reingest_step_keeps_the_full_clean_unfinished(
    monkeypatch, tmp_path, db, caplog
):
    """P4. The step itself fails: nothing came back, so nothing may be
    tombstoned or pruned. The log names the exception type only, and so do
    the complete event (output_check_failed, nothing listed: nothing about
    the output is known) and the marker (the check failed, by its type;
    the next scan tries again)."""
    files = _generated_library(monkeypatch, tmp_path, db)
    events = _capture(monkeypatch)
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
    [complete] = [data for event, data in events if event == "complete"]
    assert complete["output_check_failed"] is True
    assert (
        complete["unreachable_output"],
        complete["unreadable_output"],
        complete["unreadable_output_count"],
    ) == ([], [], 0)
    block = _marker_block(db)
    assert "it failed with an error of type RuntimeError" in " ".join(block.split())
    assert "the next scan tries again" in _flat(block)
    assert "/secret/path" not in _marker_text(db)
    assert "disk I/O" not in _marker_text(db)

    monkeypatch.setattr(db, "generated_paths_without_media", real)
    events.clear()
    _run(full_clean=False)
    _all_back(db, files)
    [complete] = [data for event, data in events if event == "complete"]
    assert complete["output_check_failed"] is False


def test_a_reingest_step_that_fails_during_a_cancel_reports_nothing(
    monkeypatch, tmp_path, db, caplog
):
    """The step raises while a cancel is under way: a cancel reports
    nothing, its failure included -- output_check_failed stays false and the
    marker is left as it is. The ERROR still names the error's type, and
    the clean stays unfinished."""
    files = _generated_library(monkeypatch, tmp_path, db)
    events = _capture(monkeypatch)

    def cancelled_then_failed():
        scan_api._cancel_requested = True  # the owner cancels...
        raise RuntimeError("disk I/O error")  # ...and the query fails

    monkeypatch.setattr(db, "generated_paths_without_media", cancelled_then_failed)
    with caplog.at_level(logging.ERROR, logger="backend.api.scan"):
        _run(full_clean=True)

    [complete] = [data for event, data in events if event == "complete"]
    assert (
        complete["output_check_failed"],
        complete["unreachable_output"],
        complete["unreadable_output"],
        complete["unreadable_output_count"],
    ) == (False, [], [], 0)
    assert _marker_block(db) == ""
    assert any("(RuntimeError)" in r.getMessage() for r in caplog.records)
    _unfinished(db, files)


def test_an_unmounted_output_folder_keeps_the_full_clean_unfinished(
    monkeypatch, tmp_path, db
):
    """P5. The output folder is missing (an unmounted volume): its files are
    unreachable, not vanished -- nothing is tombstoned, the marker stays.
    Mounted again, the next scan brings them back. The folder is T2I's
    output root, <comfy.output_root>/t2i: only a configured output root that
    is missing or unreadable holds a full clean back."""
    files = _generated_library(monkeypatch, tmp_path, db)
    comfy = {"comfy": {"output_root": str(tmp_path / "comfy_output")}}
    monkeypatch.setattr(scan_api, "load_app_config", lambda: comfy)
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


# ----------------------------------------------------------------------
# Only an output ROOT that can't be reached holds a full clean back (final
# review: one old dangling clip whose folder was deleted by hand kept every
# full clean unfinished, and nothing said so)
# ----------------------------------------------------------------------


def _route_full_clean(monkeypatch, db, config, scanner_cls=None):
    """POST /api/scan/start {full_clean} with ``config`` as config.json --
    real folders, the real Scanner unless given another -- and wait for the
    scan it started. Returns the scan's one ``complete`` event (no ``error``
    event)."""
    return _route_scan(monkeypatch, db, config, scanner_cls, full_clean=True)


def _route_scan(monkeypatch, db, config, scanner_cls=None, full_clean=False):
    """``_route_full_clean`` for an ordinary scan too."""
    from metascan.core.scanner import Scanner

    events = []

    async def broadcast(channel, event, data=None):
        events.append((event, data))

    monkeypatch.setattr(scan_api, "get_db", lambda: db)
    monkeypatch.setattr(scan_api, "get_thumbnail_cache", lambda: None)
    monkeypatch.setattr(scan_api, "Scanner", scanner_cls or Scanner)
    monkeypatch.setattr(scan_api, "load_app_config", lambda: config)
    monkeypatch.setattr(scan_api.ws_manager, "broadcast", broadcast)
    monkeypatch.setattr(scan_api, "_scan_task", None)
    monkeypatch.setattr(scan_api, "_cancel_requested", False)

    async def go():
        await scan_api.start_scan(scan_api.ScanRequest(full_clean=full_clean))
        await scan_api._scan_task

    asyncio.run(go())
    assert [data for event, data in events if event == "error"] == []
    [complete] = [data for event, data in events if event == "complete"]
    return complete


def _library_with_a_deleted_image(tmp_path, db):
    """A scan folder with keep.png and gone.png, both ✓, and gone.png then
    deleted on disk; the lifespan's prune guard installed. Returns (a config
    naming the folder, keep, gone)."""
    from metascan.core.scanner import Scanner

    lib = tmp_path / "lib"
    keep, gone = _png(lib / "keep.png"), _png(lib / "gone.png")
    Scanner(db).scan_directory(str(lib))
    for path in (keep, gone):
        db.set_grade(str(path), "pass", now=T0)
    gone.unlink()  # deleted on disk behind the library's back
    _wire_prune_guard(db)
    config = {"directories": [{"filepath": str(lib), "search_subfolders": True}]}
    return config, keep, gone


# Where a generated file sits below its output root: (kind, the root's
# config key or None for the default under comfy.output_root, the root
# relative to tmp_path, the file below the root).
LOST_FOLDER_LAYOUTS = {
    "i2v image folder": ("i2v", None, "comfy/i2v", "cat/clip.mp4"),
    "i2v dated folder": ("i2v", "i2v", "videos", "2026-01-01/i2v_1767225600.mp4"),
    "t2i dated folder": ("t2i", None, "comfy/t2i", "2026-01-01/t2i_1767225600.png"),
    "t2i configured root": ("t2i", "t2i", "images", "2026-01-01/t2i_1.png"),
}


@pytest.mark.parametrize("layout", sorted(LOST_FOLDER_LAYOUTS))
def test_a_folder_lost_below_a_present_output_root_never_holds_a_full_clean_back(
    monkeypatch, tmp_path, db, layout
):
    """The final review's probe_stuck. An old unrated clip (or image) whose
    media row went at a stale cleanup long ago still dangles -- the I2V
    dialog of its source was never opened since -- and the folder it was in
    below its output root (I2V writes one per source image, or a dated one)
    was deleted by hand. Its output root is there, so the file is gone, not
    unreachable: the full clean completes, tombstones the deleted ✓ library
    image and removes the marker. The unrated row gets no tombstone, and
    the prune takes it once the clean is finished."""
    config, keep, gone = _library_with_a_deleted_image(tmp_path, db)
    kind, key, root_rel, below = LOST_FOLDER_LAYOUTS[layout]
    root = tmp_path / root_rel
    root.mkdir(parents=True)
    config["comfy"] = {"output_root": str(tmp_path / "comfy")}
    if key is not None:
        config[key] = {"output_root": str(root)}
    path = str(root / below)
    source = str(tmp_path / "lib" / "cat.png")
    if kind == "i2v":
        db.create_i2v_video(source_path=source, file_path=path, **I2V_LINEAGE)
    else:
        db.create_t2i_image(file_path=path, **T2I_LINEAGE)
    assert db.generated_paths_without_media() == [path]

    complete = _route_full_clean(monkeypatch, db, config)

    assert not _marker_exists(db) and _pending(db) is None
    [t] = db.list_tombstones()
    assert (t["file_path"], t["grade"]) == (str(gone), "pass")
    assert db.get_judgment(str(keep)).grade == "pass"
    db.list_t2i_images()
    db.list_i2v_videos(source)
    assert _lineage_rows(db) == (0, 0)  # finished: the dangling row is pruned
    assert (complete["missing_folders"], complete["unreachable_output"]) == ([], [])


@pytest.mark.parametrize("configured", ["i2v", "t2i"])
def test_a_missing_output_root_holds_the_full_clean_back_and_is_named(
    monkeypatch, tmp_path, db, caplog, configured
):
    """The drive the output goes to is not mounted: both output roots -- one
    configured (``t2i.output_root`` or ``i2v.output_root``), the other the
    default under comfy.output_root -- are missing, and their files may well
    still exist. The full clean stays unfinished -- nothing tombstoned, not
    even the deleted library image, the ratings pending, the lineage and the
    marker kept -- and names the roots: in the complete event, the WARNING
    (folders, never a prompt) and the marker. The way out loses nothing:
    recreate the roots, empty, and run Full clean & rescan. Their files are
    gone then, and the completed clean tombstones each rated one with its
    lineage."""
    config, keep, gone = _library_with_a_deleted_image(tmp_path, db)
    drive = tmp_path / "drive"
    config["comfy"] = {"output_root": str(drive / "comfy")}
    root = {"t2i": drive / "comfy" / "t2i", "i2v": drive / "comfy" / "i2v"}
    root[configured] = drive / f"{configured}_output"
    config[configured] = {"output_root": str(root[configured])}
    image = str(root["t2i"] / "2026-01-01" / "t2i_1.png")
    clip = str(root["i2v"] / "cat" / "clip.mp4")
    for path in (image, clip):
        db.save_media(_media(path))  # ingested while the drive was there
    db.create_t2i_image(file_path=image, **T2I_LINEAGE)
    db.create_i2v_video(source_path=str(keep), file_path=clip, **I2V_LINEAGE)
    db.set_grade(image, "pass", now=T0)
    db.set_grade(clip, "fail", now=T0)
    roots = [str(root["t2i"]), str(root["i2v"])]

    with caplog.at_level(logging.INFO, logger="backend.api.scan"):
        complete = _route_full_clean(monkeypatch, db, config)

    assert complete["unreachable_output"] == roots
    assert complete["missing_folders"] == []
    assert db.list_tombstones() == []
    assert _pending_paths(db) == sorted([str(gone), image, clip])
    assert db.get_judgment(str(keep)).grade == "pass"
    assert _marker_exists(db)
    db.list_t2i_images()
    db.list_i2v_videos(str(keep))
    assert _lineage_rows(db) == (1, 1)
    marker = ts.full_clean_marker_path(Path(db.db_path)).read_text(encoding="utf-8")
    assert all(f"{root} (missing)" in marker for root in roots)
    logged = [r for r in caplog.records if r.name == "backend.api.scan"]
    [warning] = [
        r.getMessage()
        for r in logged
        if r.levelno == logging.WARNING and roots[0] in r.getMessage()
    ]
    assert all(root in warning for root in roots)
    assert "recreate it (empty is fine)" in warning
    prompts = [I2V_LINEAGE["prompt_used"]] + [
        T2I_LINEAGE[k] for k in ("caption", "prompt_used", "draft_prompt")
    ]
    text = " ".join(r.getMessage() for r in logged)
    assert not any(p in text for p in prompts)
    assert image not in text and clip not in text  # folders, never a file

    for root in roots:  # the way out: recreated, empty
        Path(root).mkdir(parents=True)
    complete = _route_full_clean(monkeypatch, db, config)

    assert complete["unreachable_output"] == []
    assert not _marker_exists(db) and _pending(db) is None
    tombs = {t["file_path"]: t for t in db.list_tombstones()}
    assert sorted(tombs) == sorted([str(gone), image, clip])
    assert tombs[image]["grade"] == "pass"
    assert _lineage(tombs[image]["lineage"]["t2i"], T2I_LINEAGE) == T2I_LINEAGE
    assert tombs[clip]["grade"] == "fail"
    assert _lineage(tombs[clip]["lineage"]["i2v"], I2V_LINEAGE) == I2V_LINEAGE


def test_an_unreadable_output_root_holds_the_full_clean_back_until_it_is_read(
    monkeypatch, tmp_path, db
):
    """T2I's output root can't be read (a permission error): its files can be
    neither seen nor ruled out, so the full clean stays unfinished and names
    the root. Readable again, the next full clean re-ingests the image with
    its ✓ and completes."""
    from metascan.core.scanner import Scanner

    config, keep, gone = _library_with_a_deleted_image(tmp_path, db)
    out = tmp_path / "comfy"
    config["comfy"] = {"output_root": str(out)}
    image = _png(out / "t2i" / "2026-01-01" / "t2i_1.png")
    assert Scanner(db).ingest_file(image) is not None  # what ComfyClient does
    db.create_t2i_image(file_path=str(image), **T2I_LINEAGE)
    db.set_grade(str(image), "pass", now=T0)
    root = out / "t2i"
    os.chmod(root, 0)
    try:
        try:
            os.listdir(root)
        except PermissionError:
            pass
        else:
            pytest.skip("permissions don't stop reading a folder here")
        complete = _route_full_clean(monkeypatch, db, config)
    finally:
        os.chmod(root, 0o755)

    assert complete["unreachable_output"] == [str(root)]
    assert db.list_tombstones() == []
    assert _marker_exists(db)
    assert _pending_paths(db) == sorted([str(gone), str(image)])

    complete = _route_full_clean(monkeypatch, db, config)

    assert complete["unreachable_output"] == []
    assert db.get_judgment(str(image)).grade == "pass"
    [row] = db.list_t2i_images()
    assert _lineage(row, T2I_LINEAGE) == T2I_LINEAGE
    [t] = db.list_tombstones()
    assert t["file_path"] == str(gone)
    assert not _marker_exists(db) and _pending(db) is None


def test_a_missing_file_outside_every_output_root_is_gone(monkeypatch, tmp_path, db):
    """The output root moved in config and the old drive is gone: a file left
    under the old root lies under no configured one, so no unmounted output
    root can be hiding it -- it is gone, and the completed full clean
    tombstones it with its ✓ and its lineage. The new root, not created
    yet, holds nothing back: no file of the library lies under it."""
    config, keep, gone = _library_with_a_deleted_image(tmp_path, db)
    config["comfy"] = {"output_root": str(tmp_path / "new_comfy")}
    old = str(tmp_path / "old_drive" / "comfy" / "t2i" / "2026-01-01" / "t2i_1.png")
    db.save_media(_media(old))
    db.create_t2i_image(file_path=old, **T2I_LINEAGE)
    db.set_grade(old, "pass", now=T0)

    complete = _route_full_clean(monkeypatch, db, config)

    assert not _marker_exists(db) and _pending(db) is None
    tombs = {t["file_path"]: t for t in db.list_tombstones()}
    assert sorted(tombs) == sorted([str(gone), old])
    assert tombs[old]["grade"] == "pass"
    assert _lineage(tombs[old]["lineage"]["t2i"], T2I_LINEAGE) == T2I_LINEAGE
    assert (complete["missing_folders"], complete["unreachable_output"]) == ([], [])


def _rendered_library(tmp_path, db, n):
    """``_library_with_a_deleted_image`` plus ``n`` ✓ T2I renders on disk in
    <comfy.output_root>/t2i/2026-01-01/, ingested and recorded the way
    ComfyClient does. Returns (config, gone, the T2I root, the renders)."""
    from metascan.core.scanner import Scanner

    config, keep, gone = _library_with_a_deleted_image(tmp_path, db)
    out = tmp_path / "comfy"
    config["comfy"] = {"output_root": str(out)}
    renders = [_png(out / "t2i" / "2026-01-01" / f"t2i_{i}.png") for i in range(n)]
    for path in renders:
        assert Scanner(db).ingest_file(path) is not None
        db.create_t2i_image(file_path=str(path), **T2I_LINEAGE)
        db.set_grade(str(path), "pass", now=T0)
    return config, gone, out / "t2i", renders


def test_an_output_root_lost_during_the_reingest_holds_the_full_clean_back(
    monkeypatch, tmp_path, db
):
    """The roots are checked after the pass over the files: a drive
    unplugged after the first render came back makes the second one
    unreachable, not gone -- nothing is tombstoned and the root is named."""
    from metascan.core.scanner import Scanner

    config, gone, root, renders = _rendered_library(tmp_path, db, 2)
    unplugged = tmp_path / "unplugged"

    class UnplugsAfterOneRender(Scanner):
        def ingest_file(self, path):
            media = super().ingest_file(path)
            if str(path).startswith(str(root)) and root.exists():
                root.rename(unplugged)  # the drive goes
            return media

    complete = _route_full_clean(monkeypatch, db, config, UnplugsAfterOneRender)

    assert complete["unreachable_output"] == [str(root)]
    assert db.list_tombstones() == []
    assert _marker_exists(db)
    in_library = db.get_existing_file_paths()
    [back] = [str(p) for p in renders if str(p) in in_library]
    assert db.get_judgment(back).grade == "pass"  # the one before the unplug
    [other] = [str(p) for p in renders if str(p) != back]
    assert _pending_paths(db) == sorted([str(gone), other])


def test_an_output_root_back_by_its_check_brings_its_files_back(
    monkeypatch, tmp_path, db
):
    """A file found gone is stat-ed again after its root's check: a drive
    away during the pass but back by the check has its render re-ingested,
    never tombstoned."""
    config, gone, root, [render] = _rendered_library(tmp_path, db, 1)
    unplugged = tmp_path / "unplugged"
    root.rename(unplugged)
    real_probe = scan_api._probe_folder

    def probe(path):
        if path == str(root) and unplugged.exists():
            unplugged.rename(root)  # mounted again just before the check
        return real_probe(path)

    monkeypatch.setattr(scan_api, "_probe_folder", probe)
    complete = _route_full_clean(monkeypatch, db, config)

    assert complete["unreachable_output"] == []
    assert db.get_judgment(str(render)).grade == "pass"
    [t] = db.list_tombstones()
    assert t["file_path"] == str(gone)
    assert not _marker_exists(db)


def test_a_render_that_cannot_be_read_holds_the_full_clean_back(
    monkeypatch, tmp_path, db
):
    """Its root is there but the render can't be stat-ed (its dated folder
    may not be entered): it may well exist, so it is not gone -- the full
    clean stays unfinished, the root is not named as unreachable (it is
    there) but as the folder holding a file that could not be read, the
    file is listed, and once the folder can be read again the next full
    clean brings it back."""
    config, gone, root, [render] = _rendered_library(tmp_path, db, 1)
    day = render.parent
    os.chmod(day, 0)
    try:
        try:
            os.stat(render)
        except PermissionError:
            pass
        else:
            pytest.skip("permissions don't stop entering a folder here")
        complete = _route_full_clean(monkeypatch, db, config)
    finally:
        os.chmod(day, 0o755)

    assert complete["unreachable_output"] == []
    assert complete["unreadable_output"] == [str(render)]
    assert complete["unreadable_output_count"] == 1
    assert f"  in {root}\n" in _marker_block(db)
    assert db.list_tombstones() == []
    assert _marker_exists(db)
    assert _pending_paths(db) == sorted([str(gone), str(render)])

    complete = _route_full_clean(monkeypatch, db, config)
    assert db.get_judgment(str(render)).grade == "pass"
    [t] = db.list_tombstones()
    assert t["file_path"] == str(gone)
    assert not _marker_exists(db)


def test_the_deepest_output_root_that_holds_a_file_decides(monkeypatch, tmp_path, db):
    """Nested roots: T2I writes to <out>, I2V to <out>/clips, where a drive of
    its own is mounted. With that drive not mounted the clip is unreachable
    although <out> is there: the deepest root that holds a file decides."""
    config, keep, gone = _library_with_a_deleted_image(tmp_path, db)
    out = tmp_path / "out"
    out.mkdir()
    clips = out / "clips"  # its drive is not mounted
    config["t2i"] = {"output_root": str(out)}
    config["i2v"] = {"output_root": str(clips)}
    clip = str(clips / "2026-01-01" / "i2v_1.mp4")
    db.save_media(_media(clip))
    db.create_i2v_video(source_path=str(keep), file_path=clip, **I2V_LINEAGE)
    db.set_grade(clip, "pass", now=T0)

    complete = _route_full_clean(monkeypatch, db, config)

    assert complete["unreachable_output"] == [str(clips)]
    assert db.list_tombstones() == []
    assert _marker_exists(db)
    assert _pending_paths(db) == sorted([str(gone), clip])


# ----------------------------------------------------------------------
# A generated file on disk that can't be imported keeps a full clean
# unfinished, and is listed until it is dealt with (final review: one such
# file held every later full clean back, and nothing said which file)
# ----------------------------------------------------------------------


def _scanner_failing_on(paths, failure="returns None"):
    """The real Scanner, except that ``paths`` can't be imported: their
    ingest returns None (a file it can't read as media) or raises, with the
    path in the error's text -- which must never reach a log."""
    from metascan.core.scanner import Scanner

    failing = {str(p) for p in paths}

    class FailsToImport(Scanner):
        def ingest_file(self, path):
            if str(path) in failing:
                if failure == "raises":
                    raise OSError(f"database is locked while saving {path}")
                return None
            return super().ingest_file(path)

    return FailsToImport


@pytest.mark.parametrize("failure", ["returns None", "raises"])
def test_a_generated_file_that_cannot_be_imported_is_listed_until_dealt_with(
    monkeypatch, tmp_path, db, caplog, failure
):
    """A ✓ T2I render with its lineage row is on disk, but the scanner can't
    import it (its ingest returns None, or raises). The full clean stays
    unfinished -- nothing tombstoned, not even the deleted library image,
    the ✓ pending, the lineage row and the marker kept -- and now says
    which file: the complete event lists it, absolute, and counts it; the
    marker counts it and names its output folder, with the way out, never
    its path; and no log line, at any level, carries the path. The way out:
    delete it and run Full clean & rescan. That clean completes, records
    the render as deleted with its ✓ and its lineage, and finishes."""
    config, gone, root, [render] = _rendered_library(tmp_path, db, 1)

    with caplog.at_level(logging.DEBUG):
        complete = _route_full_clean(
            monkeypatch, db, config, _scanner_failing_on([render], failure)
        )

    assert complete["unreadable_output"] == [str(render)]
    assert complete["unreadable_output_count"] == 1
    assert complete["output_check_failed"] is False
    assert complete["unreachable_output"] == []
    # Unfinished: nothing tombstoned, the ✓ pending, the lineage row and
    # the marker kept.
    assert db.list_tombstones() == []
    assert _marker_exists(db)
    assert _pending_paths(db) == sorted([str(gone), str(render)])
    [pending] = [j for j in _pending(db)["judgments"] if j["file_path"] == str(render)]
    assert pending["grade"] == "pass"
    assert str(render) not in db.get_existing_file_paths()
    db.list_t2i_images()
    assert _lineage_rows(db) == (1, 0)
    # The marker counts it and names the output folder holding it, with the
    # way out; never the file.
    block = _marker_block(db)
    flat = _flat(block)
    assert "could not read or import 1 generated image or clip that is on disk" in flat
    assert f"  in {root}\n" in block
    assert (
        "delete each one or move it somewhere else (or repair it), then run "
        "full clean & rescan"
    ) in flat
    assert str(render) not in _marker_text(db)
    assert render.name not in _marker_text(db)
    # The WARNING names the folder and says where the files are listed.
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    [warning] = [w for w in warnings if "could not be read or re-imported" in w]
    assert f"1 generated images / clips on disk (in {root})" in warning
    assert "Done view lists the files" in warning
    # No log line at any level has the file's path (or its name).
    logged = caplog.text + "".join(r.getMessage() for r in caplog.records)
    assert str(render) not in logged and render.name not in logged

    render.unlink()  # the way out: delete it
    complete = _route_full_clean(monkeypatch, db, config)

    assert complete["unreadable_output"] == []
    assert complete["unreadable_output_count"] == 0
    assert not _marker_exists(db) and _pending(db) is None
    tombs = {t["file_path"]: t for t in db.list_tombstones()}
    assert sorted(tombs) == sorted([str(gone), str(render)])
    assert tombs[str(render)]["grade"] == "pass"
    assert _lineage(tombs[str(render)]["lineage"]["t2i"], T2I_LINEAGE) == T2I_LINEAGE


def test_a_failed_file_under_no_output_folder_is_said_to_be_outside_them(
    monkeypatch, tmp_path, db, caplog
):
    """A ✓ render whose lineage row lies outside both configured output
    roots (the output folder moved in config; the old drive's files stayed)
    is on disk but can't be imported. It is failed like any other, and the
    WARNING and the marker say it lies outside the configured output
    folders -- no folder of theirs holds it -- with the way out that fits a
    file anywhere: delete it or move it somewhere else."""
    config, keep, gone = _library_with_a_deleted_image(tmp_path, db)
    config["comfy"] = {"output_root": str(tmp_path / "comfy")}
    stray = _png(tmp_path / "old_drive" / "t2i" / "2026-01-01" / "t2i_1.png")
    db.save_media(_media(str(stray)))
    db.create_t2i_image(file_path=str(stray), **T2I_LINEAGE)
    db.set_grade(str(stray), "pass", now=T0)

    with caplog.at_level(logging.WARNING, logger="backend.api.scan"):
        complete = _route_full_clean(
            monkeypatch, db, config, _scanner_failing_on([stray])
        )

    assert complete["unreadable_output"] == [str(stray)]
    assert complete["unreachable_output"] == []
    block = _marker_block(db)
    assert "  outside the configured output folders\n" in block
    assert "\n  in " not in block  # no configured output folder holds it
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    [warning] = [w for w in warnings if "could not be read or re-imported" in w]
    assert warning.startswith(
        "1 generated images / clips on disk (outside the configured output "
        "folders) could not be read or re-imported;"
    )
    assert (
        "delete each one or move it somewhere else (or repair it), then run "
        "Full clean & rescan"
    ) in warning
    assert str(stray) not in " ".join(warnings) + _marker_text(db)
    assert db.list_tombstones() == [] and _marker_exists(db)
    assert str(stray) in _pending_paths(db)


def test_a_file_that_keeps_failing_is_never_recorded_as_deleted(
    monkeypatch, tmp_path, db
):
    """No retry-to-gone: however many full cleans fail to import a file that
    is on disk, it stays listed and pending, never tombstoned -- it may be
    a passing failure, and a tombstone would leave a rated file on disk
    outside the library for good."""
    config, gone, root, [render] = _rendered_library(tmp_path, db, 1)
    for _ in range(3):
        complete = _route_full_clean(
            monkeypatch, db, config, _scanner_failing_on([render])
        )
        assert complete["unreadable_output"] == [str(render)]
        assert db.list_tombstones() == []
        assert str(render) in _pending_paths(db)
        assert _marker_exists(db)


def test_the_complete_event_lists_the_first_fifty_failed_files_and_counts_all(
    monkeypatch, tmp_path, db, caplog
):
    """More failed files than the event lists: the first
    UNREADABLE_OUTPUT_LIMIT in path order -- whatever order the library
    hands them over in -- and every one counted, in the event, the WARNING
    and the marker. These are no images at all (bytes the scanner can't
    read as media): the real Scanner returns None for each."""
    import random

    config, keep, gone = _library_with_a_deleted_image(tmp_path, db)
    out = tmp_path / "comfy"
    config["comfy"] = {"output_root": str(out)}
    day = out / "t2i" / "2026-01-01"
    day.mkdir(parents=True)
    n = scan_api.UNREADABLE_OUTPUT_LIMIT + 3
    renders = []
    for i in range(n):
        path = day / f"t2i_{i:03d}.png"
        path.write_bytes(b"not an image")
        db.create_t2i_image(file_path=str(path), **T2I_LINEAGE)
        renders.append(str(path))
    listed = db.generated_paths_without_media
    shuffled = random.Random(7).sample(listed(), n)
    assert shuffled != sorted(shuffled)
    monkeypatch.setattr(db, "generated_paths_without_media", lambda: list(shuffled))

    with caplog.at_level(logging.WARNING, logger="backend.api.scan"):
        complete = _route_full_clean(monkeypatch, db, config)

    limit = scan_api.UNREADABLE_OUTPUT_LIMIT
    assert limit == 50
    assert complete["unreadable_output"] == sorted(renders)[:limit]
    assert complete["unreadable_output_count"] == n
    flat = _flat(_marker_block(db))
    assert f"could not read or import {n} generated images or clips" in flat
    assert any(
        r.getMessage().startswith(f"{n} generated images / clips on disk")
        for r in caplog.records
    )
    assert db.list_tombstones() == [] and _marker_exists(db)


CANCEL_POINTS = ("between files", "during the last file", "during the last re-check")


@pytest.mark.parametrize("where", CANCEL_POINTS)
def test_a_cancel_during_the_reingest_reports_no_failed_file(
    monkeypatch, tmp_path, db, where
):
    """A cancel stops the re-ingest. What it found by then is partial, so --
    as for the output roots -- the complete event says nothing of the
    generated output ([], 0, false), the marker is left as it is, and the
    clean stays unfinished. The cancel is checked between files, after the
    pass (one during the last file's ingest: no output root is even looked
    up after it) and before the report is returned (one during the re-check
    of a file found gone, the last thing the step does)."""
    from metascan.core.scanner import Scanner

    n = 2 if where == "between files" else 1
    config, gone, root, renders = _rendered_library(tmp_path, db, n)
    failing = {str(p) for p in renders}
    clip = None
    if where == "during the last re-check":
        # A clip gone with its drive during the pass and back by the root
        # check: its re-ingest is where the cancel lands.
        i2v_root = tmp_path / "comfy" / "i2v"
        clip = i2v_root / "cat" / "clip.mp4"
        clip.parent.mkdir(parents=True)
        clip.write_bytes(b"a clip")
        source = str(tmp_path / "lib" / "keep.png")
        db.create_i2v_video(source_path=source, file_path=str(clip), **I2V_LINEAGE)
        unplugged = tmp_path / "unplugged"
        i2v_root.rename(unplugged)
        real_probe = scan_api._probe_folder

        def probe(path):
            if path == str(i2v_root) and unplugged.exists():
                unplugged.rename(i2v_root)  # mounted again just before the check
            return real_probe(path)

        monkeypatch.setattr(scan_api, "_probe_folder", probe)

    class CancelsAsItGoes(Scanner):
        def ingest_file(self, path):
            if str(path) in failing:
                if clip is None:
                    scan_api._cancel_requested = True
                return None
            if clip is not None and str(path) == str(clip):
                scan_api._cancel_requested = True
                db.save_media(_media(str(path)))
                return object()
            return super().ingest_file(path)

    real_roots = scan_api._generated_output_roots
    looked_up_after_a_cancel = []

    def roots(config):
        looked_up_after_a_cancel.append(scan_api._cancel_requested)
        return real_roots(config)

    monkeypatch.setattr(scan_api, "_generated_output_roots", roots)
    complete = _route_full_clean(monkeypatch, db, config, CancelsAsItGoes)

    assert (
        complete["unreachable_output"],
        complete["unreadable_output"],
        complete["unreadable_output_count"],
        complete["output_check_failed"],
    ) == ([], [], 0, False)
    assert True not in looked_up_after_a_cancel
    assert _marker_block(db) == ""
    assert db.list_tombstones() == [] and _marker_exists(db)


def test_a_cancelled_scan_leaves_the_marker_as_the_last_check_wrote_it(
    monkeypatch, tmp_path, db
):
    """The marker says what the last scan that checked the generated output
    to its end found. A full clean leaves two renders that can't be
    imported, and the marker counts them and names their folder; an
    ordinary scan then cancelled during its own re-ingest found only part
    of that, so it leaves the marker byte for byte as it was."""
    config, gone, root, renders = _rendered_library(tmp_path, db, 2)
    _route_full_clean(monkeypatch, db, config, _scanner_failing_on(renders))
    marker = ts.full_clean_marker_path(Path(db.db_path))
    written = marker.read_bytes()
    block = _marker_block(db)
    assert f"  in {root}\n" in block and "2 generated images or clips" in block

    failing = _scanner_failing_on(renders)

    class CancelsAtTheFirstFailure(failing):
        def ingest_file(self, path):
            media = super().ingest_file(path)
            if media is None:
                scan_api._cancel_requested = True
            return media

    complete = _route_scan(monkeypatch, db, config, CancelsAtTheFirstFailure)

    assert (complete["unreadable_output"], complete["unreadable_output_count"]) == (
        [],
        0,
    )
    assert marker.read_bytes() == written


def test_relative_output_folders_and_files_are_shown_absolute(
    monkeypatch, tmp_path, db, caplog
):
    """The default I2V output root is relative (data/storyboards/i2v, from
    comfy.output_root), and so are the clips below it. The scan shows them
    absolute, joined to the server's working directory: an unreachable
    root in the WARNING, the marker and the complete event, and a failed
    file and the folder holding it likewise."""
    monkeypatch.chdir(tmp_path)
    config, keep, gone = _library_with_a_deleted_image(tmp_path, db)
    config["comfy"] = {"output_root": "drive/comfy"}  # its drive is not mounted
    config["t2i"] = {"output_root": "renders"}  # relative too, and there
    clip = "drive/comfy/i2v/cat/clip.mp4"
    db.save_media(_media(clip))
    db.create_i2v_video(source_path=str(keep), file_path=clip, **I2V_LINEAGE)
    db.set_grade(clip, "pass", now=T0)
    render = "renders/2026-01-01/t2i_1.png"
    _png(tmp_path / render)
    db.create_t2i_image(file_path=render, **T2I_LINEAGE)

    with caplog.at_level(logging.WARNING, logger="backend.api.scan"):
        complete = _route_full_clean(
            monkeypatch, db, config, _scanner_failing_on([render])
        )

    clips_root = str(tmp_path / "drive" / "comfy" / "i2v")
    renders_root = str(tmp_path / "renders")
    assert complete["unreachable_output"] == [clips_root]
    assert complete["unreadable_output"] == [str(tmp_path / render)]
    block = _marker_block(db)
    assert f"  {clips_root} (missing)\n" in block
    assert f"  in {renders_root}\n" in block
    warnings = " ".join(
        r.getMessage() for r in caplog.records if r.levelno == logging.WARNING
    )
    assert f"{clips_root} (missing)" in warnings
    assert f"(in {renders_root})" in warnings
    # Never the relative form on its own.
    for shown in (block, warnings, json.dumps(complete)):
        rest = shown.replace(clips_root, "").replace(renders_root, "")
        assert "drive/comfy" not in rest and "renders/" not in rest
    assert db.list_tombstones() == [] and _marker_exists(db)


def test_an_output_root_spelled_two_ways_is_listed_once(monkeypatch, tmp_path):
    """The complete event lists each unreachable root once, however the
    configuration spells it -- here once relative, once absolute -- since
    what it shows is absolute."""
    monkeypatch.chdir(tmp_path)
    report = scan_api.ReingestReport(
        unreachable={"out": scan_api.MISSING, str(tmp_path / "out"): scan_api.MISSING}
    )
    fields = scan_api._generated_output_fields(report)
    assert fields["unreachable_output"] == [str(tmp_path / "out")]
