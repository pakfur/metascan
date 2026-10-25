"""Tombstones written while the FAISS index wasn't loaded get their CLIP
vector backfilled once it loads (final-review item 2), into
``tombstone_vectors`` under the index's CLIP model -- and never from a path
that may hold another file's vector now (Review Focus 5): not for a
tombstone that already has a vector for any model, one with an archived
thumbnail, or a path with another life."""

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


def test_backfill_lists_only_tombstones_without_any_vector(db):
    a = _graded_delete(db, "/p/a.png")
    b = _graded_delete(db, "/p/b.png", "fail")
    assert db.tombstones_for_vector_backfill() == [
        (a, "/p/a.png"),
        (b, "/p/b.png"),
    ]
    vec = np.ones(32, dtype=np.float32)
    assert db.add_tombstone_vectors([(a, "clip-x", vec.tobytes())]) == 1
    # A vector for any CLIP model takes it off the list, whatever the index's
    # model is: it was deleted before an index of another model was built.
    assert db.tombstones_for_vector_backfill() == [(b, "/p/b.png")]


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

    assert filled == 2
    assert np.array_equal(_vector(db, a, "clip-medium"), va)
    assert np.array_equal(_vector(db, b, "clip-medium"), vb)
    assert db.get_tombstone_vectors(nothere) == {}
    # A tombstone with a vector -- for this model or another -- is not even
    # looked up: the "medium" index's vector at its path is another file's.
    assert np.array_equal(_vector(db, already, "clip-medium"), have)
    assert set(db.get_tombstone_vectors(small_only)) == {"clip-small"}
    assert np.array_equal(_vector(db, small_only, "clip-small"), have)
    for key in ("/p/already.png", "/p/small_only.png"):
        assert key not in fm.calls and to_native_path(key) not in fm.calls


def test_backfill_never_borrows_across_models(db):
    """Review Focus 5 after a CLIP switch: an image deleted under the "small"
    index (its vector captured then), and a "medium" index built afterwards
    from the files live at that time -- so a vector at the deleted image's
    path can only be another file's (here one deleted again with no trace:
    never graded, no category, no lineage)."""
    small = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (small, "clip-small"))
    t = _graded_delete(db, "/p/switched.png")
    db.set_embedding_lookup(None)
    db.save_media(_media("/p/switched.png"))  # another file, same name
    assert db.delete_media(Path("/p/switched.png"))  # leaves no tombstone
    assert [x["id"] for x in db.list_tombstones()] == [t]
    other = np.full(32, 9.0, dtype=np.float32)
    fm = FakeFaiss({"/p/switched.png": other}, model_key="clip-medium")

    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 0

    assert db.get_tombstone_vectors(t) == {"clip-small": small.tobytes()}
    assert fm.calls == []


def test_backfill_leaves_archived_tombstones_to_the_reembedder(db):
    """A deleted graded image with an archived thumbnail gets its vector
    from its own image (the re-embedder), never by its old path."""
    from metascan.core.training_archive import archive_dir

    db.save_media(_media("/p/kept.png"))
    db.set_grade("/p/kept.png", "pass")
    root = archive_dir(Path(db.db_path))
    root.mkdir(parents=True, exist_ok=True)
    (root / "0123456789abcdef0123456789abcdef.jpg").write_bytes(b"jpeg")
    thumb = "archive/thumbs/0123456789abcdef0123456789abcdef.jpg"
    assert db.delete_media(Path("/p/kept.png"), thumb)
    (t,) = [x["id"] for x in db.list_tombstones()]
    fm = FakeFaiss({"/p/kept.png": np.ones(32, dtype=np.float32)})

    assert db.tombstones_for_vector_backfill() == []
    assert judgments_service.backfill_tombstone_embeddings(db, fm) == 0
    assert db.get_tombstone_vectors(t) == {} and fm.calls == []
    assert db.tombstones_needing_vectors("clip-medium") == [(t, thumb)]


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
    assert [t for t, _ in db.tombstones_for_vector_backfill()] == []
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
