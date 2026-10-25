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
from metascan.core.taste_runner import LABELS_VERSION, TasteRunner

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


def _build(tmp_path: Path, n_pos: int, n_neg: int, indexed=None):
    """``n_pos`` ✓ images (``/l/f..``) and ``n_neg`` ✗ ones (``/l/p..``)
    with vectors on either side of a direction, a ✓ video, and a deleted ✓
    image whose vector survives only on its tombstone."""
    rng = np.random.default_rng(7)
    direction = rng.normal(size=DIM)
    direction /= np.linalg.norm(direction)
    db = DatabaseManager(tmp_path / "db")
    index_dir = tmp_path / "similarity"
    vectors = {}
    for i in range(n_pos):
        vectors[f"/l/f{i:02d}.png"] = (_vec(rng, direction, 1.0), "pass")
    for i in range(n_neg):
        vectors[f"/l/p{i:02d}.png"] = (_vec(rng, direction, -1.0), "fail")
    vectors["/l/vid.mp4"] = (_vec(rng, direction, 1.0), "pass")
    for path, (_, grade) in vectors.items():
        db.save_media(_media(path))
        db.set_grade(path, grade)
    gone_vec = _vec(rng, direction, 1.0)
    db.set_embedding_lookup(lambda p: (gone_vec, MODEL_KEY))
    db.save_media(_media("/l/gone.png"))
    db.set_grade("/l/gone.png", "pass")
    db.delete_media(Path("/l/gone.png"))
    db.set_embedding_lookup(None)

    fm = FaissIndexManager(index_dir)
    fm.create(DIM, MODEL_KEY)
    # ``indexed`` simulates a partial index (a CLIP switch mid-rebuild):
    # only those paths have a vector yet.
    paths = [p for p in vectors if indexed is None or p in indexed]
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
    # 30 live ✓ + the tombstoned one; the video is never a label.
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
    assert active["labels_version"] == LABELS_VERSION == 2
    assert Path(active["params_path"]).exists()
    assert not runner.is_stale(active)
    assert not runner.training


def test_runner_and_worker_agree_on_the_labels_version():
    from metascan.workers import taste_worker

    assert taste_worker.LABELS_VERSION == LABELS_VERSION == 2


def test_worker_refuses_labels_of_another_version(tmp_path):
    from metascan.workers import taste_worker

    db, index_dir, _, _ = _build(tmp_path, 1, 1)
    db.close()
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"version": 1, "rows": [], "pairs": []}))
    task = {"mode": "train", "labels_path": str(labels), "out_dir": str(tmp_path)}
    index = taste_worker._Index(index_dir)
    with pytest.raises(taste_worker.TasteWorkerError, match="labels version 1"):
        taste_worker._train(task, index, tmp_path)


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
    # 5 live ✓ + the tombstoned one.
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
        "labels_version": LABELS_VERSION,
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


# ---- a partial index (CLIP switch mid-rebuild) ---------------------------


def _partial(n_pos_indexed: int, n_neg_indexed: int = 30) -> set:
    return {f"/l/f{i:02d}.png" for i in range(n_pos_indexed)} | {
        f"/l/p{i:02d}.png" for i in range(n_neg_indexed)
    }


async def test_partial_index_is_not_enough_vectors(tmp_path):
    # 30 ✓ graded, but only 10 have a vector yet (+ the tombstone's own
    # stored vector = 11 positives with vectors).
    db, index_dir, _, _ = _build(tmp_path, 30, 30, indexed=_partial(10))
    runner = TasteRunner(db, index_dir, tmp_path / "taste")
    try:
        r = await runner.train(180)
        assert r["status"] == "not_enough_data", r
        assert r["reason"] == "vectors"
        assert r["missing"] == {"positives": 9, "negatives": 0}
        # Not a failure: no failed row spam, and no model activated.
        assert db.list_taste_models() == []
        assert db.get_active_taste_model() is None
        assert not runner.training
    finally:
        db.close()


async def test_model_records_labels_without_vectors(tmp_path):
    db, index_dir, _, _ = _build(tmp_path, 30, 30, indexed=_partial(25))
    runner = TasteRunner(db, index_dir, tmp_path / "taste")
    try:
        r = await runner.train(180)
        assert r["status"] == "ok", r
        assert (r["n_pos"], r["n_neg"]) == (26, 30)
        assert r["n_no_vector"] == 5
        assert db.get_active_taste_model()["n_no_vector"] == 5
    finally:
        db.close()


def test_model_columns_added_to_an_existing_table(tmp_path):
    """A DB created before n_no_vector / labels_version existed keeps its
    model rows; they were trained under the first labels."""
    import sqlite3

    db = DatabaseManager(tmp_path / "db")
    db.close()
    conn = sqlite3.connect(tmp_path / "db" / "metascan.db")
    conn.execute("DROP TABLE taste_models")
    conn.execute(
        "CREATE TABLE taste_models (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "created_at TEXT NOT NULL, labels_as_of TEXT NOT NULL, status TEXT NOT NULL "
        "CHECK (status IN ('active','retired','failed')), embedding_model TEXT "
        "NOT NULL, dim INTEGER NOT NULL, categories TEXT NOT NULL, n_pos INTEGER "
        "NOT NULL, n_neg INTEGER NOT NULL, n_pairs INTEGER NOT NULL, holdout_auc "
        "REAL, half_life_days REAL NOT NULL, params_path TEXT NOT NULL, error TEXT)"
    )
    conn.execute(
        "INSERT INTO taste_models (created_at, labels_as_of, status, "
        "embedding_model, dim, categories, n_pos, n_neg, n_pairs, holdout_auc, "
        "half_life_days, params_path) VALUES ('t', 't', 'active', 'small', 32, "
        "'[]', 20, 20, 0, 0.8, 180, '/x.npz')"
    )
    conn.commit()
    conn.close()
    db = DatabaseManager(tmp_path / "db")
    try:
        active = db.get_active_taste_model()
        assert active is not None and active["n_no_vector"] == 0
        assert active["labels_version"] == 1
        assert active["n_pos"] == 20
    finally:
        db.close()


async def test_result_read_install_and_prune_off_loop(setup, monkeypatch):
    runner, *_ = setup
    calls = []
    real = asyncio.to_thread

    async def spy(fn, *a, **k):
        calls.append(getattr(fn, "__name__", repr(fn)))
        return await real(fn, *a, **k)

    monkeypatch.setattr(asyncio, "to_thread", spy)
    assert (await runner.train(180))["status"] == "ok"
    for name in ("_read_result", "_install_params", "_prune_params"):
        assert name in calls, (name, calls)


async def test_rescore_uses_the_slim_query(setup, monkeypatch):
    runner, db, *_ = setup
    assert (await runner.train(180))["status"] == "ok"

    def boom():
        raise AssertionError("rescore must not read the full training snapshot")

    monkeypatch.setattr(db, "taste_training_raw", boom)
    assert await runner.rescore() == 60


def test_scoring_raw_has_images_and_categories_only(setup):
    _, db, *_ = setup
    cid = db.create_category("Anime").id
    db.add_media_categories(["/l/f00.png"], cid, "viewer")
    raw = db.taste_scoring_raw()
    paths = {m["file_path"] for m in raw["media"]}
    assert "/l/vid.mp4" not in paths and "/l/f00.png" in paths
    cats = {m["file_path"]: m["categories"] for m in raw["media"]}
    assert cats["/l/f00.png"] == ["Anime"] and cats["/l/p00.png"] == []


async def test_score_write_failure_keeps_model_and_asks_for_rescore(setup, monkeypatch):
    runner, db, *_ = setup

    def boom(model_id, scores):
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr(db, "replace_taste_scores", boom)
    r = await runner.train(180)
    assert r["status"] == "ok" and r["rescore_needed"] is True
    assert db.get_active_taste_model()["id"] == r["model_id"]


async def test_missing_index_error_says_how_to_fix_it(setup):
    runner, db, index_dir, _, _ = setup
    (index_dir / "index_meta.json").unlink()
    r = await runner.train(180)
    assert r["status"] == "error"
    assert "Build the similarity index first" in r["error"]


def _set_created(db, image_id: int, created_at: str) -> None:
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE t2i_images SET created_at = ? WHERE id = ?", (created_at, image_id)
        )
        conn.commit()


def _index_vectors(index_dir: Path, paths) -> np.ndarray:
    import faiss

    ids = json.loads((index_dir / "id_mapping.json").read_text())
    index = faiss.read_index(str(index_dir / "faiss_index.bin"))
    return np.stack([index.reconstruct(ids.index(p)) for p in paths])


def _mean_pairwise_distance(vecs: np.ndarray) -> float:
    v = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
    n = len(v)
    sims = v @ v.T
    return float(1.0 - (sims.sum() - np.trace(sims)) / (n * (n - 1)))


async def test_diversity_computed_on_train(setup):
    from datetime import timezone

    runner, db, index_dir, rng, direction = setup
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    # ``_build`` graded /l/fNN ✓ and /l/pNN ✗: diversity is over ✓ T2I.
    latest = [f"/l/f{i:02d}.png" for i in range(5)]
    for p in latest:
        _set_created(
            db,
            db.create_t2i_image(file_path=p, writer_id="base"),
            "2026-10-15 10:00:00",
        )
    # One ✓ in the previous window: n = 1, no distance.
    _set_created(db, db.create_t2i_image(file_path="/l/f05.png"), "2026-10-01 10:00:00")
    # Outside the six windows, and a ★ ✗ in the latest one: neither counts.
    _set_created(db, db.create_t2i_image(file_path="/l/f06.png"), "2026-06-01 10:00:00")
    _set_created(db, db.create_t2i_image(file_path="/l/p00.png"), "2026-10-15 10:00:00")
    assert db.set_favorite(Path("/l/p00.png"), True)
    # A deleted ✓ generation: its vector lives only on the tombstone.
    gone_vec = _vec(rng, direction, 1.0)
    db.save_media(_media("/l/gen_gone.png"))
    _set_created(
        db, db.create_t2i_image(file_path="/l/gen_gone.png"), "2026-10-16 10:00:00"
    )
    db.set_grade("/l/gen_gone.png", "pass")
    db.set_embedding_lookup(lambda p: (gone_vec, MODEL_KEY))
    db.delete_media(Path("/l/gen_gone.png"))
    db.set_embedding_lookup(None)

    r = await runner.train(180, now=now)
    assert r["status"] == "ok", r
    div = {d["window_start"]: d for d in db.latest_taste_diversity()}
    assert sorted(div) == [
        "2026-08-01",
        "2026-08-15",
        "2026-08-29",
        "2026-09-12",
        "2026-09-26",
        "2026-10-10",
    ]
    expected = _mean_pairwise_distance(
        np.vstack([_index_vectors(index_dir, latest), gone_vec[None, :]])
    )
    assert div["2026-10-10"]["n"] == 6
    assert div["2026-10-10"]["mean_distance"] == pytest.approx(expected, abs=1e-5)
    assert div["2026-09-26"]["computed_at"]
    assert {k: v for k, v in div["2026-09-26"].items() if k != "computed_at"} == {
        "window_start": "2026-09-26",
        "n": 1,
        "mean_distance": None,
    }
    assert div["2026-08-01"]["n"] == 0 and div["2026-08-01"]["mean_distance"] is None


async def test_diversity_failure_does_not_fail_training(setup, monkeypatch):
    runner, db, _, _, _ = setup

    def boom(*a, **k):
        raise RuntimeError("diversity query failed")

    monkeypatch.setattr(db, "generation_outcomes", boom)
    r = await runner.train(180)
    assert r["status"] == "ok", r
    assert db.latest_taste_diversity() == []


async def test_worker_diversity_failure_does_not_fail_training(setup, monkeypatch):
    runner, db, _, _, _ = setup
    import metascan.core.taste_runner as tr

    # A spec the worker cannot decode (bad base64 for a matching model):
    # _diversity raises inside the subprocess.
    monkeypatch.setattr(
        tr,
        "_diversity_task",
        lambda db, now: {
            "window_starts": ["2026-10-10"],
            "items": [
                {
                    "file_path": "/l/f00.png",
                    "window_start": "2026-10-10",
                    "embedding": "!!!notbase64",
                    "embedding_model": MODEL_KEY,
                }
            ],
        },
    )
    r = await runner.train(180)
    assert r["status"] == "ok", r
    assert db.latest_taste_diversity() == []


# ---- Review Focus 5: a reused path never lends its vector to a tombstone --


def _index_with(index_dir: Path, vectors) -> None:
    fm = FaissIndexManager(index_dir)
    if not fm.load():
        fm.create(DIM, MODEL_KEY)
    fm.add_batch(list(vectors), np.stack(list(vectors.values())))
    assert fm.save()


async def test_worker_never_borrows_a_vector_by_path(tmp_path):
    db, index_dir, rng, direction = _build(tmp_path, 30, 30)
    try:
        # A ✓ image deleted while its vector existed only for another CLIP
        # model; a new, ungraded file now lives at the same path and has a
        # vector in the index -- a ✗-looking one.
        db.set_embedding_lookup(lambda p: (np.ones(64, dtype=np.float32), "large"))
        db.save_media(_media("/l/reused.png"))
        db.set_grade("/l/reused.png", "pass")
        db.delete_media(Path("/l/reused.png"))
        db.set_embedding_lookup(None)
        db.save_media(_media("/l/reused.png"))
        _index_with(index_dir, {"/l/reused.png": _vec(rng, direction, -1.0)})
        runner = TasteRunner(db, index_dir, tmp_path / "taste")
        r = await runner.train(180)
        assert r["status"] == "ok", r
        # The tombstone stays a label but has no vector: the index's vector
        # for its path belongs to the new file.
        assert (r["n_pos"], r["n_neg"], r["n_no_vector"]) == (31, 30, 1)
    finally:
        db.close()


def test_training_matrix_reads_only_a_tombstones_own_vector(tmp_path):
    import base64

    from metascan.workers import taste_worker

    rng = np.random.default_rng(3)
    direction = rng.normal(size=DIM)
    in_index = {
        p: _vec(rng, direction, 1.0)
        for p in ("/l/none.png", "/l/other.png", "/l/own.png", "/l/live.png")
    }
    index_dir = tmp_path / "similarity"
    _index_with(index_dir, in_index)
    own = _vec(rng, direction, -1.0)

    def row(key, path, tombstone, emb=None, model=None, label=1):
        return {
            "key": key,
            "file_path": path,
            "tombstone": tombstone,
            "label": label,
            "weight": 1.0,
            "categories": [],
            "batch_id": "b1",
            "embedding": (
                base64.b64encode(emb.tobytes()).decode() if emb is not None else None
            ),
            "embedding_model": model,
        }

    labels = {
        "version": LABELS_VERSION,
        "rows": [
            row("tombstone:1", "/l/none.png", True),  # no stored vector
            row(  # a stored vector, for another CLIP model only
                "tombstone:2",
                "/l/other.png",
                True,
                np.ones(64, dtype=np.float32),
                "large",
            ),
            row("tombstone:3", "/l/own.png", True, own, MODEL_KEY),
            row("/l/live.png", "/l/live.png", False, label=0),
            # A row that does not say what it is gets no vector by path.
            {**row("tombstone:4", "/l/live.png", True), "tombstone": None},
        ],
        "pairs": [[0, 3, 1.0], [2, 3, 1.0]],
    }
    emb, y, w, cats, pairs, skipped = taste_worker._training_matrix(
        labels, taste_worker._Index(index_dir)
    )
    assert skipped == 3
    assert y.tolist() == [1.0, 0.0]
    assert np.allclose(emb[0], own)  # its own vector, not the index's
    assert np.allclose(emb[1], in_index["/l/live.png"])
    assert pairs == [(0, 1, 1.0)]


async def test_diversity_never_borrows_a_vector_by_path(setup):
    from datetime import timezone

    runner, db, index_dir, rng, direction = setup
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    # A deleted ✓ generation whose vector existed only for another CLIP
    # model; a library file now lives at its path and is indexed.
    db.save_media(_media("/l/gen_reused.png"))
    image_id = db.create_t2i_image(file_path="/l/gen_reused.png")
    _set_created(db, image_id, "2026-10-16 10:00:00")
    db.set_grade("/l/gen_reused.png", "pass")
    db.set_embedding_lookup(lambda p: (np.ones(64, dtype=np.float32), "large"))
    assert db.delete_t2i_image(image_id)[0]  # the lineage row goes too
    db.set_embedding_lookup(None)
    db.save_media(_media("/l/gen_reused.png"))
    (gone,) = [
        g
        for g in db.generation_outcomes(include_embeddings=True)
        if g["file_path"] == "/l/gen_reused.png"
    ]
    assert gone["tombstone"] is True and gone["grade"] == "pass"
    _index_with(index_dir, {"/l/gen_reused.png": _vec(rng, direction, 1.0)})
    r = await runner.train(180, now=now)
    assert r["status"] == "ok", r
    div = {d["window_start"]: d for d in db.latest_taste_diversity()}
    assert div["2026-10-10"]["n"] == 0


async def test_a_diversity_row_must_say_whether_it_is_a_tombstone(
    setup, monkeypatch, caplog
):
    """A generation row that does not say whether it is a tombstone is
    refused, never taken for a live one whose vector may be read by its
    path (the path may hold another file now): no diversity is stored, and
    training still runs."""
    from datetime import timezone

    runner, db, _, _, _ = setup
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    for p in [f"/l/f{i:02d}.png" for i in range(5)]:
        _set_created(db, db.create_t2i_image(file_path=p), "2026-10-15 10:00:00")
    outcomes = db.generation_outcomes

    def unmarked(*args, **kwargs):
        rows = outcomes(*args, **kwargs)
        for row in rows:
            del row["tombstone"]
        return rows

    monkeypatch.setattr(db, "generation_outcomes", unmarked)
    with caplog.at_level("WARNING", logger="metascan.core.taste_runner"):
        r = await runner.train(180, now=now)
    assert r["status"] == "ok", r
    assert db.latest_taste_diversity() == []
    assert "KeyError: 'tombstone'" in caplog.text
