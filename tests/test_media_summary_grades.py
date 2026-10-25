"""The library summary (``GET /api/media``) carries each item's grade.

Grid tiles show a read-only ✓ / ✗ badge and the ``grade`` smart-folder rule
evaluates on the summary, so every row has ``grade`` (``"pass"`` /
``"fail"`` / None). The grade lives in ``media_judgments``, not on ``media``:
it comes from its own index-only read (``_SUMMARY_GRADES_SQL`` on the
covering ``idx_media_judgments_grade``) instead of a JOIN, so the media read
keeps the covering-index plan that makes ``/api/media`` fast (CLAUDE.md).
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from metascan.core import database_sqlite
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from tests._taste_api_fixtures import client, seeded  # noqa: F401
from tests.test_grades_migration import _build_v5_db, _judgment


@pytest.fixture
def db(tmp_path: Path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


def _add(db: DatabaseManager, path: str, minutes: int = 0) -> None:
    when = datetime(2026, 10, 1, 12, 0) + timedelta(minutes=minutes)
    db.save_media(
        Media(
            file_path=Path(path),
            file_size=1,
            width=8,
            height=8,
            format=Path(path).suffix.lstrip("."),
            created_at=when,
            modified_at=when,
        )
    )


def _grades(rows: List[Dict]) -> Dict[str, Optional[str]]:
    return {r["file_path"]: r["grade"] for r in rows}


def _plan(conn: sqlite3.Connection, sql: str) -> str:
    return " | ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql))


def test_summary_rows_carry_the_grade(db: DatabaseManager) -> None:
    for i, p in enumerate(
        (
            "/l/pass.png",
            "/l/fail.png",
            "/l/none.png",
            "/l/seen.png",
            "/l/star.png",
            "/l/cleared.png",
            "/l/clip.mp4",
        )
    ):
        _add(db, p, minutes=i)
    db.set_grade("/l/pass.png", "pass")
    db.set_grade("/l/fail.png", "fail")
    db.mark_seen("/l/seen.png")
    db.set_favorite(Path("/l/star.png"), True)
    db.set_grade("/l/cleared.png", "pass")
    db.set_grade("/l/cleared.png", None)
    # Videos keep collecting grades: their tiles show them too.
    db.set_grade("/l/clip.mp4", "fail")

    rows = db.get_all_media_summaries()
    assert _grades(rows) == {
        "/l/pass.png": "pass",
        "/l/fail.png": "fail",
        "/l/none.png": None,
        "/l/seen.png": None,
        "/l/star.png": None,
        "/l/cleared.png": None,
        "/l/clip.mp4": "fail",
    }
    by_path = {r["file_path"]: r for r in rows}
    # ★ and grade are independent: ★ alone is no grade, a grade sets no ★.
    assert by_path["/l/star.png"]["is_favorite"] is True
    assert by_path["/l/pass.png"]["is_favorite"] is False


def test_a_grade_change_shows_on_the_next_read(db: DatabaseManager) -> None:
    _add(db, "/l/a.png")
    db.set_grade("/l/a.png", "pass")
    assert _grades(db.get_all_media_summaries()) == {"/l/a.png": "pass"}
    db.set_grade("/l/a.png", "fail")
    assert _grades(db.get_all_media_summaries()) == {"/l/a.png": "fail"}
    db.set_favorite(Path("/l/a.png"), True)
    assert _grades(db.get_all_media_summaries()) == {"/l/a.png": "fail"}


def test_the_old_reject_flag_is_never_read_as_a_grade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``passed`` is the pre-grades reject flag: it must not show as ✓ or ✗.
    # v6 drops it; an SQLite too old to drop a column keeps it, so build that
    # database and plant the flag on every row.
    db_dir = tmp_path / "v5"
    _build_v5_db(db_dir, live={"/l/old.png": (0, _judgment(0, [], judged=False))})
    with monkeypatch.context() as m:
        m.setattr(database_sqlite, "_can_drop_columns", lambda: False)
        db = DatabaseManager(db_dir)
    _add(db, "/l/new.png", minutes=1)
    db.set_grade("/l/new.png", "pass")
    with db._get_connection() as conn:
        conn.execute("UPDATE media_judgments SET passed = 1")
        conn.commit()
    assert _grades(db.get_all_media_summaries()) == {
        "/l/old.png": None,
        "/l/new.png": "pass",
    }
    assert db.get_grade("/l/old.png") is None
    assert db.get_grade("/l/new.png") == "pass"


def test_grades_ride_every_sort_and_filter(db: DatabaseManager) -> None:
    _add(db, "/l/a.png", minutes=0)
    _add(db, "/l/b.png", minutes=1)
    _add(db, "/l/c.png", minutes=2)
    db.set_grade("/l/a.png", "fail")
    db.set_grade("/l/b.png", "pass")
    db.set_favorite(Path("/l/b.png"), True)
    db.set_media_hidden("/l/c.png", True)
    db.set_grade("/l/c.png", "pass")

    for sort in ("date_added", "date_modified", "file_name"):
        assert _grades(db.get_all_media_summaries(sort=sort)) == {
            "/l/a.png": "fail",
            "/l/b.png": "pass",
        }, sort
        assert _grades(db.get_all_media_summaries(sort=sort, include_hidden=True)) == {
            "/l/a.png": "fail",
            "/l/b.png": "pass",
            "/l/c.png": "pass",
        }, sort
        assert _grades(db.get_all_media_summaries(sort=sort, favorites_only=True)) == {
            "/l/b.png": "pass"
        }, sort
    # Order is unchanged by the grade read (newest added first).
    assert [
        r["file_path"] for r in db.get_all_media_summaries(include_hidden=True)
    ] == [
        "/l/c.png",
        "/l/b.png",
        "/l/a.png",
    ]


@pytest.mark.parametrize(
    "sort, index",
    [
        ("date_added", "idx_media_summary_added"),
        ("date_modified", "idx_media_summary_modified"),
        ("file_name", "idx_media_summary_added"),
    ],
)
@pytest.mark.parametrize("favorites_only", [False, True])
@pytest.mark.parametrize("include_hidden", [False, True])
def test_summary_reads_stay_on_covering_indexes(
    db: DatabaseManager,
    sort: str,
    index: str,
    favorites_only: bool,
    include_hidden: bool,
) -> None:
    # The media read is answered from its covering index alone and never
    # touches media_judgments (no JOIN: one probe per media row); the
    # grades come from the (grade, file_path) index alone, visiting graded
    # rows only.
    media_sql = DatabaseManager._media_summary_sql(
        favorites_only=favorites_only, sort=sort, include_hidden=include_hidden
    )
    with db._get_connection() as conn:
        media_plan = _plan(conn, media_sql)
        grades_plan = _plan(conn, DatabaseManager._SUMMARY_GRADES_SQL)
    assert f"COVERING INDEX {index}" in media_plan, media_plan
    assert "media_judgments" not in media_plan, media_plan
    assert "COVERING INDEX idx_media_judgments_grade" in grades_plan, grades_plan
    assert " media " not in f" {grades_plan} ", grades_plan


def test_get_grade(db: DatabaseManager) -> None:
    _add(db, "/l/a.png")
    _add(db, "/l/b.png", minutes=1)
    db.set_grade("/l/a.png", "fail")
    assert db.get_grade("/l/a.png") == "fail"
    assert db.get_grade("/l/b.png") is None
    assert db.get_grade("/l/missing.png") is None


def test_media_list_and_the_legacy_patch_carry_the_grade(
    client, seeded  # noqa: F811
) -> None:
    client.patch("/api/judgments//p/a.png", json={"grade": "fail"})
    rows = {r["file_path"]: r for r in client.get("/api/media").json()}
    assert (rows["/p/a.png"]["grade"], rows["/p/b.png"]["grade"]) == ("fail", None)

    # The legacy ★ PATCH answers with a summary row: the grade rides along
    # (the grid replaces its row with it) and ★ never changes it.
    r = client.patch("/api/media//p/a.png", json={"is_favorite": True})
    assert r.status_code == 200
    assert (r.json()["is_favorite"], r.json()["grade"]) == (True, "fail")
    r = client.patch("/api/media//p/b.png", json={"playback_speed": 1.5})
    assert r.status_code == 200 and r.json()["grade"] is None
    # Same keys as a list row (the list row also has ``hidden``).
    assert "grade" in r.json() and set(r.json()) <= set(rows["/p/b.png"])

    rows = {r["file_path"]: r for r in client.get("/api/media").json()}
    assert (rows["/p/a.png"]["is_favorite"], rows["/p/a.png"]["grade"]) == (
        True,
        "fail",
    )


def test_the_legacy_patch_leaves_out_a_grade_it_cannot_read(
    client, seeded, monkeypatch, caplog  # noqa: F811
) -> None:
    # A failed read is not "no grade": answering grade null would clear the
    # item's real ✗ badge (the grid merges the answer over its row), so the
    # key is left out, ★ still applies, and the failure is logged.
    import backend.dependencies as deps

    client.patch("/api/judgments//p/a.png", json={"grade": "fail"})

    def broken(file_path: str) -> None:
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as m:
        m.setattr(deps._db_singleton, "get_grade", broken)
        r = client.patch("/api/media//p/a.png", json={"is_favorite": True})
    assert r.status_code == 200
    assert r.json()["is_favorite"] is True and "grade" not in r.json()
    assert any("Could not read the grade" in rec.getMessage() for rec in caplog.records)
    rows = {r["file_path"]: r for r in client.get("/api/media").json()}
    assert (rows["/p/a.png"]["is_favorite"], rows["/p/a.png"]["grade"]) == (
        True,
        "fail",
    )
