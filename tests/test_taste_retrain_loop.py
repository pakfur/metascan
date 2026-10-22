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
