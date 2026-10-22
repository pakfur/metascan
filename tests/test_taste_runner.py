"""Taste worker subprocess + TasteRunner, end to end against a real small
FAISS index on disk and an isolated temp DB (synthetic data only)."""

import asyncio
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from metascan.core.database_sqlite import DatabaseManager
from metascan.core.embedding_manager import FaissIndexManager
from metascan.core.media import Media
from metascan.core.taste_runner import TasteRunner

DIM = 32
MODEL_KEY = "small"


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


def _vec(rng: np.random.Generator, direction: np.ndarray, sign: float) -> np.ndarray:
    v = rng.normal(size=DIM) + 2.5 * sign * direction
    return (v / np.linalg.norm(v)).astype(np.float32)


def _build(tmp_path: Path, n_fav: int, n_pass: int):
    rng = np.random.default_rng(7)
    direction = rng.normal(size=DIM)
    direction /= np.linalg.norm(direction)
    db = DatabaseManager(tmp_path / "db")
    index_dir = tmp_path / "similarity"
    vectors = {}
    for i in range(n_fav):
        vectors[f"/l/f{i:02d}.png"] = (_vec(rng, direction, 1.0), "favorite")
    for i in range(n_pass):
        vectors[f"/l/p{i:02d}.png"] = (_vec(rng, direction, -1.0), "pass")
    vectors["/l/vid.mp4"] = (_vec(rng, direction, 1.0), "favorite")
    for path, (_, verdict) in vectors.items():
        db.save_media(_media(path))
        db.set_verdict(path, verdict)
    # A tombstoned favorite: its vector survives only on the tombstone.
    gone_vec = _vec(rng, direction, 1.0)
    db.set_embedding_lookup(lambda p: (gone_vec, MODEL_KEY))
    db.save_media(_media("/l/gone.png"))
    db.set_verdict("/l/gone.png", "favorite")
    db.delete_media(Path("/l/gone.png"))
    db.set_embedding_lookup(None)

    fm = FaissIndexManager(index_dir)
    fm.create(DIM, MODEL_KEY)
    paths = list(vectors)
    fm.add_batch(paths, np.stack([vectors[p][0] for p in paths]))
    assert fm.save()
    return db, index_dir, rng, direction


@pytest.fixture
def setup(tmp_path):
    db, index_dir, rng, direction = _build(tmp_path, 30, 30)
    runner = TasteRunner(db, index_dir, tmp_path / "taste")
    yield runner, db, index_dir, rng, direction
    db.close()


@pytest.fixture
def runner(setup):
    return setup[0]


@pytest.fixture
def db(setup):
    return setup[1]


@pytest.fixture
def runner_few(tmp_path):
    db, index_dir, _, _ = _build(tmp_path, 5, 30)
    yield TasteRunner(db, index_dir, tmp_path / "taste")
    db.close()


async def test_train_end_to_end(runner, db):
    r = await runner.train(180)
    assert r["status"] == "ok", r
    assert r["holdout_auc"] is not None and r["holdout_auc"] > 0.8
    # 30 live favorites + the tombstoned one; the video is never a label.
    assert (r["n_pos"], r["n_neg"]) == (31, 30)
    model_id, scores = db.get_taste_scores()
    assert model_id == r["model_id"]
    assert "/l/vid.mp4" not in scores and len(scores) == 60
    assert all(0.0 <= s <= 1.0 for s in scores.values())
    favs = np.mean([s for p, s in scores.items() if "/f" in p])
    passes = np.mean([s for p, s in scores.items() if "/p" in p])
    assert favs > passes
    active = db.get_active_taste_model()
    assert active["id"] == r["model_id"]
    assert active["embedding_model"] == MODEL_KEY and active["dim"] == DIM
    assert Path(active["params_path"]).exists()
    assert not runner.is_stale(active)
    assert not runner.training


async def test_labels_as_of_is_snapshot_time(runner, db):
    from datetime import timezone

    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    r = await runner.train(180, now=now)
    assert r["status"] == "ok"
    assert db.get_active_taste_model()["labels_as_of"] == now.isoformat()


async def test_not_enough_data_spawns_nothing(runner_few, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("worker must not be spawned")

    monkeypatch.setattr(runner_few, "_run_worker", boom)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", boom)
    r = await runner_few.train(180)
    assert r["status"] == "not_enough_data"
    # 5 live favorites + the tombstoned one.
    assert r["missing"] == {"positives": 14, "negatives": 0}
    assert runner_few.db.list_taste_models() == []
    assert not runner_few.training


async def test_worker_error_recorded(setup):
    runner, db, index_dir, _, _ = setup
    first = await runner.train(180)
    assert first["status"] == "ok"
    (index_dir / "faiss_index.bin").write_bytes(b"not a faiss index")
    r = await runner.train(180)
    assert r["status"] == "error" and r["error"]
    latest = db.list_taste_models()[0]
    assert latest["status"] == "failed" and latest["error"]
    assert db.get_active_taste_model()["id"] == first["model_id"]
    assert db.get_taste_scores()[0] == first["model_id"]
    assert not runner.training


async def test_stale_after_model_switch(setup):
    runner, db, index_dir, _, _ = setup
    assert (await runner.train(180))["status"] == "ok"
    meta_path = index_dir / "index_meta.json"
    meta = json.loads(meta_path.read_text())
    meta["model_key"] = "large"
    meta_path.write_text(json.dumps(meta))
    assert runner.is_stale(db.get_active_taste_model())
    assert await runner.rescore() is None


def test_is_stale_on_dim_and_missing_meta(setup):
    runner, _, index_dir, _, _ = setup
    assert not runner.is_stale({"embedding_model": MODEL_KEY, "dim": DIM})
    assert runner.is_stale({"embedding_model": MODEL_KEY, "dim": 64})
    (index_dir / "index_meta.json").unlink()
    assert runner.is_stale({"embedding_model": MODEL_KEY, "dim": DIM})


async def test_rescore_without_model_is_noop(runner):
    assert await runner.rescore() is None


async def test_second_train_while_running_raises(runner):
    first = asyncio.create_task(runner.train(180))
    await asyncio.sleep(0)
    assert runner.training
    with pytest.raises(RuntimeError, match="already_training"):
        await runner.train(180)
    assert (await first)["status"] == "ok"
    assert not runner.training


async def test_rescore_scores_new_images(setup):
    runner, db, index_dir, rng, direction = setup
    r = await runner.train(180)
    assert r["status"] == "ok"
    new = [f"/l/new{i}.png" for i in range(5)]
    for p in new:
        db.save_media(_media(p))
    fm = FaissIndexManager(index_dir)
    assert fm.load()
    fm.add_batch(new, np.stack([_vec(rng, direction, 1.0) for _ in new]))
    assert fm.save()
    assert await runner.rescore() == 65
    model_id, scores = db.get_taste_scores()
    assert model_id == r["model_id"] and all(p in scores for p in new)
    assert "/l/vid.mp4" not in scores


async def test_params_pruned_to_newest_five(runner, db):
    for _ in range(7):
        assert (await runner.train(180))["status"] == "ok"
    kept = sorted(runner.models_dir.glob("*.npz"))
    assert len(kept) == 5
    assert Path(db.get_active_taste_model()["params_path"]) in kept


def test_record_failed_model_keeps_active(tmp_path):
    db = DatabaseManager(tmp_path / "db")
    base = {
        "created_at": "2026-10-22T00:00:00+00:00",
        "labels_as_of": "2026-10-22T00:00:00+00:00",
        "embedding_model": MODEL_KEY,
        "dim": DIM,
        "categories": [],
        "n_pos": 20,
        "n_neg": 20,
        "n_pairs": 0,
        "half_life_days": 180.0,
        "params_path": "/x.npz",
    }
    active_id = db.record_taste_model(base)
    failed_id = db.record_taste_model({**base, "status": "failed", "error": "boom"})
    assert db.get_active_taste_model()["id"] == active_id
    rows = {m["id"]: m for m in db.list_taste_models()}
    assert rows[failed_id]["status"] == "failed" and rows[failed_id]["error"] == "boom"
    with pytest.raises(ValueError):
        db.record_taste_model({**base, "status": "retired"})
    db.close()


async def test_drain_survives_overlong_stderr_line(tmp_path, caplog):
    import logging

    runner = TasteRunner(None, tmp_path / "idx", tmp_path / "taste")
    stream = asyncio.StreamReader(limit=1024)
    stream.feed_data(b"x" * 5000 + b"\n" + b"after the long line\n")
    stream.feed_eof()
    with caplog.at_level(logging.INFO, logger="metascan.core.taste_runner"):
        await asyncio.wait_for(runner._drain(stream), timeout=5)
    assert "after the long line" in caplog.text


async def test_scoring_targets_and_task_write_off_loop(setup, monkeypatch):
    runner, *_ = setup
    calls = []
    real = asyncio.to_thread

    async def spy(fn, *a, **k):
        calls.append(getattr(fn, "__name__", repr(fn)))
        return await real(fn, *a, **k)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    assert (await runner.train(180))["status"] == "ok"
    assert "_scoring_targets" in calls
    assert calls.count("_write_json") >= 2  # labels doc + task file


def test_index_fingerprint_tracks_meta(setup):
    import os

    runner, _, index_dir, _, _ = setup
    fp = runner.index_fingerprint()
    assert fp is not None and fp[:2] == (MODEL_KEY, DIM)
    meta_path = index_dir / "index_meta.json"
    st = meta_path.stat()
    os.utime(meta_path, (st.st_atime, st.st_mtime + 10))
    assert runner.index_fingerprint() != fp
    meta_path.unlink()
    assert runner.index_fingerprint() is None
