"""Full clean & rescan must not destroy taste data (final-review item 1).

``truncate_all_data`` deletes every media row, which cascades away
``media_judgments`` and ``media_categories``. The scan route snapshots the
taste state first (``export_taste_state``) and puts it back verbatim after
the rescan (``restore_taste_state``) -- original timestamps, no new events --
and tombstones every snapshot path that did not come back.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
T1 = datetime(2026, 2, 3, 4, 5, 6, tzinfo=timezone.utc)


# A seeded rating as (★ favorite, grade): ★ with ✓, what a pre-grades
# favorite became, and ✗ without ★, what an old reject became.
FAVORITE = (True, "pass")
REJECT = (False, "fail")


def _marks(state):
    return (state.favorite, state.grade)


def _favorite(db, path, now):
    """★ + ✓: a favorite that was also graded pass."""
    db.set_favorite_state(path, True, now=now)
    db.set_grade(path, "pass", now=now)


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


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


def _rows(db, sql):
    with db._get_connection() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def _seed(db):
    """fav (★ + ✓, reasons, a category), reject (✗, reasons, two
    categories), seen-only, untouched, and a ★ + ✓ that will not come
    back."""
    for p in ("/l/fav.png", "/l/reject.png", "/l/seen.png", "/l/plain.png"):
        db.save_media(_media(p))
    db.save_media(_media("/l/gone.png"))
    anime = db.create_category("Anime")
    scifi = db.create_category("Sci-Fi")

    _favorite(db, "/l/fav.png", T0)
    db.set_reason("/l/fav.png", "light", True, now=T0)
    db.set_reason("/l/fav.png", "realism", True, now=T0)
    db.add_media_categories(["/l/fav.png"], anime.id, "viewer")

    db.set_grade("/l/reject.png", "fail", now=T1)
    db.set_reason("/l/reject.png", "composition", True, now=T1)
    db.add_media_categories(["/l/reject.png"], anime.id, "bulk")
    db.add_media_categories(["/l/reject.png"], scifi.id, "viewer")

    db.mark_seen("/l/seen.png", now=T0)

    _favorite(db, "/l/gone.png", T1)
    db.set_reason("/l/gone.png", "emotion", True, now=T1)
    db.add_media_categories(["/l/gone.png"], scifi.id, "viewer")
    return anime, scifi


GRADE_PATHS = (
    "/g/fail.png",
    "/g/pass.png",
    "/g/plain.png",
    "/g/seen.png",
    "/g/star.png",
    "/g/star_pass.png",
)


def _seed_grades(db):
    """Grade-era ratings, set through the grade and ★ APIs: ★ + ✓ with a
    reason and a category, ✓ without ★, ✗ with a reason, ★ alone (no
    judgment row: ★ is not a grade), seen-only, and an untouched item."""
    for p in GRADE_PATHS:
        db.save_media(_media(p))
    portrait = db.create_category("Portrait")
    db.set_favorite_state("/g/star_pass.png", True, now=T0)
    db.set_grade("/g/star_pass.png", "pass", now=T0)
    db.set_reason("/g/star_pass.png", "light", True, now=T0)
    db.add_media_categories(["/g/star_pass.png"], portrait.id, "viewer")
    db.set_grade("/g/pass.png", "pass", now=T1)
    db.set_grade("/g/fail.png", "fail", now=T1)
    db.set_reason("/g/fail.png", "composition", True, now=T1)
    db.set_favorite_state("/g/star.png", True, now=T0)
    db.mark_seen("/g/seen.png", now=T0)
    return portrait


def _state(db):
    return (
        _rows(db, "SELECT * FROM media_judgments ORDER BY file_path"),
        _rows(db, "SELECT * FROM media_categories ORDER BY file_path, category_id"),
        _rows(db, "SELECT file_path FROM media WHERE is_favorite = 1 ORDER BY 1"),
    )


def test_truncate_keeps_event_log_and_categories(db):
    _seed(db)
    events = _rows(db, "SELECT * FROM judgment_events ORDER BY id")
    cats = _rows(db, "SELECT * FROM categories ORDER BY id")
    assert db.truncate_all_data() is True
    assert _rows(db, "SELECT * FROM judgment_events ORDER BY id") == events
    assert _rows(db, "SELECT * FROM categories ORDER BY id") == cats
    # ...while the cascading tables are gone -- which is why we snapshot.
    assert _rows(db, "SELECT * FROM media_judgments") == []
    assert _rows(db, "SELECT * FROM media_categories") == []


def test_snapshot_truncate_rescan_restore_roundtrip(db):
    _seed(db)
    judgments, links, favs = _state(db)
    before_gone = [j for j in judgments if j["file_path"] == "/l/gone.png"]
    events = _rows(db, "SELECT * FROM judgment_events ORDER BY id")

    snapshot = db.export_taste_state()
    assert db.truncate_all_data() is True
    for p in ("/l/fav.png", "/l/reject.png", "/l/seen.png", "/l/plain.png"):
        db.save_media(_media(p))  # rescan; gone.png does not come back

    counts = db.restore_taste_state(snapshot)

    assert counts["restored"] == 3
    assert counts["missing"] == 1
    j2, l2, f2 = _state(db)
    assert j2 == [j for j in judgments if j["file_path"] != "/l/gone.png"]
    assert l2 == [lk for lk in links if lk["file_path"] != "/l/gone.png"]
    assert f2 == [{"file_path": "/l/fav.png"}]
    assert favs == [{"file_path": "/l/fav.png"}, {"file_path": "/l/gone.png"}]
    # No new events: the restore is a put-back, not a re-judgment.
    assert _rows(db, "SELECT * FROM judgment_events ORDER BY id") == events
    # Judgment reads see the original ★, grades, reasons and timestamps.
    fav = db.get_judgment("/l/fav.png")
    assert _marks(fav) == FAVORITE and fav.reasons == ["realism", "light"]
    assert fav.confirmed_at == T0.isoformat()
    rejected = db.get_judgment("/l/reject.png")
    assert _marks(rejected) == REJECT and rejected.reasons == ["composition"]
    assert [c.name for c in rejected.categories] == ["Anime", "Sci-Fi"]
    assert db.get_judgment("/l/seen.png").seen_at == T0.isoformat()
    assert before_gone  # sanity: the missing path really carried a judgment


@pytest.mark.parametrize("version", [None, 1, 2, 4])
def test_restore_refuses_a_snapshot_that_is_not_v3(db, version):
    """A version-less snapshot carries the old meaning (a favorite is ✓):
    put back as stored it would lose every grade, so the restore refuses
    it -- and a version-2 one, which the pending-file reader translates
    first, and any unknown version -- before writing anything."""
    _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    db.save_media(_media("/l/fav.png"))
    old = {k: v for k, v in snapshot.items() if k != "version"}
    if version is not None:
        old["version"] = version
    with pytest.raises(ValueError):
        db.restore_taste_state(old, tombstone_missing=True)
    assert _state(db) == ([], [], [])
    assert db.list_tombstones() == []


def test_restore_writes_one_tombstone_per_missing_path(db):
    _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    for p in ("/l/fav.png", "/l/reject.png", "/l/seen.png"):
        db.save_media(_media(p))
    vec = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (vec, "clip-small"))

    counts = db.restore_taste_state(snapshot, tombstone_missing=True)
    counts.pop("remaining")

    assert counts == {"restored": 3, "missing": 1, "tombstoned": 1}
    [t] = db.list_tombstones()
    assert t["file_path"] == "/l/gone.png"
    assert (t["is_favorite"], t["grade"]) == (1, "pass")
    assert json.loads(t["reasons"]) == ["emotion"]
    assert json.loads(t["categories"]) == ["Sci-Fi"]
    assert t["confirmed_at"] == T1.isoformat()
    assert t["judged_at"] == T1.isoformat()
    assert db.get_tombstone_vectors(t["id"]) == {"clip-small": vec.tobytes()}


def test_restore_without_tombstones_flag_writes_none(db):
    _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    counts = db.restore_taste_state(snapshot)
    assert counts.pop("remaining")["judgments"] == snapshot["judgments"]
    assert counts == {"restored": 0, "missing": 4, "tombstoned": 0}
    assert db.list_tombstones() == []


def test_restore_skips_categories_deleted_meanwhile(db):
    anime, scifi = _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    db.save_media(_media("/l/reject.png"))
    db.delete_category(scifi.id)
    db.restore_taste_state(snapshot)
    assert [c.name for c in db.get_media_categories("/l/reject.png")] == ["Anime"]


def test_restore_is_idempotent(db):
    _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    for p in ("/l/fav.png", "/l/reject.png", "/l/seen.png"):
        db.save_media(_media(p))
    db.restore_taste_state(snapshot)
    first = _state(db)
    db.restore_taste_state(snapshot)
    assert _state(db) == first


def test_snapshot_survives_json_roundtrip(db):
    """The snapshot is plain data (so it could be persisted)."""
    _seed(db)
    snapshot = json.loads(json.dumps(db.export_taste_state()))
    db.truncate_all_data()
    db.save_media(_media("/l/fav.png"))
    assert db.restore_taste_state(snapshot)["restored"] == 1
    assert _marks(db.get_judgment("/l/fav.png")) == FAVORITE


def test_v3_snapshot_round_trips(db):
    """The snapshot is version 3 (grades since version 2): each judgment row
    carries its grade and no legacy reject flag, and putting it back
    restores every grade-era rating exactly -- grades as stored, never
    derived (★ alone stays ★ alone) -- with no new events. (Briefs and
    library prompts: tests/test_taste_snapshot_briefs.py.)"""
    portrait = _seed_grades(db)
    before = _state(db)
    events = _rows(db, "SELECT * FROM judgment_events ORDER BY id")

    snapshot = json.loads(json.dumps(db.export_taste_state()))

    assert snapshot["version"] == 3
    assert (snapshot["briefs"], snapshot["library_prompts"]) == ([], [])
    t0, t1 = T0.isoformat(), T1.isoformat()
    assert snapshot["judgments"] == [
        {
            "file_path": "/g/fail.png",
            "grade": "fail",
            "reasons": '["composition"]',
            "seen_at": t1,
            "judged_at": t1,
            "confirmed_at": t1,
        },
        {
            "file_path": "/g/pass.png",
            "grade": "pass",
            "reasons": "[]",
            "seen_at": t1,
            "judged_at": t1,
            "confirmed_at": t1,
        },
        {
            "file_path": "/g/seen.png",
            "grade": None,
            "reasons": "[]",
            "seen_at": t0,
            "judged_at": None,
            "confirmed_at": None,
        },
        {
            "file_path": "/g/star_pass.png",
            "grade": "pass",
            "reasons": '["light"]',
            "seen_at": t0,
            "judged_at": t0,
            "confirmed_at": t0,
        },
    ]
    assert snapshot["favorites"] == ["/g/star.png", "/g/star_pass.png"]
    assert [
        (lk["file_path"], lk["category_id"], lk["category_name"], lk["source"])
        for lk in snapshot["categories"]
    ] == [("/g/star_pass.png", portrait.id, "Portrait", "viewer")]

    assert db.truncate_all_data() is True
    for p in GRADE_PATHS:
        db.save_media(_media(p))
    counts = db.restore_taste_state(snapshot)

    assert (counts["restored"], counts["missing"]) == (5, 0)
    assert _state(db) == before
    assert _rows(db, "SELECT * FROM judgment_events ORDER BY id") == events
    assert {p: _marks(db.get_judgment(p)) for p in GRADE_PATHS} == {
        "/g/fail.png": (False, "fail"),
        "/g/pass.png": (False, "pass"),
        "/g/plain.png": (False, None),
        "/g/seen.png": (False, None),
        "/g/star.png": (True, None),
        "/g/star_pass.png": (True, "pass"),
    }
    assert db.get_judgment("/g/fail.png").reasons == ["composition"]


# ----------------------------------------------------------------------
# Pending restore: never overwrite newer data, report what is left
# ----------------------------------------------------------------------


def test_restore_reports_the_unrestored_remainder(db):
    _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    db.save_media(_media("/l/fav.png"))

    counts = db.restore_taste_state(snapshot)

    rem = counts["remaining"]
    assert sorted(j["file_path"] for j in rem["judgments"]) == [
        "/l/gone.png",
        "/l/reject.png",
        "/l/seen.png",
    ]
    assert rem["favorites"] == ["/l/gone.png"]
    assert sorted({lk["file_path"] for lk in rem["categories"]}) == [
        "/l/gone.png",
        "/l/reject.png",
    ]
    assert rem["taken_at"] == snapshot["taken_at"]
    # The remainder restores the rest later, exactly like the original.
    for p in ("/l/reject.png", "/l/gone.png"):
        db.save_media(_media(p))
    again = db.restore_taste_state(json.loads(json.dumps(rem)))
    assert again["restored"] == 2 and again["missing"] == 1
    assert _marks(db.get_judgment("/l/gone.png")) == FAVORITE
    assert db.get_judgment("/l/reject.png").reasons == ["composition"]


def test_restore_never_overwrites_a_newer_grade(db):
    """A path graded after it re-appeared keeps its row: a new grade, a
    cleared one (judged_at), or a grade with no judged_at (as v6 leaves a
    starred seen-only row) -- and its newer category link. ★ is not a
    grade: the snapshot's ★ is put back beside the newer grade (a restore
    only ever sets ★), and a live ★ is never cleared."""
    portrait = _seed_grades(db)
    scenery = db.create_category("Scenery")
    db.add_media_categories(["/g/star_pass.png"], scenery.id, "viewer")
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    for p in ("/g/star_pass.png", "/g/pass.png", "/g/fail.png", "/g/seen.png"):
        db.save_media(_media(p))
    # Graded again after they re-appeared, before the restore.
    db.set_grade("/g/star_pass.png", "fail", now=T1)
    db.add_media_categories(["/g/star_pass.png"], portrait.id, "bulk")
    db.set_grade("/g/pass.png", "fail", now=T0)
    db.set_grade("/g/pass.png", None, now=T1)
    db.set_grade("/g/fail.png", "pass", now=T0)
    db.set_favorite_state("/g/fail.png", True, now=T0)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO media_judgments (file_path, grade, seen_at) "
            "VALUES (?, 'pass', ?)",
            ("/g/seen.png", T1.isoformat()),
        )
        conn.commit()
    newer = _state(db)
    events = _rows(db, "SELECT * FROM judgment_events ORDER BY id")
    newer_portrait_link = [
        lk
        for lk in newer[1]
        if lk["file_path"] == "/g/star_pass.png" and lk["category_id"] == portrait.id
    ]

    counts = db.restore_taste_state(snapshot)

    assert counts["restored"] == 4
    j2, l2, _ = _state(db)
    assert j2 == newer[0]  # every newer judgment row as it was
    assert _rows(db, "SELECT * FROM judgment_events ORDER BY id") == events
    star_pass = db.get_judgment("/g/star_pass.png")
    assert _marks(star_pass) == (True, "fail")
    assert (star_pass.reasons, star_pass.confirmed_at) == ([], T1.isoformat())
    assert _marks(db.get_judgment("/g/pass.png")) == (False, None)
    assert _marks(db.get_judgment("/g/fail.png")) == (True, "pass")
    assert db.get_judgment("/g/fail.png").reasons == []
    assert _marks(db.get_judgment("/g/seen.png")) == (False, "pass")
    # Category links: the existing (newer) link is kept as-is, missing
    # snapshot links are added.
    portrait_now = [
        lk
        for lk in l2
        if lk["file_path"] == "/g/star_pass.png" and lk["category_id"] == portrait.id
    ]
    assert portrait_now == newer_portrait_link
    assert [c.name for c in star_pass.categories] == ["Portrait", "Scenery"]


def test_restore_puts_the_grade_back_under_a_newer_favorite(db):
    """★ is not a grade: starring a re-appeared item before the restore
    never blocks its snapshot grade and reasons, and that ★ stays (a
    restore never clears ★)."""
    _seed_grades(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    for p in ("/g/pass.png", "/g/fail.png"):
        db.save_media(_media(p))
    db.set_favorite_state("/g/pass.png", True, now=T0)
    db.set_favorite_state("/g/fail.png", True, now=T0)
    events = _rows(db, "SELECT * FROM judgment_events ORDER BY id")

    db.restore_taste_state(snapshot)

    graded = db.get_judgment("/g/pass.png")
    assert (_marks(graded), graded.judged_at) == ((True, "pass"), T1.isoformat())
    failed = db.get_judgment("/g/fail.png")
    assert (_marks(failed), failed.reasons, failed.confirmed_at) == (
        (True, "fail"),
        ["composition"],
        T1.isoformat(),
    )
    assert _rows(db, "SELECT * FROM judgment_events ORDER BY id") == events


def test_restore_fills_a_seen_only_row(db):
    """Just viewing a re-appeared item (seen-only row) is not a rating:
    the snapshot rating still comes back."""
    _seed(db)
    snapshot = db.export_taste_state()
    db.truncate_all_data()
    db.save_media(_media("/l/fav.png"))
    db.mark_seen("/l/fav.png", now=T1)

    db.restore_taste_state(snapshot)

    fav = db.get_judgment("/l/fav.png")
    assert _marks(fav) == FAVORITE
    assert fav.reasons == ["realism", "light"]
    assert fav.confirmed_at == T0.isoformat()
    assert fav.seen_at == T0.isoformat()
