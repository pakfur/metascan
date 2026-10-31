"""A moved file keeps its FAISS vector: the row is renamed, not re-embedded.

``move_media_batch`` queues ``faiss_pending_renames`` rows in its own
transaction; the embedding worker (at its start) or the server (while no
worker is alive) renames the rows, stamps the tombstones at both paths
before the save, saves, and clears the rows. Vectors are dim 32 and
normalised (FAISS SIMD alignment on ARM).
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List

import numpy as np
import pytest

from backend.api import similarity
from backend.services.judgments_service import faiss_embedding_lookup
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.embedding_manager import FaissIndexManager
from metascan.core.faiss_renames import apply_pending_renames
from metascan.core.media import Media
from metascan.workers import embedding_worker as ew

DIM = 32
MODEL = "small"


def _unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


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


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    index = tmp_path / "index"
    data.mkdir()
    index.mkdir()
    db = DatabaseManager(data)
    yield {"db": db, "index": index, "lib": tmp_path, "data": data}
    db.close()


def _p(env, name: str) -> str:
    return str(env["lib"] / name)


def _fm(env, rows) -> FaissIndexManager:
    fm = FaissIndexManager(env["index"])
    fm.create(embedding_dim=DIM, model_key=MODEL)
    for path, vec in rows:
        fm.add(path, vec)
    assert fm.save()
    return fm


def _embedded_media(env, path: str) -> None:
    env["db"].save_media(_media(path))
    env["db"].mark_embedded([path], MODEL)


def _disk(env) -> FaissIndexManager:
    fm = FaissIndexManager(env["index"])
    assert fm.load()
    return fm


# ---------------------------------------------------------------- manager


def test_rename_keeps_the_vector_and_drops_the_old_path():
    fm = FaissIndexManager(Path("/tmp/does-not-matter-faiss-rename"))
    fm.create(embedding_dim=DIM, model_key=MODEL)
    v = _unit(1)
    fm.add("/a", v)
    fm.add("/z", _unit(2))
    assert fm.rename([("/a", "/b")]) == 1
    assert not fm.has_file("/a")
    assert np.allclose(fm.get_embedding("/b"), v)
    assert fm.search(v, 1)[0][0] == "/b"
    assert fm.size == 2


def test_rename_skips_a_path_that_is_not_in_the_index():
    fm = FaissIndexManager(Path("/tmp/does-not-matter-faiss-rename2"))
    fm.create(embedding_dim=DIM, model_key=MODEL)
    fm.add("/a", _unit(1))
    assert fm.rename([("/nope", "/b")]) == 0
    assert fm.has_file("/a") and not fm.has_file("/b")


def test_rename_overwrites_a_stale_row_at_the_target():
    fm = FaissIndexManager(Path("/tmp/does-not-matter-faiss-rename3"))
    fm.create(embedding_dim=DIM, model_key=MODEL)
    mine, stale = _unit(1), _unit(2)
    fm.add("/a", mine)
    fm.add("/b", stale)
    assert fm.rename([("/a", "/b")]) == 1
    assert fm.size == 1
    assert np.allclose(fm.get_embedding("/b"), mine)


def test_rename_applies_a_chain_at_once():
    fm = FaissIndexManager(Path("/tmp/does-not-matter-faiss-rename4"))
    fm.create(embedding_dim=DIM, model_key=MODEL)
    va, vb = _unit(1), _unit(2)
    fm.add("/a", va)
    fm.add("/b", vb)
    assert fm.rename([("/a", "/b"), ("/b", "/c")]) == 2
    assert np.allclose(fm.get_embedding("/b"), va)
    assert np.allclose(fm.get_embedding("/c"), vb)
    assert fm.size == 2


# ---------------------------------------------------------------- queueing


def test_a_move_queues_its_renames_in_the_same_transaction(env):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    db.save_media(_media(a))
    assert db.move_media_batch_applied([(a, b)]) == [(a, b)]
    assert db.list_faiss_pending_renames() == [(a, b)]
    # a skipped move queues nothing
    assert db.move_media_batch_applied([(a, _p(env, "c.png"))]) == []
    assert db.list_faiss_pending_renames() == [(a, b)]


def test_chained_moves_collapse_and_keep_the_old_row(env):
    db = env["db"]
    a, b, c = (_p(env, n) for n in ("a.png", "b.png", "c.png"))
    db.save_media(_media(a))
    db.move_media_batch_applied([(a, b)])
    db.move_media_batch_applied([(b, c)])
    assert db.list_faiss_pending_renames() == [(a, c)]
    db.move_media_batch_applied([(c, a)])  # back where it started
    assert db.list_faiss_pending_renames() == []


def test_a_chain_inside_one_batch_queues_both_rows(env):
    db = env["db"]
    a, b, c = (_p(env, n) for n in ("a.png", "b.png", "c.png"))
    db.save_media(_media(a))
    db.save_media(_media(b))
    db.move_media_batch_applied([(a, b), (b, c)])
    assert sorted(db.list_faiss_pending_renames()) == sorted([(a, b), (b, c)])


# ---------------------------------------------------------------- applying


def test_apply_renames_saves_and_clears_the_pending_rows(env):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    v = _unit(3)
    _fm(env, [(a, v)])
    _embedded_media(env, a)
    db.move_media_batch_applied([(a, b)])

    fm = _disk(env)
    result = apply_pending_renames(db, fm)
    assert (result.renamed, result.cleared, result.failed) == (1, 1, False)
    assert db.list_faiss_pending_renames() == []
    on_disk = _disk(env)
    assert not on_disk.has_file(a)
    assert np.allclose(on_disk.get_embedding(b), v)


def test_a_file_never_embedded_is_skipped_and_the_row_clears(env):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    stale = _unit(4)
    _fm(env, [(a, stale)])  # a deleted file's row at a
    db.save_media(_media(a))  # never embedded
    db.move_media_batch_applied([(a, b)])

    result = apply_pending_renames(db, _disk(env))
    assert (result.renamed, result.cleared) == (0, 1)
    assert not _disk(env).has_file(b)
    assert db.list_faiss_pending_renames() == []


def test_a_replaced_index_drops_the_pending_rows_without_error(env):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    _embedded_media(env, a)
    db.move_media_batch_applied([(a, b)])
    fresh = FaissIndexManager(env["index"])
    fresh.create(embedding_dim=DIM, model_key="large")  # a CLIP switch
    result = apply_pending_renames(db, fresh)
    assert (result.renamed, result.cleared, result.failed) == (0, 1, False)


def test_tombstones_at_either_path_are_stamped_before_the_save(env):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    # deleted files with tombstones at both paths
    for path in (a, b):
        db.save_media(_media(path))
        db.set_grade(path, "pass")
        assert db.delete_media(Path(path))
    _fm(env, [(a, _unit(5)), (b, _unit(6))])
    _embedded_media(env, a)
    db.move_media_batch_applied([(a, b)])
    assert all(t["path_rewritten"] == 0 for t in db.list_tombstones())

    order: List[str] = []
    real_mark = db.mark_tombstone_paths_rewritten
    db.mark_tombstone_paths_rewritten = lambda p: (  # type: ignore[method-assign]
        order.append("stamp"),
        real_mark(p),
    )[1]
    fm = _disk(env)
    real_save = fm.save
    fm.save = lambda: (order.append("save"), real_save())[1]  # type: ignore
    assert apply_pending_renames(db, fm).renamed == 1
    assert order == ["stamp", "save"]
    assert all(t["path_rewritten"] == 1 for t in db.list_tombstones())


def test_a_failed_stamp_saves_nothing_and_keeps_the_rows_pending(env):
    import sqlite3

    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    v = _unit(7)
    _fm(env, [(a, v)])
    _embedded_media(env, a)
    db.move_media_batch_applied([(a, b)])

    def boom(_paths):
        raise sqlite3.OperationalError("database is locked")

    db.mark_tombstone_paths_rewritten = boom  # type: ignore[method-assign]
    fm = _disk(env)
    result = apply_pending_renames(db, fm, delays=(), sleep=lambda s: None)
    assert result.failed
    assert db.list_faiss_pending_renames() == [(a, b)]
    assert _disk(env).has_file(a) and not _disk(env).has_file(b)
    assert fm.has_file(a)  # the unsaved change was thrown away


def test_a_later_delete_at_the_new_path_still_has_its_evidence(env, monkeypatch):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    v = _unit(8)
    _fm(env, [(a, v)])
    _embedded_media(env, a)
    db.set_grade(a, "pass")
    db.move_media_batch_applied([(a, b)])
    apply_pending_renames(db, _disk(env))
    # the marks are older than the index the server loads next
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE media_hashes SET embedding_updated_at = "
            "datetime(embedding_updated_at, '-10 seconds')"
        )
        conn.commit()
    fm = _disk(env)
    monkeypatch.setattr(similarity, "_faiss_manager", fm)
    db.set_embedding_lookup(faiss_embedding_lookup)

    assert db.delete_media(Path(b))
    (tomb,) = [t for t in db.list_tombstones() if t["file_path"] == b]
    assert tomb["embedded_model"] == MODEL
    blob = db.get_tombstone_vectors(tomb["id"]).get(MODEL)
    assert blob is not None
    assert np.allclose(np.frombuffer(blob, dtype=np.float32), v)


# ---------------------------------------------------------------- server


class _Queue:
    def __init__(self, index_dir: Path, indexing: bool = False) -> None:
        self.index_dir = index_dir
        self.indexing = indexing
        self.started: List[tuple] = []

    def is_indexing(self) -> bool:
        return self.indexing

    def start_indexing(self, *args) -> bool:
        self.started.append(args)
        return True


@pytest.fixture
def server(env, monkeypatch):
    q = _Queue(env["index"])
    monkeypatch.setattr(similarity, "_get_embedding_queue", lambda: q)
    monkeypatch.setattr(similarity, "get_db", lambda: env["db"])
    monkeypatch.setattr(similarity, "_faiss_manager", None)
    monkeypatch.setattr(similarity, "_pending_map", {})
    monkeypatch.setattr(similarity, "_server_loop", None)
    monkeypatch.setattr(similarity, "_wait_for_the_next_second", lambda: None)
    return q


def test_search_results_show_the_new_path_before_the_rename_applies(env, server):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    db.save_media(_media(a))
    db.move_media_batch_applied([(a, b)])
    assert similarity.map_pending(a) == a  # not refreshed yet
    similarity.refresh_pending_map()
    assert similarity.map_pending(a) == b
    assert similarity.map_pending(_p(env, "other.png")) == _p(env, "other.png")


def test_the_server_never_applies_while_a_worker_is_alive(env, server):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    _fm(env, [(a, _unit(9))])
    _embedded_media(env, a)
    db.move_media_batch_applied([(a, b)])

    server.indexing = True
    assert similarity.apply_pending_faiss_renames() == 0
    assert db.list_faiss_pending_renames() == [(a, b)]
    assert _disk(env).has_file(a)

    server.indexing = False
    assert similarity.apply_pending_faiss_renames() == 1
    assert db.list_faiss_pending_renames() == []
    assert _disk(env).has_file(b)
    assert similarity.map_pending(a) == a  # map refreshed


def test_a_build_cannot_start_while_the_server_step_is_saving(env, server):
    release = threading.Event()
    inside = threading.Event()
    real = apply_pending_renames

    def slow(db, fm):
        inside.set()
        release.wait(5)
        return real(db, fm)

    similarity.apply_pending_renames = slow  # type: ignore[assignment]
    try:
        t = threading.Thread(target=similarity.apply_pending_faiss_renames)
        t.start()
        assert inside.wait(5)
        starter = threading.Thread(
            target=similarity._start_indexing_locked, args=(server, "x")
        )
        starter.start()
        time.sleep(0.2)
        assert server.started == []  # blocked behind the step
        release.set()
        t.join(5)
        starter.join(5)
        assert server.started == [("x",)]
    finally:
        similarity.apply_pending_renames = real  # type: ignore[assignment]


# ---------------------------------------------------------------- worker


def test_the_worker_applies_renames_at_its_start_without_re_embedding(env, monkeypatch):
    db = env["db"]
    queue = env["lib"] / "queue"
    queue.mkdir()
    calls: List[str] = []

    class FakeClip:
        def __init__(self, model_key=MODEL, device="auto") -> None:
            self.model_config = {"name": "fake", "pretrained": "fake"}

        def _check_model_needs_download(self, *a) -> bool:
            return False

        def _ensure_model_loaded(self) -> None:
            return None

        embedding_dim = DIM

        def compute_image_embedding(self, path):
            calls.append(path)
            return _unit(99)

        def compute_video_embedding(self, *a, **k):
            return None

        def unload_model(self) -> None:
            return None

    monkeypatch.setattr(ew, "EmbeddingManager", FakeClip)
    monkeypatch.setattr(ew, "build_vocabulary", lambda *a, **k: None)

    a, b, other = _p(env, "a.png"), _p(env, "b.png"), _p(env, "other.png")
    Path(other).write_bytes(b"x")
    v = _unit(10)
    _fm(env, [(a, v)])
    _embedded_media(env, a)
    db.save_media(_media(other))
    db.move_media_batch_applied([(a, b)])

    task = {
        "model_key": MODEL,
        "device": "cpu",
        "file_paths": [other],
        "db_path": str(env["data"]),
        "index_dir": str(env["index"]),
        "compute_phash": False,
        "video_keyframes": 4,
        "tag_with_vlm": False,
    }
    (queue / "embedding_task.json").write_text(json.dumps(task))
    ew.EmbeddingWorker(queue).run()

    assert calls == [other]  # the moved file was not embedded again
    on_disk = _disk(env)
    assert not on_disk.has_file(a)
    assert np.allclose(on_disk.get_embedding(b), v)
    assert db.list_faiss_pending_renames() == []


# ---------------------------------------------------------------- fix round 1


def test_the_map_keeps_mapping_after_the_apply_until_the_swap(env, server, monkeypatch):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    _fm(env, [(a, _unit(11))])
    _embedded_media(env, a)
    memory = _disk(env)
    monkeypatch.setattr(similarity, "_faiss_manager", memory)
    db.move_media_batch_applied([(a, b)])
    similarity.refresh_pending_map()
    reloads: List[int] = []
    monkeypatch.setattr(
        similarity, "_reload_faiss_after_index_build", lambda: reloads.append(1)
    )

    assert similarity.apply_pending_faiss_renames() == 1
    assert db.list_faiss_pending_renames() == []
    assert reloads == [1]
    assert similarity.map_pending(a) == b  # memory still holds a
    similarity.refresh_pending_map()  # any later refresh keeps it too
    assert similarity.map_pending(a) == b

    similarity._rebuild_faiss_manager(1)  # the swap
    assert similarity._faiss_manager is not memory
    assert similarity.map_pending(a) == a  # the new index holds b
    assert similarity._faiss_manager.has_file(b)


def test_a_failed_run_reloads_the_index_too(monkeypatch):
    from types import SimpleNamespace

    reloads: List[int] = []
    monkeypatch.setattr(
        similarity, "_reload_faiss_after_index_build", lambda: reloads.append(1)
    )
    monkeypatch.setattr(similarity.ws_manager, "broadcast_sync", lambda *a, **k: None)
    monkeypatch.setattr(similarity, "get_embedding_process", lambda: None)
    eq = SimpleNamespace(on_progress=None, on_complete=None, on_error=None)
    similarity._attach_embedding_callbacks(eq)  # type: ignore[arg-type]
    eq.on_error("boom")
    assert reloads == [1]


def _worker_env(env, monkeypatch, on_embed=None):
    queue = env["lib"] / "queue"
    queue.mkdir(exist_ok=True)

    class FakeClip:
        def __init__(self, model_key=MODEL, device="auto") -> None:
            self.model_config = {"name": "fake", "pretrained": "fake"}

        def _check_model_needs_download(self, *a) -> bool:
            return False

        def _ensure_model_loaded(self) -> None:
            return None

        embedding_dim = DIM

        def compute_image_embedding(self, path):
            if on_embed is not None:
                on_embed(path)
            return _unit(int.from_bytes(Path(path).read_bytes()[:4], "little"))

        def compute_video_embedding(self, *a, **k):
            return None

        def unload_model(self) -> None:
            return None

    monkeypatch.setattr(ew, "EmbeddingManager", FakeClip)
    monkeypatch.setattr(ew, "build_vocabulary", lambda *a, **k: None)

    def run(paths):
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
        (queue / "embedding_task.json").write_text(json.dumps(task))
        ew.EmbeddingWorker(queue).run()

    return run


def _file(env, name: str, content: bytes) -> str:
    path = _p(env, name)
    Path(path).write_bytes(content)
    return path


def _assert_moved_file_has_no_vector(env, moved: str, other_vec: np.ndarray):
    """The moved file is marked not embedded and no row at its path holds
    the other file's vector."""
    assert moved not in env["db"].embedded_paths([moved])
    row = _disk(env).get_embedding(moved)
    assert row is None or not np.allclose(row, other_vec)


def test_case_a_a_failed_apply_then_a_new_file_at_the_old_path(env, monkeypatch):
    db = env["db"]
    a, b = _file(env, "a.png", b"NEW1"), _p(env, "b.png")
    old_vec = _unit(12)
    _fm(env, [(a, old_vec)])
    _embedded_media(env, a)  # the file that moves a -> b
    db.move_media_batch_applied([(a, b)])
    db.save_media(_media(a))  # a different file now lives at a
    run = _worker_env(env, monkeypatch)
    monkeypatch.setattr(
        ew,
        "apply_pending_renames",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("apply failed")),
    )
    run([a])
    new_vec = _unit(int.from_bytes(b"NEW1", "little"))
    assert np.allclose(_disk(env).get_embedding(a), new_vec)
    assert db.list_faiss_pending_renames() == []
    _assert_moved_file_has_no_vector(env, b, new_vec)
    _assert_moved_file_has_no_vector(env, b, old_vec)


def test_case_b_a_move_during_the_run_then_the_run_embeds_the_old_path(
    env, monkeypatch
):
    db = env["db"]
    a, b = _file(env, "a.png", b"NEW2"), _p(env, "b.png")
    dummy = _file(env, "dummy.png", b"DUMM")
    old_vec = _unit(13)
    _fm(env, [(a, old_vec)])
    _embedded_media(env, a)
    db.save_media(_media(dummy))
    moved = []

    def on_embed(path):
        if path == dummy and not moved:  # the move lands mid-run
            db.move_media_batch_applied([(a, b)])
            db.save_media(_media(a))  # and a different file takes a
            moved.append(1)

    run = _worker_env(env, monkeypatch, on_embed)
    run([dummy, a])
    assert moved
    new_vec = _unit(int.from_bytes(b"NEW2", "little"))
    assert np.allclose(_disk(env).get_embedding(a), new_vec)
    assert db.list_faiss_pending_renames() == []
    _assert_moved_file_has_no_vector(env, b, new_vec)
    _assert_moved_file_has_no_vector(env, b, old_vec)


def test_case_c_a_file_at_a_pending_old_path_moves_on(env):
    db = env["db"]
    a, b, d = (_p(env, n) for n in ("a.png", "b.png", "d.png"))
    old_vec = _unit(14)
    _fm(env, [(a, old_vec)])
    _embedded_media(env, a)
    db.move_media_batch_applied([(a, b)])
    _embedded_media(env, a)  # a different file at a, marked embedded
    db.move_media_batch_applied([(a, d)])

    assert db.list_faiss_pending_renames() == []
    assert db.embedded_paths([b, d]) == set()
    result = apply_pending_renames(db, _disk(env))
    assert result.renamed == 0
    for moved in (b, d):
        assert _disk(env).get_embedding(moved) is None


def test_a_chain_onto_a_pending_old_path_is_not_case_c(env):
    db = env["db"]
    z, a, b, d = (_p(env, n) for n in ("z.png", "a.png", "b.png", "d.png"))
    db.save_media(_media(a))
    db.save_media(_media(z))
    db.move_media_batch_applied([(a, b)])
    db.move_media_batch_applied([(z, a)])
    db.move_media_batch_applied([(a, d)])  # z's file moves on
    assert sorted(db.list_faiss_pending_renames()) == sorted([(a, b), (z, d)])


def test_a_moved_file_with_no_row_to_take_is_marked_not_embedded(env):
    db = env["db"]
    a, b = _p(env, "a.png"), _p(env, "b.png")
    _fm(env, [(_p(env, "other.png"), _unit(15))])  # no row at a
    _embedded_media(env, a)
    db.move_media_batch_applied([(a, b)])
    result = apply_pending_renames(db, _disk(env))
    assert (result.renamed, result.reset) == (0, 1)
    assert db.embedded_paths([b]) == set()
    assert db.list_faiss_pending_renames() == []
