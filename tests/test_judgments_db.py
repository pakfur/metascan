"""Schema, lineage columns and migration v5 for taste-rating capture, and
the judgment write / read model: ★ favorite and the ✓ pass / ✗ fail grade
are independent."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


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


def test_new_tables_exist(db):
    with db._get_connection() as conn:
        names = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "media_judgments",
        "judgment_events",
        "media_tombstones",
        "categories",
        "media_categories",
    } <= names


def test_lineage_columns_added(db):
    with db._get_connection() as conn:
        for table in ("t2i_images", "i2v_videos"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert {"writer_id", "variant_group", "variant_index"} <= cols


def test_user_version_is_6(db):
    with db._get_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6


def test_migration_seeds_existing_favorites(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    mgr.save_media(_media("/pics/fav.png"))
    mgr.set_favorite(Path("/pics/fav.png"), True)
    with mgr._get_connection() as conn:
        conn.execute("DELETE FROM media_judgments")
        conn.execute("DELETE FROM judgment_events")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    mgr.close()
    mgr = DatabaseManager(tmp_path / "db")
    with mgr._get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM media_judgments WHERE file_path = '/pics/fav.png'"
        ).fetchone()
        assert (
            row["confirmed_at"] is not None and row["judged_at"] == row["confirmed_at"]
        )
        assert row["seen_at"] == row["confirmed_at"]
        events = conn.execute("SELECT event FROM judgment_events").fetchall()
        assert [e["event"] for e in events] == ["favorite"]
    mgr.close()


def test_judgment_survives_rescan_upsert(db):
    db.save_media(_media("/pics/a.png"))
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO media_judgments (file_path, grade, reasons) "
            "VALUES ('/pics/a.png', 'fail', '[\"light\"]')"
        )
        conn.commit()
    db.save_media(_media("/pics/a.png"))
    with db._get_connection() as conn:
        row = conn.execute("SELECT grade, reasons FROM media_judgments").fetchone()
        assert (row["grade"], row["reasons"]) == ("fail", '["light"]')
    assert db.get_judgment("/pics/a.png").grade == "fail"


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _events(db, p):
    return [e["event"] for e in db.list_judgment_events(p)]


def test_reason_requires_a_known_name(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "fail")
    with pytest.raises(ValueError, match="unknown_reason"):
        db.set_reason("/p/a.png", "vibes", True)


def test_reasons_keep_canonical_order(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "pass")
    db.set_reason("/p/a.png", "motion", True)
    db.set_reason("/p/a.png", "realism", True)
    assert db.get_judgment("/p/a.png").reasons == ["realism", "motion"]


def test_reason_toggle_is_idempotent(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "pass")
    db.set_reason("/p/a.png", "light", True)
    db.set_reason("/p/a.png", "light", True)
    db.set_reason("/p/a.png", "light", False)
    db.set_reason("/p/a.png", "light", False)
    assert _events(db, "/p/a.png") == ["grade_pass", "reason_on", "reason_off"]


def test_toggle_favorite_on_a_failed_item_keeps_grade_and_reasons(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "fail")
    db.set_reason("/p/a.png", "light", True)
    assert db.toggle_favorite(Path("/p/a.png")) is True
    s = db.get_judgment("/p/a.png")
    assert (s.favorite, s.grade, s.reasons) == (True, "fail", ["light"])
    assert db.toggle_favorite(Path("/p/a.png")) is False
    s = db.get_judgment("/p/a.png")
    assert (s.favorite, s.grade, s.reasons) == (False, "fail", ["light"])
    assert _events(db, "/p/a.png") == [
        "grade_fail",
        "reason_on",
        "favorite",
        "unfavorite",
    ]


def test_legacy_set_and_toggle_return_values(db):
    db.save_media(_media("/p/a.png"))
    assert db.set_favorite(Path("/p/a.png"), True) is True
    assert db.toggle_favorite(Path("/p/a.png")) is False
    assert db.get_judgment("/p/a.png").favorite is False
    # Un-starring an item without ★ is a successful no-op.
    assert db.set_favorite(Path("/p/a.png"), False) is True
    assert _events(db, "/p/a.png") == ["favorite", "unfavorite"]
    assert db.set_favorite(Path("/nope.png"), True) is False
    assert db.toggle_favorite(Path("/nope.png")) is False


def test_unjudged_item_seen_twice_logs_once(db):
    db.save_media(_media("/p/a.png"))
    db.mark_seen("/p/a.png", now=T0)
    db.mark_seen("/p/a.png", now=T0 + timedelta(days=3))
    assert _events(db, "/p/a.png") == ["seen"]


def test_score_shown_recorded(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "pass", score_shown=0.82)
    assert db.list_judgment_events("/p/a.png")[0]["score_shown"] == pytest.approx(0.82)


def test_missing_media_returns_none(db):
    assert db.set_grade("/nope.png", "pass") is None
    assert db.set_grade("/nope.png", None) is None
    assert db.set_favorite_state("/nope.png", True) is None
    assert db.mark_seen("/nope.png") is None
    assert db.get_judgment("/nope.png") is None
    assert db.set_reason("/nope.png", "light", True) is None
    assert db.list_judgment_events("/nope.png") == []


def test_unknown_sticky_category_raises(db):
    db.save_media(_media("/p/a.png"))
    with pytest.raises(ValueError, match="unknown_category"):
        db.set_grade("/p/a.png", "pass", sticky_category_id=999)
    assert db.get_judgment("/p/a.png").grade is None
    assert _events(db, "/p/a.png") == []


def test_path_with_spaces(db):
    db.save_media(_media("/p/my pics/a b.png"))
    assert db.set_grade("/p/my pics/a b.png", "pass").grade == "pass"
    assert db.set_favorite_state("/p/my pics/a b.png", True).favorite is True


def test_reason_on_a_rowless_favorite_is_no_grade(db):
    """A ★ that came in through the media upsert has no judgment row and
    no grade: a reason is refused and nothing is written."""
    m = _media("/p/a.png")
    m.is_favorite = True
    db.save_media(m)
    with pytest.raises(ValueError, match="no_grade"):
        db.set_reason("/p/a.png", "light", True)
    with db._get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media_judgments").fetchone()[0] == 0
    assert _events(db, "/p/a.png") == []
    s = db.get_judgment("/p/a.png")
    assert (s.favorite, s.grade, s.reasons) == (True, None, [])


# ----------------------------------------------------------------------
# Pass / fail grades: ★ favorite and the grade are independent
# ----------------------------------------------------------------------


def _row(db, p):
    with db._get_connection() as conn:
        r = conn.execute(
            "SELECT * FROM media_judgments WHERE file_path = ?", (p,)
        ).fetchone()
        return dict(r) if r is not None else None


def _is_favorite(db, p):
    with db._get_connection() as conn:
        return conn.execute(
            "SELECT is_favorite FROM media WHERE file_path = ?", (p,)
        ).fetchone()["is_favorite"]


def test_grade_and_favorite_are_independent(db):
    for p in ("/p/a.png", "/p/b.png"):
        db.save_media(_media(p))
    # ★ then ✗ keeps ★; clearing the grade keeps ★ too.
    assert db.set_favorite(Path("/p/a.png"), True) is True
    s = db.set_grade("/p/a.png", "fail", now=T0)
    assert (s.favorite, s.grade) == (True, "fail")
    s = db.set_grade("/p/a.png", None, now=T0)
    assert (s.favorite, s.grade) == (True, None)
    assert _events(db, "/p/a.png") == ["favorite", "grade_fail", "grade_clear"]
    # ✓ never sets ★, and ★ on / off never touches the grade, its reasons
    # or its timestamps.
    s = db.set_grade("/p/b.png", "pass", now=T0)
    assert (s.favorite, s.grade) == (False, "pass")
    assert _is_favorite(db, "/p/b.png") == 0
    db.set_reason("/p/b.png", "light", True, now=T0)
    before = _row(db, "/p/b.png")
    assert db.set_favorite(Path("/p/b.png"), True) is True
    assert db.set_favorite(Path("/p/b.png"), False) is True
    s = db.get_judgment("/p/b.png")
    assert (s.favorite, s.grade, s.reasons) == (False, "pass", ["light"])
    assert _row(db, "/p/b.png") == before
    assert _events(db, "/p/b.png") == [
        "grade_pass",
        "reason_on",
        "favorite",
        "unfavorite",
    ]


def test_favorite_alone_writes_no_judgment_row(db):
    db.save_media(_media("/p/a.png"))
    s = db.set_favorite_state("/p/a.png", True)
    assert (s.favorite, s.grade, s.reasons) == (True, None, [])
    assert (s.seen_at, s.judged_at, s.confirmed_at) == (None, None, None)
    assert _row(db, "/p/a.png") is None
    # The same ★ again changes nothing and logs nothing.
    db.set_favorite_state("/p/a.png", True)
    assert _events(db, "/p/a.png") == ["favorite"]
    # A favorite event never carries a score: ★ is not a rating.
    assert db.list_judgment_events("/p/a.png")[0]["score_shown"] is None


def test_grade_switch_clears_reasons_and_logs_in_order(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "pass", now=T0)
    s = db.set_reason("/p/a.png", "realism", True, now=T0)
    assert s.reasons == ["realism"]
    s = db.set_grade("/p/a.png", "fail", now=T0 + timedelta(minutes=1))
    assert (s.grade, s.reasons) == ("fail", [])
    assert _events(db, "/p/a.png") == ["grade_pass", "reason_on", "grade_fail"]
    assert s.judged_at == s.confirmed_at == (T0 + timedelta(minutes=1)).isoformat()


def test_same_grade_again_only_refreshes_confirmed_at(db):
    db.save_media(_media("/p/a.png"))
    db.set_grade("/p/a.png", "fail", now=T0)
    db.set_reason("/p/a.png", "light", True, now=T0)
    s = db.set_grade("/p/a.png", "fail", now=T0 + timedelta(days=2))
    assert _events(db, "/p/a.png") == ["grade_fail", "reason_on"]
    assert (s.grade, s.reasons) == ("fail", ["light"])
    assert s.judged_at == T0.isoformat()
    assert s.confirmed_at.startswith("2026-01-03")


def test_clearing_a_grade_logs_grade_clear_and_resets(db):
    db.save_media(_media("/p/a.png"))
    db.mark_seen("/p/a.png", now=T0)
    db.set_grade("/p/a.png", "pass", score_shown=0.4, now=T0)
    db.set_reason("/p/a.png", "light", True, now=T0)
    t1 = T0 + timedelta(hours=1)
    s = db.set_grade("/p/a.png", None, score_shown=0.6, now=t1)
    assert (s.grade, s.reasons, s.seen_at) == (None, [], T0.isoformat())
    assert s.judged_at == s.confirmed_at == t1.isoformat()
    events = db.list_judgment_events("/p/a.png")
    assert [e["event"] for e in events] == [
        "seen",
        "grade_pass",
        "reason_on",
        "grade_clear",
    ]
    assert events[1]["score_shown"] == pytest.approx(0.4)
    assert events[3]["score_shown"] == pytest.approx(0.6)
    # Clearing when there is no grade is a no-op.
    db.set_grade("/p/a.png", None, now=t1 + timedelta(hours=1))
    assert len(db.list_judgment_events("/p/a.png")) == 4


def test_grade_sets_seen_at_only_when_unset(db):
    db.save_media(_media("/p/a.png"))
    s = db.set_grade("/p/a.png", "pass", now=T0)
    assert s.seen_at == T0.isoformat()
    s = db.set_grade("/p/a.png", "fail", now=T0 + timedelta(days=1))
    assert s.seen_at == T0.isoformat()


def test_unknown_grade_value_is_refused(db):
    db.save_media(_media("/p/a.png"))
    with pytest.raises(ValueError, match="unknown_grade"):
        db.set_grade("/p/a.png", "favorite")
    assert db.list_judgment_events("/p/a.png") == []


def test_rapid_grade_sequences_end_consistent(db):
    """Review Focus 3: keys typed faster than the screen repaints, each
    toggling against the state the previous one left (P, X, X, F, P with a
    reason in between), end in one consistent state with every event in
    key order and the reason cleared by the grade switch."""
    db.save_media(_media("/p/a.png"))
    state = db.get_judgment("/p/a.png")
    step = [T0]

    def press(key):
        nonlocal state
        step[0] += timedelta(seconds=1)
        if key == "F":
            state = db.set_favorite_state("/p/a.png", not state.favorite)
            return
        target = {"P": "pass", "X": "fail"}[key]
        state = db.set_grade(
            "/p/a.png", None if state.grade == target else target, now=step[0]
        )

    press("P")
    state = db.set_reason("/p/a.png", "concept", True, now=step[0])
    press("X")  # switch: the reason described the ✓
    assert (state.grade, state.reasons) == ("fail", [])
    press("X")  # the key of the grade already set clears it
    assert state.grade is None
    press("F")  # ★ in between grade changes
    press("P")
    assert (state.favorite, state.grade, state.reasons) == (True, "pass", [])
    assert db.get_judgment("/p/a.png") == state
    assert _events(db, "/p/a.png") == [
        "grade_pass",
        "reason_on",
        "grade_fail",
        "grade_clear",
        "favorite",
        "grade_pass",
    ]


def test_reason_without_grade_raises_no_grade(db):
    db.save_media(_media("/p/a.png"))
    with pytest.raises(ValueError, match="no_grade"):
        db.set_reason("/p/a.png", "light", True)
    # ★ is not a grade.
    db.set_favorite(Path("/p/a.png"), True)
    with pytest.raises(ValueError, match="no_grade"):
        db.set_reason("/p/a.png", "light", True)
    assert _events(db, "/p/a.png") == ["favorite"]


def test_viewing_never_reconfirms(db):
    db.save_media(_media("/p/a.png"))
    db.mark_seen("/p/a.png", now=T0)
    db.set_grade("/p/a.png", "pass", now=T0)
    db.mark_seen("/p/a.png", now=T0 + timedelta(hours=25))
    db.mark_seen("/p/a.png", now=T0 + timedelta(days=3))
    assert _events(db, "/p/a.png") == ["seen", "grade_pass"]
    s = db.get_judgment("/p/a.png")
    assert s.confirmed_at == s.judged_at == T0.isoformat()
    assert s.seen_at == T0.isoformat()
    # Not even a graded row without a confirmed_at is filled by a view.
    with db._get_connection() as conn:
        conn.execute("UPDATE media_judgments SET confirmed_at = NULL")
        conn.commit()
    s = db.mark_seen("/p/a.png", now=T0 + timedelta(days=4))
    assert s.confirmed_at is None
    assert _events(db, "/p/a.png") == ["seen", "grade_pass"]


def test_judgment_state_dict_keys(db):
    db.save_media(_media("/p/a.png"))
    d = db.set_grade("/p/a.png", "pass", now=T0).to_dict()
    assert list(d) == [
        "file_path",
        "favorite",
        "grade",
        "reasons",
        "seen_at",
        "judged_at",
        "confirmed_at",
        "categories",
    ]
    assert (d["favorite"], d["grade"], d["judged_at"]) == (
        False,
        "pass",
        T0.isoformat(),
    )


def test_sticky_category_applies_on_grade_only(db):
    db.save_media(_media("/p/a.png"))
    db.save_media(_media("/p/b.png"))
    cid = db.create_category("Sci-Fi").id
    db.mark_seen("/p/a.png")
    db.set_favorite(Path("/p/a.png"), True)
    assert db.get_judgment("/p/a.png").categories == []
    # Clearing with a sticky category set links nothing.
    db.set_grade("/p/b.png", None, sticky_category_id=cid)
    assert db.get_judgment("/p/b.png").categories == []
    s = db.set_grade("/p/a.png", "fail", sticky_category_id=cid)
    assert [c.name for c in s.categories] == ["Sci-Fi"]
    assert _events(db, "/p/a.png")[-1] == "category_on"
    db.set_grade("/p/a.png", "pass", sticky_category_id=cid)
    assert _events(db, "/p/a.png").count("category_on") == 1


def test_t2i_and_i2v_rows_carry_the_grade(db):
    for p in ("/lib/a.png", "/lib/b.png", "/lib/src.png", "/lib/v.mp4"):
        db.save_media(_media(p))
    db.create_t2i_image(file_path="/lib/a.png", batch_id="b1")
    db.create_t2i_image(file_path="/lib/b.png", batch_id="b1")
    db.create_i2v_video(source_path="/lib/src.png", file_path="/lib/v.mp4")
    db.set_grade("/lib/a.png", "fail")
    db.set_grade("/lib/v.mp4", "pass")
    db.set_favorite(Path("/lib/b.png"), True)
    rows = {Path(r["file_path"]).name: r for r in db.list_t2i_images()}
    assert (rows["a.png"]["grade"], rows["a.png"]["is_favorite"]) == ("fail", False)
    assert (rows["b.png"]["grade"], rows["b.png"]["is_favorite"]) == (None, True)
    # The pre-grades reject flag is gone from the payload.
    assert "passed" not in rows["a.png"]
    (video,) = db.list_i2v_videos("/lib/src.png")
    assert (video["grade"], video["is_favorite"]) == ("pass", False)
