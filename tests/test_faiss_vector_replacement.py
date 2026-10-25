"""A path's FAISS row follows its file, and a deleted file's tombstone takes
a vector from the index by path only with evidence that the row is its own.

``FaissIndexManager.add`` used to keep the first vector a path ever had, so a
"Rebuild all" with the same CLIP model, a full rescan and a new file landing
on a deleted file's path all recomputed a vector and then threw it away. It
now overwrites the row: a path's row holds the vector of the last file
embedded there. These tests run the real embedding worker (with a fake CLIP
whose vector is a pure function of the file's bytes) and check the stored
rows.

A row read by path is a tombstone's own only when its file was embedded with
the index's CLIP model when it was deleted (``media_tombstones
.embedded_model``, copied from its ``media_hashes`` row) and nothing has been
embedded at the path since (``path_rewritten``: the worker stamps every
tombstone of each path it appends or overwrites, before it saves the index),
and only from an index loaded after the file was marked embedded
(``embedded_at``, its ``media_hashes.embedding_updated_at``, against the
manager's ``loaded_at``: the server's in-memory index can lag the disk).
When unsure, no vector. The R1a / R1b / R2 / R2b sequences are the review's
reproductions of a hand-off that once gave a tombstone another file's vector.
"""

import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pytest

from backend.api import similarity
from backend.services import judgments_service
from backend.services.judgments_service import faiss_embedding_lookup
from metascan.core.database_sqlite import DatabaseManager, MigrationError
from metascan.core.embedding_manager import FaissIndexManager
from metascan.core.media import Media
from metascan.workers import embedding_worker as ew

DIM = 32
MODEL = "small"
# What a test lookup reports as its index's load time: after every mark.
LATER = time.time() + 3600
# An archived thumbnail as a tombstone stores it (the re-embedder's input).
THUMB = "archive/thumbs/0123456789abcdef0123456789abcdef.jpg"


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _bytes_vector(path: str) -> np.ndarray:
    digest = hashlib.sha256(Path(path).read_bytes()).digest()
    return _unit(int.from_bytes(digest[:4], "little"))


class FakeClip:
    """Stands in for EmbeddingManager: no model, a file's vector is a pure
    function of its bytes, and every image embedded is recorded."""

    calls: List[str] = []

    def __init__(self, model_key: str = MODEL, device: str = "auto") -> None:
        self.model_key = model_key
        self.model_config = {"name": "fake", "pretrained": "fake"}

    def _check_model_needs_download(self, name: str, pretrained: str) -> bool:
        return False

    def _ensure_model_loaded(self) -> None:
        return None

    @property
    def embedding_dim(self) -> int:
        return DIM

    def compute_image_embedding(self, path: str) -> np.ndarray:
        FakeClip.calls.append(path)
        return _bytes_vector(path)

    def compute_video_embedding(self, path: str, num_keyframes: int = 4) -> None:
        return None

    def unload_model(self) -> None:
        return None


@pytest.fixture
def env(tmp_path, monkeypatch):
    FakeClip.calls = []
    monkeypatch.setattr(ew, "EmbeddingManager", FakeClip)
    monkeypatch.setattr(ew, "build_vocabulary", lambda *a, **k: None)
    data_dir = tmp_path / "data"
    index_dir = tmp_path / "index"
    queue_dir = tmp_path / "queue"
    for d in (data_dir, index_dir, queue_dir):
        d.mkdir()
    db = DatabaseManager(data_dir)
    yield {
        "db": db,
        "data": data_dir,
        "index": index_dir,
        "queue": queue_dir,
        "lib": tmp_path,
    }
    db.close()


def _write(env, name: str, content: bytes) -> str:
    path = env["lib"] / name
    path.write_bytes(content)
    return str(path)


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


def _seed_index(env, rows, model: str = MODEL) -> None:
    fm = FaissIndexManager(env["index"])
    fm.create(embedding_dim=DIM, model_key=model)
    for path, vec in rows:
        fm.add(path, vec)
    assert fm.save()


def _run_worker(
    env, paths: List[str], model: str = MODEL, status: str = "complete"
) -> Dict[str, Any]:
    task = {
        "model_key": model,
        "device": "cpu",
        "file_paths": paths,
        "db_path": str(env["data"]),
        "index_dir": str(env["index"]),
        "compute_phash": False,
        "video_keyframes": 4,
        "tag_with_vlm": False,
    }
    (env["queue"] / "embedding_task.json").write_text(json.dumps(task))
    ew.EmbeddingWorker(env["queue"]).run()
    progress = json.loads((env["queue"] / "progress_embedding.json").read_text())
    assert progress["status"] == status, progress
    return progress


def _index_vector(env, path: str) -> np.ndarray:
    fm = FaissIndexManager(env["index"])
    assert fm.load()
    vec = fm.get_embedding(path)
    assert vec is not None
    return vec


def _marks_a_while_ago(db, seconds: int = 10) -> None:
    """Move every embedding mark (live and on tombstones) ``seconds`` back:
    an index loaded now was loaded after them, without waiting out the
    one-second resolution of the marks."""
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE media_hashes SET embedding_updated_at = "
            "datetime(embedding_updated_at, ?) WHERE embedding_updated_at IS NOT NULL",
            (f"-{seconds} seconds",),
        )
        conn.execute(
            "UPDATE media_tombstones SET embedded_at = datetime(embedded_at, ?) "
            "WHERE embedded_at IS NOT NULL",
            (f"-{seconds} seconds",),
        )
        conn.commit()


def _backfill(env, db) -> int:
    """What the server does whenever it loads the on-disk index, here a
    while after every mark so far."""
    _marks_a_while_ago(db)
    fm = FaissIndexManager(env["index"])
    assert fm.load()
    return judgments_service.backfill_tombstone_embeddings(db, fm)


def _server_index(env, db, monkeypatch) -> FaissIndexManager:
    """Load the on-disk index as the server's in-memory one and install the
    delete-time lookup on ``db``, as the lifespan does."""
    fm = FaissIndexManager(env["index"])
    assert fm.load()
    monkeypatch.setattr(similarity, "_faiss_manager", fm)
    db.set_embedding_lookup(faiss_embedding_lookup)
    return fm


def _tombstone_vector(db, tomb_id: int, model: str = MODEL):
    blob = db.get_tombstone_vectors(tomb_id).get(model)
    return None if blob is None else np.frombuffer(blob, dtype=np.float32)


def _tombstone(db, tomb_id: int) -> Dict[str, Any]:
    (row,) = [t for t in db.list_tombstones() if t["id"] == tomb_id]
    return row


def _graded_delete(
    db,
    path: str,
    grade: str = "pass",
    *,
    embedded_with: Optional[str] = None,
    thumb: Optional[str] = None,
) -> int:
    """Ingest ``path``, mark it embedded with ``embedded_with`` (as the
    worker does after writing its row), grade it and delete it; returns the
    tombstone id."""
    db.save_media(_media(path))
    if embedded_with is not None:
        db.mark_embedded([path], embedded_with)
    db.set_grade(path, grade)
    assert db.delete_media(Path(path), thumb)
    return max(t["id"] for t in db.list_tombstones() if t["file_path"] == path)


def _flags(db) -> Dict[str, int]:
    with db._get_connection() as conn:
        return {
            r["file_path"]: r["has_embedding"]
            for r in conn.execute("SELECT file_path, has_embedding FROM media_hashes")
        }


def _mark_times(db) -> Dict[str, Optional[str]]:
    with db._get_connection() as conn:
        return {
            r["file_path"]: r["embedding_updated_at"]
            for r in conn.execute(
                "SELECT file_path, embedding_updated_at FROM media_hashes"
            )
        }


def _set_user_version(db, version: int) -> None:
    conn = sqlite3.connect(str(db.db_file))
    try:
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
    finally:
        conn.close()


def _user_version(db) -> int:
    conn = sqlite3.connect(str(db.db_file))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A path's row is the last file embedded there
# ---------------------------------------------------------------------------


def test_a_rebuild_replaces_a_changed_files_vector(env):
    db = env["db"]
    path = _write(env, "edited.png", b"the picture as it is now")
    stale = _unit(1)
    _seed_index(env, [(path, stale)])
    db.save_media(_media(path))
    db.mark_embedded([path], MODEL)

    db.clear_embeddings()  # what a "Rebuild all" does first
    _run_worker(env, db.get_unembedded_file_paths())

    np.testing.assert_allclose(_index_vector(env, path), _bytes_vector(path))
    fm = FaissIndexManager(env["index"])
    fm.load()
    assert fm.file_count == 1 and fm.size == 1


def test_a_new_file_on_a_reused_path_gets_its_own_vector(env):
    """The row overwritten is the deleted file's: nothing hands it to the
    tombstone (the worker cannot tell whose it is), and the tombstone is
    stamped, so it never takes the new file's row by path either."""
    db = env["db"]
    path = _write(env, "reused.png", b"the second file at this path")
    first = _unit(2)
    _seed_index(env, [(path, first)])
    tomb = _graded_delete(db, path, embedded_with=MODEL)
    assert _tombstone_vector(db, tomb) is None  # deleted with no index in memory

    db.save_media(_media(path))  # the second file lands on the path
    _run_worker(env, db.get_unembedded_file_paths())

    np.testing.assert_allclose(_index_vector(env, path), _bytes_vector(path))
    assert db.get_tombstone_vectors(tomb) == {}, "the replaced row was handed over"
    assert _tombstone(db, tomb)["path_rewritten"] == 1
    assert _backfill(env, db) == 0
    assert db.get_tombstone_vectors(tomb) == {}


def test_an_incremental_build_leaves_unchanged_files_alone(env):
    db = env["db"]
    kept = _write(env, "kept.png", b"already embedded")
    fresh = _write(env, "fresh.png", b"new today")
    kept_vec = _unit(3)
    _seed_index(env, [(kept, kept_vec)])
    db.save_media(_media(kept))
    db.mark_embedded([kept], MODEL)
    db.save_media(_media(fresh))

    paths = db.get_unembedded_file_paths()
    assert paths == [fresh]
    _run_worker(env, paths)

    assert FakeClip.calls == [fresh]
    np.testing.assert_allclose(_index_vector(env, kept), kept_vec)
    np.testing.assert_allclose(_index_vector(env, fresh), _bytes_vector(fresh))


# ---------------------------------------------------------------------------
# The review's sequences: no tombstone ever takes another file's vector
# ---------------------------------------------------------------------------


def test_r1a_a_clip_switch_then_rebuild_never_gives_a_tombstone_the_live_files(env):
    """A (graded, archived) is deleted while the "small" index is in memory:
    its tombstone keeps A's "small" vector. B lands on the path. The owner
    switches CLIP to "large" (a fresh index: B is appended), then runs
    "Rebuild all" (B's row is overwritten). A's tombstone never gets B's
    "large" vector, from the worker or the backfill, and stays in the
    re-embedder's queue for "large": its archive is A's own image."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    vec_a = _bytes_vector(p)
    _seed_index(env, [(p, vec_a)])
    db.set_embedding_lookup(lambda q: (vec_a, MODEL, LATER))  # the in-memory index
    tomb_a = _graded_delete(db, p, "fail", embedded_with=MODEL, thumb=THUMB)
    db.set_embedding_lookup(None)
    np.testing.assert_allclose(_tombstone_vector(db, tomb_a), vec_a)

    _write(env, "reused.png", b"file B")
    vec_b = _bytes_vector(p)
    db.save_media(_media(p))
    _run_worker(env, db.get_unembedded_file_paths(), "large")  # CLIP switch
    assert _tombstone_vector(db, tomb_a, "large") is None
    db.clear_embeddings()  # "Rebuild all" with the same model
    _run_worker(env, db.get_unembedded_file_paths(), "large")
    assert _backfill(env, db) == 0

    np.testing.assert_allclose(_index_vector(env, p), vec_b)
    assert (
        _tombstone_vector(db, tomb_a, "large") is None
    ), "A's tombstone took live file B's vector"
    np.testing.assert_allclose(_tombstone_vector(db, tomb_a), vec_a)
    assert db.tombstones_needing_vectors("large") == [(tomb_a, THUMB)]


def test_r1b_a_file_deleted_before_the_index_existed_never_takes_one(env):
    """A (graded) was deleted before the index was first built, so its file
    was never embedded. B lands on the path; the first build appends B and a
    rebuild overwrites it. A's tombstone gets no vector at any point."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    tomb_a = _graded_delete(db, p, "pass")

    _write(env, "reused.png", b"file B")
    db.save_media(_media(p))
    _run_worker(env, db.get_unembedded_file_paths())  # first build: B appended
    assert _backfill(env, db) == 0
    db.clear_embeddings()
    _run_worker(env, db.get_unembedded_file_paths())
    assert _backfill(env, db) == 0

    assert db.get_tombstone_vectors(tomb_a) == {}, "A's tombstone took B's vector"
    assert _tombstone(db, tomb_a)["embedded_model"] == ""


def test_r2_an_archived_tombstone_never_takes_an_older_files_vector(env):
    """A is embedded at the path and deleted ungraded (no tombstone). B lands
    and is graded and deleted, with an archived thumbnail, before any
    embedding run. C lands and is embedded: the row overwritten is A's. B's
    tombstone gets no vector and stays in the re-embedder's queue (the
    archive is B's own image)."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    vec_a = _bytes_vector(p)
    _seed_index(env, [(p, vec_a)])
    db.save_media(_media(p))
    db.mark_embedded([p], MODEL)
    assert db.delete_media(Path(p))  # ungraded: no tombstone
    assert db.list_tombstones() == []

    _write(env, "reused.png", b"file B")
    tomb_b = _graded_delete(db, p, "fail", thumb=THUMB)
    assert db.tombstones_needing_vectors(MODEL) == [(tomb_b, THUMB)]

    _write(env, "reused.png", b"file C")
    db.save_media(_media(p))
    _run_worker(env, db.get_unembedded_file_paths())
    assert _backfill(env, db) == 0

    np.testing.assert_allclose(_index_vector(env, p), _bytes_vector(p))
    assert db.get_tombstone_vectors(tomb_b) == {}, "B's tombstone took A's vector"
    assert db.tombstones_needing_vectors(MODEL) == [(tomb_b, THUMB)]


def test_r2b_the_newer_of_two_tombstones_never_takes_the_older_files(env):
    """A is embedded, graded and deleted (T_A). B lands and is graded and
    deleted before any embedding run (T_B, never embedded). No archives. C
    lands and is embedded: T_B never gets A's vector (a ✓ image's vector on
    a ✗ label) or C's."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    vec_a = _bytes_vector(p)
    _seed_index(env, [(p, vec_a)])
    tomb_a = _graded_delete(db, p, "pass", embedded_with=MODEL)
    _write(env, "reused.png", b"file B")
    tomb_b = _graded_delete(db, p, "fail")
    assert tomb_b > tomb_a

    _write(env, "reused.png", b"file C")
    db.save_media(_media(p))
    _run_worker(env, db.get_unembedded_file_paths())
    assert _backfill(env, db) == 0

    assert db.get_tombstone_vectors(tomb_b) == {}, "T_B took A's vector"
    assert db.get_tombstone_vectors(tomb_a) == {}  # stamped: C's row is not A's


def test_evidence_lets_the_backfill_recover_a_vector_on_a_path_with_two_lives(env):
    """The R2b path before C lands: the index still holds A's row, and T_A's
    evidence says so (embedded with the index's model, nothing embedded at
    the path since), so the backfill gives T_A its own vector; T_B (never
    embedded) gets none. The old by-path guard refused both, as the path
    had two lives."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    vec_a = _bytes_vector(p)
    _seed_index(env, [(p, vec_a)])
    tomb_a = _graded_delete(db, p, "pass", embedded_with=MODEL)
    _write(env, "reused.png", b"file B")
    tomb_b = _graded_delete(db, p, "fail")

    assert _backfill(env, db) == 1

    np.testing.assert_allclose(_tombstone_vector(db, tomb_a), vec_a)
    assert db.get_tombstone_vectors(tomb_b) == {}


# ---------------------------------------------------------------------------
# The stamp: every tombstone of every path the worker appends or overwrites
# ---------------------------------------------------------------------------


def test_the_worker_stamps_every_tombstone_at_each_path_it_embeds(env):
    db = env["db"]
    eye = np.eye(DIM, dtype=np.float32)
    # Appended (the index never had the path), two lives there.
    appended = _write(env, "appended.png", b"new where the index has no row")
    twice_old = _graded_delete(db, appended, "pass")
    twice_new = _graded_delete(db, appended, "fail")
    # Overwritten (the index holds the deleted file's row).
    overwritten = _write(env, "overwritten.png", b"new where the index has a row")
    over = _graded_delete(db, overwritten, "pass", embedded_with=MODEL)
    _seed_index(env, [(overwritten, eye[0])])
    # Listed but never embedded: the file is gone, or CLIP gives no vector.
    missing = str(env["lib"] / "missing.png")
    gone = _graded_delete(db, missing, "pass")
    video = _write(env, "clip.mp4", b"the fake makes no video vector")
    vid = _graded_delete(db, video, "pass")
    # Not in this run at all.
    other = _graded_delete(db, _write(env, "elsewhere.png", b"x"), "pass")
    for path in (appended, overwritten, missing, video):
        db.save_media(_media(path))

    _run_worker(env, [appended, overwritten, missing, video])

    assert {t["id"]: t["path_rewritten"] for t in db.list_tombstones()} == {
        twice_old: 1,
        twice_new: 1,
        over: 1,
        gone: 0,
        vid: 0,
        other: 0,
    }
    np.testing.assert_allclose(
        _index_vector(env, overwritten), _bytes_vector(overwritten)
    )


def test_the_stamp_is_committed_before_each_index_save(env, monkeypatch):
    """The worker saves the index every 100 files and once at the end, and
    the server may load it between two saves: the tombstones of each path
    a save rewrites are stamped before it, so a rewritten row on disk is
    never read by path for an older tombstone."""
    db = env["db"]
    paths = [_write(env, f"f{i:03d}.png", f"file {i}".encode()) for i in range(101)]
    t_first = _graded_delete(db, paths[0], "pass")
    t_last = _graded_delete(db, paths[-1], "pass")
    for path in paths:
        db.save_media(_media(path))
    seen = []
    real_save = FaissIndexManager.save

    def save(self):
        stamps = {t["id"]: t.get("path_rewritten") for t in db.list_tombstones()}
        seen.append((stamps[t_first], stamps[t_last]))
        return real_save(self)

    monkeypatch.setattr(FaissIndexManager, "save", save)
    _run_worker(env, paths)

    # The periodic save (files 1-100), then the final one (file 101).
    assert seen == [(1, 0), (1, 1)]


def test_a_failed_stamp_keeps_the_rewritten_row_off_disk(env, monkeypatch):
    """When the stamp cannot be written the worker saves nothing: the
    deleted file's row stays on disk (so its tombstone's evidence still
    holds) and the new file is embedded again by the next run."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    vec_a = _bytes_vector(p)
    _seed_index(env, [(p, vec_a)])
    tomb = _graded_delete(db, p, "pass", embedded_with=MODEL)
    _write(env, "reused.png", b"file B")
    db.save_media(_media(p))

    def locked(self, file_paths):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(
        DatabaseManager, "mark_tombstone_paths_rewritten", locked, raising=False
    )
    monkeypatch.setattr(ew, "STAMP_RETRY_DELAYS_S", (0.0, 0.0, 0.0), raising=False)
    _run_worker(env, [p], status="error")

    np.testing.assert_allclose(_index_vector(env, p), vec_a)
    assert db.get_unembedded_file_paths() == [p]
    assert _tombstone(db, tomb)["path_rewritten"] == 0


def test_a_locked_database_is_retried_before_the_stamp_fails_the_run(env, monkeypatch):
    """A brief lock (another writer inside SQLite's busy timeout) on the
    final stamp is retried, so the run still saves and marks its batch."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    _seed_index(env, [(p, _bytes_vector(p))])
    tomb = _graded_delete(db, p, "pass", embedded_with=MODEL)
    _write(env, "reused.png", b"file B")
    db.save_media(_media(p))
    real = DatabaseManager.mark_tombstone_paths_rewritten
    attempts = []

    def briefly_locked(self, file_paths):
        attempts.append(list(file_paths))
        if len(attempts) < 3:
            raise sqlite3.OperationalError("database is locked")
        return real(self, file_paths)

    monkeypatch.setattr(
        DatabaseManager, "mark_tombstone_paths_rewritten", briefly_locked
    )
    monkeypatch.setattr(ew, "STAMP_RETRY_DELAYS_S", (0.0, 0.0, 0.0), raising=False)
    _run_worker(env, [p])

    assert len(attempts) == 3
    assert _tombstone(db, tomb)["path_rewritten"] == 1
    np.testing.assert_allclose(_index_vector(env, p), _bytes_vector(p))
    assert db.get_unembedded_file_paths() == []


def test_a_failed_index_save_marks_nothing(env, monkeypatch):
    """A file is marked embedded only once the index holding its row is on
    disk: the marks are the evidence a deleted file's row is in the index."""
    db = env["db"]
    paths = [_write(env, f"f{i}.png", f"file {i}".encode()) for i in range(3)]
    for path in paths:
        db.save_media(_media(path))
    monkeypatch.setattr(FaissIndexManager, "save", lambda self: False)

    _run_worker(env, paths)

    assert sorted(db.get_unembedded_file_paths()) == sorted(paths)
    assert _mark_times(db) == {}


def test_a_batch_whose_save_failed_is_marked_by_the_next_save(env, monkeypatch):
    """The periodic save after 100 files fails, the final one succeeds: the
    final save writes every row, so the whole run is marked then."""
    db = env["db"]
    paths = [_write(env, f"f{i:03d}.png", f"file {i}".encode()) for i in range(101)]
    for path in paths:
        db.save_media(_media(path))
    real_save = FaissIndexManager.save
    unmarked = []  # how many of the run's files are unmarked at each save

    def first_save_fails(self):
        unmarked.append(len(db.get_unembedded_file_paths()))
        return False if len(unmarked) == 1 else real_save(self)

    monkeypatch.setattr(FaissIndexManager, "save", first_save_fails)
    _run_worker(env, paths)

    # The first 100 were not marked after their save failed: they wait for
    # the final save, which writes every row.
    assert unmarked == [101, 101]
    assert db.get_unembedded_file_paths() == []
    np.testing.assert_allclose(_index_vector(env, paths[0]), _bytes_vector(paths[0]))


# ---------------------------------------------------------------------------
# The in-memory index can lag the disk: evidence by load time
# ---------------------------------------------------------------------------


def test_a_capture_never_reads_an_in_memory_index_older_than_the_files_mark(
    env, monkeypatch
):
    """The server's in-memory index is replaced only after a run completes
    (never after a failed one). B lands on A's path and is embedded and
    marked while the server still holds the index with A's row: deleting B
    then must not take that row. B keeps its evidence, so the backfill on
    the next load gives it its own vector."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    vec_a = _bytes_vector(p)
    _seed_index(env, [(p, vec_a)])
    db.save_media(_media(p))
    db.mark_embedded([p], MODEL)
    _marks_a_while_ago(db)  # A was embedded well before the server loaded
    _server_index(env, db, monkeypatch)
    tomb_a = _graded_delete(db, p, "pass")
    np.testing.assert_allclose(_tombstone_vector(db, tomb_a), vec_a)

    _write(env, "reused.png", b"file B")  # B lands on the freed path
    vec_b = _bytes_vector(p)
    db.save_media(_media(p))
    _run_worker(env, db.get_unembedded_file_paths())  # B's row on disk, B marked
    db.set_grade(p, "fail")  # before the server swaps in the reloaded index
    assert db.delete_media(Path(p))
    tomb_b = max(t["id"] for t in db.list_tombstones() if t["file_path"] == p)

    assert db.get_tombstone_vectors(tomb_b) == {}, "T_B took the in-memory A row"
    row = _tombstone(db, tomb_b)
    assert row["embedded_model"] == MODEL and row["embedded_at"] is not None
    assert _backfill(env, db) == 1  # the next load: B's own row
    np.testing.assert_allclose(_tombstone_vector(db, tomb_b), vec_b)
    np.testing.assert_allclose(_tombstone_vector(db, tomb_a), vec_a)


def test_a_capture_waits_out_the_marks_one_second_resolution(env, monkeypatch):
    """A mark is stored to the second: an index loaded in the second the
    file was marked may predate it, so it is not read."""
    db = env["db"]
    p = _write(env, "a.png", b"file A")
    _seed_index(env, [(p, _bytes_vector(p))])
    db.save_media(_media(p))
    db.mark_embedded([p], MODEL)
    fm = _server_index(env, db, monkeypatch)
    (mark,) = _mark_times(db).values()
    stored = datetime.fromisoformat(mark).replace(tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(fm, "_loaded_at", stored + 0.9, raising=False)

    tomb = _graded_delete(db, p, "pass")

    assert db.get_tombstone_vectors(tomb) == {}


# ---------------------------------------------------------------------------
# Rows the code before the fix left stale (user_version 7)
# ---------------------------------------------------------------------------


def test_v7_re_embeds_a_live_file_left_with_a_deleted_files_vector(env):
    """Before the fix a file landing on a deleted file's path kept the
    deleted file's row while ``media_hashes`` said it was embedded, so no
    later incremental build looked at it again. v7 resets the flag of every
    live path that also has a tombstone, and the next incremental build
    gives the live file its own row."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    vec_a = _bytes_vector(p)
    _seed_index(env, [(p, vec_a)])
    tomb = _graded_delete(db, p, "pass", embedded_with=MODEL)
    _write(env, "reused.png", b"file B")
    db.save_media(_media(p))
    db.mark_embedded([p], MODEL)  # the build before the fix: A's row kept
    assert db.get_unembedded_file_paths() == []
    _set_user_version(db, 6)

    db = DatabaseManager(env["data"])  # the upgrade
    assert db.get_unembedded_file_paths() == [p]
    _run_worker(env, db.get_unembedded_file_paths())

    np.testing.assert_allclose(_index_vector(env, p), _bytes_vector(p))
    assert db.get_unembedded_file_paths() == []
    assert db.get_tombstone_vectors(tomb) == {}


def test_v7_takes_the_evidence_from_marks_made_before_the_fix(env):
    """A reused path whose earlier file left no tombstone is invisible to
    v7: the live file keeps ``has_embedding = 1`` over the earlier file's
    row. Its mark predates the fix, so v7 clears its time: when it is graded
    and deleted it has no evidence (NULL, the old guards), so its archived
    tombstone stays the re-embedder's and never takes the earlier row."""
    db = env["db"]
    p = _write(env, "reused.png", b"file A")
    vec_a = _bytes_vector(p)
    _seed_index(env, [(p, vec_a)])
    db.save_media(_media(p))
    db.mark_embedded([p], MODEL)
    assert db.delete_media(Path(p))  # ungraded: no tombstone
    _write(env, "reused.png", b"file B")
    db.save_media(_media(p))
    db.mark_embedded([p], MODEL)  # the build before the fix: A's row kept at p
    _set_user_version(db, 6)

    db = DatabaseManager(env["data"])  # the upgrade: no tombstone at p
    assert db.get_unembedded_file_paths() == []  # B stays "embedded"
    assert _mark_times(db) == {p: None}
    db.set_grade(p, "fail")
    assert db.delete_media(Path(p), THUMB)  # archived, no index in memory
    (tomb_b,) = [t["id"] for t in db.list_tombstones()]
    assert _tombstone(db, tomb_b)["embedded_model"] is None

    assert _backfill(env, db) == 0
    assert db.get_tombstone_vectors(tomb_b) == {}, "T_B took the earlier file's row"
    assert db.tombstones_needing_vectors(MODEL) == [(tomb_b, THUMB)]


def test_a_file_marked_after_the_upgrade_is_evidence_again(env, monkeypatch):
    """v7 only clears the marks it found: the fixed worker's own marks carry
    a time again, so a file it embeds is captured at deletion as usual."""
    db = env["db"]
    p = _write(env, "a.png", b"file A")
    db.save_media(_media(p))
    db.mark_embedded([p], MODEL)
    _set_user_version(db, 6)
    db = DatabaseManager(env["data"])  # v7 clears the old mark's time
    assert _mark_times(db) == {p: None}
    db.clear_embeddings()
    _run_worker(env, db.get_unembedded_file_paths())  # the fixed worker
    assert _mark_times(db)[p] is not None
    _marks_a_while_ago(db)
    _server_index(env, db, monkeypatch)

    tomb = _graded_delete(db, p, "pass")

    assert _tombstone(db, tomb)["embedded_model"] == MODEL
    np.testing.assert_allclose(_tombstone_vector(db, tomb), _bytes_vector(p))


def test_v7_resets_only_live_paths_that_have_a_tombstone(env, caplog):
    db = env["db"]
    reused = "/lib/reused.png"  # embedded, has a tombstone: re-embedded
    skipped = "/lib/skipped.png"  # skipped (unreadable), has a tombstone: retried
    kept = "/lib/kept.png"  # embedded, no tombstone: left alone
    for path in (reused, skipped, "/lib/gone.png"):
        _graded_delete(db, path)
    for path in (reused, skipped, kept):
        db.save_media(_media(path))
    db.mark_embedded([reused, kept], MODEL)
    db.mark_embedding_skipped([skipped])
    _set_user_version(db, 6)

    caplog.set_level(logging.DEBUG, logger="metascan.core.database_sqlite")
    db = DatabaseManager(env["data"])

    assert _user_version(db) == 7
    assert _flags(db) == {reused: 0, skipped: 0, kept: 1}
    # The mark kept (no tombstone at its path) predates the fix: no time.
    assert _mark_times(db)[kept] is None
    [line] = [
        r
        for r in caplog.records
        if r.getMessage().startswith("Database upgraded to version 7")
    ]
    assert line.levelno == logging.INFO
    assert "2 live file(s)" in line.getMessage()
    assert "1 earlier mark(s)" in line.getMessage()
    assert "/lib/" not in line.getMessage()  # counts only

    # Once: a later open resets nothing and keeps the new marks' times.
    db.mark_embedded([reused], MODEL)
    caplog.clear()
    DatabaseManager(env["data"])
    assert _flags(db)[reused] == 1 and _mark_times(db)[reused] is not None
    assert not [r for r in caplog.records if "version 7" in r.getMessage()]


def test_v7_on_a_library_with_nothing_to_reset_logs_at_debug(env, caplog):
    db = env["db"]
    db.save_media(_media("/lib/a.png"))  # never embedded
    _set_user_version(db, 6)
    caplog.set_level(logging.DEBUG, logger="metascan.core.database_sqlite")
    DatabaseManager(env["data"])
    [line] = [
        r
        for r in caplog.records
        if r.getMessage().startswith("Database upgraded to version 7")
    ]
    assert line.levelno == logging.DEBUG and "0 live file(s)" in line.getMessage()
    assert "0 earlier mark(s)" in line.getMessage()


def test_v7_failure_changes_nothing_and_stops_the_open(env):
    db = env["db"]
    _graded_delete(db, "/lib/reused.png")
    db.save_media(_media("/lib/reused.png"))
    db.mark_embedded(["/lib/reused.png"], MODEL)
    conn = sqlite3.connect(str(db.db_file))
    try:
        conn.execute(
            "CREATE TRIGGER v7_refused BEFORE UPDATE ON media_hashes "
            "BEGIN SELECT RAISE(ABORT, 'refused'); END"
        )
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(MigrationError, match="version 7"):
        DatabaseManager(env["data"])
    assert _user_version(db) == 6
    assert _flags(db) == {"/lib/reused.png": 1}

    conn = sqlite3.connect(str(db.db_file))
    try:
        conn.execute("DROP TRIGGER v7_refused")
        conn.commit()
    finally:
        conn.close()
    DatabaseManager(env["data"])  # the next start retries
    assert _user_version(db) == 7
    assert _flags(db) == {"/lib/reused.png": 0}
