"""db.move_media_batch: a media row changes path and keeps everything.

Losing a rating here is an anti-goal, so the tests seed every table that
holds a media path and compare before and after.
"""

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.services import taste_snapshot
from metascan.core.database_sqlite import (
    MOVE_EXCLUDED_COLUMNS,
    MOVE_PATH_COLUMNS,
    DatabaseManager,
)
from metascan.core.media import Media

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


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


def _q(db, sql, args=()):
    with db._get_connection() as conn:
        return [tuple(r) for r in conn.execute(sql, args).fetchall()]


def _exec(db, sql, args=()):
    with db._get_connection() as conn:
        conn.execute(sql, args)
        conn.commit()


# ---------------------------------------------------------------- schema


def test_every_path_column_is_listed(db):
    listed = set(MOVE_PATH_COLUMNS) | set(MOVE_EXCLUDED_COLUMNS)
    assert not set(MOVE_PATH_COLUMNS) & set(MOVE_EXCLUDED_COLUMNS)
    seen = set()
    with db._get_connection() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for t in tables:
            for col in conn.execute(f"PRAGMA table_info('{t}')"):
                name = col[1].lower()
                if "path" in name or "file" in name:
                    seen.add((t, col[1]))
    assert seen - listed == set(), f"unlisted path columns: {sorted(seen - listed)}"
    # a stale entry (renamed or dropped column) would hide a real gap
    assert listed - seen == set(), f"listed but absent: {sorted(listed - seen)}"


# ---------------------------------------------------------------- seeding


def _seed_everything(db, src="/l/a/old.png"):
    """Rows for `src` in every table of MOVE_PATH_COLUMNS."""
    db.save_media(_media(src))
    db.save_media(_media("/l/other.png"))
    db.set_favorite_state(src, True, now=T0)
    db.set_grade(src, "pass", now=T0)
    db.set_reason(src, "light", True, now=T0)
    cat = db.create_category("Anime")
    db.add_media_categories([src], cat.id, "viewer")
    db.add_tag_indices(Path(src), ["cat", "dog"], source="clip")
    db.set_hindsight_brief(src, "brief text", "m", now=T0)
    db.enqueue_tags([src])
    db.mark_tag_exhausted(src)
    db.replace_taste_scores(1, {src: 0.7})
    db.create_folder("f1", "manual", "Folder")
    db.add_folder_items("f1", [src])
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO media_hashes(file_path, phash) VALUES (?, 'ab')", (src,)
        )
        conn.execute(
            "INSERT INTO saved_prompts(file_path,name,prompt,target_model,"
            "architecture,mode) VALUES (?,'n','p','m','a','generate')",
            (src,),
        )
        conn.execute(
            "INSERT INTO t2i_images(file_path, batch_id) VALUES (?, 'b')", (src,)
        )
        conn.execute(
            "INSERT INTO i2v_videos(source_path, file_path) VALUES (?, '/l/vid.mp4')",
            (src,),
        )
        conn.execute(
            "INSERT INTO i2v_videos(source_path, file_path) VALUES ('/l/x.png', ?)",
            (src,),
        )
        conn.execute(
            "INSERT INTO workflow_presets(name,kind,workflow_json,bindings) "
            "VALUES ('p','t2i','{}','{}')"
        )
        conn.execute(
            "INSERT INTO generation_jobs(preset_id, params, i2v_source_path) "
            "VALUES (1,'{}',?)",
            (src,),
        )
        conn.execute(
            "INSERT INTO storyboards(name,target_model,architecture,base_seed) "
            "VALUES ('s','m','a',1)"
        )
        conn.execute(
            "INSERT INTO scenes(storyboard_id,name,reference_path) VALUES (1,'sc',?)",
            (src,),
        )
        conn.execute(
            "INSERT INTO storyboard_subjects(storyboard_id,name,description,"
            "reference_path,reference_path_2,voice_ref_path) "
            "VALUES (1,'n','d',?,?,'/audio/v.wav')",
            (src, src),
        )
        conn.execute("INSERT INTO panels(scene_id, action) VALUES (1,'act')")
        conn.execute("INSERT INTO beats(panel_id, action) VALUES (1,'act')")
        conn.execute(
            "INSERT INTO beat_images(beat_id, file_path) VALUES (1, ?)", (src,)
        )
        conn.execute(
            "INSERT INTO panel_videos(panel_id, file_path) VALUES (1, ?)", (src,)
        )
        conn.commit()


def _count_at(db, path):
    """{(table, column): rows holding `path`} over MOVE_PATH_COLUMNS."""
    out = {}
    for table, col in MOVE_PATH_COLUMNS:
        n = _q(db, f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (path,))[0][0]
        out[(table, col)] = n
    return out


def test_move_keeps_grade_favorite_tags_categories_folders_lineage(db):
    src, dst = "/l/a/old.png", "/l/b/new.png"
    _seed_everything(db, src)
    before = _count_at(db, src)
    assert all(n >= 1 for n in before.values()), before  # nothing unseeded
    judgment = _q(db, "SELECT * FROM media_judgments WHERE file_path=?", (src,))[0]
    events = _q(
        db,
        "SELECT event, reason, created_at FROM judgment_events WHERE file_path=? ORDER BY id",
        (src,),
    )
    assert events
    idx = _q(
        db,
        "SELECT index_type, index_key, source FROM indices WHERE file_path=? ORDER BY 1,2",
        (src,),
    )
    assert ("tag", "cat", "clip") in idx

    assert db.move_media_batch([(src, dst)]) == 1

    assert all(n == 0 for n in _count_at(db, src).values()), _count_at(db, src)
    assert _count_at(db, dst) == before
    moved = _q(db, "SELECT * FROM media_judgments WHERE file_path=?", (dst,))[0]
    assert moved[1:] == judgment[1:]  # reasons, timestamps, grade untouched
    state = db.get_judgment(dst)
    assert state.favorite and state.grade == "pass"
    assert (
        _q(
            db,
            "SELECT event, reason, created_at FROM judgment_events WHERE file_path=? ORDER BY id",
            (dst,),
        )
        == events
    )
    assert [c.name for c in db.get_media_categories(dst)] == ["Anime"]
    assert (
        _q(
            db,
            "SELECT index_type, index_key, source FROM indices WHERE file_path=? ORDER BY 1,2",
            (dst,),
        )
        == idx
    )
    # unrelated data and the audio reference stay put
    assert (
        _q(db, "SELECT COUNT(*) FROM media WHERE file_path='/l/other.png'")[0][0] == 1
    )
    assert (
        _q(db, "SELECT voice_ref_path FROM storyboard_subjects")[0][0] == "/audio/v.wav"
    )
    assert _q(db, "SELECT file_path FROM i2v_videos WHERE source_path=?", (dst,)) == [
        ("/l/vid.mp4",)
    ]
    assert _q(db, "PRAGMA foreign_key_check") == []
    assert db.get_media(Path(dst)) is not None
    assert db.get_media(Path(src)) is None


def test_move_rewrites_the_json_blob_path(db):
    src, dst = "/l/a/old.png", "/l/b/new.png"
    db.save_media(_media(src))
    assert json.loads(_q(db, "SELECT data FROM media")[0][0])["file_path"] == src
    db.move_media_batch([(src, dst)])
    blob = json.loads(_q(db, "SELECT data FROM media")[0][0])
    assert blob["file_path"] == dst
    got = db.get_media(Path(dst))
    assert got is not None and got.file_path == Path(dst)
    assert got.width == 8 and got.format == "png"  # rest of the blob intact


def test_target_with_a_row_is_skipped(db):
    db.save_media(_media("/l/a.png"))
    db.save_media(_media("/l/b.png"))
    db.set_grade("/l/a.png", "pass", now=T0)
    db.set_grade("/l/b.png", "fail", now=T0)
    assert db.move_media_batch([("/l/a.png", "/l/b.png")]) == 0
    assert db.get_judgment("/l/a.png").grade == "pass"
    assert db.get_judgment("/l/b.png").grade == "fail"


def test_target_with_lineage_row_is_skipped(db):
    """A dangling T2I row on the target path (UNIQUE, no FK) is not ours to
    delete: the pair is skipped, nothing is lost."""
    db.save_media(_media("/l/a.png"))
    _exec(db, "INSERT INTO t2i_images(file_path) VALUES ('/l/gone.png')")
    assert db.move_media_batch([("/l/a.png", "/l/gone.png")]) == 0
    assert _q(db, "SELECT COUNT(*) FROM media WHERE file_path='/l/a.png'")[0][0] == 1
    assert _q(db, "SELECT COUNT(*) FROM t2i_images")[0][0] == 1


def test_missing_source_duplicates_and_noops_are_skipped(db):
    db.save_media(_media("/l/a.png"))
    pairs = [
        ("/l/nope.png", "/l/x.png"),
        ("/l/a.png", "/l/a.png"),
        ("/l/a.png", "/l/b.png"),
        ("/l/a.png", "/l/c.png"),  # repeated source: first wins
    ]
    assert db.move_media_batch(pairs) == 1
    assert _q(db, "SELECT file_path FROM media") == [("/l/b.png",)]
    assert db.move_media_batch([]) == 0


def test_case_only_change_updates_the_spelling(db):
    """On a case-insensitive OS old != new as strings but is the same file:
    the row keeps everything under the new spelling."""
    db.save_media(_media("/l/Photo.PNG"))
    db.set_grade("/l/Photo.PNG", "pass", now=T0)
    assert db.move_media_batch([("/l/Photo.PNG", "/l/photo.png")]) == 1
    assert _q(db, "SELECT file_path FROM media") == [("/l/photo.png",)]
    assert db.get_judgment("/l/photo.png").grade == "pass"
    assert _q(db, "SELECT file_path FROM media_judgments") == [("/l/photo.png",)]


def test_chain_is_ordered_and_swap_is_skipped(db):
    for p in ("/l/a.png", "/l/b.png"):
        db.save_media(_media(p))
    db.set_grade("/l/a.png", "pass", now=T0)
    db.set_grade("/l/b.png", "fail", now=T0)
    # a -> b and b -> c: b must go first, whatever the order given
    assert (
        db.move_media_batch([("/l/a.png", "/l/b.png"), ("/l/b.png", "/l/c.png")]) == 2
    )
    assert db.get_judgment("/l/b.png").grade == "pass"
    assert db.get_judgment("/l/c.png").grade == "fail"
    assert _q(db, "SELECT COUNT(*) FROM media WHERE file_path='/l/a.png'")[0][0] == 0
    # a swap cannot be ordered: skipped, nothing changes
    assert (
        db.move_media_batch([("/l/b.png", "/l/c.png"), ("/l/c.png", "/l/b.png")]) == 0
    )
    assert db.get_judgment("/l/b.png").grade == "pass"
    assert db.get_judgment("/l/c.png").grade == "fail"


def test_chain_moves_children_too(db):
    for p in ("/l/a.png", "/l/b.png"):
        db.save_media(_media(p))
        db.enqueue_tags([p])
        db.add_tag_indices(Path(p), [p[-5]], source="clip")
    db.move_media_batch([("/l/a.png", "/l/b.png"), ("/l/b.png", "/l/c.png")])
    assert _q(
        db, "SELECT index_key, file_path FROM indices WHERE index_type='tag' ORDER BY 2"
    ) == [
        ("a", "/l/b.png"),
        ("b", "/l/c.png"),
    ]
    assert _q(db, "PRAGMA foreign_key_check") == []


def test_failure_rolls_the_whole_batch_back(db, monkeypatch):
    src, dst = "/l/a/old.png", "/l/b/new.png"
    _seed_everything(db, src)
    db.save_media(_media("/l/p.png"))
    db.set_grade("/l/p.png", "fail", now=T0)
    before = _count_at(db, src)

    # a trigger that fails on a late table forces a mid-batch error
    _exec(
        db,
        "CREATE TRIGGER boom BEFORE UPDATE ON vlm_tag_queue "
        "BEGIN SELECT RAISE(ABORT, 'boom'); END",
    )
    with pytest.raises(sqlite3.Error):
        db.move_media_batch([("/l/p.png", "/l/q.png"), (src, dst)])
    assert _count_at(db, src) == before
    assert _count_at(db, dst) == {k: 0 for k in before}
    assert db.get_judgment("/l/p.png").grade == "fail"
    assert _q(db, "PRAGMA foreign_key_check") == []


def test_dangling_foreign_key_rolls_back(db):
    """A child the move forgot would dangle: the deferred check at commit
    refuses, and nothing is moved."""
    db.save_media(_media("/l/a.png"))
    db.set_grade("/l/a.png", "pass", now=T0)
    import metascan.core.database_sqlite as mod

    original = mod.MOVE_PATH_COLUMNS
    monkeypatch_cols = tuple(c for c in original if c[0] != "media_judgments")
    mod.MOVE_PATH_COLUMNS = monkeypatch_cols
    try:
        with pytest.raises(sqlite3.IntegrityError):
            db.move_media_batch([("/l/a.png", "/l/b.png")])
    finally:
        mod.MOVE_PATH_COLUMNS = original
    assert _q(db, "SELECT file_path FROM media") == [("/l/a.png",)]
    assert db.get_judgment("/l/a.png").grade == "pass"


def test_tombstones_are_untouched(db):
    db.save_media(_media("/l/dead.png"))
    db.set_grade("/l/dead.png", "pass", now=T0)
    db.delete_media(Path("/l/dead.png"))
    tomb = _q(db, "SELECT * FROM media_tombstones")
    assert tomb
    db.save_media(_media("/l/dead.png"))  # a new file at the old path
    db.move_media_batch([("/l/dead.png", "/l/moved.png")])
    assert _q(db, "SELECT * FROM media_tombstones") == tomb


# ------------------------------------------------------------- the snapshot


def _write_pending(data_dir: Path, **lists):
    snap = {
        "version": 3,
        "taken_at": "2026-01-01T00:00:00+00:00",
        "judgments": [],
        "categories": [],
        "favorites": [],
        "briefs": [],
        "library_prompts": [],
    }
    snap.update(lists)
    taste_snapshot.write_snapshot_file(data_dir, snap)


def _judgment(path):
    return {
        "file_path": path,
        "reasons": "[]",
        "seen_at": None,
        "judged_at": "2026-01-01T00:00:00+00:00",
        "confirmed_at": "2026-01-01T00:00:00+00:00",
        "grade": "pass",
    }


def test_pending_snapshot_entry_is_rekeyed(tmp_path):
    _write_pending(
        tmp_path,
        judgments=[_judgment("/l/a.png"), _judgment("/l/keep.png")],
        categories=[
            {
                "file_path": "/l/a.png",
                "category_id": 1,
                "category_name": "Anime",
                "source": "viewer",
                "assigned_at": "2026-01-01T00:00:00+00:00",
            }
        ],
        favorites=["/l/a.png", "/l/keep.png"],
        briefs=[
            {
                "file_path": "/l/a.png",
                "brief": "b",
                "model": "m",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    )
    n = taste_snapshot.rekey_pending(tmp_path, [("/l/a.png", "/l/z.png")])
    assert n == 4
    snap = taste_snapshot.load_pending_snapshot(tmp_path)
    assert {j["file_path"] for j in snap["judgments"]} == {"/l/z.png", "/l/keep.png"}
    assert snap["categories"][0]["file_path"] == "/l/z.png"
    assert snap["briefs"][0]["file_path"] == "/l/z.png"
    assert sorted(snap["favorites"]) == ["/l/keep.png", "/l/z.png"]
    assert taste_snapshot.rekey_pending(tmp_path, [("/l/a.png", "/l/z.png")]) == 0


def test_pending_rekey_chain_and_collision(tmp_path):
    _write_pending(
        tmp_path,
        judgments=[
            _judgment("/l/a.png"),
            _judgment("/l/b.png"),
            _judgment("/l/x.png"),
            _judgment("/l/y.png"),
        ],
    )
    taste_snapshot.rekey_pending(
        tmp_path,
        [("/l/a.png", "/l/b.png"), ("/l/b.png", "/l/c.png"), ("/l/x.png", "/l/y.png")],
    )
    snap = taste_snapshot.load_pending_snapshot(tmp_path)
    # chain moves both; x -> y is refused because y's entry stays
    assert sorted(j["file_path"] for j in snap["judgments"]) == [
        "/l/b.png",
        "/l/c.png",
        "/l/x.png",
        "/l/y.png",
    ]


def test_pending_rekey_without_a_file_does_nothing(tmp_path):
    assert taste_snapshot.rekey_pending(tmp_path, [("/l/a.png", "/l/b.png")]) == 0
    assert not taste_snapshot.pending_snapshot_exists(tmp_path)


def test_pending_rekey_corrupt_file_is_quarantined_not_raised(tmp_path):
    taste_snapshot.snapshot_file_path(tmp_path).write_text("{not json")
    assert taste_snapshot.rekey_pending(tmp_path, [("/l/a.png", "/l/b.png")]) == 0
    assert not taste_snapshot.snapshot_file_path(tmp_path).exists()
    assert list(tmp_path.glob("*.corrupt-*"))


def test_pending_rekey_leaves_other_versions_readable(tmp_path):
    """A rewritten file still loads (and restores) like any other."""
    _write_pending(tmp_path, judgments=[_judgment("/l/a.png")])
    taste_snapshot.rekey_pending(tmp_path, [("/l/a.png", "/l/b.png")])
    assert taste_snapshot.pending_snapshot_paths(tmp_path) == {"/l/b.png"}


# --------------------------------------------------------------- scale


def test_batch_of_5000_is_one_transaction(db):
    n = 5000
    db.save_media_batch([_media(f"/l/old/{i}.png") for i in range(n)])
    with db._get_connection() as conn:
        conn.executemany(
            "INSERT INTO media_judgments(file_path, grade) VALUES (?, 'pass') "
            "ON CONFLICT(file_path) DO UPDATE SET grade='pass'",
            [(f"/l/old/{i}.png",) for i in range(n)],
        )
        conn.commit()
    pairs = [(f"/l/old/{i}.png", f"/l/new/{i}.png") for i in range(n)]

    commits = []
    real = db._get_connection

    from contextlib import contextmanager

    @contextmanager
    def spy():
        with real() as conn:
            conn.set_trace_callback(
                lambda s: (
                    commits.append(s)
                    if s.strip().upper() in ("COMMIT", "BEGIN IMMEDIATE")
                    else None
                )
            )
            yield conn

    db._get_connection = spy
    start = time.monotonic()
    try:
        assert db.move_media_batch(pairs) == n
    finally:
        db._get_connection = real
    elapsed = time.monotonic() - start
    assert commits.count("COMMIT") == 1
    assert elapsed < 5, elapsed
    assert (
        _q(db, "SELECT COUNT(*) FROM media_judgments WHERE file_path LIKE '/l/new/%'")[
            0
        ][0]
        == n
    )
    assert (
        _q(db, "SELECT COUNT(*) FROM media WHERE file_path LIKE '/l/old/%'")[0][0] == 0
    )
