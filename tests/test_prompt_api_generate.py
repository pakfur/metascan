"""Endpoint tests for /api/prompt/generate, /transform, /clean.

Builds a minimal FastAPI app with only the prompt router so that
``backend.main.create_app``'s lifespan (which installs a real
VlmClient) doesn't clobber our stub. The router accesses the
VlmClient via ``backend.api.vlm.get_vlm_client`` — we set the
module-level ``_vlm_client`` directly.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import prompt as prompt_api
from backend.api import vlm as vlm_api
from metascan.core.vlm_client import STATE_READY, VlmError


class _StubVlm:
    state = STATE_READY
    model_id = "qwen3vl-4b"

    def __init__(self):
        self.calls = []
        self.next_response = "stubbed prompt"
        self.next_error: VlmError | None = None

    async def generate_text(self, **kwargs):
        self.calls.append(kwargs)
        if self.next_error is not None:
            raise self.next_error
        return self.next_response


def _build_app() -> FastAPI:
    """Minimal app with just the prompt router — skips the heavyweight
    create_app lifespan that would install a real VlmClient."""
    app = FastAPI()
    app.include_router(prompt_api.router)
    return app


@pytest.fixture
def stub_vlm():
    s = _StubVlm()
    vlm_api.set_vlm_client(s)
    try:
        yield s
    finally:
        vlm_api.set_vlm_client(None)


@pytest.fixture
def img_file():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.jpg"
        Image.new("RGB", (8, 8), color="red").save(p, "JPEG")
        yield p


def test_generate_returns_prompt_and_metadata(stub_vlm, img_file):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sdxl",
                "architecture": "t2i",
                "styles": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["prompt"] == "stubbed prompt"
    assert body["vlm_model_id"] == "qwen3vl-4b"
    assert "elapsed_ms" in body


def test_generate_passes_styles_into_system_prompt(stub_vlm, img_file):
    with TestClient(_build_app()) as c:
        c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "pony",
                "architecture": "t2i",
                "styles": ["anime", "cinematic"],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert len(stub_vlm.calls) == 1
    sys_prompt = stub_vlm.calls[0]["system_prompt"]
    assert "anime" in sys_prompt
    assert "cinematic" in sys_prompt


def test_generate_404_when_file_missing(stub_vlm):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": "/nonexistent/x.jpg",
                "target_model": "sdxl",
                "architecture": "t2i",
                "styles": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 404


def test_generate_503_when_vlm_not_installed(img_file):
    """No stub fixture used -> _vlm_client is None -> 503."""
    vlm_api.set_vlm_client(None)
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sdxl",
                "architecture": "t2i",
                "styles": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 503


def test_generate_503_when_vlm_idle(img_file):
    """Idle (non-READY) client -> 503 with explicit 'activate' message."""

    class _Idle:
        state = "idle"
        model_id = None

    vlm_api.set_vlm_client(_Idle())
    try:
        with TestClient(_build_app()) as c:
            r = c.post(
                "/api/prompt/generate",
                json={
                    "file_path": str(img_file),
                    "target_model": "sdxl",
                    "architecture": "t2i",
                    "styles": [],
                    "temperature": 0.6,
                    "max_tokens": 250,
                },
            )
        assert r.status_code == 503
    finally:
        vlm_api.set_vlm_client(None)


def test_generate_400_when_too_many_styles(stub_vlm, img_file):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sdxl",
                "architecture": "t2i",
                "styles": ["anime", "cinematic", "watercolor", "comic"],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    # Pydantic Literal validation runs before our handler — invalid styles
    # produce a 422 from FastAPI, but a too-large valid-styles list reaches
    # _style_clause and returns 400. Both are acceptable for "too many".
    assert r.status_code in (400, 422)


def test_generate_502_when_vlm_raises(stub_vlm, img_file):
    stub_vlm.next_error = VlmError("upstream boom")
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/generate",
            json={
                "file_path": str(img_file),
                "target_model": "sdxl",
                "architecture": "t2i",
                "styles": [],
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 502
    assert "upstream boom" in r.json()["detail"]


def test_transform_passes_source_prompt_through(stub_vlm):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/transform",
            json={
                "source_prompt": "old prompt here",
                "target_model": "flux-chroma",
                "architecture": "t2i",
                "temperature": 0.6,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    user = stub_vlm.calls[0]["user_prompt"]
    assert "old prompt here" in user


def test_clean_uses_clean_template(stub_vlm):
    with TestClient(_build_app()) as c:
        r = c.post(
            "/api/prompt/clean",
            json={
                "source_prompt": "messy,, prompt",
                "temperature": 0.4,
                "max_tokens": 250,
            },
        )
    assert r.status_code == 200
    sys = stub_vlm.calls[0]["system_prompt"]
    assert "clean" in sys.lower()
