"""Per-CLIP-model tombstone vectors and the background re-embedder (spec
2026-10-24-pass-fail-grades, "Deleting keeps the signal", items 5 and 6).

A deleted graded image's vector must be for the FAISS index's CLIP model.
When it has none for that model (a CLIP switch, or deleted while the index
was not in memory) the re-embedder computes one from the image's archived
thumbnail through the live CLIP worker -- a fake one here: no test starts a
real CLIP model. It never uses the GPU while writer training holds the lock,
a generation is in flight or an index build runs; it stops between images
when one starts and resumes on its own once it is over. A CLIP error never
makes it give up on an image (only a gone archive does).

Invented images in an isolated temp DB (the shared taste-service fixtures)."""

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pytest
from PIL import Image

from backend.services.tombstone_reembed import (
    MAX_CONSECUTIVE_ERRORS,
    TombstoneReembedder,
    reembed_blocked,
    reembed_model_key,
)
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.embedding_manager import FaissIndexManager
from metascan.core.gpu_guard import GPU_BUSY_MESSAGE, gpu_reserved, set_gpu_reservation
from metascan.core.inference_client import InferenceError
from metascan.core.taste_runner import TasteRunner
from metascan.core.training_archive import archive_dir, archived_file
from tests._taste_service_fixtures import (  # noqa: F401
    after,
    db,
    fake_runner,
    media,
    model_row,
    recorder,
    seed_labels,
    service,
    taste_config,
)

DIM = 32
RETRY = 0.02


# ---- helpers ----------------------------------------------------------------


def _vector_of(path: str) -> np.ndarray:
    """What the fake CLIP worker answers for an image file: a unit vector
    derived from the file's bytes, so a stored vector says which file it was
    computed from."""
    digest = hashlib.sha256(Path(path).read_bytes()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
    v = rng.normal(size=DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _archive(db: DatabaseManager, color: Tuple[int, int, int]) -> str:
    """An archived thumbnail in the DB's data dir; its stored relative path."""
    root = archive_dir(Path(db.db_path))
    root.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.jpg"
    Image.new("RGB", (64, 48), color).save(root / name, "JPEG", quality=85)
    return f"archive/thumbs/{name}"


def _color(i: int) -> Tuple[int, int, int]:
    return ((37 * i) % 256, (91 * i + 40) % 256, (53 * i + 90) % 256)


_SEQ = [0]


def _delete_graded(
    db: DatabaseManager,
    path: str,
    grade: Optional[str],
    *,
    archive: bool = True,
    at: Optional[datetime] = None,
) -> int:
    """Save, grade (None: only viewed) and delete ``path`` with an archived
    thumbnail, as the delete services leave a graded image; its tombstone id."""
    _SEQ[0] += 1
    db.save_media(media(path))
    if grade is None:
        db.mark_seen(path, at)
    else:
        db.set_grade(path, grade, None, at)
    thumb = _archive(db, _color(_SEQ[0])) if archive else None
    assert db.delete_media(Path(path), thumb)
    return max(t["id"] for t in db.list_tombstones() if t["file_path"] == path)


LOGGER = "backend.services.tombstone_reembed"


def _warnings(caplog) -> List[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == LOGGER and r.levelno >= logging.WARNING
    ]


def _vectors(db: DatabaseManager, tomb_id: int) -> Dict[str, np.ndarray]:
    return {
        model: np.frombuffer(blob, dtype=np.float32)
        for model, blob in db.get_tombstone_vectors(tomb_id).items()
    }


def _archive_of(db: DatabaseManager, tomb_id: int) -> str:
    (t,) = [t for t in db.list_tombstones() if t["id"] == tomb_id]
    path = archived_file(Path(db.db_path), t["thumb_path"])
    assert path is not None
    return str(path)


async def _until(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "timed out waiting for the re-embedder"
        await asyncio.sleep(0.005)


class FakeClip:
    """Stands in for ``InferenceClient``: ensure_started / encode_image /
    shutdown, no subprocess. Like the real one, it refuses to load while
    training holds the GPU."""

    def __init__(self) -> None:
        self.state = "idle"
        self.model_key: Optional[str] = None
        self.device: Optional[str] = None
        self.dim: Optional[int] = None
        self.starts: List[Tuple[str, str]] = []
        self.encoded: List[str] = []
        self.unreadable: set = set()
        # path -> the vector CLIP answers for it instead of a usable one (a
        # NaN vector, as autocast can produce, or one of the wrong size).
        self.unusable: Dict[str, np.ndarray] = {}
        # Every image fails, as when the worker runs out of GPU memory: it
        # answers ok:false, which the client raises as InferenceError.
        self.failing = False
        # Runs inside encode_image, before it answers (e.g. a T2I batch or
        # writer training starting while an image is being encoded).
        self.during_encode: Optional[Callable[[str], None]] = None

    async def ensure_started(self, model_key: str, device: str = "auto") -> None:
        self.starts.append((model_key, device))
        if gpu_reserved():
            raise InferenceError(GPU_BUSY_MESSAGE)
        self.state, self.model_key, self.device, self.dim = (
            "ready",
            model_key,
            device,
            DIM,
        )

    async def encode_image(self, path: str, timeout: float = 120.0) -> np.ndarray:
        if self.state != "ready":
            raise RuntimeError("inference worker is not running")
        self.encoded.append(path)
        if self.during_encode is not None:
            self.during_encode(path)
        await asyncio.sleep(0)
        if self.state != "ready":
            raise RuntimeError("worker shut down")
        if self.failing or path in self.unreadable:
            raise InferenceError("compute_image_embedding returned None")
        if path in self.unusable:
            return self.unusable[path]
        return _vector_of(path)

    async def shutdown(self) -> None:
        self.state = "stopped"


class FakeT2i:
    def __init__(self) -> None:
        self.batches: List[str] = []

    def active_batches(self) -> List[str]:
        return list(self.batches)


class FakeComfy:
    def __init__(self) -> None:
        self.pending = False

    def has_pending_jobs(self) -> bool:
        return self.pending


class Env:
    """A re-embedder wired the way the lifespan wires it, with fakes for CLIP,
    the T2I runner, ComfyUI and the index build."""

    def __init__(self, db: DatabaseManager, model_key: Optional[str] = "large"):
        self.db = db
        self.clip = FakeClip()
        self.t2i = FakeT2i()
        self.comfy = FakeComfy()
        self.indexing = False
        self.model_key = model_key
        self.done: List[int] = []
        self.progress: List[Optional[Dict[str, Any]]] = []
        self.on_done: Callable[[int], None] = self.done.append

    def reembedder(self) -> TombstoneReembedder:
        return TombstoneReembedder(
            self.db,
            client_factory=lambda: self.clip,
            model_key_provider=lambda: self.model_key,
            blocked=lambda: reembed_blocked(
                self.t2i, self.comfy, lambda: self.indexing
            ),
            on_done=lambda n: self.on_done(n),
            on_progress=self.progress.append,
            delay_s=0.0,
            retry_s=RETRY,
        )


@pytest.fixture
def gpu_lock():
    held = {"on": False}
    set_gpu_reservation(lambda: held["on"])
    yield held
    set_gpu_reservation(None)


# ---- the re-embedder --------------------------------------------------------


async def test_reembeds_graded_tombstones_for_the_current_model(db, caplog):
    caplog.set_level(logging.INFO, logger=LOGGER)
    small = np.full(DIM, 1 / np.sqrt(DIM), dtype=np.float32)
    # A ✓ image embedded and deleted while the index (CLIP "small") was in
    # memory: its vector for "small" is captured at deletion, but the index
    # is "large" now.
    db.save_media(media("/l/pass.png"))
    db.mark_embedded(["/l/pass.png"], "small")
    db.set_embedding_lookup(lambda p: (small, "small", float("inf")))
    t_pass = _delete_graded(db, "/l/pass.png", "pass")
    db.set_embedding_lookup(None)
    t_fail = _delete_graded(db, "/l/fail.png", "fail")  # no vector at all
    t_done = _delete_graded(db, "/l/done.png", "pass")
    assert db.add_tombstone_vectors([(t_done, "large", small.tobytes())]) == 1
    t_seen = _delete_graded(db, "/l/seen.png", None)  # ungraded: no label
    t_bare = _delete_graded(db, "/l/bare.png", "pass", archive=False)
    t_clip = _delete_graded(db, "/l/clip.mp4", "pass")  # videos never
    # The deleted ✓ image's path holds another file now (Review Focus 5).
    db.save_media(media("/l/pass.png"))

    pending = db.tombstones_needing_vectors("large")
    assert sorted(t for t, _ in pending) == sorted([t_pass, t_fail])
    assert all(thumb.startswith("archive/thumbs/") for _, thumb in pending)
    assert sorted(t for t, _ in db.tombstones_needing_vectors("small")) == sorted(
        [t_fail, t_done]
    )

    env = Env(db)
    r = env.reembedder()
    r.notify()
    await r.wait_idle()

    assert env.clip.starts == [("large", "auto")]
    # Only the archived thumbnails are read, never a library path.
    archives = {t: _archive_of(db, t) for t in (t_pass, t_fail)}
    assert sorted(env.clip.encoded) == sorted(archives.values())
    for t, archive in archives.items():
        assert np.array_equal(_vectors(db, t)["large"], _vector_of(archive))
    # Vectors are kept per model: the "small" one stays beside the new one.
    assert np.array_equal(_vectors(db, t_pass)["small"], small)
    assert set(_vectors(db, t_done)) == {"large"}
    assert np.array_equal(_vectors(db, t_done)["large"], small)
    for t in (t_seen, t_bare, t_clip):
        assert db.get_tombstone_vectors(t) == {}
    assert db.tombstones_needing_vectors("large") == []
    assert r.status() is None
    assert env.done == [2]
    assert env.progress[0] == {"running": True, "done": 0, "total": 2}
    assert env.progress[-1] is None
    assert _warnings(caplog) == []  # nothing skipped: no WARNING

    # Nothing left: a second notify starts nothing and marks nothing.
    r.notify()
    await r.wait_idle()
    assert len(env.clip.encoded) == 2 and env.done == [2]
    await r.aclose()


async def test_stops_when_generation_starts_and_resumes_later(db):
    ids = [_delete_graded(db, f"/l/g{i}.png", "pass") for i in range(3)]
    env = Env(db)

    def batch_starts(path: str) -> None:
        if len(env.clip.encoded) == 1:
            env.t2i.batches = ["b1"]

    env.clip.during_encode = batch_starts
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    # The image being encoded is finished; nothing else while the batch runs.
    assert len(env.clip.encoded) == 1
    assert sum(1 for t in ids if db.get_tombstone_vectors(t)) == 1
    assert r.status() == {"running": False, "done": 1, "total": 3}
    assert env.progress[-1] == {"running": False, "done": 1, "total": 3}
    await asyncio.sleep(RETRY * 5)
    assert len(env.clip.encoded) == 1
    # A ComfyUI job (I2V, storyboard) in flight holds it back as well.
    env.t2i.batches = []
    env.comfy.pending = True
    await asyncio.sleep(RETRY * 5)
    assert len(env.clip.encoded) == 1
    assert env.done == []
    # The generation settled: it resumes on its own, where it stopped.
    env.comfy.pending = False
    await _until(lambda: r.status() is None)
    assert len(env.clip.encoded) == 3
    assert len(set(env.clip.encoded)) == 3
    assert all(db.get_tombstone_vectors(t) for t in ids)
    assert env.done == [3]
    await r.aclose()


async def test_waits_for_an_index_build_to_finish(db):
    t = _delete_graded(db, "/l/a.png", "fail")
    env = Env(db)
    env.indexing = True
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    await asyncio.sleep(RETRY * 5)
    assert env.clip.starts == [] and env.clip.encoded == []
    assert r.status() == {"running": False, "done": 0, "total": 1}
    env.indexing = False
    await _until(lambda: r.status() is None)
    assert db.get_tombstone_vectors(t)
    await r.aclose()


async def test_never_runs_while_training_holds_the_lock(db, gpu_lock):
    first = [_delete_graded(db, f"/l/a{i}.png", "pass") for i in range(3)]
    env = Env(db)
    gpu_lock["on"] = True
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    await asyncio.sleep(RETRY * 5)
    # Training holds the GPU: CLIP is never even started.
    assert env.clip.starts == [] and env.clip.encoded == []
    assert r.status() == {"running": False, "done": 0, "total": 3}
    gpu_lock["on"] = False
    await _until(lambda: r.status() is None)
    assert all(db.get_tombstone_vectors(t) for t in first)
    assert env.done == [3]

    # Training starts mid-run. It takes the lock first, then unloads CLIP
    # (release_gpu): the image being encoded is dropped, nothing else runs,
    # and CLIP is not started again while the lock is held.
    later = [_delete_graded(db, f"/l/b{i}.png", "fail") for i in range(3)]

    def training_starts(path: str) -> None:
        gpu_lock["on"] = True
        env.clip.state = "stopped"

    env.clip.during_encode = training_starts
    starts = len(env.clip.starts)
    r.notify()
    await r.wait_idle()
    assert len(env.clip.encoded) == 4
    assert all(db.get_tombstone_vectors(t) == {} for t in later)
    assert r.status() == {"running": False, "done": 0, "total": 3}
    await asyncio.sleep(RETRY * 5)
    assert len(env.clip.starts) == starts + 1
    assert len(env.clip.encoded) == 4
    # The lock is released: the dropped image is not lost, it is redone.
    env.clip.during_encode = None
    gpu_lock["on"] = False
    await _until(lambda: r.status() is None)
    assert all(db.get_tombstone_vectors(t) for t in later)
    assert env.done == [3, 3]
    await r.aclose()


async def test_marks_the_scorer_due_when_vectors_were_added(service, db, fake_runner):
    seed_labels(db)
    db.record_taste_model(model_row(labels_as_of=after(1).isoformat()))
    env = Env(db, model_key="small")
    env.on_done = lambda n: (env.done.append(n), service.mark_due("reembed"))
    r = env.reembedder()
    # A current model and no new grades: not due.
    assert await service.maybe_retrain(now=after(500)) is False

    # Nothing to re-embed: CLIP is not started and nothing is marked.
    r.notify()
    await r.wait_idle()
    assert env.clip.starts == [] and env.done == []
    # An image whose archive cannot be read adds no vector: still not due.
    bad = _delete_graded(db, "/l/bad.png", "fail", at=after(5))
    env.clip.unreadable.add(_archive_of(db, bad))
    r.notify()
    await r.wait_idle()
    assert env.done == [] and db.get_tombstone_vectors(bad) == {}
    assert await service.maybe_retrain(now=after(500)) is False

    # Two deleted graded images get vectors: due at the next check, without
    # waiting for 25 new grades.
    _delete_graded(db, "/l/a.png", "pass", at=after(5))
    _delete_graded(db, "/l/b.png", "fail", at=after(5))
    r.notify()
    await r.wait_idle()
    assert env.done == [2]
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    assert len(fake_runner.train_calls) == 1
    # That run saw the vectors: the mark is used up.
    assert await service.maybe_retrain(now=after(600)) is False
    await r.aclose()


async def test_a_failed_run_is_retried_only_after_new_vectors(service, db, fake_runner):
    seed_labels(db)
    db.record_taste_model(model_row(labels_as_of=after(1).isoformat()))
    fake_runner.result = {"status": "error", "error": "boom"}
    service.mark_due("reembed")
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    # Same inputs after a failed run: no retry storm...
    assert await service.maybe_retrain(now=after(600)) is False
    # ...until new vectors land again.
    service.mark_due("reembed")
    assert await service.maybe_retrain(now=after(700)) is True
    await service.wait_idle()
    assert len(fake_runner.train_calls) == 2


async def test_status_reports_the_reembed_progress(service, db):
    assert (await service.status())["reembed"] is None
    ids = [_delete_graded(db, f"/l/s{i}.png", "pass") for i in range(2)]
    env = Env(db, model_key="small")
    env.indexing = True
    r = env.reembedder()
    service.set_reembed_status(r.status)
    r.notify()
    await r.wait_idle()
    assert (await service.status())["reembed"] == {
        "running": False,
        "done": 0,
        "total": 2,
    }
    env.indexing = False
    await _until(lambda: r.status() is None)
    assert (await service.status())["reembed"] is None
    assert all(db.get_tombstone_vectors(t) for t in ids)
    await r.aclose()


async def test_skips_unreadable_archives_and_a_changed_clip_model(db, caplog):
    """An archive that is gone is skipped until the server restarts; an
    image CLIP fails on is skipped for that pass only -- the worker reports
    any failure that way (an out-of-memory too), so it is never a reason to
    give up on the image. A pass that skipped anything ends with one
    WARNING giving the counts."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    gone = _delete_graded(db, "/l/gone.png", "pass")
    Path(_archive_of(db, gone)).unlink()  # the archive was removed by hand
    bad = _delete_graded(db, "/l/bad.png", "fail")
    ok = _delete_graded(db, "/l/ok.png", "pass")
    env = Env(db)
    env.clip.unreadable.add(_archive_of(db, bad))
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    # Newest first; the gone archive is never sent to CLIP.
    assert env.clip.encoded == [_archive_of(db, ok), _archive_of(db, bad)]
    assert db.get_tombstone_vectors(ok) and r.status() is None
    assert env.done == [1]
    (warning,) = _warnings(caplog)
    assert "Re-embedded 1 of 3" in warning
    assert "1 could not be encoded" in warning
    assert "1 archived thumbnail(s) gone" in warning
    # The next pass tries the image CLIP failed on again, never the gone one.
    caplog.clear()
    r.notify()
    await r.wait_idle()
    assert env.clip.encoded == [_archive_of(db, x) for x in (ok, bad, bad)]
    assert db.get_tombstone_vectors(bad) == {} and db.get_tombstone_vectors(gone) == {}
    assert env.done == [1]
    (warning,) = _warnings(caplog)
    assert "Re-embedded 0 of 1" in warning and "1 could not be encoded" in warning

    # The CLIP model is switched while it runs (Similarity Settings reloads
    # the worker): a vector from the other model is never stored as "large".
    more = [_delete_graded(db, f"/l/m{i}.png", "pass") for i in range(3)]

    def switched(path: str) -> None:
        env.clip.model_key = "medium"

    env.clip.during_encode = switched
    caplog.clear()
    r.notify()
    await r.wait_idle()
    assert all(db.get_tombstone_vectors(t) == {} for t in more)
    assert r.status() is None
    (warning,) = _warnings(caplog)
    assert "stopped: the CLIP model changed" in warning
    await r.aclose()


async def test_clip_errors_in_a_row_stop_the_pass_without_blacklisting(db, caplog):
    """A CUDA out-of-memory reaches the client as an InferenceError for every
    image. After MAX_CONSECUTIVE_ERRORS in a row the pass ends -- one
    WARNING, nothing given up on -- and it tries again on its own."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    ids = [_delete_graded(db, f"/l/o{i}.png", "pass") for i in range(8)]
    env = Env(db)
    env.clip.failing = True
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    assert len(env.clip.encoded) == MAX_CONSECUTIVE_ERRORS
    assert r.status() is None and env.done == []
    assert all(db.get_tombstone_vectors(t) == {} for t in ids)
    (warning,) = _warnings(caplog)
    assert f"{MAX_CONSECUTIVE_ERRORS} CLIP errors in a row" in warning
    assert "Re-embedded 0 of 8" in warning
    assert f"{MAX_CONSECUTIVE_ERRORS} could not be encoded" in warning
    # CLIP recovers: its own next try takes every image, the failed ones too.
    env.clip.failing = False
    await _until(lambda: all(db.get_tombstone_vectors(t) for t in ids))
    await _until(lambda: r.status() is None)
    assert env.done == [8]
    await r.aclose()


async def test_images_clip_failed_on_go_last_and_never_hold_up_the_rest(db):
    good = [_delete_graded(db, f"/l/good{i}.png", "fail") for i in range(3)]
    # Newer, so tried first: as many unreadable ones as end a pass.
    bad = [
        _delete_graded(db, f"/l/bad{i}.png", "pass")
        for i in range(MAX_CONSECUTIVE_ERRORS)
    ]
    env = Env(db)
    env.clip.unreadable.update(_archive_of(db, t) for t in bad)
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    assert env.clip.encoded == [_archive_of(db, t) for t in reversed(bad)]
    assert all(db.get_tombstone_vectors(t) == {} for t in good)
    # The next try takes the images that have not failed first.
    await _until(lambda: all(db.get_tombstone_vectors(t) for t in good))
    first = len(bad)
    assert env.clip.encoded[first : first + len(good)] == [
        _archive_of(db, t) for t in reversed(good)
    ]
    assert all(db.get_tombstone_vectors(t) == {} for t in bad)
    await _until(lambda: env.done == [3])
    await r.aclose()


async def test_scattered_clip_errors_do_not_stop_the_pass(db, caplog):
    """Only errors IN A ROW mean CLIP is failing: unreadable images between
    good ones are skipped and the pass runs to its end."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    good, bad = [], []
    for i in range(MAX_CONSECUTIVE_ERRORS):
        good.append(_delete_graded(db, f"/l/good{i}.png", "pass"))
        bad.append(_delete_graded(db, f"/l/bad{i}.png", "fail"))
    env = Env(db)
    env.clip.unreadable.update(_archive_of(db, t) for t in bad)
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    assert len(env.clip.encoded) == 2 * MAX_CONSECUTIVE_ERRORS
    assert all(db.get_tombstone_vectors(t) for t in good)
    assert env.done == [MAX_CONSECUTIVE_ERRORS] and r.status() is None
    (warning,) = _warnings(caplog)
    assert "in a row" not in warning
    assert f"{MAX_CONSECUTIVE_ERRORS} could not be encoded" in warning
    await r.aclose()


async def test_a_clip_error_skips_the_image_for_the_rest_of_the_pass(db):
    """A pass that pauses for the GPU and resumes does not try an image it
    already got a CLIP error for; the next pass does."""
    good = [_delete_graded(db, f"/l/g{i}.png", "pass") for i in range(2)]
    bad = _delete_graded(db, "/l/bad.png", "fail")  # newest: tried first
    env = Env(db)
    env.clip.unreadable.add(_archive_of(db, bad))

    def batch_starts(path: str) -> None:
        if path == _archive_of(db, bad):
            env.t2i.batches = ["b1"]

    env.clip.during_encode = batch_starts
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    assert env.clip.encoded == [_archive_of(db, bad)]
    assert r.status() == {"running": False, "done": 1, "total": 3}
    env.clip.during_encode = None
    env.t2i.batches = []
    await _until(lambda: r.status() is None)  # resumed by its own retry
    assert env.clip.encoded == [_archive_of(db, t) for t in (bad, *reversed(good))]
    assert all(db.get_tombstone_vectors(t) for t in good)
    assert env.done == [2]
    r.notify()
    await r.wait_idle()
    assert env.clip.encoded[-1] == _archive_of(db, bad)  # the next pass
    await r.aclose()


@pytest.mark.parametrize(
    "vector",
    [
        np.full(DIM, np.nan, dtype=np.float32),  # NaN under autocast
        np.ones(DIM // 2, dtype=np.float32) / np.sqrt(DIM // 2),  # wrong size
        np.zeros(0, dtype=np.float32),
    ],
    ids=["nan", "wrong-size", "empty"],
)
async def test_an_unusable_vector_skips_the_image_for_the_pass(db, caplog, vector):
    """CLIP can answer with a vector that is no use (NaN under autocast:
    embedding_manager skips the normalisation when the norm is NaN). That
    is this image's problem, not CLIP's: like a CLIP error the image is
    skipped for the rest of the pass and tried again on the next one, after
    the others -- it never ends the pass, which would put it back at the
    head of the queue and hold up every older image for good."""
    caplog.set_level(logging.INFO, logger=LOGGER)
    good = [_delete_graded(db, f"/l/g{i}.png", "pass") for i in range(2)]
    bad = _delete_graded(db, "/l/nan.png", "fail")  # newest: tried first
    env = Env(db)
    env.clip.unusable[_archive_of(db, bad)] = vector
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    assert env.clip.encoded == [_archive_of(db, t) for t in (bad, *reversed(good))]
    assert all(db.get_tombstone_vectors(t) for t in good)
    assert db.get_tombstone_vectors(bad) == {}
    assert env.done == [2] and r.status() is None
    (warning,) = _warnings(caplog)
    assert "stopped" not in warning
    assert "Re-embedded 2 of 3" in warning and "1 could not be encoded" in warning

    # Never given up on: the next pass tries it again, and once CLIP answers
    # with a usable vector it gets one.
    r.notify()
    await r.wait_idle()
    assert env.clip.encoded[-1] == _archive_of(db, bad)
    assert db.get_tombstone_vectors(bad) == {}
    env.clip.unusable.clear()
    r.notify()
    await r.wait_idle()
    assert np.array_equal(_vectors(db, bad)["large"], _vector_of(_archive_of(db, bad)))
    assert env.done == [2, 1]
    await r.aclose()


async def test_the_pass_is_running_while_clip_starts(db):
    """Loading CLIP can take a while. The pass is running then -- the
    Training tab reads ``running: False`` as "paused while the GPU is busy",
    which would blame the GPU for the model load."""
    ids = [_delete_graded(db, f"/l/c{i}.png", "pass") for i in range(2)]
    env = Env(db)
    entered, gate = asyncio.Event(), asyncio.Event()
    real_start = env.clip.ensure_started

    async def slow_start(model_key: str, device: str = "auto") -> None:
        entered.set()
        await gate.wait()
        await real_start(model_key, device)

    env.clip.ensure_started = slow_start  # type: ignore[method-assign]
    r = env.reembedder()
    r.notify()
    await _until(entered.is_set)
    assert r.status() == {"running": True, "done": 0, "total": 2}
    assert env.progress == [{"running": True, "done": 0, "total": 2}]
    gate.set()
    await r.wait_idle()
    assert all(db.get_tombstone_vectors(t) for t in ids)
    assert r.status() is None and env.done == [2]
    await r.aclose()


async def test_nothing_happens_without_a_current_model(db):
    _delete_graded(db, "/l/a.png", "pass")
    env = Env(db, model_key=None)
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    assert env.clip.starts == [] and r.status() is None and env.done == []
    await r.aclose()


def test_the_model_is_the_index_model_while_clip_agrees():
    assert reembed_model_key("large", "large") == "large"
    # Mid-switch (the worker runs another model until the index is rebuilt):
    # starting CLIP for the index's model would fight every search.
    assert reembed_model_key("small", "large") is None
    assert reembed_model_key(None, "large") is None
    assert reembed_model_key("", "") is None


def test_blocked_by_training_generation_and_index_builds(gpu_lock):
    t2i, comfy = FakeT2i(), FakeComfy()
    assert reembed_blocked(t2i, comfy, lambda: False) is False
    t2i.batches = ["b"]
    assert reembed_blocked(t2i, comfy, lambda: False) is True
    t2i.batches = []
    comfy.pending = True
    assert reembed_blocked(t2i, comfy, lambda: False) is True
    comfy.pending = False
    assert reembed_blocked(t2i, comfy, lambda: True) is True
    gpu_lock["on"] = True
    assert reembed_blocked(t2i, comfy, lambda: False) is True


# ---- lifespan wiring ----------------------------------------------------------


def test_the_embedding_hook_rescores_then_looks_for_missing_vectors():
    from backend.main import _after_embedding_run

    calls: List[str] = []

    class Service:
        def schedule_rescore_threadsafe(self) -> None:
            calls.append("rescore")
            raise RuntimeError("no loop")

    class Reembedder:
        def notify(self) -> None:
            calls.append("notify")

    _after_embedding_run(Service(), Reembedder())()  # type: ignore[arg-type]
    # A failed rescore never keeps the re-embedder from hearing of the run.
    assert calls == ["rescore", "notify"]


def test_the_lifespan_notifies_at_startup_and_after_every_embedding_run(
    monkeypatch,
):
    from fastapi.testclient import TestClient

    from backend.api import similarity
    from backend.api import taste as taste_api
    from backend.main import create_app

    notified: List[Any] = []
    monkeypatch.setattr(
        TombstoneReembedder, "notify", lambda self: notified.append(self)
    )
    with TestClient(create_app()) as client:
        assert len(notified) == 1  # once at startup
        service = taste_api.get_taste_service()
        assert service is not None
        rescores: List[int] = []
        monkeypatch.setattr(
            service, "schedule_rescore_threadsafe", lambda: rescores.append(1)
        )
        hook = similarity._on_embedding_complete
        assert hook is not None
        hook()  # an embedding run completed
        assert rescores == [1]
        assert len(notified) == 2 and notified[1] is notified[0]
        assert client.get("/api/taste/status").json()["reembed"] is None
    assert similarity._on_embedding_complete is None


# ---- per-model vectors in the taste labels and diversity --------------------


def _set_tombstone_legacy_vector(db, tomb_id: int, vec: np.ndarray, model: str):
    """What tombstones carried before per-model vectors (the v6 migration
    copied these into tombstone_vectors; nothing may read them now)."""
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE media_tombstones SET embedding = ?, embedding_model = ? "
            "WHERE id = ?",
            (vec.tobytes(), model, tomb_id),
        )
        conn.commit()


def test_labels_use_only_the_current_models_vectors(db):
    v_small = np.full(DIM, 0.1, dtype=np.float32)
    v_large = np.full(DIM, 0.2, dtype=np.float32)
    both = _delete_graded(db, "/l/both.png", "pass")
    assert (
        db.add_tombstone_vectors(
            [(both, "small", v_small.tobytes()), (both, "large", v_large.tobytes())]
        )
        == 2
    )
    old = _delete_graded(db, "/l/old.png", "fail")
    db.add_tombstone_vectors([(old, "small", v_small.tobytes())])
    legacy = _delete_graded(db, "/l/legacy.png", "pass")
    _set_tombstone_legacy_vector(db, legacy, v_large, "large")
    # An existing vector is never replaced, and no orphan row is written.
    assert db.add_tombstone_vectors([(both, "large", v_small.tobytes())]) == 0
    assert db.add_tombstone_vectors([(999_999, "large", v_small.tobytes())]) == 0
    assert np.array_equal(_vectors(db, both)["large"], v_large)

    def stored(model: Optional[str]) -> Dict[str, Tuple[Any, Any]]:
        raw = db.taste_training_raw(model)
        return {
            t["file_path"]: (t["embedding"], t["embedding_model"])
            for t in raw["tombstones"]
        }

    assert stored("large") == {
        "/l/both.png": (v_large.tobytes(), "large"),
        "/l/old.png": (None, None),
        "/l/legacy.png": (None, None),
    }
    assert stored("small") == {
        "/l/both.png": (v_small.tobytes(), "small"),
        "/l/old.png": (v_small.tobytes(), "small"),
        "/l/legacy.png": (None, None),
    }
    assert set(stored(None).values()) == {(None, None)}


def _index(index_dir: Path, vectors: Dict[str, np.ndarray], model_key: str) -> None:
    fm = FaissIndexManager(index_dir)
    fm.create(DIM, model_key)
    fm.add_batch(list(vectors), np.stack(list(vectors.values())))
    assert fm.save()


def _seed_scorer(db, rng: np.random.Generator) -> Dict[str, np.ndarray]:
    """30 ✓ and 30 ✗ live images with vectors on either side of a direction."""
    direction = rng.normal(size=DIM)
    direction /= np.linalg.norm(direction)
    vectors: Dict[str, np.ndarray] = {}
    for sign, grade in ((1.0, "pass"), (-1.0, "fail")):
        for i in range(30):
            v = rng.normal(size=DIM) + 2.5 * sign * direction
            path = f"/l/{grade}{i:02d}.png"
            vectors[path] = (v / np.linalg.norm(v)).astype(np.float32)
            db.save_media(media(path))
            db.set_grade(path, grade)
    return vectors


async def test_training_reads_the_index_models_tombstone_vectors(db, tmp_path):
    rng = np.random.default_rng(11)
    vectors = _seed_scorer(db, rng)
    unit = np.full(DIM, 1 / np.sqrt(DIM), dtype=np.float32)
    own = _delete_graded(db, "/l/own.png", "pass")  # re-embedded for "small"
    db.add_tombstone_vectors([(own, "small", unit.tobytes())])
    other = _delete_graded(db, "/l/other.png", "pass")  # only for "large"
    db.add_tombstone_vectors([(other, "large", unit.tobytes())])
    legacy = _delete_graded(db, "/l/legacy.png", "pass")  # legacy column only
    _set_tombstone_legacy_vector(db, legacy, unit, "small")
    index_dir = tmp_path / "similarity"
    _index(index_dir, vectors, "small")

    r = await TasteRunner(db, index_dir, tmp_path / "taste").train(180)

    assert r["status"] == "ok", r
    # 30 live ✓ + the one with a "small" vector; the other two have none.
    assert (r["n_pos"], r["n_neg"], r["n_no_vector"]) == (31, 30, 2)


def _generation(db, path: str, created_at: str, grade: str = "pass") -> int:
    db.save_media(media(path))
    image_id = db.create_t2i_image(file_path=path, writer_id="base")
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE t2i_images SET created_at = ? WHERE id = ?", (created_at, image_id)
        )
        conn.commit()
    db.set_grade(path, grade)
    return image_id


def _delete_generation(db, image_id: int, path: str, *, archive: bool = True) -> int:
    """The T2I delete, with an archived thumbnail (or none)."""
    _SEQ[0] += 1
    thumbs = {path: _archive(db, _color(_SEQ[0]))} if archive else None
    assert db.delete_t2i_image(image_id, thumbs)[0]
    return max(t["id"] for t in db.list_tombstones() if t["file_path"] == path)


def _mean_pairwise_distance(vecs: np.ndarray) -> float:
    v = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    n = len(v)
    sims = v @ v.T
    return float(1.0 - (sims.sum() - np.trace(sims)) / (n * (n - 1)))


async def test_reembedded_passes_reach_diversity(db, tmp_path):
    """Task 7 carry-over: ✓ pass diversity gets a deleted generation's vector
    from tombstone_vectors for the index's model -- so a ✓ generation
    re-embedded after a CLIP switch still counts, and the collapse warning
    keeps its input."""
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    rng = np.random.default_rng(5)
    vectors = _seed_scorer(db, rng)
    live = [f"/g/live{i}.png" for i in range(5)]
    for p in live:
        _generation(db, p, "2026-10-15 10:00:00")
        v = rng.normal(size=DIM)
        vectors[p] = (v / np.linalg.norm(v)).astype(np.float32)
    # Two deleted ✓ generations in the same window, neither with a vector at
    # deletion: one is re-embedded from its archive for "small" (the index's
    # model); the other has no archive and only a "large" vector. Their
    # paths hold other vectors in the index -- never read for a deleted item.
    re_id = _generation(db, "/g/reembedded.png", "2026-10-16 10:00:00")
    t_re = _delete_generation(db, re_id, "/g/reembedded.png")
    far_id = _generation(db, "/g/other_model.png", "2026-10-16 10:00:00")
    t_far = _delete_generation(db, far_id, "/g/other_model.png", archive=False)
    db.add_tombstone_vectors([(t_far, "large", np.ones(DIM, np.float32).tobytes())])
    for p in ("/g/reembedded.png", "/g/other_model.png"):
        vectors[p] = np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)

    env = Env(db, model_key="small")
    r = env.reembedder()
    r.notify()
    await r.wait_idle()
    await r.aclose()
    assert env.clip.encoded == [_archive_of(db, t_re)]
    assert set(_vectors(db, t_re)) == {"small"}
    assert set(_vectors(db, t_far)) == {"large"}

    index_dir = tmp_path / "similarity"
    _index(index_dir, vectors, "small")
    result = await TasteRunner(db, index_dir, tmp_path / "taste").train(180, now=now)
    assert result["status"] == "ok", result

    div = {d["window_start"]: d for d in db.latest_taste_diversity()}
    expected = _mean_pairwise_distance(
        np.vstack([np.stack([vectors[p] for p in live]), _vectors(db, t_re)["small"]])
    )
    assert div["2026-10-10"]["n"] == 6
    assert div["2026-10-10"]["mean_distance"] == pytest.approx(expected, abs=1e-5)
