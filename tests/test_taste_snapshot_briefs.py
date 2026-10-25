"""A full clean & rescan keeps what a vanished ✓ image still teaches the
prompt writer (taste snapshot version 3).

``truncate_all_data`` cascades ``hindsight_briefs`` away, and a completed
full clean tombstones every snapshot path that did not come back. So the
snapshot carries every hindsight brief, and each ✓ library image's metadata
prompt and model names, or a passed image whose file vanished would lose its
hindsight example for good: it was not deleted through the app, so there is
no archived thumbnail to brief it again from. A delete keeps all four on its
tombstone (``_write_tombstones``); a completed full clean writes the same
row from the snapshot."""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from backend.api import scan as scan_api
from backend.services import taste_snapshot as ts
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.prompt_store import PromptStore
from metascan.core.writer_dataset import examples_from_raw
from tests.test_taste_snapshot_pending import _assert_quarantined
from tests.test_taste_snapshot_scan import _pending, _wire
from tests.test_writer_dataset import _YAML

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 9, 2, 8, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)

LIB = "/lib/pass.png"  # ★ + ✓ library image: brief, prompt, model names, a category
GEN = "/gen/pass.png"  # ✓ T2I image: brief (its prompt is in its lineage)
FAILED = "/lib/fail.png"  # ✗, briefed while it was ✓: brief, no prompt
SEEN = "/lib/seen.png"  # viewed only: neither
VIDEO = "/lib/clip.mp4"  # ✓ library video: no prompt (images only), no brief
BACK = "/lib/back.png"  # ✓ library image whose file comes back
VANISHED = [FAILED, LIB, SEEN, VIDEO, GEN]
# Every file but LIB, FAILED and SEEN is something a tombstone keeps no
# prompt for, yet each carries a metadata prompt and model names: GEN as
# ComfyUI embeds them in a render, VIDEO as a scan reads them.

# Inserted unsorted: a tombstone keeps them as a sorted JSON list.
LIB_MODELS = ("vae_ft.safetensors", "krea2_dev.safetensors")
MODEL_NAME = json.dumps(sorted(LIB_MODELS))
BRIEF_MODEL = "qwen3vl-8b"

# Every brief and prompt is a secret the log must never see.
BRIEF = {
    LIB: "SECRET-LIB-BRIEF a fox in the snow",
    GEN: "SECRET-GEN-BRIEF a hare at night",
    FAILED: "SECRET-FAILED-BRIEF a cat on a sofa",
    BACK: "SECRET-BACK-BRIEF an owl on a branch",
}
PROMPT = {
    LIB: "SECRET-LIB-PROMPT fox, snow, dusk light",
    GEN: "SECRET-GEN-PROMPT hare, moonlight",
    FAILED: "SECRET-FAILED-PROMPT cat, sofa",
    SEEN: "SECRET-SEEN-PROMPT a lighthouse",
    VIDEO: "SECRET-VIDEO-PROMPT an owl takes off",
    BACK: "SECRET-BACK-PROMPT owl, branch",
}
SECRETS = sorted(set(BRIEF.values()) | set(PROMPT.values()))


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


def _marks(state):
    return (state.favorite, state.grade)


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture
def store(tmp_path):
    p = tmp_path / "meta_prompt.yml"
    p.write_text(_YAML, encoding="utf-8")
    return PromptStore(p)


def _import_file(db: DatabaseManager, path: str) -> None:
    """(Re-)import a file the way a scan does: with the metadata prompt and
    model names it carries."""
    db.save_media(_media(path))
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE media SET data = json_set(data, '$.prompt', ?) "
            "WHERE file_path = ?",
            (PROMPT[path], path),
        )
        conn.executemany(
            "INSERT OR IGNORE INTO indices (index_type, index_key, file_path) "
            "VALUES ('model', ?, ?)",
            [(m, path) for m in LIB_MODELS],
        )
        conn.commit()


def _seed(db: DatabaseManager) -> None:
    _import_file(db, LIB)
    db.set_favorite_state(LIB, True, now=T0)
    db.set_grade(LIB, "pass", now=T0)
    db.set_reason(LIB, "light", True, now=T0)
    night = db.create_category("Night")
    db.add_media_categories([LIB], night.id, "viewer")
    db.set_hindsight_brief(LIB, BRIEF[LIB], BRIEF_MODEL, now=T0)

    _import_file(db, GEN)
    image_id = db.create_t2i_image(
        file_path=GEN,
        model="krea2",
        caption="a hare at night",
        prompt_used=PROMPT[GEN],
        writer_id="base",
        writer_model=BRIEF_MODEL,
    )
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE t2i_images SET created_at = ? WHERE id = ?",
            ("2026-08-31 09:00:00", image_id),
        )
        conn.commit()
    db.set_grade(GEN, "pass", now=T0)
    db.set_hindsight_brief(GEN, BRIEF[GEN], BRIEF_MODEL, now=T0)

    _import_file(db, FAILED)
    db.set_grade(FAILED, "pass", now=T0)
    db.set_hindsight_brief(FAILED, BRIEF[FAILED], BRIEF_MODEL, now=T0)
    db.set_grade(FAILED, "fail", now=T1)

    _import_file(db, SEEN)
    db.mark_seen(SEEN, now=T0)

    _import_file(db, VIDEO)
    db.set_grade(VIDEO, "pass", now=T0)

    _import_file(db, BACK)
    db.set_grade(BACK, "pass", now=T1)
    db.set_hindsight_brief(BACK, BRIEF[BACK], BRIEF_MODEL, now=T1)


def _brief_row(path: str, now: datetime = T0) -> Dict[str, Any]:
    return {"brief": BRIEF[path], "model": BRIEF_MODEL, "created_at": now.isoformat()}


def _hindsight(db: DatabaseManager, store: PromptStore):
    """Every hindsight example, from ``writer_dataset_raw`` through
    ``examples_from_raw``."""
    raw = db.writer_dataset_raw()
    sft, _, counts = examples_from_raw(raw, "all", store, LATER, content_mode="default")
    return counts.hindsight, sorted(
        (e.chosen, e.user, e.target_model, e.group, e.as_of)
        for e in sft
        if e.source == "hindsight"
    )


def _rescanner(db: DatabaseManager, then: Optional[str] = None):
    """A scanner that re-imports BACK as a real scan would, then cancels
    (the user hits Cancel during the last directory) or fails."""

    class Scanner:
        def __init__(self, *a, **k):
            pass

        def scan_directory(self, *a, **k):
            _import_file(db, BACK)
            if then == "cancel":
                scan_api._cancel_requested = True
            elif then == "error":
                raise RuntimeError("scanner failed")
            return 1

    return Scanner


def _full_clean(monkeypatch, db: DatabaseManager, then: Optional[str] = None):
    events = _wire(monkeypatch, db, [], scanner_cls=_rescanner(db, then))
    try:
        asyncio.run(scan_api._run_scan(full_cleanup=False, full_clean=True))
    finally:
        scan_api._cancel_requested = False
    return events


def _tombstones(db: DatabaseManager) -> Dict[str, Dict[str, Any]]:
    return {t["file_path"]: t for t in db.list_tombstones()}


def _paths(entries: List[Dict[str, Any]]) -> List[str]:
    return sorted(e["file_path"] for e in entries)


# ----------------------------------------------------------------------
# The snapshot
# ----------------------------------------------------------------------


def test_export_carries_every_brief_and_each_pass_library_prompt(db):
    """Version 3: every hindsight brief as stored, and each ✓ library
    image's metadata prompt and model names exactly as a delete keeps them
    on its tombstone (``_library_prompt``) -- not a generation's (its
    lineage has its prompt), a video's, a ✗ or a viewed image's, though
    each of those has a metadata prompt and model names too."""
    _seed(db)
    with db._get_connection() as conn:
        kept = {p: db._library_prompt(conn, p) for p in sorted(PROMPT)}

    snap = json.loads(json.dumps(db.export_taste_state()))

    assert snap["version"] == 3
    assert snap["briefs"] == [
        {"file_path": GEN, **_brief_row(GEN)},
        {"file_path": BACK, **_brief_row(BACK, T1)},
        {"file_path": FAILED, **_brief_row(FAILED)},
        {"file_path": LIB, **_brief_row(LIB)},
    ]
    assert snap["library_prompts"] == [
        {"file_path": BACK, "prompt": PROMPT[BACK], "model_name": MODEL_NAME},
        {"file_path": LIB, "prompt": PROMPT[LIB], "model_name": MODEL_NAME},
    ]
    assert kept == {p: (PROMPT[p], MODEL_NAME) for p in PROMPT}
    # The pending file reads back exactly as written.
    data_dir = Path(db.db_path)
    ts.write_snapshot_file(data_dir, snap)
    assert ts.load_pending_snapshot(data_dir) == snap


# ----------------------------------------------------------------------
# A completed full clean
# ----------------------------------------------------------------------


def test_completed_full_clean_keeps_briefs_and_prompts_of_vanished_images(
    monkeypatch, db, store
):
    """A ✓ library image and a ✓ T2I image whose files vanished keep their
    brief, brief model, and (library) prompt and model names on the
    tombstones a completed full clean writes, so they still make the same
    hindsight examples. The image that came back gets its brief back."""
    _seed(db)
    before = _hindsight(db, store)
    assert before[0] == 3  # LIB, GEN and BACK

    _full_clean(monkeypatch, db)

    tombs = _tombstones(db)
    assert sorted(tombs) == sorted(VANISHED)
    lib = tombs[LIB]
    assert (
        lib["is_favorite"],
        lib["grade"],
        lib["brief"],
        lib["brief_model"],
        lib["prompt"],
        lib["model_name"],
        lib["lineage"],
    ) == (1, "pass", BRIEF[LIB], BRIEF_MODEL, PROMPT[LIB], MODEL_NAME, None)
    assert json.loads(lib["categories"]) == ["Night"]
    gen = tombs[GEN]
    assert (gen["grade"], gen["brief"], gen["brief_model"]) == (
        "pass",
        BRIEF[GEN],
        BRIEF_MODEL,
    )
    assert gen["prompt"] is None and gen["model_name"] is None
    assert gen["lineage"]["t2i"]["prompt_used"] == PROMPT[GEN]
    failed = tombs[FAILED]
    assert (failed["grade"], failed["brief"], failed["brief_model"]) == (
        "fail",
        BRIEF[FAILED],
        BRIEF_MODEL,
    )
    assert failed["prompt"] is None and failed["model_name"] is None
    seen = tombs[SEEN]
    assert (seen["grade"], seen["brief"], seen["prompt"], seen["model_name"]) == (
        None,
        None,
        None,
        None,
    )
    # A ✓ video keeps its grade, never a prompt: hindsight is images only.
    video = tombs[VIDEO]
    assert (video["grade"], video["brief"], video["prompt"], video["model_name"]) == (
        "pass",
        None,
        None,
        None,
    )

    assert db.get_hindsight_briefs() == {BACK: _brief_row(BACK, T1)}
    assert _hindsight(db, store) == before
    assert _pending(db) is None


def test_a_full_clean_tombstone_is_the_one_a_delete_writes(monkeypatch, tmp_path):
    """The same library, its files gone two ways: deleted through the app,
    or vanished before a completed full clean. Both write the same
    tombstones (no thumbnail either way: the DB delete was given none) but
    for the FAISS evidence: a delete reads it from the file's media_hashes
    row ('' = never embedded), while the full clean's truncate took that
    row, so its tombstones carry none (NULL: the old guards, no capture)."""
    evidence = ("embedded_model", "embedded_at", "path_rewritten")
    deleted = DatabaseManager(tmp_path / "deleted")
    cleaned = DatabaseManager(tmp_path / "cleaned")
    try:
        for db in (deleted, cleaned):
            _seed(db)
        for path in VANISHED:
            assert deleted.delete_media(Path(path))
        _full_clean(monkeypatch, cleaned)

        def rows(db):
            skip = ("id", "deleted_at", *evidence)
            return {
                path: {k: v for k, v in t.items() if k not in skip}
                for path, t in _tombstones(db).items()
            }

        def evidence_of(db):
            return {
                p: tuple(t[k] for k in evidence) for p, t in _tombstones(db).items()
            }

        assert sorted(rows(cleaned)) == sorted(VANISHED)
        assert rows(cleaned) == rows(deleted)
        assert evidence_of(deleted) == dict.fromkeys(VANISHED, ("", None, 0))
        assert evidence_of(cleaned) == dict.fromkeys(VANISHED, (None, None, 0))
    finally:
        deleted.close()
        cleaned.close()


# ----------------------------------------------------------------------
# Restore: a path that comes back, the remainder
# ----------------------------------------------------------------------


def test_a_path_that_comes_back_gets_its_brief_back_never_over_a_newer_one(db):
    _seed(db)
    snap = db.export_taste_state()
    assert db.truncate_all_data() is True
    assert db.get_hindsight_briefs() == {}  # cascaded: why the snapshot has them
    _import_file(db, BACK)
    db.save_media(_media(GEN))
    # Briefed again after it came back, before the restore: that one stays.
    db.set_hindsight_brief(GEN, "a newer brief", "qwen3vl-4b", now=T1)
    events = db.list_judgment_events()

    result = db.restore_taste_state(snap)

    assert (result["restored"], result["missing"]) == (2, 4)
    assert db.get_hindsight_briefs() == {
        BACK: _brief_row(BACK, T1),
        GEN: {
            "brief": "a newer brief",
            "model": "qwen3vl-4b",
            "created_at": T1.isoformat(),
        },
    }
    assert db.list_judgment_events() == events  # a put-back, not a re-grade
    # The remainder keeps the briefs and prompts of the paths still missing.
    rem = json.loads(json.dumps(result["remaining"]))
    assert rem["version"] == 3
    assert _paths(rem["judgments"]) == sorted([FAILED, LIB, SEEN, VIDEO])
    assert rem["briefs"] == [
        {"file_path": FAILED, **_brief_row(FAILED)},
        {"file_path": LIB, **_brief_row(LIB)},
    ]
    assert rem["library_prompts"] == [
        {"file_path": LIB, "prompt": PROMPT[LIB], "model_name": MODEL_NAME}
    ]
    # Idempotent: a second put-back changes nothing.
    db.restore_taste_state(snap)
    assert db.get_hindsight_briefs()[GEN]["brief"] == "a newer brief"
    # The remainder tombstones exactly like the original snapshot would.
    db.restore_taste_state(rem, tombstone_missing=True)
    tombs = _tombstones(db)
    assert sorted(tombs) == sorted([FAILED, LIB, SEEN, VIDEO])
    assert (tombs[LIB]["brief"], tombs[LIB]["prompt"], tombs[LIB]["model_name"]) == (
        BRIEF[LIB],
        PROMPT[LIB],
        MODEL_NAME,
    )
    assert tombs[FAILED]["brief"] == BRIEF[FAILED]


def test_a_cancelled_full_clean_keeps_the_unrestored_briefs_pending(
    monkeypatch, db, store
):
    """Cancel lands during the last directory: what came back is restored
    (BACK, with its brief), the rest is rewritten to the pending file with
    its briefs and prompts and no tombstone. The next full clean merges the
    leftovers and, once it completes, tombstones them with their briefs."""
    _seed(db)
    before = _hindsight(db, store)

    _full_clean(monkeypatch, db, then="cancel")

    assert db.list_tombstones() == []
    assert db.get_hindsight_briefs() == {BACK: _brief_row(BACK, T1)}
    pending = _pending(db)
    assert pending["version"] == 3
    assert _paths(pending["judgments"]) == sorted(VANISHED)
    assert pending["briefs"] == [
        {"file_path": GEN, **_brief_row(GEN)},
        {"file_path": FAILED, **_brief_row(FAILED)},
        {"file_path": LIB, **_brief_row(LIB)},
    ]
    assert pending["library_prompts"] == [
        {"file_path": LIB, "prompt": PROMPT[LIB], "model_name": MODEL_NAME}
    ]

    _full_clean(monkeypatch, db)

    tombs = _tombstones(db)
    assert sorted(tombs) == sorted(VANISHED)
    assert {p: tombs[p]["brief"] for p in (LIB, GEN, FAILED)} == {
        p: BRIEF[p] for p in (LIB, GEN, FAILED)
    }
    assert (tombs[LIB]["prompt"], tombs[LIB]["model_name"]) == (
        PROMPT[LIB],
        MODEL_NAME,
    )
    assert db.get_hindsight_briefs() == {BACK: _brief_row(BACK, T1)}
    assert _hindsight(db, store) == before
    assert _pending(db) is None


def test_a_failed_full_clean_keeps_the_unrestored_briefs_pending(monkeypatch, db):
    _seed(db)

    events = _full_clean(monkeypatch, db, then="error")

    assert ("scan", "error", {"message": "scanner failed"}) in events
    assert db.list_tombstones() == []
    assert db.get_hindsight_briefs() == {BACK: _brief_row(BACK, T1)}
    pending = _pending(db)
    assert _paths(pending["briefs"]) == [GEN, FAILED, LIB]
    assert _paths(pending["library_prompts"]) == [LIB]


def test_merge_carries_the_pending_briefs_and_prompts(db):
    """The pending leftovers ride into the next full clean's snapshot with
    their briefs and prompts; a path the fresh snapshot covers keeps only
    the fresh entries (the fresh one is newer)."""
    _seed(db)
    data_dir = Path(db.db_path)
    ts.write_snapshot_file(data_dir, db.export_taste_state())
    db.truncate_all_data()
    _import_file(db, BACK)
    ts.apply_pending_taste_snapshot(db, data_dir)  # BACK comes back
    # GEN re-appears and is briefed again; its pending entry stays (no apply).
    db.save_media(_media(GEN))
    db.set_grade(GEN, "fail", now=T1)
    db.set_hindsight_brief(GEN, "a newer brief", "qwen3vl-4b", now=T1)
    fresh = db.export_taste_state()

    merged = ts.merge_pending_into_snapshot(data_dir, fresh)

    assert merged["version"] == 3
    assert {b["file_path"]: b["brief"] for b in merged["briefs"]} == {
        BACK: BRIEF[BACK],
        GEN: "a newer brief",
        FAILED: BRIEF[FAILED],
        LIB: BRIEF[LIB],
    }
    assert len(merged["briefs"]) == 4
    assert {lp["file_path"]: lp["prompt"] for lp in merged["library_prompts"]} == {
        BACK: PROMPT[BACK],
        LIB: PROMPT[LIB],
    }
    assert len(merged["library_prompts"]) == 2


def test_a_pending_file_holding_only_briefs_is_kept_until_they_come_back(db):
    data_dir = Path(db.db_path)
    target = ts.snapshot_file_path(data_dir)
    snap = {
        "version": 3,
        "taken_at": T0.isoformat(),
        "judgments": [],
        "categories": [],
        "favorites": [],
        "briefs": [{"file_path": BACK, **_brief_row(BACK)}],
        "library_prompts": [],
    }
    ts.write_snapshot_file(data_dir, snap)

    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 0,
        "missing": 1,
    }
    assert json.loads(target.read_text(encoding="utf-8")) == snap

    db.save_media(_media(BACK))
    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 1,
        "missing": 0,
    }
    assert db.get_hindsight_briefs() == {BACK: _brief_row(BACK)}
    assert not target.exists()


# ----------------------------------------------------------------------
# Older pending files
# ----------------------------------------------------------------------

_T = "2026-08-01T10:00:00+00:00"


def _row(path, **kw):
    return {
        "file_path": path,
        "reasons": "[]",
        "seen_at": _T,
        "judged_at": _T,
        "confirmed_at": _T,
        **kw,
    }


# A version-2 file (grades, no briefs or prompts) and a version-less one
# (before grades: ``passed = 1`` a reject, a favorite ★ + ✓).
_V2_FILE = {
    "version": 2,
    "taken_at": _T,
    "judgments": [_row("/p/back.png", grade="pass"), _row("/p/gone.png", grade="fail")],
    "categories": [],
    "favorites": ["/p/back.png"],
}
_VERSIONLESS_FILE = {
    "taken_at": _T,
    "judgments": [_row("/p/back.png", passed=0), _row("/p/gone.png", passed=1)],
    "categories": [],
    "favorites": ["/p/back.png"],
}


def test_a_v2_snapshot_upgrades_with_its_rows_as_they_are():
    assert ts.upgrade_legacy_snapshot(json.loads(json.dumps(_V2_FILE))) == {
        **_V2_FILE,
        "version": 3,
        "briefs": [],
        "library_prompts": [],
    }


@pytest.mark.parametrize(
    "pending", [_V2_FILE, _VERSIONLESS_FILE], ids=["v2", "versionless"]
)
def test_v2_and_versionless_pending_files_still_restore(db, pending):
    """Both read as version 3 with no briefs and no prompts: what came back
    is restored, the rest is rewritten as a version-3 file, and a completed
    full clean tombstones it (with no brief or prompt to keep)."""
    data_dir = Path(db.db_path)
    target = ts.snapshot_file_path(data_dir)
    target.write_text(json.dumps(pending), encoding="utf-8")
    loaded = ts.load_pending_snapshot(data_dir)
    assert loaded is not None
    assert (loaded["version"], loaded["briefs"], loaded["library_prompts"]) == (
        3,
        [],
        [],
    )

    db.save_media(_media("/p/back.png"))
    assert ts.apply_pending_taste_snapshot(db, data_dir) == {
        "restored": 1,
        "missing": 1,
    }

    assert _marks(db.get_judgment("/p/back.png")) == (True, "pass")
    left = json.loads(target.read_text(encoding="utf-8"))
    assert (left["version"], _paths(left["judgments"]), left["briefs"]) == (
        3,
        ["/p/gone.png"],
        [],
    )
    assert left["library_prompts"] == []
    assert list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*")) == []

    left_snapshot = ts.load_pending_snapshot(data_dir)
    assert left_snapshot is not None
    counts = ts.restore_and_persist(db, data_dir, left_snapshot, True)
    assert counts["tombstoned"] == 1
    [t] = db.list_tombstones()
    assert (t["file_path"], t["grade"], t["brief"], t["prompt"]) == (
        "/p/gone.png",
        "fail",
        None,
        None,
    )
    assert not target.exists()


def test_every_readable_version_upgrades_to_the_current_one():
    for version in ts._READABLE_VERSIONS:
        old = {"version": version, "taken_at": "t", "judgments": [], "favorites": []}
        assert ts._valid_shape(old)
        assert ts.upgrade_legacy_snapshot(old)["version"] == ts.TASTE_SNAPSHOT_VERSION
    assert ts._READABLE_VERSIONS[-1] == ts.TASTE_SNAPSHOT_VERSION


def test_a_version_bump_never_reads_a_v3_file_as_v2(monkeypatch):
    """Once the current version moves past 3, a v3 file is still read as v3:
    its briefs validated, and never dropped as if it were a v2 file -- until
    an upgrade from 3 is written, upgrading one refuses."""
    good = _v3(briefs=[_BRIEF], library_prompts=[_LIB])
    bad = _v3(briefs=[{**_BRIEF, "brief": 5}])
    monkeypatch.setattr(ts, "TASTE_SNAPSHOT_VERSION", 4)
    monkeypatch.setattr(ts, "_READABLE_VERSIONS", (2, 3, 4))

    assert ts._valid_shape(good)
    assert not ts._valid_shape(bad)
    with pytest.raises(ValueError):
        ts.upgrade_legacy_snapshot(good)


# ----------------------------------------------------------------------
# Pending files the parser cannot read
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version": 3, "taken_at": "t", "judgments": [], "favorites": ["/x\xff.png"]}',
        b"\xff\xfe not a snapshot",
        b"[" * 100_000,
    ],
    ids=["not-utf8-in-a-path", "not-utf8", "nested-too-deep"],
)
def test_a_pending_file_the_parser_cannot_read_is_set_aside(db, caplog, raw):
    """Bytes that are not UTF-8, or JSON nested deeper than the parser can
    read, make a corrupt file like any other: set aside and logged, never
    raised. Neither error is an OSError, so it used to escape the loader,
    and every full clean aborted on the file."""
    data_dir = Path(db.db_path)
    target = ts.snapshot_file_path(data_dir)
    target.write_bytes(raw)

    with caplog.at_level(logging.WARNING, logger=ts.logger.name):
        counts = ts.apply_pending_taste_snapshot(db, data_dir, raise_unreadable=True)

    assert counts == {"restored": 0, "missing": 0}
    assert not target.exists()
    [kept] = list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*"))
    assert kept.read_bytes() == raw
    assert any("corrupt" in r.getMessage() for r in caplog.records)


def test_a_full_clean_runs_past_a_pending_file_that_is_not_utf8(monkeypatch, db):
    _seed(db)
    data_dir = Path(db.db_path)
    ts.snapshot_file_path(data_dir).write_bytes(b"\xff\xfe not a snapshot")

    events = _full_clean(monkeypatch, db)

    assert not [e for e in events if e[1] == "error"]
    assert sorted(_tombstones(db)) == sorted(VANISHED)
    assert len(list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*"))) == 1
    assert _pending(db) is None


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------

_BRIEF = {"file_path": "/x.png", "brief": "b", "model": "m", "created_at": "t"}
_LIB = {"file_path": "/x.png", "prompt": "p", "model_name": '["krea2_dev"]'}


def _v3(**lists):
    return {
        "version": 3,
        "taken_at": "t",
        "judgments": [],
        "categories": [],
        "favorites": [],
        "briefs": [],
        "library_prompts": [],
        **lists,
    }


def _without(d, key):
    return {k: v for k, v in d.items() if k != key}


def test_good_briefs_and_prompts_pass_validation():
    assert ts._valid_shape(
        _v3(
            briefs=[_BRIEF],
            library_prompts=[
                _LIB,
                {**_LIB, "prompt": None},
                {**_LIB, "model_name": None},
                {**_LIB, "model_name": "[]"},
            ],
        )
    )
    # Version 3 written without the new lists reads as having none.
    assert ts._valid_shape(_without(_without(_v3(), "briefs"), "library_prompts"))


@pytest.mark.parametrize(
    "snap",
    [
        _v3(briefs=[{**_BRIEF, "brief": 5}]),
        _v3(briefs=[{**_BRIEF, "brief": None}]),
        _v3(briefs=[{**_BRIEF, "model": None}]),
        _v3(briefs=[_without(_BRIEF, "model")]),
        _v3(briefs=[{**_BRIEF, "created_at": 7}]),
        _v3(briefs=[_without(_BRIEF, "created_at")]),
        _v3(briefs=[{**_BRIEF, "file_path": 3}]),
        _v3(briefs=["/x.png"]),
        _v3(briefs={"/x.png": _BRIEF}),
        _v3(library_prompts=[{**_LIB, "prompt": 5}]),
        _v3(library_prompts=[_without(_LIB, "prompt")]),
        _v3(library_prompts=[_without(_LIB, "model_name")]),
        _v3(library_prompts=[{**_LIB, "file_path": None}]),
        # model_name is the stored JSON text of a list of names.
        _v3(library_prompts=[{**_LIB, "model_name": ["krea2_dev"]}]),
        _v3(library_prompts=[{**_LIB, "model_name": "krea2_dev"}]),
        _v3(library_prompts=[{**_LIB, "model_name": '{"a": 1}'}]),
        _v3(library_prompts=[{**_LIB, "model_name": "[1]"}]),
        _v3(library_prompts="/x.png"),
    ],
)
def test_a_malformed_brief_or_prompt_quarantines_the_file(db, snap):
    """A file whose brief or prompt entry has the wrong type is set aside
    before anything is restored, while the well-formed file is read."""
    assert ts._valid_shape(_v3(briefs=[_BRIEF], library_prompts=[_LIB]))
    _assert_quarantined(db, snap)


# What a JSON "\udc80" escape reads as: a lone surrogate, which is not
# UTF-8, so SQLite cannot store it (binding it raises UnicodeEncodeError).
_SURROGATE = "\udc80"


@pytest.mark.parametrize(
    "snap",
    [
        _v3(briefs=[{**_BRIEF, "brief": "a fox" + _SURROGATE}]),
        _v3(briefs=[{**_BRIEF, "model": _SURROGATE}]),
        _v3(briefs=[{**_BRIEF, "created_at": _SURROGATE}]),
        _v3(briefs=[{**_BRIEF, "file_path": "/x" + _SURROGATE + ".png"}]),
        _v3(library_prompts=[{**_LIB, "prompt": "fox" + _SURROGATE}]),
        _v3(library_prompts=[{**_LIB, "file_path": "/x" + _SURROGATE + ".png"}]),
        # The JSON text itself, or a name it holds.
        _v3(library_prompts=[{**_LIB, "model_name": '["krea' + _SURROGATE + '"]'}]),
        _v3(
            library_prompts=[{**_LIB, "model_name": json.dumps(["krea" + _SURROGATE])}]
        ),
        # Nested past what the parser can read: rejected, never raised.
        _v3(library_prompts=[{**_LIB, "model_name": "[" * 100_000}]),
    ],
)
def test_a_brief_or_prompt_sqlite_cannot_store_is_set_aside_unrestored(
    db, monkeypatch, snap
):
    """Validation rejects a brief or prompt text that is not UTF-8, so the
    file is set aside before any restore runs -- a restore would fail on the
    bind halfway, with the text in the error."""
    assert not ts._valid_shape(snap)
    restores: List[Any] = []
    monkeypatch.setattr(db, "restore_taste_state", lambda *a, **k: restores.append(a))
    _assert_quarantined(db, snap)
    assert restores == []


# ----------------------------------------------------------------------
# Logs
# ----------------------------------------------------------------------


def test_no_brief_or_prompt_text_reaches_the_logs(monkeypatch, db, caplog):
    """Counts only: through a cancelled and a failed full clean (the pending
    file is rewritten), the startup apply, a completed full clean
    (tombstones), a malformed pending file (quarantined) and one the
    restore rejects (quarantined: the error's type, never its text)."""
    from backend import main as main_mod

    _seed(db)
    data_dir = Path(db.db_path)
    target = ts.snapshot_file_path(data_dir)
    rejected = "SECRET-REJECTED-BRIEF a heron in the reeds"
    with caplog.at_level(logging.DEBUG):
        _full_clean(monkeypatch, db, then="cancel")
        _full_clean(monkeypatch, db, then="error")
        asyncio.run(main_mod._apply_pending_taste_snapshot(db, data_dir))
        _full_clean(monkeypatch, db)
        target.write_text(
            json.dumps(_v3(briefs=[{**_BRIEF, "brief": SECRETS, "model": None}])),
            encoding="utf-8",
        )
        ts.apply_pending_taste_snapshot(db, data_dir)
        # BACK came back. Its brief here is one SQLite cannot store, let past
        # validation as a gap there would: binding it fails, and the
        # UnicodeEncodeError's repr carries the whole text.
        target.write_text(
            json.dumps(
                _v3(
                    briefs=[
                        {
                            **_BRIEF,
                            "file_path": BACK,
                            "brief": rejected + _SURROGATE,
                        }
                    ]
                )
            ),
            encoding="utf-8",
        )
        with monkeypatch.context() as m:
            m.setattr(ts, "_valid_shape", lambda snap: True)
            assert ts.apply_pending_taste_snapshot(db, data_dir) == {
                "restored": 0,
                "missing": 0,
            }
    assert len(db.list_tombstones()) == len(VANISHED)
    assert len(list(data_dir.glob(ts.TASTE_SNAPSHOT_FILENAME + ".corrupt-*"))) == 2
    logged = caplog.text + "".join(r.getMessage() for r in caplog.records)
    # The restores and the quarantines were logged -- as counts and types.
    assert "Full clean: taste state restored for" in logged
    assert "is corrupt" in logged
    assert "restore rejected it: UnicodeEncodeError" in logged
    for secret in SECRETS + [rejected]:
        assert secret not in logged
