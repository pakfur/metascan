"""Describe route contracts (spec V2 §4): return JSON, never write the DB.

Mirrors tests/test_storyboard_api.py's client fixture (StubRunner installed
*as* StoryboardRunner so the lifespan survives startup) and
tests/test_vlm_api.py's pattern of installing a fake VLM client via
backend.api.vlm.set_vlm_client *after* the lifespan has run, so it
supersedes the real VlmClient the lifespan constructs.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from backend.api import storyboard as storyboard_api
from backend.api import vlm as vlm_api
from backend.main import create_app
from metascan.core.database_sqlite import DatabaseManager
from metascan.core.media import Media


class StubRunner:
    """Minimal stand-in for StoryboardRunner -- see test_storyboard_api.py.

    The describe routes don't touch the runner at all, but the lifespan
    constructs one unconditionally, so it must survive startup.
    """

    def __init__(self, db=None, comfy=None, get_vlm=None, output_root=None, **kwargs):
        self.db = db

    def on_event(self, cb):
        pass

    def handle_job_event(self, event, payload):
        pass

    async def aclose(self):
        return None


class FakeVlm:
    """Duck-typed VlmClient stand-in installed via vlm_api.set_vlm_client."""

    model_id = "qwen3vl-8b"

    def __init__(
        self,
        payload: Any = None,
        error: Optional[Exception] = None,
        ensure_started_error: Optional[Exception] = None,
    ):
        self.payload = payload
        self.error = error
        self.ensure_started_error = ensure_started_error
        self.calls: List[Dict[str, Any]] = []

    async def ensure_started(self, model_id: str) -> None:
        if self.ensure_started_error is not None:
            raise self.ensure_started_error

    async def generate_text(self, **kw: Any) -> str:
        self.calls.append(kw)
        if self.error is not None:
            raise self.error
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = DatabaseManager(Path(tmp))

        def make_stub(*args, **kwargs):
            return StubRunner(db=db)

        monkeypatch.setattr("backend.dependencies.get_db", lambda: db)
        monkeypatch.setattr("backend.api.storyboard.get_db", lambda: db)
        monkeypatch.setattr("backend.main.StoryboardRunner", make_stub)

        app = create_app()
        with TestClient(app) as c:
            c.db = db
            yield c
        storyboard_api.set_storyboard_runner(None)
        vlm_api.set_vlm_client(None)


@pytest.fixture
def db(client):
    return client.db


@pytest.fixture
def board_id(client) -> int:
    r = client.post(
        "/api/storyboard",
        json={"name": "Board", "target_model": "sd", "aspect_ratio": "16:9"},
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _seed_media(db: DatabaseManager, path: str) -> str:
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
    return path


@pytest.fixture
def media_path(db) -> str:
    return _seed_media(db, "/tmp/ref-describe-a.png")


@pytest.fixture
def media_path_2(db) -> str:
    return _seed_media(db, "/tmp/ref-describe-b.png")


@pytest.fixture
def fake_vlm() -> FakeVlm:
    """Installs a default-payload FakeVlm. Tests override .payload/.error."""
    stub = FakeVlm(payload={"description": "late 20s, shaved head", "voice": "alto"})
    vlm_api.set_vlm_client(stub)
    yield stub
    vlm_api.set_vlm_client(None)


# ---- subject describe -------------------------------------------------------


def test_describe_subject_happy_path_no_db_write(client, db, board_id, media_path):
    sid = db.create_subject(
        board_id, name="Maya", description="old text", reference_path=media_path
    )
    vlm_api.set_vlm_client(
        FakeVlm(payload={"description": "late 20s, shaved head", "voice": "alto"})
    )
    r = client.post(f"/api/storyboard/subjects/{sid}/describe")
    assert r.status_code == 200
    assert r.json() == {"description": "late 20s, shaved head", "voice": "alto"}
    assert db.get_subject(sid)["description"] == "old text"  # unchanged


def test_describe_subject_sends_both_refs_in_order(
    client, db, board_id, media_path, media_path_2, fake_vlm
):
    sid = db.create_subject(
        board_id,
        name="M",
        description="d",
        reference_path=media_path,
        reference_path_2=media_path_2,
    )
    client.post(f"/api/storyboard/subjects/{sid}/describe")
    sent = fake_vlm.calls[0]["image_paths"]
    assert [str(p) for p in sent] == [media_path, media_path_2]


def test_describe_400_when_no_refs(client, db, board_id, fake_vlm):
    sid = db.create_subject(board_id, name="M", description="d")
    assert client.post(f"/api/storyboard/subjects/{sid}/describe").status_code == 400


def test_describe_subject_404_unknown(client):
    assert client.post("/api/storyboard/subjects/9999/describe").status_code == 404


def test_describe_503_when_no_vlm_client(client, db, board_id, media_path):
    sid = db.create_subject(
        board_id, name="M", description="d", reference_path=media_path
    )
    vlm_api.set_vlm_client(None)
    r = client.post(f"/api/storyboard/subjects/{sid}/describe")
    assert r.status_code == 503


def test_describe_subject_502_on_vlm_error(client, db, board_id, media_path):
    from metascan.core.vlm_client import VlmError

    sid = db.create_subject(
        board_id, name="M", description="d", reference_path=media_path
    )
    vlm_api.set_vlm_client(FakeVlm(error=VlmError("boom")))
    r = client.post(f"/api/storyboard/subjects/{sid}/describe")
    assert r.status_code == 502


def test_describe_subject_502_on_garbage_response(client, db, board_id, media_path):
    sid = db.create_subject(
        board_id, name="M", description="d", reference_path=media_path
    )
    vlm_api.set_vlm_client(FakeVlm(payload="not json"))
    r = client.post(f"/api/storyboard/subjects/{sid}/describe")
    assert r.status_code == 502


def test_describe_subject_503_on_vlm_select_error(
    client, db, board_id, media_path, monkeypatch
):
    from metascan.core import vlm_select

    sid = db.create_subject(
        board_id, name="M", description="d", reference_path=media_path
    )
    stub = FakeVlm(payload={"description": "x", "voice": None})
    stub.model_id = None  # forces pick_vlm_model past the "already loaded" shortcut
    vlm_api.set_vlm_client(stub)

    def _no_model(vlm):
        raise vlm_select.VlmSelectError("no VLM model available on this hardware")

    monkeypatch.setattr(
        "backend.api.storyboard.pick_vlm_model", _no_model, raising=False
    )
    r = client.post(f"/api/storyboard/subjects/{sid}/describe")
    assert r.status_code == 503


def test_describe_subject_502_on_ensure_started_timeout(
    client, db, board_id, media_path
):
    sid = db.create_subject(
        board_id, name="M", description="d", reference_path=media_path
    )
    vlm_api.set_vlm_client(
        FakeVlm(
            payload={"description": "x", "voice": "alto"},
            ensure_started_error=TimeoutError("model load timed out"),
        )
    )
    r = client.post(f"/api/storyboard/subjects/{sid}/describe")
    assert r.status_code == 502
    assert "model load timed out" in r.json()["detail"]


def test_describe_subject_502_on_ensure_started_runtime_error(
    client, db, board_id, media_path
):
    sid = db.create_subject(
        board_id, name="M", description="d", reference_path=media_path
    )
    vlm_api.set_vlm_client(
        FakeVlm(
            payload={"description": "x", "voice": "alto"},
            ensure_started_error=RuntimeError("failed to start VLM: model not ready"),
        )
    )
    r = client.post(f"/api/storyboard/subjects/{sid}/describe")
    assert r.status_code == 502
    assert "failed to start VLM" in r.json()["detail"]


# ---- scene describe ----------------------------------------------------------


def test_describe_scene_happy_and_never_writes(client, db, board_id, media_path):
    scene_id = db.create_scene(board_id, name="Yard", reference_path=media_path)
    vlm_api.set_vlm_client(
        FakeVlm(payload={"setting": "salvage yard", "lighting": None, "mood": "tense"})
    )
    r = client.post(f"/api/storyboard/scenes/{scene_id}/describe")
    assert r.status_code == 200
    assert r.json() == {"setting": "salvage yard", "lighting": None, "mood": "tense"}
    assert db.get_scene(scene_id)["setting"] is None  # never written


def test_describe_scene_502_on_garbage(client, db, board_id, media_path):
    scene_id = db.create_scene(board_id, name="Yard", reference_path=media_path)
    vlm_api.set_vlm_client(FakeVlm(payload="not json"))
    r = client.post(f"/api/storyboard/scenes/{scene_id}/describe")
    assert r.status_code == 502


def test_describe_scene_400_when_no_ref(client, db, board_id, fake_vlm):
    scene_id = db.create_scene(board_id, name="Yard")
    r = client.post(f"/api/storyboard/scenes/{scene_id}/describe")
    assert r.status_code == 400


def test_describe_scene_404(client):
    assert client.post("/api/storyboard/scenes/9999/describe").status_code == 404


def test_describe_scene_502_on_ensure_started_timeout(
    client, db, board_id, media_path
):
    scene_id = db.create_scene(board_id, name="Yard", reference_path=media_path)
    vlm_api.set_vlm_client(
        FakeVlm(
            payload={"setting": "yard", "lighting": None, "mood": "tense"},
            ensure_started_error=TimeoutError("model load timed out"),
        )
    )
    r = client.post(f"/api/storyboard/scenes/{scene_id}/describe")
    assert r.status_code == 502
    assert "model load timed out" in r.json()["detail"]


def test_describe_scene_502_on_ensure_started_runtime_error(
    client, db, board_id, media_path
):
    scene_id = db.create_scene(board_id, name="Yard", reference_path=media_path)
    vlm_api.set_vlm_client(
        FakeVlm(
            payload={"setting": "yard", "lighting": None, "mood": "tense"},
            ensure_started_error=RuntimeError("failed to start VLM: model not ready"),
        )
    )
    r = client.post(f"/api/storyboard/scenes/{scene_id}/describe")
    assert r.status_code == 502
    assert "failed to start VLM" in r.json()["detail"]


# ---- controller ruling: reference fields round-trip via PATCH ---------------


def test_patch_subject_reference_path_2_round_trips_and_clears(
    client, board_id, media_path_2
):
    subj_id = client.post(
        f"/api/storyboard/{board_id}/subjects",
        json={"name": "Alice", "description": "x"},
    ).json()["id"]

    r = client.patch(
        f"/api/storyboard/subjects/{subj_id}",
        json={"reference_path_2": media_path_2},
    )
    assert r.status_code == 200

    tree = client.get(f"/api/storyboard/{board_id}").json()
    subject = next(s for s in tree["subjects"] if s["id"] == subj_id)
    assert subject["reference_path_2"] == media_path_2

    r = client.patch(
        f"/api/storyboard/subjects/{subj_id}",
        json={"reference_path_2": None},
    )
    assert r.status_code == 200

    tree2 = client.get(f"/api/storyboard/{board_id}").json()
    subject2 = next(s for s in tree2["subjects"] if s["id"] == subj_id)
    assert subject2["reference_path_2"] is None


def test_patch_scene_reference_path_unknown_is_400_known_is_200(
    client, board_id, media_path
):
    scene_id = client.post(
        f"/api/storyboard/{board_id}/scenes", json={"name": "Yard"}
    ).json()["id"]

    r = client.patch(
        f"/api/storyboard/scenes/{scene_id}",
        json={"reference_path": "/nope-does-not-exist.png"},
    )
    assert r.status_code == 400

    r = client.patch(
        f"/api/storyboard/scenes/{scene_id}",
        json={"reference_path": media_path},
    )
    assert r.status_code == 200

    tree = client.get(f"/api/storyboard/{board_id}").json()
    scene = next(s for s in tree["scenes"] if s["id"] == scene_id)
    assert scene["reference_path"] == media_path
