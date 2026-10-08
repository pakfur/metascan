"""Tests for the chat window backend: VlmClient.chat_stream + /api/chat.

``chat_stream`` is exercised against a real ``httpx.AsyncClient`` wired
to an ``httpx.MockTransport`` that replays llama-server's SSE stream, so
the streaming code path is the one used in production. The endpoint
tests use a minimal FastAPI app with only the chat router and a stub VLM
(same pattern as ``test_prompt_api_generate.py``).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import chat as chat_api
from backend.api import vlm as vlm_api
from metascan.core.vlm_client import STATE_IDLE, STATE_READY, VlmClient, VlmError


def _sse(*chunks: Dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _delta(**delta: str) -> Dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta}]}


@pytest.fixture
def img_file():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "test.png"
        Image.new("RGB", (8, 8), color="blue").save(p, "PNG")
        yield p


def _client_with(handler) -> VlmClient:
    client = VlmClient()
    client._http = httpx.AsyncClient(  # type: ignore[assignment]
        base_url="http://stub", transport=httpx.MockTransport(handler)
    )
    client._state = STATE_READY
    client._model_id = "qwen3vl-8b"
    return client


async def _collect(client: VlmClient, **kwargs) -> List[tuple]:
    out = []
    async for item in client.chat_stream(**kwargs):
        out.append(item)
    return out


# ---- VlmClient.chat_stream ----------------------------------------------


@pytest.mark.asyncio
async def test_chat_stream_yields_content_and_reasoning():
    seen: List[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(
            200,
            content=_sse(
                _delta(role="assistant"),
                _delta(reasoning_content="hmm"),
                _delta(content="Hel"),
                _delta(content="lo"),
            ),
            headers={"content-type": "text/event-stream"},
        )

    client = _client_with(handler)
    try:
        out = await _collect(
            client,
            messages=[
                {"role": "system", "content": "be nice"},
                {"role": "user", "content": "hi"},
            ],
            temperature=0.3,
            max_tokens=64,
        )
    finally:
        await client._http.aclose()  # type: ignore[union-attr]
        client._http = None
        client._state = STATE_IDLE

    assert out == [("reasoning", "hmm"), ("content", "Hel"), ("content", "lo")]
    body = seen[0]
    assert body["stream"] is True
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 64
    assert body["messages"][1] == {"role": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_chat_stream_attaches_image_to_first_user_message_only(img_file):
    seen: List[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, content=_sse(_delta(content="ok")))

    client = _client_with(handler)
    try:
        await _collect(
            client,
            messages=[
                {"role": "user", "content": "describe"},
                {"role": "assistant", "content": "a blue square"},
                {"role": "user", "content": "shorter"},
            ],
            image_path=img_file,
        )
    finally:
        await client._http.aclose()  # type: ignore[union-attr]

    msgs = seen[0]["messages"]
    first = msgs[0]["content"]
    assert isinstance(first, list)
    assert first[0] == {"type": "text", "text": "describe"}
    assert first[1]["type"] == "image_url"
    assert first[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert msgs[1]["content"] == "a blue square"
    assert msgs[2]["content"] == "shorter"


@pytest.mark.asyncio
async def test_chat_stream_http_error_raises_vlm_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "the request exceeds the context size"}},
        )

    client = _client_with(handler)
    try:
        with pytest.raises(VlmError, match="exceeds the context size"):
            await _collect(client, messages=[{"role": "user", "content": "x"}])
    finally:
        await client._http.aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_chat_stream_not_ready_raises():
    client = VlmClient()
    with pytest.raises(VlmError, match="not ready"):
        await _collect(client, messages=[{"role": "user", "content": "x"}])


# ---- /api/chat -----------------------------------------------------------


class _StubVlm:
    state = STATE_READY
    model_id = "qwen3vl-8b"

    def __init__(self):
        self.calls: List[dict] = []
        self.chunks: List[tuple] = [("content", "Hello"), ("content", " there")]
        self.error: VlmError | None = None

    async def chat_stream(self, **kwargs):
        self.calls.append(kwargs)
        for c in self.chunks:
            yield c
        if self.error is not None:
            raise self.error


@pytest.fixture
def stub_vlm():
    s = _StubVlm()
    vlm_api.set_vlm_client(s)
    try:
        yield s
    finally:
        vlm_api.set_vlm_client(None)


@pytest.fixture
def http():
    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app)


def _lines(resp) -> List[dict]:
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


def test_chat_streams_deltas_then_done(http, stub_vlm):
    r = http.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "system_prompt": "You are helpful.",
            "temperature": 0.5,
            "max_tokens": 200,
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = _lines(r)
    assert events[0] == {"type": "delta", "text": "Hello"}
    assert events[1] == {"type": "delta", "text": " there"}
    assert events[-1]["type"] == "done"
    assert events[-1]["vlm_model_id"] == "qwen3vl-8b"

    call = stub_vlm.calls[0]
    assert call["messages"] == [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
    ]
    assert call["image_path"] is None
    assert call["temperature"] == 0.5
    assert call["max_tokens"] == 200


def test_chat_blank_system_prompt_is_omitted(http, stub_vlm):
    http.post(
        "/api/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "system_prompt": "  "},
    )
    assert stub_vlm.calls[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_chat_with_image(http, stub_vlm, img_file):
    r = http.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "what is this"}],
            "file_path": str(img_file),
            "include_image": True,
        },
    )
    assert r.status_code == 200
    assert stub_vlm.calls[0]["image_path"] == img_file


def test_chat_file_path_ignored_without_include_image(http, stub_vlm):
    r = http.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "file_path": "/does/not/exist.png",
            "include_image": False,
        },
    )
    assert r.status_code == 200
    assert stub_vlm.calls[0]["image_path"] is None


def test_chat_missing_image_404(http, stub_vlm):
    r = http.post(
        "/api/chat",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "file_path": "/does/not/exist.png",
            "include_image": True,
        },
    )
    assert r.status_code == 404


def test_chat_video_rejected(http, stub_vlm):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "clip.mp4"
        p.write_bytes(b"\x00")
        r = http.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "file_path": str(p),
                "include_image": True,
            },
        )
    assert r.status_code == 422


def test_chat_last_message_must_be_user(http, stub_vlm):
    r = http.post(
        "/api/chat",
        json={
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        },
    )
    assert r.status_code == 422


def test_chat_not_ready_503(http):
    vlm_api.set_vlm_client(None)
    r = http.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503


def test_chat_error_mid_stream(http, stub_vlm):
    stub_vlm.chunks = [("content", "partial")]
    stub_vlm.error = VlmError("llama-server request failed: boom")
    r = http.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    events = _lines(r)
    assert events[0] == {"type": "delta", "text": "partial"}
    assert events[-1] == {
        "type": "error",
        "message": "llama-server request failed: boom",
    }
