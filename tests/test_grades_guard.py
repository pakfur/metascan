"""Guards for the pass / fail grades split (spec
docs/superpowers/specs/2026-10-24-pass-fail-grades-design.md, "Testing").

Before v6 one marker did two jobs, and its old words mean the opposite of
the new ✓: every ``passed`` column and every ``pass`` / ``unpass`` event is
the old *reject*. These tests keep live code from reading them as a grade:

* No code in ``metascan/`` or ``backend/`` names ``passed`` as a SQL column,
  a dict key, a keyword or an attribute, except the v6 migration (which
  maps it to ✗ and then drops it), the placeholder an SQLite too old to
  drop it still needs, and the translation of a full-clean snapshot written
  before grades. The frontend reads no ``passed`` field either. Prose --
  comments, docstrings, user-facing text -- is not code and is not checked.
* No code reads the old ``pass`` / ``unpass`` events as ✓. The one legacy
  mapping is ``LEGACY_GRADE_EVENTS`` (the score_shown of a migrated grade),
  where the old "pass" stands for ✗; the old events never count toward a
  retrain. Every reader agrees on an old reject history.
* The v6 migration drops both ``passed`` columns (SQLite >= 3.35.5). An
  older SQLite keeps them, and nothing reads them.
"""

import ast
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

import pytest

from metascan.core import database_sqlite
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.judgments import LEGACY_REJECT_EVENTS
from metascan.core.taste_labels import (
    GRADE_EVENT_KINDS,
    LEGACY_GRADE_EVENTS,
    TRAINING_EVENT_KINDS,
    build_label_set,
)
from tests.test_grades_migration import (
    EXPECTED_GRADES,
    EXPECTED_TOMB_GRADES,
    T_JUDGED,
    T_SEEN,
    _build_v5_db,
    _judgment,
    _media,
    _query,
    _user_version,
)

ROOT = Path(__file__).resolve().parent.parent
LIVE_CODE = ("metascan", "backend")

# ---------------------------------------------------------------------------
# A small AST scanner: every node with the name of the function, class or
# assigned constant it sits in ("DatabaseManager._migrate_to_v6",
# "LEGACY_REJECT_EVENTS").
# ---------------------------------------------------------------------------

Check = Callable[[ast.AST, Set[int]], Optional[str]]


def _walk(node: ast.AST, scope: Tuple[str, ...] = ()) -> Iterator[Tuple[ast.AST, str]]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        scope = scope + (node.name,)
    elif (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        scope = scope + (node.targets[0].id,)
    elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        scope = scope + (node.target.id,)
    yield node, ".".join(scope)
    for child in ast.iter_child_nodes(node):
        yield from _walk(child, scope)


def _prose(tree: ast.AST) -> Set[int]:
    """The string constants that are prose, not code: docstrings and any
    other bare string statement."""
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _hits(source: str, check: Check) -> List[Tuple[int, str, str]]:
    tree = ast.parse(source)
    prose = _prose(tree)
    out = []
    for node, scope in _walk(tree):
        what = check(node, prose)
        if what:
            out.append((getattr(node, "lineno", 0), scope, what))
    return out


def _allowed(path: str, scope: str, allowed: Dict[Tuple[str, str], str]) -> bool:
    return any(
        path == where and (scope == name or scope.startswith(name + "."))
        for where, name in allowed
    )


def _scan(check: Check, allowed: Dict[Tuple[str, str], str]) -> List[str]:
    found = []
    for root in LIVE_CODE:
        for file in sorted((ROOT / root).rglob("*.py")):
            path = file.relative_to(ROOT).as_posix()
            for line, scope, what in _hits(file.read_text(encoding="utf-8"), check):
                if not _allowed(path, scope, allowed):
                    found.append(f"{path}:{line} ({scope or 'module'}): {what}")
    return found


# ---------------------------------------------------------------------------
# The old reject flag ``passed``
# ---------------------------------------------------------------------------

# Where code may still name the old reject flag, and why.
PASSED_ALLOWED = {
    ("metascan/core/database_sqlite.py", "DatabaseManager._migrate_to_v6"): (
        "the v6 migration: maps the old reject flag to ✗, then drops it"
    ),
    ("metascan/core/database_sqlite.py", "_legacy_tombstone_fill"): (
        "an SQLite too old to drop it keeps media_tombstones.passed, NOT NULL: "
        "a new tombstone gives it a constant placeholder, never read"
    ),
    ("backend/services/taste_snapshot.py", "upgrade_legacy_snapshot"): (
        "a full-clean snapshot written before grades: passed = 1 is ✗"
    ),
    ("backend/services/taste_snapshot.py", "_valid_judgment"): (
        "validates such a snapshot"
    ),
}

_SQL_WORD = re.compile(
    r"\b(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|FROM|WHERE|JOIN|"
    r"VALUES|PRAGMA|SET|INTO)\b"
)
# ``passed`` as an identifier in a column list or an expression.
_SQL_IDENT = re.compile(r"[.(,]\s*passed\b|\bpassed\s*[)=!<>]")
_PASSED = re.compile(r"\bpassed\b")


def _passed_use(node: ast.AST, prose: Set[int]) -> Optional[str]:
    if isinstance(node, ast.Attribute) and node.attr == "passed":
        return "reads the attribute .passed"
    if isinstance(node, ast.keyword) and node.arg == "passed":
        return "passes the keyword passed="
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in prose
    ):
        text = node.value
        if text.strip() == "passed":
            return 'uses the key "passed"'
        if _PASSED.search(text) and (_SQL_WORD.search(text) or _SQL_IDENT.search(text)):
            return "names the column passed in SQL"
    return None


def test_no_live_code_reads_passed():
    assert _scan(_passed_use, PASSED_ALLOWED) == []


_PASSED_FORMS = """
def f(d, row, conn, v):
    a = d["passed"]
    b = row.passed
    g(passed=1)
    conn.execute("SELECT passed FROM media_judgments")
    conn.execute(f"UPDATE media_judgments SET grade = ?, passed = ? WHERE x = {v}")
    cols = "file_path, passed, grade"
    h = {"passed": 1}
"""

_PASSED_PROSE = '''
"""Images that passed review."""
# A comment: passed.


def f():
    """Only fields passed as non-None are written."""
    passed = True
    return passed, "no passed images with a known prompt match this caption"
'''


def test_the_passed_scanner_sees_every_form_and_skips_prose():
    found = [what for _, _, what in _hits(_PASSED_FORMS, _passed_use)]
    assert found == [
        'uses the key "passed"',
        "reads the attribute .passed",
        "passes the keyword passed=",
        "names the column passed in SQL",
        "names the column passed in SQL",
        "names the column passed in SQL",
        'uses the key "passed"',
    ]
    assert _hits(_PASSED_PROSE, _passed_use) == []
    # The allow-list is by enclosing name: a nested helper is covered.
    [(_, scope, _)] = _hits(
        "class C:\n    def m(self, d):\n        return d['passed']\n", _passed_use
    )
    assert scope == "C.m"
    assert _allowed("x.py", "C.m.inner", {("x.py", "C.m"): ""})
    assert not _allowed("x.py", "C.mx", {("x.py", "C.m"): ""})


# The T2I payload's ``passed`` mirror is gone: no frontend code reads or
# declares the field (prose such as "Hindsight briefs (passed)" is copy).
_FRONTEND_PASSED = re.compile(r"\.passed\b|\bpassed\??\s*:|['\"`]passed['\"`]")


def test_the_frontend_reads_no_passed_field():
    found = []
    for file in sorted((ROOT / "frontend" / "src").rglob("*")):
        if file.suffix not in (".ts", ".vue"):
            continue
        for n, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if _FRONTEND_PASSED.search(line):
                found.append(f"{file.relative_to(ROOT).as_posix()}:{n}: {line.strip()}")
    assert found == []


# ---------------------------------------------------------------------------
# The old reject events "pass" / "unpass"
# ---------------------------------------------------------------------------

# Where code may still name an old reject event, and why. (The other
# legacy mapping, LEGACY_GRADE_EVENTS, names "pass" only as the event a ✗
# came from, which nothing here flags.)
EVENT_ALLOWED = {
    ("metascan/core/judgments.py", "LEGACY_REJECT_EVENTS"): (
        "the definition: the old events stay in the log, never read as a grade"
    ),
}

_LEGACY = {"pass", "unpass"}
_EVENT_SQL = re.compile(
    r"\bevent\s*(?:==?|!=|<>)\s*'(?:un)?pass'"
    r"|\bevent\s+(?:NOT\s+)?IN\s*\([^)]*'(?:un)?pass'",
    re.IGNORECASE,
)


def _names_legacy(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value in _LEGACY
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_names_legacy(e) for e in node.elts)
    return False


def _callee(node: ast.Call) -> str:
    f = node.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")


def _event_use(node: ast.AST, prose: Set[int]) -> Optional[str]:
    if isinstance(node, ast.Constant) and id(node) not in prose:
        if node.value == "unpass":
            return 'names the old reject event "unpass"'
        if isinstance(node.value, str) and _EVENT_SQL.search(node.value):
            return "compares an event to an old reject event in SQL"
    if (
        isinstance(node, ast.Call)
        and _callee(node) == "_log_judgment_event"
        and any(_names_legacy(a) for a in node.args)
    ):
        return "logs an old reject event"
    if (
        isinstance(node, ast.Compare)
        and "event" in ast.unparse(node)
        and any(_names_legacy(x) for x in [node.left, *node.comparators])
    ):
        return "compares an event to an old reject event"
    return None


def test_no_code_treats_pass_event_as_grade():
    assert _scan(_event_use, EVENT_ALLOWED) == []
    # The one legacy mapping: a migrated ✓ takes the score_shown of the old
    # favorite that set it, a migrated ✗ that of the old reject "pass".
    assert LEGACY_GRADE_EVENTS == {"pass": "favorite", "fail": "pass"}
    assert LEGACY_REJECT_EVENTS == ("pass", "unpass")
    # The old events are neither grade events nor retrain events.
    assert not set(LEGACY_REJECT_EVENTS) & set(GRADE_EVENT_KINDS)
    assert not set(LEGACY_REJECT_EVENTS) & set(TRAINING_EVENT_KINDS)


_EVENT_FORMS = """
def f(db, conn, e, row):
    conn.execute("SELECT * FROM judgment_events WHERE event = 'pass'")
    conn.execute("SELECT 1 FROM judgment_events e WHERE e.event IN ('favorite', 'pass')")
    db._log_judgment_event(conn, "/p", "pass", "t", None)
    if row["event"] == "pass":
        return "pass"
    if e.event in ("unpass", "seen"):
        return None
"""

_EVENT_FINE = '''
"""A docstring may say pass and unpass."""


def f(grade, e):
    if grade == "pass":
        return {"pass": "grade_pass"}[grade]
    return "CASE grade WHEN 'pass' THEN :ev_legacy_favorite END", e.event == "seen"
'''


def test_the_event_scanner_sees_every_form():
    found = [what for _, _, what in _hits(_EVENT_FORMS, _event_use)]
    assert found == [
        "compares an event to an old reject event in SQL",
        "compares an event to an old reject event in SQL",
        "logs an old reject event",
        "compares an event to an old reject event",
        "compares an event to an old reject event",
        'names the old reject event "unpass"',
    ]
    assert _hits(_EVENT_FINE, _event_use) == []


# Old reject histories as v5 left them, (★, judgment row) and events: a
# reject withdrawn (no grade), a reject (✗), a reject later favorited (✓),
# and a reject that is deleted after the upgrade. Every old reject event
# carried a score, so a reader taking "pass" for ✓ would halve the ✓.
_HISTORIES: Dict[str, Any] = {
    "/g/withdrawn.png": (0, _judgment(0, [])),
    "/g/rejected.png": (0, _judgment(1, ["light"])),
    "/g/kept.png": (1, _judgment(0, [])),
    "/g/gone.png": (0, _judgment(1, [])),
}
_HISTORY_EVENTS = [
    ("/g/withdrawn.png", "pass", 0.9, T_SEEN),
    ("/g/withdrawn.png", "unpass", 0.9, T_JUDGED),
    ("/g/rejected.png", "pass", 0.8, T_JUDGED),
    ("/g/kept.png", "pass", 0.7, T_SEEN),
    ("/g/kept.png", "unpass", None, T_JUDGED),
    ("/g/kept.png", "favorite", None, T_JUDGED),
    ("/g/gone.png", "pass", 0.6, T_JUDGED),
]
_GRADES = {
    "/g/withdrawn.png": None,
    "/g/rejected.png": "fail",
    "/g/kept.png": "pass",
    "/g/gone.png": "fail",
}


def test_an_old_reject_history_never_reads_as_a_pass(tmp_path):
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir, live=_HISTORIES)
    conn = sqlite3.connect(str(db_file))
    conn.executemany(
        "INSERT INTO judgment_events (file_path, event, score_shown, created_at) "
        "VALUES (?, ?, ?, ?)",
        _HISTORY_EVENTS,
    )
    conn.commit()
    conn.close()
    db = DatabaseManager(db_dir)
    for path in _HISTORIES:
        db.create_t2i_image(file_path=path, batch_id="b1", writer_id="base")
    assert db.delete_media(Path("/g/gone.png"))
    live = {p: g for p, g in _GRADES.items() if p != "/g/gone.png"}

    # Judgments, the library grid and the T2I strip.
    assert {p: db.get_judgment(p).grade for p in live} == live
    assert {r["file_path"]: r["grade"] for r in db.get_all_media_summaries()} == live
    rows = db.list_t2i_images()
    assert {r["file_path"]: r["grade"] for r in rows} == live
    assert not [r for r in rows if "passed" in r]
    # The taste scorer: labels, and the score_shown halving of the grade
    # the old event really set (the old reject "pass" halves a ✗ only).
    raw = db.taste_training_raw()
    assert {
        m["file_path"]: (m["grade"], m["grade_score_shown"]) for m in raw["media"]
    } == {
        "/g/withdrawn.png": (None, None),
        "/g/rejected.png": ("fail", 0.8),
        "/g/kept.png": ("pass", None),
    }
    assert [
        (t["file_path"], t["grade"], t["grade_score_shown"]) for t in raw["tombstones"]
    ] == [("/g/gone.png", "fail", 0.6)]
    labels = build_label_set(raw, datetime.fromisoformat(T_JUDGED), 180)
    assert {r.file_path: r.label for r in labels.rows} == {
        "/g/rejected.png": 0,
        "/g/kept.png": 1,
        "/g/gone.png": 0,
    }
    # The old events never count toward a retrain.
    assert db.count_training_events_since(None) == (0, None)
    # Writer stats and the full-clean snapshot.
    assert {o["file_path"]: o["grade"] for o in db.generation_outcomes()} == _GRADES
    snap = db.export_taste_state()
    assert {j["file_path"]: j["grade"] for j in snap["judgments"]} == live
    assert "passed" not in json.dumps(snap)


# ---------------------------------------------------------------------------
# The v6 migration drops the legacy columns
# ---------------------------------------------------------------------------


def _columns(db_file: Path, table: str) -> Set[str]:
    return {r[1] for r in _query(db_file, f"PRAGMA table_info({table})")}


def _bak(db_dir: Path) -> Path:
    [backup] = sorted(db_dir.glob("metascan.db.pre-v6-*.bak"))
    return backup


def _drop_safe() -> bool:
    return sqlite3.sqlite_version_info >= (3, 35, 5)


def test_drop_column_needs_sqlite_3_35_5():
    # 3.35.0 added DROP COLUMN; 3.35.5 fixed defects in it that could
    # corrupt the database file.
    assert database_sqlite.DROP_COLUMN_MIN_SQLITE == (3, 35, 5)
    assert database_sqlite._can_drop_columns() is _drop_safe()


@pytest.mark.skipif(not _drop_safe(), reason="this SQLite cannot drop a column")
def test_v6_drops_legacy_columns(tmp_path):
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    for table in ("media_judgments", "media_tombstones"):
        assert "passed" in _columns(db_file, table)

    db = DatabaseManager(db_dir)
    assert _user_version(db_file) == 6
    for table in ("media_judgments", "media_tombstones"):
        assert "passed" not in _columns(db_file, table)
        # The backup is the v5 database as it was, old columns included.
        assert "passed" in _columns(_bak(db_dir), table)
    # Read before they went: the old rejects are ✗, the favorites ✓.
    assert dict(_query(db_file, "SELECT file_path, grade FROM media_judgments")) == (
        EXPECTED_GRADES
    )
    assert dict(_query(db_file, "SELECT file_path, grade FROM media_tombstones")) == (
        EXPECTED_TOMB_GRADES
    )
    # Every write still works without them: a grade, ★, a reason, a delete
    # (tombstone) and a full-clean put-back.
    db.save_media(_media("/lib/new.png"))
    db.set_grade("/lib/new.png", "fail")
    db.set_reason("/lib/new.png", "light", True)
    db.set_favorite(Path("/lib/new.png"), True)
    assert db.delete_media(Path("/lib/new.png"))
    assert [
        (t["grade"], t["reasons"])
        for t in db.list_tombstones()
        if t["file_path"] == "/lib/new.png"
    ] == [("fail", '["light"]')]
    snap = db.export_taste_state()
    db.truncate_all_data()
    db.save_media(_media("/lib/reject.png"))
    db.restore_taste_state(snap)
    assert db.get_judgment("/lib/reject.png").grade == "fail"

    # A second open finds nothing left to drop; a new database never had
    # the columns.
    DatabaseManager(db_dir)
    DatabaseManager(tmp_path / "fresh")
    for table in ("media_judgments", "media_tombstones"):
        assert "passed" not in _columns(tmp_path / "fresh" / "metascan.db", table)


def test_an_old_sqlite_keeps_the_legacy_columns_unread(tmp_path, monkeypatch):
    """An SQLite older than 3.35.5 cannot drop a column safely: v6 maps the
    grades and leaves both columns in place. Nothing reads them -- the old
    reject flag planted on every row changes no answer -- and every write
    still works (media_judgments.passed defaults to 0; media_tombstones'
    copy is NOT NULL with no default, so a tombstone fills it with 0)."""
    db_dir = tmp_path / "db"
    db_file = _build_v5_db(db_dir)
    with monkeypatch.context() as m:
        m.setattr(database_sqlite, "_can_drop_columns", lambda: False)
        db = DatabaseManager(db_dir)
        DatabaseManager(db_dir)  # and every later open, too
    assert _user_version(db_file) == 6
    for table in ("media_judgments", "media_tombstones"):
        assert "passed" in _columns(db_file, table)
    assert dict(_query(db_file, "SELECT file_path, grade FROM media_judgments")) == (
        EXPECTED_GRADES
    )

    # Writes: a new grade, a delete, a full-clean put-back.
    db.save_media(_media("/lib/new.png"))
    db.set_grade("/lib/new.png", "pass")
    assert db.delete_media(Path("/lib/seen.png"))
    assert db.delete_media(Path("/lib/reject.png"))
    tombs = {t["file_path"]: t for t in db.list_tombstones()}
    assert (tombs["/lib/reject.png"]["grade"], tombs["/lib/reject.png"]["passed"]) == (
        "fail",
        0,
    )
    snap = db.export_taste_state()
    assert "passed" not in json.dumps(snap)

    # The old reject flag on every row: no answer changes.
    with db._get_connection() as conn:
        conn.execute("UPDATE media_judgments SET passed = 1")
        conn.execute("UPDATE media_tombstones SET passed = 1")
        conn.commit()
    graded = {**EXPECTED_GRADES, "/lib/new.png": "pass"}
    del graded["/lib/seen.png"], graded["/lib/reject.png"]
    summaries = {r["file_path"]: r["grade"] for r in db.get_all_media_summaries()}
    assert {p: summaries[p] for p in graded} == graded
    assert {p: db.get_judgment(p).grade for p in graded} == graded
    assert {p: db.get_grade(p) for p in graded} == graded
    raw = db.taste_training_raw()
    assert {m["file_path"]: m["grade"] for m in raw["media"]} == {
        **graded,
        "/lib/plain.png": None,
    }
    assert {t["file_path"]: t["grade"] for t in raw["tombstones"]} == {
        p: g
        for p, g in {
            **EXPECTED_TOMB_GRADES,
            "/lib/reject.png": "fail",
        }.items()
        if g is not None
    }
    assert {
        j["file_path"]: j["grade"] for j in db.export_taste_state()["judgments"]
    } == graded

    db.truncate_all_data()
    db.save_media(_media("/lib/new.png"))
    db.restore_taste_state(snap)
    assert db.get_judgment("/lib/new.png").grade == "pass"
