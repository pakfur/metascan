"""A path's FAISS vector follows its file's current content.

``FaissIndexManager.add`` used to keep the first vector a path ever had, so a
"Rebuild all" with the same CLIP model, a full rescan and a new file landing
on a deleted file's path all recomputed a vector and then threw it away.
These tests run the real embedding worker (with a fake CLIP whose vector is a
pure function of the file's bytes) and check the stored rows.

Before the worker overwrites a path's row it hands the old vector to the
newest graded deleted image at that path when it has no vector for the
index's model: the worker is the only writer of the on-disk index, so that
row belongs to the file deleted there. Without the hand-off, an archive-less
tombstone's by-path backfill would pick up the newer file's vector.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import List

import numpy as np
import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.embedding_manager import FaissIndexManager
from metascan.core.media import Media
from metascan.workers import embedding_worker as ew

DIM = 32
MODEL = "small"


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


def _seed_index(env, rows) -> None:
    fm = FaissIndexManager(env["index"])
    fm.create(embedding_dim=DIM, model_key=MODEL)
    for path, vec in rows:
        fm.add(path, vec)
    assert fm.save()


def _run_worker(env, paths: List[str]) -> None:
    task = {
        "model_key": MODEL,
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
    assert progress["status"] == "complete", progress


def _index_vector(env, path: str) -> np.ndarray:
    fm = FaissIndexManager(env["index"])
    assert fm.load()
    vec = fm.get_embedding(path)
    assert vec is not None
    return vec


def _tombstone_vector(db, tomb_id: int):
    blob = db.get_tombstone_vectors(tomb_id).get(MODEL)
    return None if blob is None else np.frombuffer(blob, dtype=np.float32)


def _graded_delete(db, path: str, grade: str = "pass") -> int:
    db.save_media(_media(path))
    db.set_grade(path, grade)
    db.delete_media(Path(path))
    return max(t["id"] for t in db.list_tombstones() if t["file_path"] == path)


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
    db = env["db"]
    path = _write(env, "reused.png", b"the second file at this path")
    first = _unit(2)
    _seed_index(env, [(path, first)])
    tomb = _graded_delete(db, path)  # the first file, deleted with no vector
    assert _tombstone_vector(db, tomb) is None

    db.save_media(_media(path))  # the second file lands on the path
    _run_worker(env, db.get_unembedded_file_paths())

    np.testing.assert_allclose(_index_vector(env, path), _bytes_vector(path))
    # The row the worker overwrote was the first file's: its tombstone keeps it.
    np.testing.assert_allclose(_tombstone_vector(db, tomb), first)


def test_only_the_newest_graded_tombstone_without_a_vector_adopts(env):
    db = env["db"]
    eye = np.eye(DIM, dtype=np.float32)

    # Newest tombstone ungraded: nothing adopts, not even an older graded one.
    a = _write(env, "a.png", b"a now")
    older = _graded_delete(db, a)
    db.save_media(_media(a))
    db.mark_seen(a)  # a viewed-only item still leaves an (ungraded) tombstone
    db.delete_media(Path(a))
    newest = max(t["id"] for t in db.list_tombstones() if t["file_path"] == a)
    assert newest != older
    # Newest tombstone graded but already holding a vector: kept as it was.
    b = _write(env, "b.png", b"b now")
    has_one = _graded_delete(db, b)
    db.add_tombstone_vectors([(has_one, MODEL, eye[5].tobytes())])
    # A video's tombstone never adopts (images only).
    c = _write(env, "c.mp4", b"not decoded by the fake")
    video = _graded_delete(db, c)

    _seed_index(env, [(a, eye[0]), (b, eye[1]), (c, eye[2])])
    for p in (a, b, c):
        db.save_media(_media(p))
    _run_worker(env, [a, b])

    assert _tombstone_vector(db, older) is None
    assert _tombstone_vector(db, newest) is None
    np.testing.assert_allclose(_tombstone_vector(db, has_one), eye[5])
    assert _tombstone_vector(db, video) is None


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
