"""Hindsight briefs job: selection, cleaning, resumability, cancel, GPU
waits, explicit unloads and the routes -- a fake VLM, an isolated DB."""

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.services.hindsight_briefs import (
    HindsightBriefsJob,
    clean_brief,
    set_briefs_job,
)
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media
from metascan.core.prompt_store import PromptStore
from metascan.core.vlm_client import VlmError, is_background_vlm_work

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    mgr = DatabaseManager(tmp_path / "db")
    yield mgr
    mgr.close()


@pytest.fixture
def store(tmp_path):
    p = tmp_path / "meta_prompt.yml"
    p.write_text("HINDSIGHT_BRIEF_SYSTEM: |-\n  BRIEF-SYSTEM\n", encoding="utf-8")
    return PromptStore(p)


def _fav(db, tmp_path, name, *, writer="base", model="krea2", prompt="P", fav=True):
    f = tmp_path / name
    f.write_bytes(b"x")
    path = str(f)
    db.save_media(
        Media(
            file_path=Path(path),
            file_size=1,
            width=8,
            height=8,
            format="png",
            created_at=datetime.now(),
            modified_at=datetime.now(),
        )
    )
    db.create_t2i_image(
        file_path=path,
        model=model,
        caption="c",
        prompt_used=prompt,
        writer_id=writer,
        writer_model="qwen3vl-8b",
    )
    if fav:
        db.set_verdict(path, "favorite", None, NOW)


class FakeClient:
    def __init__(self, replies=None):
        self.model_id = "qwen3vl-4b"
        self.preferred_model_id = "qwen3vl-4b"
        self._explicit_stops = 0
        self.calls: List[dict] = []
        self.started: List[Any] = []
        self.replies = replies or (lambda i, path: f"a scene number {i}.")
        self.background_seen: List[bool] = []
        self.on_call = None

    async def ensure_started(self, model_id, *, unless_stopped_since=None):
        if (
            unless_stopped_since is not None
            and self._explicit_stops != unless_stopped_since
        ):
            raise VlmError("VLM was unloaded; not bringing it back")
        self.started.append(model_id)

    async def generate_text(self, **kw):
        self.background_seen.append(is_background_vlm_work())
        self.calls.append(kw)
        if self.on_call:
            self.on_call(len(self.calls))
        r = self.replies(len(self.calls), kw["image_path"])
        if isinstance(r, Exception):
            raise r
        return r


def _job(db, store, client, events, **kw):
    kw.setdefault("poll_s", 0.01)
    return HindsightBriefsJob(
        db=db,
        get_client=lambda: client,
        get_store=lambda: store,
        broadcast=lambda ch, ev, data: events.append((ch, ev, data)),
        **kw,
    )


async def _run(job):
    job.start()
    await job.wait()


def test_clean_brief():
    assert (
        clean_brief('  "A fox in snow. It is cute."\n second line') == "A fox in snow."
    )
    assert clean_brief("") == ""
    assert clean_brief(None) == ""
    long = ("word " * 80).strip()
    out = clean_brief(long)
    assert len(out) <= 200 and not out.endswith(" ")
    assert clean_brief("x" * 500) == "x" * 200


def test_writes_briefs_for_eligible_images_only(db, store, tmp_path):
    _fav(db, tmp_path, "a.png")
    _fav(db, tmp_path, "b.png")
    _fav(db, tmp_path, "notfav.png", fav=False)
    _fav(db, tmp_path, "man.png", writer="manual")
    _fav(db, tmp_path, "fb.png", writer="fallback")
    _fav(db, tmp_path, "unk.png", model="mystery")
    client, events = FakeClient(), []
    job = _job(db, store, client, events)
    asyncio.run(_run(job))
    got = db.get_hindsight_briefs()
    assert sorted(Path(p).name for p in got) == ["a.png", "b.png"]
    assert all(v["model"] == "qwen3vl-4b" for v in got.values())
    assert len(client.calls) == 2
    assert all(c["system_prompt"] == "BRIEF-SYSTEM" for c in client.calls)
    assert all(c["image_path"].name in ("a.png", "b.png") for c in client.calls)
    assert all(client.background_seen)
    progress = [d for ch, ev, d in events if ev == "briefs_progress"]
    assert all(ch == "training" for ch, _, _ in events)
    assert progress[0] == {"done": 0, "total": 2}
    assert progress[-1] == {"done": 2, "total": 2}
    assert events[-1][1] == "briefs_done"
    assert job.status()["running"] is False


def test_resumable_skips_briefed(db, store, tmp_path):
    _fav(db, tmp_path, "a.png")
    _fav(db, tmp_path, "b.png")
    db.set_hindsight_brief(str(tmp_path / "a.png"), "existing", "m")
    client, events = FakeClient(), []
    asyncio.run(_run(_job(db, store, client, events)))
    assert [c["image_path"].name for c in client.calls] == ["b.png"]
    assert (
        db.get_hindsight_briefs([str(tmp_path / "a.png")])[str(tmp_path / "a.png")][
            "brief"
        ]
        == "existing"
    )
    first = [d for _, ev, d in events if ev == "briefs_progress"][0]
    assert first == {"done": 1, "total": 2}


def test_nothing_pending_finishes_without_vlm(db, store, tmp_path):
    _fav(db, tmp_path, "a.png")
    db.set_hindsight_brief(str(tmp_path / "a.png"), "x", "m")
    client, events = FakeClient(), []
    asyncio.run(_run(_job(db, store, client, events)))
    assert client.calls == [] and client.started == []
    assert events[-1][1] == "briefs_done"


def test_cancel_keeps_finished_briefs(db, store, tmp_path):
    for n in "abcd":
        _fav(db, tmp_path, f"{n}.png")
    client, events = FakeClient(), []
    job = _job(db, store, client, events)
    client.on_call = lambda i: job.cancel() if i == 2 else None
    asyncio.run(_run(job))
    assert len(client.calls) == 2
    assert events[-1][1] == "briefs_cancelled"
    # The in-flight image finished when cancel landed is dropped.
    assert len(db.get_hindsight_briefs()) == 1
    # A new run resumes.
    client2, events2 = FakeClient(), []
    asyncio.run(_run(_job(db, store, client2, events2)))
    assert len(db.get_hindsight_briefs()) == 4
    assert len(client2.calls) == 3


def test_only_one_job_at_a_time(db, store, tmp_path):
    _fav(db, tmp_path, "a.png")
    client, events = FakeClient(), []
    job = _job(db, store, client, events)

    async def go():
        job.start()
        with pytest.raises(Exception) as ei:
            job.start()
        assert ei.value.code == "briefs_running"
        await job.wait()

    asyncio.run(go())


def test_waits_while_generation_is_busy(db, store, tmp_path):
    _fav(db, tmp_path, "a.png")
    client, events = FakeClient(), []
    busy = {"n": 3}

    def probe():
        busy["n"] -= 1
        return busy["n"] >= 0

    job = _job(db, store, client, events, generation_busy=probe)
    asyncio.run(_run(job))
    assert busy["n"] < 0  # polled until it settled
    assert len(client.calls) == 1


def test_no_vlm_call_while_blocked_and_start_refused(db, store, tmp_path):
    _fav(db, tmp_path, "a.png")
    client, events = FakeClient(), []
    gate = {"blocked": True}
    job = _job(db, store, client, events, blocked=lambda: gate["blocked"])
    with pytest.raises(Exception) as ei:
        asyncio.run(_run(job))
    assert ei.value.code == "gpu_busy_training"

    # Blocked after the start: the job pauses, then continues.
    gate["blocked"] = False
    for n in "bc":
        _fav(db, tmp_path, f"{n}.png")

    async def go():
        job.start()
        await asyncio.sleep(0.05)
        calls_before = len(client.calls)
        gate["blocked"] = True
        await asyncio.sleep(0.1)
        frozen = len(client.calls)
        gate["blocked"] = False
        await job.wait()
        return calls_before, frozen

    asyncio.run(go())
    assert len(db.get_hindsight_briefs()) == 3


def test_explicit_unload_mid_run_ends_job_without_restart(db, store, tmp_path):
    for n in "abc":
        _fav(db, tmp_path, f"{n}.png")
    client, events = FakeClient(), []

    def replies(i, path):
        if i == 2:
            client._explicit_stops += 1
            return VlmError("not ready")
        return "a scene."

    client.replies = replies
    asyncio.run(_run(_job(db, store, client, events)))
    assert events[-1][1] == "briefs_error"
    assert events[-1][2]["code"] == "vlm_unloaded"
    assert len(client.calls) == 2
    assert len(client.started) == 1  # never restarted
    assert len(db.get_hindsight_briefs()) == 1


def test_unload_before_start_is_not_overridden_after_wait(db, store, tmp_path):
    # A render unloads the VLM while we wait: the wait re-snapshots, so the
    # job restarts it (the render's own unload is behind us).
    _fav(db, tmp_path, "a.png")
    client, events = FakeClient(), []
    state = {"n": 2}

    def probe():
        state["n"] -= 1
        if state["n"] == 0:
            client._explicit_stops += 1
        return state["n"] > 0

    asyncio.run(_run(_job(db, store, client, events, generation_busy=probe)))
    assert events[-1][1] == "briefs_done"
    assert len(db.get_hindsight_briefs()) == 1


def test_failures_skip_and_repeat_failures_abort(db, store, tmp_path):
    for n in "abcdefgh":
        _fav(db, tmp_path, f"{n}.png")
    client, events = FakeClient(), []
    client.replies = lambda i, path: "" if i == 1 else RuntimeError("boom")
    asyncio.run(_run(_job(db, store, client, events)))
    assert events[-1][1] == "briefs_error"
    assert events[-1][2]["code"] == "vlm_failing"
    assert len(client.calls) == 5
    assert db.get_hindsight_briefs() == {}

    client2, events2 = FakeClient(), []
    client2.replies = lambda i, path: "" if i == 2 else "ok scene."
    asyncio.run(_run(_job(db, store, client2, events2)))
    assert events2[-1][1] == "briefs_done"
    assert events2[-1][2]["failed"] == 1
    assert len(db.get_hindsight_briefs()) == 7


def test_brief_text_is_never_logged_at_info(db, store, tmp_path, caplog):
    _fav(db, tmp_path, "a.png", prompt="SECRET-PROMPT")
    client, events = FakeClient(), []
    client.replies = lambda i, p: "SECRET-BRIEF text."
    with caplog.at_level(logging.INFO):
        asyncio.run(_run(_job(db, store, client, events)))
    assert "SECRET" not in caplog.text


def test_library_item_with_known_family_is_a_candidate(db, store, tmp_path):
    f = tmp_path / "lib.png"
    f.write_bytes(b"x")
    path = str(f)
    db.save_media(
        Media(
            file_path=f,
            file_size=1,
            width=1,
            height=1,
            format="png",
            created_at=datetime.now(),
            modified_at=datetime.now(),
        )
    )
    with db.lock, db._get_connection() as conn:
        conn.execute(
            "UPDATE media SET data = json_set(data, '$.prompt', 'LIB') "
            "WHERE file_path = ?",
            (path,),
        )
        conn.execute(
            "INSERT INTO indices (index_type, index_key, file_path) "
            "VALUES ('model', 'krea2_dev.safetensors', ?)",
            (path,),
        )
        conn.commit()
    db.set_verdict(path, "favorite", None, NOW)
    client, events = FakeClient(), []
    asyncio.run(_run(_job(db, store, client, events)))
    assert len(db.get_hindsight_briefs()) == 1


def test_real_prompt_is_in_the_shipped_yaml():
    from metascan.core.prompt_store import PromptStore

    text = PromptStore(Path("data/meta_prompt.yml")).get("HINDSIGHT_BRIEF_SYSTEM")
    assert "one" in text.lower() and "style" in text.lower()


# ---- routes ------------------------------------------------------------------


@pytest.fixture
def api(db, store, tmp_path):
    os.environ.setdefault("METASCAN_API_KEY", "")
    client, events = FakeClient(), []
    job = _job(db, store, client, events)
    set_briefs_job(job)
    from backend.api import writers

    app = FastAPI()
    app.include_router(writers.router)
    # The context manager keeps one event loop alive across requests, so the
    # background task outlives the POST that started it.
    with TestClient(app) as tc:
        yield tc, job, client, events
    set_briefs_job(None)


def test_routes_run_status_cancel(db, api, tmp_path):
    c, job, client, events = api
    _fav(db, tmp_path, "a.png")
    assert c.get("/api/writers/briefs/status").json()["running"] is False
    assert c.post("/api/writers/briefs/cancel").json() == {"status": "idle"}
    resp = c.post("/api/writers/briefs/run")
    assert resp.status_code == 202
    # TestClient runs the app on its own loop thread; wait for the result.
    import time

    for _ in range(200):
        if not c.get("/api/writers/briefs/status").json()["running"]:
            break
        time.sleep(0.01)
    st = c.get("/api/writers/briefs/status").json()
    assert (st["done"], st["total"]) == (1, 1)
    assert len(db.get_hindsight_briefs()) == 1


def test_route_refuses_while_blocked(api):
    c, job, client, events = api
    job.blocked = lambda: True
    resp = c.post("/api/writers/briefs/run")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "gpu_busy_training"
