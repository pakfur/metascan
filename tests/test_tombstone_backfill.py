"""Tombstones written while the FAISS index wasn't loaded get their CLIP
vector backfilled once it loads (final-review item 2)."""

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


def _judged_delete(db, path, verdict="favorite"):
    db.save_media(_media(path))
    db.set_verdict(path, verdict)
    db.delete_media(Path(path))


def test_missing_embedding_listing_and_batch_set(db):
    _judged_delete(db, "/p/a.png")
    _judged_delete(db, "/p/b.png", "pass")
    rows = db.tombstones_missing_embedding()
    assert [p for _, p in rows] == ["/p/a.png", "/p/b.png"]
    vec = np.ones(32, dtype=np.float32)
    assert db.set_tombstone_embeddings([(rows[0][0], vec.tobytes(), "clip-x")]) == 1
    assert [p for _, p in db.tombstones_missing_embedding()] == ["/p/b.png"]


def test_set_never_overwrites_an_existing_embedding(db):
    vec = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (vec, "clip-small"))
    _judged_delete(db, "/p/a.png")
    [t] = db.list_tombstones()
    other = np.zeros(32, dtype=np.float32).tobytes()
    assert db.set_tombstone_embedding(t["id"], other, "clip-large") == 0
    [t2] = db.list_tombstones()
    assert t2["embedding_model"] == "clip-small"
    assert np.array_equal(np.frombuffer(t2["embedding"], dtype=np.float32), vec)


def test_backfill_fills_only_null_rows_from_native_or_posix_key(db):
    have = np.arange(32, dtype=np.float32)
    db.set_embedding_lookup(lambda p: (have, "clip-small"))
    _judged_delete(db, "/p/already.png")
    db.set_embedding_lookup(None)
    _judged_delete(db, "/p/a.png")
    _judged_delete(db, "/p/b.png", "pass")
    _judged_delete(db, "/p/nothere.png")

    va = np.full(32, 1.0, dtype=np.float32)
    vb = np.full(32, 2.0, dtype=np.float32)
    fm = FakeFaiss(
        {
            to_native_path("/p/a.png"): va,  # native key
            "/p/b.png": vb,  # posix key
            "/p/already.png": np.zeros(32, dtype=np.float32),
        }
    )

    filled = judgments_service.backfill_tombstone_embeddings(db, fm)

    assert filled == 2
    by_path = {t["file_path"]: t for t in db.list_tombstones()}
    assert np.array_equal(
        np.frombuffer(by_path["/p/a.png"]["embedding"], dtype=np.float32), va
    )
    assert np.array_equal(
        np.frombuffer(by_path["/p/b.png"]["embedding"], dtype=np.float32), vb
    )
    assert by_path["/p/a.png"]["embedding_model"] == "clip-medium"
    assert by_path["/p/nothere.png"]["embedding"] is None
    # The row that already had a vector is not even looked up.
    assert by_path["/p/already.png"]["embedding_model"] == "clip-small"
    assert "/p/already.png" not in fm.calls


def test_backfill_noop_when_index_not_loaded_or_no_model_key(db):
    db.set_embedding_lookup(None)
    _judged_delete(db, "/p/a.png")
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
    assert db.list_tombstones()[0]["embedding"] is None


def test_backfill_reads_faiss_outside_the_db_lock(db):
    db.set_embedding_lookup(None)
    _judged_delete(db, "/p/a.png")

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
