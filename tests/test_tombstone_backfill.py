"""Tombstones written while the FAISS index wasn't loaded get their CLIP
vector backfilled once it loads (final-review item 2), into
``tombstone_vectors`` under the index's CLIP model -- and never from a path
that may hold another file's vector now (Review Focus 5)."""

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from backend.services import judgments_service
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.utils.path_utils import to_native_path


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


class FakeFaiss:
    def __init__(self, vectors, model_key="clip-medium", loaded=True):
        self.vectors = vectors
        self.meta = {"model_key": model_key}
        self.is_loaded = loaded
        self.calls = []

    def get_embedding(self, key):
        self.calls.append(key)
        return self.vectors.get(key)


def _graded_delete(db, path, grade="pass") -> int:
    db.save_media(_media(path))
    db.set_grade(path, grade)
    db.delete_media(Path(path))
    return max(t["id"] for t in db.list_tombstones() if t["file_path"] == path)


def _vector(db, tomb_id, model):
    blob = db.get_tombstone_vectors(tomb_id).get(model)
    return None if blob is None else np.frombuffer(blob, dtype=np.float32)


def test_backfill_listing_and_per_model_add(db):
    a = _graded_delete(db, "/p/a.png")
    b = _graded_delete(db, "/p/b.png", "fail")
    assert db.tombstones_for_vector_backfill("clip-x") == [
        (a, "/p/a.png"),
        (b, "/p/b.png"),
    ]
    vec = np.ones(32, dtype=np.float32)
    assert db.add_tombstone_vectors([(a, "clip-x", vec.tobytes())]) == 1
    assert db.tombstones_for_vector_backfill("clip-x") == [(b, "/p/b.png")]
    # Per model: "a" still needs one for another CLIP model.
    assert [t for t, _ in db.tombstones_for_vector_backfill("clip-y")] == [a, b]


def test_delete_time_vector_is_kept_per_model_and_never_overwritten(db):
    vec = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (vec, "clip-small"))
    t = _graded_delete(db, "/p/a.png")
    db.set_embedding_lookup(None)
    (row,) = db.list_tombstones()
    # The legacy single-vector columns are no longer written.
    assert row["embedding"] is None and row["embedding_model"] is None
    other = np.zeros(32, dtype=np.float32).tobytes()
    assert db.add_tombstone_vectors([(t, "clip-small", other)]) == 0
    assert np.array_equal(_vector(db, t, "clip-small"), vec)
    assert db.add_tombstone_vectors([(t, "clip-large", other)]) == 1
    assert set(db.get_tombstone_vectors(t)) == {"clip-small", "clip-large"}


def test_backfill_fills_missing_vectors_from_native_or_posix_key(db):
    have = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (have, "clip-medium"))
    already = _graded_delete(db, "/p/already.png")
    db.set_embedding_lookup(lambda p: (have, "clip-small"))
    small_only = _graded_delete(db, "/p/small_only.png")
    db.set_embedding_lookup(None)
    a = _graded_delete(db, "/p/a.png")
    b = _graded_delete(db, "/p/b.png", "fail")
    nothere = _graded_delete(db, "/p/nothere.png")

    va = np.full(32, 1.0, dtype=np.float32)
    vb = np.full(32, 2.0, dtype=np.float32)
    vs = np.full(32, 3.0, dtype=np.float32)
    fm = FakeFaiss(
        {
            to_native_path("/p/a.png"): va,  # native key
            "/p/b.png": vb,  # posix key
            "/p/small_only.png": vs,
            "/p/already.png": np.zeros(32, dtype=np.float32),
        }
    )

    filled = judgments_service.backfill_tombstone_embeddings(db, fm)

    assert filled == 3
    assert np.array_equal(_vector(db, a, "clip-medium"), va)
    assert np.array_equal(_vector(db, b, "clip-medium"), vb)
    # A vector for another CLIP model does not count: the index's is added.
    assert np.array_equal(_vector(db, small_only, "clip-medium"), vs)
    assert np.array_equal(_vector(db, small_only, "clip-small"), have)
    assert db.get_tombstone_vectors(nothere) == {}
    # The row that already had a vector for this model is not even looked up.
    assert np.array_equal(_vector(db, already, "clip-medium"), have)
    assert "/p/already.png" not in fm.calls


def test_backfill_skips_a_reused_path(db):
    """Review Focus 5: the index keys a vector by path (and never replaces
    one), so a path that held another file after the deletion may give the
    tombstone that file's vector. Such tombstones get none by path (the
    re-embedder computes theirs from the archived thumbnail)."""
    # A ✓ image deleted while the index was not in memory; a new file lives
    # at its path now.
    reused = _graded_delete(db, "/p/reused.png")
    db.save_media(_media("/p/reused.png"))
    # A ✗ image whose path was reused by a file that was deleted as well:
    # two lives of one path, and nothing says whose vector the index holds.
    twice_old = _graded_delete(db, "/p/twice.png", "fail")
    twice_new = _graded_delete(db, "/p/twice.png", "pass")
    # An ordinary deletion: one life, nothing at the path now.
    plain = _graded_delete(db, "/p/plain.png")
    fm = FakeFaiss(
        {
            "/p/reused.png": np.full(32, 1.0, dtype=np.float32),
            "/p/twice.png": np.full(32, 2.0, dtype=np.float32),
            "/p/plain.png": np.full(32, 3.0, dtype=np.float32),
        }
    )

    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 1

    assert np.array_equal(
        _vector(db, plain, "clip-medium"), np.full(32, 3.0, dtype=np.float32)
    )
    for t in (reused, twice_old, twice_new):
        assert db.get_tombstone_vectors(t) == {}
    assert [t for t, _ in db.tombstones_for_vector_backfill("clip-medium")] == []
    # The skipped paths are not even read from the index.
    assert all(k not in fm.calls for k in ("/p/reused.png", "/p/twice.png"))
    assert all(
        to_native_path(k) not in fm.calls for k in ("/p/reused.png", "/p/twice.png")
    )


def test_backfill_noop_when_index_not_loaded_or_no_model_key(db):
    db.set_embedding_lookup(None)
    t = _graded_delete(db, "/p/a.png")
    v = {"/p/a.png": np.ones(32, dtype=np.float32)}
    assert (
        judgments_service.backfill_tombstone_embeddings(db, FakeFaiss(v, loaded=False))
        == 0
    )
    assert (
        judgments_service.backfill_tombstone_embeddings(
            db, FakeFaiss(v, model_key=None)
        )
        == 0
    )
    assert db.get_tombstone_vectors(t) == {}


def test_backfill_reads_faiss_outside_the_db_lock(db):
    db.set_embedding_lookup(None)
    _graded_delete(db, "/p/a.png")

    class LockCheckingFaiss(FakeFaiss):
        def get_embedding(self, key):
            assert not db.lock.locked(), "FAISS read under the DB lock"
            return super().get_embedding(key)

    fm = LockCheckingFaiss({"/p/a.png": np.ones(32, dtype=np.float32)})
    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 1


def test_loading_the_faiss_manager_triggers_backfill(monkeypatch, tmp_path):
    from backend.api import similarity

    started = []

    class FakeManager:
        def __init__(self, index_dir):
            self.is_loaded = False

        def load(self):
            self.is_loaded = True
            return True

    class FakeQueue:
        index_dir = tmp_path

    monkeypatch.setattr(similarity, "_faiss_manager", None)
    monkeypatch.setattr(similarity, "FaissIndexManager", FakeManager)
    monkeypatch.setattr(similarity, "_get_embedding_queue", lambda: FakeQueue())
    monkeypatch.setattr(
        similarity, "_start_tombstone_backfill", lambda fm: started.append(fm)
    )

    fm = similarity._get_faiss_manager()
    assert started == [fm] and fm.is_loaded
    similarity._get_faiss_manager()  # cached: no second backfill
    assert started == [fm]
