"""REST tests for /api/taste/* and the judgment batch position (synthetic
data, isolated temp DB, fake TasteRunner)."""

import asyncio
import os
import threading
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._taste_service_fixtures import (  # noqa: F401
    db,
    fake_runner,
    media,
    model_row,
    recorder,
    seed_labels,
    service,
    taste_config,
)


@pytest.fixture
def client(db, service, tmp_path):
    os.environ.setdefault("METASCAN_API_KEY", "")
    import backend.dependencies as deps
    from backend.api import judgments, taste

    old = deps._db_singleton
    deps._db_singleton = db  # type: ignore[attr-defined]
    taste.set_taste_service(service)
    with patch("backend.dependencies.get_data_dir", return_value=tmp_path):
        app = FastAPI()
        app.include_router(judgments.router)
        app.include_router(taste.router)
        # One portal (one event loop) for the whole test, so a background
        # training task started by a request outlives that request.
        with TestClient(app) as tc:
            yield tc
    taste.set_taste_service(None)
    deps._db_singleton = old  # type: ignore[attr-defined]


def _scores(db, scores):
    for p in scores:
        db.save_media(media(p))
    model_id = db.record_taste_model(model_row())
    db.replace_taste_scores(model_id, scores)
    return model_id


# ---------------------------------------------------------------- status


def test_status_without_model(client):
    body = client.get("/api/taste/status").json()
    assert body["active_model"] is None
    assert body["stale"] is False and body["training"] is False
    # Nothing to be "new since the last training" before the first one.
    assert body["new_events"] is None
    assert body["missing"] == {"positives": 20, "negatives": 20}
    assert body["missing_reason"] == "labels"
    assert body["auto_retrain"] is True
    assert body["last_error"] is None
    assert body["labels_outdated"] is False
    # No deleted images being re-embedded for the CLIP model.
    assert body["reembed"] is None


def test_status_with_model(client, db, fake_runner):
    seed_labels(db)
    mid = db.record_taste_model(model_row(labels_as_of="2000-01-01T00:00:00+00:00"))
    body = client.get("/api/taste/status").json()
    am = body["active_model"]
    assert am["id"] == mid and am["holdout_auc"] == 0.81
    assert set(am) == {
        "id",
        "created_at",
        "n_pos",
        "n_neg",
        "n_pairs",
        "holdout_auc",
        "embedding_model",
        "half_life_days",
    }
    # 40 grade events; the model was trained on the current labels.
    assert body["new_events"] == 40 and body["missing"] is None
    assert body["labels_outdated"] is False
    fake_runner.stale = True
    assert client.get("/api/taste/status").json()["stale"] is True


def test_status_says_when_the_model_predates_grades(client, db):
    db.record_taste_model(model_row(labels_version=1))
    body = client.get("/api/taste/status").json()
    assert body["labels_outdated"] is True
    assert body["stale"] is False


def test_status_last_error_from_failed_row(client, db):
    db.record_taste_model(model_row(status="failed", error="index unreadable"))
    assert client.get("/api/taste/status").json()["last_error"] == "index unreadable"
    db.record_taste_model(model_row())
    assert client.get("/api/taste/status").json()["last_error"] is None


# ---------------------------------------------------------------- train


def test_train_conflict(client, fake_runner):
    fake_runner.training = True
    r = client.post("/api/taste/train")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "already_training"


def test_not_enough_data_409(client, db, fake_runner):
    seed_labels(db, n_pos=5, n_neg=30)
    r = client.post("/api/taste/train")
    assert r.status_code == 409
    assert r.json()["detail"] == {
        "code": "not_enough_data",
        "missing": {"positives": 15, "negatives": 0},
    }
    assert fake_runner.train_calls == []


def test_train_started_and_broadcasts(client, db, fake_runner, recorder):
    seed_labels(db)
    fake_runner.result = {
        "status": "ok",
        "model_id": 7,
        "holdout_auc": 0.9,
        "n_pos": 20,
        "n_neg": 20,
    }
    r = client.post("/api/taste/train")
    assert r.status_code == 202 and r.json() == {"status": "started"}
    # TestClient runs the background task to completion on its loop.
    for _ in range(50):
        if "scores_updated" in recorder.names():
            break
        time.sleep(0.02)
    assert fake_runner.train_calls == [90.0]
    assert recorder.names() == [
        "training_started",
        "training_complete",
        "scores_updated",
    ]
    complete = [d for _, e, d in recorder.events if e == "training_complete"][0]
    assert complete == {"model_id": 7, "holdout_auc": 0.9, "n_pos": 20, "n_neg": 20}


# ---------------------------------------------------------------- scores


def test_scores_served_when_fresh(client, db):
    mid = _scores(db, {"/s/a.png": 0.25, "/s/b.png": 0.75})
    body = client.get("/api/taste/scores").json()
    assert body["model_id"] == mid
    assert body["scores"] == {"/s/a.png": 0.25, "/s/b.png": 0.75}
    sub = client.post("/api/taste/scores", json={"file_paths": ["/s/b.png"]}).json()
    assert sub == {"model_id": mid, "scores": {"/s/b.png": 0.75}}


def test_scores_empty_when_stale(client, db, fake_runner):
    _scores(db, {"/s/a.png": 0.25})
    fake_runner.stale = True
    assert client.get("/api/taste/scores").json() == {"model_id": None, "scores": {}}
    assert client.post(
        "/api/taste/scores", json={"file_paths": ["/s/a.png"]}
    ).json() == {"model_id": None, "scores": {}}


def test_scores_empty_without_model(client):
    assert client.get("/api/taste/scores").json() == {"model_id": None, "scores": {}}


# ---------------------------------------------------------------- review queue


def test_review_queue_is_ungraded_images(client, db):
    _scores(
        db,
        {
            "/q/far_hi.png": 0.9,
            "/q/near.png": 0.45,
            "/q/mid.png": 0.6,
            "/q/far_lo.png": 0.1,
            "/q/seen.png": 0.52,
            "/q/star.png": 0.42,
            "/q/cleared.png": 0.7,
            "/q/ok.png": 0.5,
            "/q/bad.png": 0.5,
            "/q/star_bad.png": 0.5,
        },
    )
    # Views and ★ are not grades: those images still want one.
    db.mark_seen("/q/seen.png")
    db.set_favorite_state("/q/star.png", True)
    db.set_grade("/q/cleared.png", "pass")
    db.set_grade("/q/cleared.png", None)
    db.set_grade("/q/ok.png", "pass")
    db.set_grade("/q/bad.png", "fail")
    db.set_favorite_state("/q/star_bad.png", True)
    db.set_grade("/q/star_bad.png", "fail")
    body = client.get("/api/taste/review-queue").json()
    assert body["paths"] == [
        "/q/seen.png",
        "/q/near.png",
        "/q/star.png",
        "/q/mid.png",
        "/q/cleared.png",
        "/q/far_hi.png",
        "/q/far_lo.png",
    ]
    assert client.get("/api/taste/review-queue?limit=2").json()["paths"] == [
        "/q/seen.png",
        "/q/near.png",
    ]


def test_review_queue_empty_without_usable_model(client, db, fake_runner):
    assert client.get("/api/taste/review-queue").json() == {"paths": []}
    _scores(db, {"/q/a.png": 0.5})
    fake_runner.stale = True
    assert client.get("/api/taste/review-queue").json() == {"paths": []}


# ---------------------------------------------------------------- batch position


def test_judgment_has_batch_position(client, db):
    for i in range(3):
        p = f"/t2i/b1_{i}.png"
        db.save_media(media(p))
        db.create_t2i_image(file_path=p, batch_id="b1")
    db.save_media(media("/t2i/other.png"))
    db.create_t2i_image(file_path="/t2i/other.png", batch_id="b2")
    db.save_media(media("/lib/plain.png"))
    assert client.get("/api/judgments//t2i/b1_1.png").json()["batch"] == {
        "index": 2,
        "size": 3,
    }
    assert client.get("/api/judgments//t2i/other.png").json()["batch"] == {
        "index": 1,
        "size": 1,
    }
    assert client.get("/api/judgments//lib/plain.png").json()["batch"] is None
    assert db.get_t2i_batch_position("/t2i/b1_0.png") == (1, 3)
    assert db.get_t2i_batch_position("/lib/plain.png") is None


# ---------------------------------------------------------------- service-level


async def test_two_train_requests_one_worker(service, db, fake_runner):
    from backend.services.taste_service import TrainConflict

    seed_labels(db)
    fake_runner.gate = asyncio.Event()
    results = await asyncio.gather(
        service.start_training(), service.start_training(), return_exceptions=True
    )
    conflicts = [r for r in results if isinstance(r, TrainConflict)]
    assert len(conflicts) == 1 and conflicts[0].code == "already_training"
    await fake_runner.started.wait()
    assert service.is_training
    fake_runner.gate.set()
    await service.wait_idle()
    assert len(fake_runner.train_calls) == 1
    assert not service.is_training


async def test_train_exception_becomes_last_error(service, db, fake_runner, recorder):
    seed_labels(db)
    fake_runner.raise_on_train = OSError("snapshot read failed")
    await service.start_training()
    await service.wait_idle()
    assert recorder.names() == ["training_started", "training_failed"]
    assert recorder.events[-1][2] == {"error": "snapshot read failed"}
    assert (await service.status())["last_error"] == "snapshot read failed"


async def test_train_error_result_broadcasts_failed(service, db, fake_runner, recorder):
    seed_labels(db)
    fake_runner.result = {"status": "error", "error": "worker exited 1"}
    await service.start_training()
    await service.wait_idle()
    assert recorder.names() == ["training_started", "training_failed"]
    assert (await service.status())["last_error"] == "worker exited 1"


async def test_rescore_after_embedding_from_thread(service, fake_runner, recorder, db):
    model_id = db.record_taste_model(model_row())
    service.attach_loop(asyncio.get_running_loop())
    t = threading.Thread(target=service.schedule_rescore_threadsafe)
    t.start()
    t.join()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if recorder.names():
            break
    await service.wait_idle()
    assert fake_runner.rescore_calls == 1
    assert recorder.events == [("taste", "scores_updated", {"model_id": model_id})]


async def test_rescore_exception_is_swallowed(service, fake_runner, recorder):
    fake_runner.raise_on_rescore = RuntimeError("spawn failed")
    assert await service.rescore() is None
    assert recorder.names() == []


async def test_rescore_none_does_not_broadcast(service, fake_runner, recorder):
    fake_runner.rescore_result = None
    assert await service.rescore() is None
    assert recorder.names() == []


async def test_aclose_cancels_inflight_training(service, db, fake_runner):
    seed_labels(db)
    fake_runner.gate = asyncio.Event()
    service.start_retrain_loop()
    await service.start_training()
    await fake_runner.started.wait()
    await service.aclose()
    assert not service.is_training
    assert service._loop_task is None or service._loop_task.done()


def test_embedding_complete_runs_hook_and_survives_its_error(monkeypatch):
    from types import SimpleNamespace

    from backend.api import similarity

    eq = SimpleNamespace(get_last_progress=lambda: {})
    similarity._attach_embedding_callbacks(eq)
    monkeypatch.setattr(similarity, "_current_scan_tag_with_vlm", False)
    calls = []
    similarity.set_embedding_complete_hook(lambda: calls.append(1))
    try:
        eq.on_complete(5)
        assert calls == [1]

        def boom():
            raise RuntimeError("hook broke")

        similarity.set_embedding_complete_hook(boom)
        eq.on_complete(5)  # logged, not raised
    finally:
        similarity.set_embedding_complete_hook(None)


async def test_rescore_during_training_runs_after(service, db, fake_runner):
    seed_labels(db)
    db.record_taste_model(model_row())
    fake_runner.gate = asyncio.Event()
    await service.start_training()
    await fake_runner.started.wait()
    assert await service.rescore() is None
    assert fake_runner.rescore_calls == 0
    fake_runner.gate.set()
    await service.wait_idle()
    assert fake_runner.rescore_calls == 1


async def test_rescore_during_rescore_runs_once_more(service, db, fake_runner):
    db.record_taste_model(model_row())
    fake_runner.rescore_gate = asyncio.Event()
    first = asyncio.create_task(service.rescore())
    await fake_runner.rescore_started.wait()
    assert await service.rescore() is None
    assert await service.rescore() is None  # coalesced into one follow-up
    fake_runner.rescore_gate.set()
    await first
    await service.wait_idle()
    assert fake_runner.rescore_calls == 2
