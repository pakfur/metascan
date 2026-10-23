"""Auto-retrain decision and loop for the taste scorer (fake runner, real
isolated DB, explicit clock)."""

import asyncio

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


async def test_loop_waits_for_quiet(service, db, fake_runner):
    # 40 label events (>= 25) at T0, no model yet.
    seed_labels(db)
    assert await service.maybe_retrain(now=after(10)) is False
    assert fake_runner.train_calls == []
    assert await service.maybe_retrain(now=after(130)) is True
    await service.wait_idle()
    assert fake_runner.train_calls == [90.0]


async def test_loop_needs_enough_events(service, db, fake_runner):
    seed_labels(db)
    db.record_taste_model(model_row(labels_as_of=after(1).isoformat()))
    for i in range(24):
        p = f"/lib/new{i:02d}.png"
        db.save_media(media(p))
        db.set_verdict(p, "pass", None, after(5))
    assert await service.maybe_retrain(now=after(500)) is False
    db.save_media(media("/lib/new24.png"))
    db.set_verdict("/lib/new24.png", "pass", None, after(5))
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    assert len(fake_runner.train_calls) == 1


async def test_loop_retrains_when_stale(service, db, fake_runner):
    seed_labels(db)
    db.record_taste_model(model_row(labels_as_of=after(1).isoformat()))
    # No new events since the model: not due while fresh...
    assert await service.maybe_retrain(now=after(500)) is False
    # ...due once the CLIP model changed under it.
    fake_runner.stale = True
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    assert len(fake_runner.train_calls) == 1


async def test_loop_respects_auto_retrain_off(service, db, fake_runner, taste_config):
    seed_labels(db)
    taste_config["auto_retrain"] = False
    assert await service.maybe_retrain(now=after(500)) is False
    assert (await service.status())["auto_retrain"] is False


async def test_loop_skips_when_labels_insufficient(service, db, fake_runner):
    seed_labels(db, n_fav=5, n_pass=30)
    assert await service.maybe_retrain(now=after(500)) is False
    assert fake_runner.train_calls == []


async def test_loop_skips_while_training(service, db, fake_runner):
    seed_labels(db)
    fake_runner.training = True
    assert await service.maybe_retrain(now=after(500)) is False


async def test_failed_auto_retrain_not_repeated_without_new_events(
    service, db, fake_runner
):
    seed_labels(db)
    fake_runner.result = {"status": "error", "error": "boom"}
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    # Same inputs: no retry storm every tick.
    assert await service.maybe_retrain(now=after(600)) is False
    db.save_media(media("/lib/x.png"))
    db.set_verdict("/lib/x.png", "favorite", None, after(550))
    assert await service.maybe_retrain(now=after(700)) is True
    await service.wait_idle()
    assert len(fake_runner.train_calls) == 2


async def test_events_during_training_count_next(service, db, fake_runner):
    seed_labels(db)
    # The run snapshots its labels at T0+1; ratings arrive while it runs.
    fake_runner.record_model_at = after(1)
    fake_runner.gate = asyncio.Event()
    await service.start_training()
    await fake_runner.started.wait()
    for i in range(3):
        p = f"/lib/during{i}.png"
        db.save_media(media(p))
        db.set_verdict(p, "favorite", None, after(20))
    fake_runner.gate.set()
    await service.wait_idle()
    status = await service.status()
    assert status["active_model"] is not None
    assert status["new_events"] == 3


async def test_retrain_loop_ticks_swallows_errors_and_cancels(service, monkeypatch):
    import backend.services.taste_service as ts

    monkeypatch.setattr(ts, "RETRAIN_CHECK_S", 0.01)
    ticks = []

    async def flaky(now=None):
        ticks.append(1)
        if len(ticks) == 1:
            raise RuntimeError("db locked")
        return False

    monkeypatch.setattr(service, "maybe_retrain", flaky)
    service.start_retrain_loop()
    for _ in range(200):
        await asyncio.sleep(0.01)
        if len(ticks) >= 3:
            break
    assert len(ticks) >= 3
    task = service._loop_task
    await service.aclose()
    assert task is not None and task.done()


async def test_failed_stale_retry_after_index_change(service, db, fake_runner):
    seed_labels(db)
    db.record_taste_model(model_row(labels_as_of=after(1).isoformat()))
    fake_runner.stale = True
    fake_runner.fingerprint = None  # index missing mid-rebuild
    fake_runner.result = {"status": "error", "error": "no index"}
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    assert await service.maybe_retrain(now=after(600)) is False
    fake_runner.fingerprint = ("large", 64, 2.0)  # new index written
    assert await service.maybe_retrain(now=after(700)) is True
    await service.wait_idle()
    assert len(fake_runner.train_calls) == 2


async def test_embedding_complete_clears_failed_guard(service, db, fake_runner):
    seed_labels(db)
    fake_runner.result = {"status": "error", "error": "boom"}
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    assert await service.maybe_retrain(now=after(600)) is False
    service.attach_loop(asyncio.get_running_loop())
    service.schedule_rescore_threadsafe()
    await asyncio.sleep(0.05)
    await service.wait_idle()
    assert await service.maybe_retrain(now=after(700)) is True
    await service.wait_idle()


async def test_not_enough_data_clears_last_error(service, db, fake_runner):
    seed_labels(db)
    fake_runner.raise_on_train = RuntimeError("boom")
    await service.start_training()
    await service.wait_idle()
    assert (await service.status())["last_error"] == "boom"
    fake_runner.raise_on_train = None
    fake_runner.result = {"status": "not_enough_data", "missing": {}}
    await service.start_training()
    await service.wait_idle()
    assert (await service.status())["last_error"] is None


async def test_config_read_off_the_event_loop(db, fake_runner, recorder):
    import threading

    from backend.services.taste_service import TasteService

    loop_thread = threading.get_ident()
    readers = []

    def get_config():
        readers.append(threading.get_ident())
        return {"half_life_days": 90.0, "auto_retrain": True}

    svc = TasteService(db, fake_runner, get_config=get_config, broadcast=recorder)
    await svc.status()
    await svc.maybe_retrain(now=after(500))
    assert readers and loop_thread not in readers


def test_label_counts_match_build_label_set(db):
    from pathlib import Path

    from metascan.core.taste_labels import build_label_set, count_labels

    seed_labels(db, n_fav=4, n_pass=3)
    for p in ("/m/seen.png", "/m/unseen.png", "/m/vid.mp4", "/m/gone.png"):
        db.save_media(media(p))
    db.mark_seen("/m/seen.png")
    db.set_verdict("/m/vid.mp4", "favorite")
    # Tombstoned favorite, path never re-added.
    db.set_verdict("/m/gone.png", "favorite")
    db.delete_media(Path("/m/gone.png"))
    # Tombstoned pass, path re-added live and labeled: live wins.
    db.set_verdict("/lib/f00.png", "pass")
    db.delete_media(Path("/lib/f00.png"))
    db.save_media(media("/lib/f00.png"))
    db.set_verdict("/lib/f00.png", "favorite")
    # Tombstoned twice: only the newest tombstone counts.
    db.save_media(media("/m/twice.png"))
    db.set_verdict("/m/twice.png", "favorite")
    db.delete_media(Path("/m/twice.png"))
    db.save_media(media("/m/twice.png"))
    db.set_verdict("/m/twice.png", "pass")
    db.delete_media(Path("/m/twice.png"))
    full = build_label_set(db.taste_training_raw(), after(1000), 180.0)
    assert count_labels(db.taste_label_counts_raw(), after(1000)) == (
        full.n_pos,
        full.n_neg,
    )
    assert (full.n_pos, full.n_neg) == (5, 5)


# ---- partial index / CLIP switch guards ----------------------------------


async def test_loop_skips_while_the_index_is_building(service, db, fake_runner):
    seed_labels(db)
    fake_runner.indexing = True
    assert await service.maybe_retrain(now=after(500)) is False
    assert fake_runner.train_calls == []
    fake_runner.indexing = False
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()


async def test_not_enough_vectors_is_reported_as_missing(
    service, db, fake_runner, recorder
):
    seed_labels(db)
    fake_runner.result = {
        "status": "not_enough_data",
        "reason": "vectors",
        "missing": {"positives": 9, "negatives": 0},
    }
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    st = await service.status()
    assert st["missing"] == {"positives": 9, "negatives": 0}
    assert st["missing_reason"] == "vectors"
    assert st["last_error"] is None
    assert db.list_taste_models() == []
    # Same inputs: not retried every tick.
    assert await service.maybe_retrain(now=after(600)) is False
    # A finished embedding run may have added the vectors: try again.
    service._on_embedding_complete()
    await service.wait_idle()
    st = await service.status()
    assert st["missing"] is None and st["missing_reason"] is None
    fake_runner.result = {"status": "ok"}
    assert await service.maybe_retrain(now=after(700)) is True
    await service.wait_idle()


async def test_label_shortage_reports_labels_reason(service, db):
    seed_labels(db, n_fav=5, n_pass=30)
    st = await service.status()
    assert st["missing"] == {"positives": 15, "negatives": 0}
    assert st["missing_reason"] == "labels"


async def test_under_vectored_model_is_due_after_an_embedding_run(
    service, db, fake_runner
):
    seed_labels(db)
    # Trained while 10 of 50 labeled rows (20%) had no vector.
    db.record_taste_model(
        model_row(labels_as_of=after(1).isoformat(), n_pos=20, n_neg=20, n_no_vector=10)
    )
    assert await service.maybe_retrain(now=after(500)) is False
    service._on_embedding_complete()
    await service.wait_idle()
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    assert len(fake_runner.train_calls) == 1
    # Not due again until the next embedding run.
    assert await service.maybe_retrain(now=after(600)) is False


async def test_well_vectored_model_is_not_due_after_an_embedding_run(
    service, db, fake_runner
):
    seed_labels(db)
    # 4 of 44 (9%) without a vector: under the 10% threshold.
    db.record_taste_model(
        model_row(labels_as_of=after(1).isoformat(), n_pos=20, n_neg=20, n_no_vector=4)
    )
    service._on_embedding_complete()
    await service.wait_idle()
    assert await service.maybe_retrain(now=after(500)) is False
    assert fake_runner.train_calls == []


# ---- stale scores, first model, score-write failure ----------------------


async def test_stale_model_broadcasts_scores_updated_once(
    service, db, fake_runner, recorder
):
    mid = db.record_taste_model(model_row())
    await service.check_stale()
    assert "scores_updated" not in recorder.names()
    fake_runner.stale = True
    await service.check_stale()
    await service.check_stale()
    ups = [d for c, e, d in recorder.events if e == "scores_updated"]
    assert ups == [{"model_id": None}]
    # Switched back to the model's CLIP: its scores are served again.
    fake_runner.stale = False
    await service.check_stale()
    ups = [d for c, e, d in recorder.events if e == "scores_updated"]
    assert ups == [{"model_id": None}, {"model_id": mid}]


async def test_rescore_of_a_stale_model_broadcasts(service, db, fake_runner, recorder):
    db.record_taste_model(model_row())
    fake_runner.stale = True
    fake_runner.rescore_result = None
    assert await service.rescore() is None
    assert [d for c, e, d in recorder.events if e == "scores_updated"] == [
        {"model_id": None}
    ]


async def test_retrain_loop_tick_checks_stale(
    service, db, fake_runner, recorder, monkeypatch
):
    import backend.services.taste_service as ts

    monkeypatch.setattr(ts, "RETRAIN_CHECK_S", 0.01)
    db.record_taste_model(model_row())
    fake_runner.stale = True
    service.start_retrain_loop()
    for _ in range(100):
        if "scores_updated" in recorder.names():
            break
        await asyncio.sleep(0.01)
    await service.aclose()
    assert "scores_updated" in recorder.names()


async def test_first_model_trains_without_25_events(service, db, fake_runner):
    # 20 favorites (20 events) + 20 seen-only negatives (no training events).
    seed_labels(db, n_fav=20, n_pass=0)
    for i in range(20):
        p = f"/lib/s{i:02d}.png"
        db.save_media(media(p))
        db.mark_seen(p, now=after(0))
    assert (await service.status())["missing"] is None
    assert await service.maybe_retrain(now=after(10)) is False  # not idle yet
    assert await service.maybe_retrain(now=after(500)) is True
    await service.wait_idle()
    assert len(fake_runner.train_calls) == 1


async def test_status_new_events_is_null_without_a_model(service, db):
    seed_labels(db)
    assert (await service.status())["new_events"] is None
    db.record_taste_model(model_row(labels_as_of=after(1).isoformat()))
    assert (await service.status())["new_events"] == 0


async def test_score_write_failure_schedules_a_rescore(service, db, fake_runner):
    seed_labels(db)
    fake_runner.result = {"status": "ok", "rescore_needed": True}
    await service.start_training()
    await service.wait_idle()
    assert fake_runner.rescore_calls == 1
