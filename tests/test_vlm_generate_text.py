"""Tests for VlmClient.generate_text.

Uses a small in-process httpx stub instead of the fake llama-server
subprocess: generate_text only exercises the request-marshalling /
response-parsing path, so spinning up a subprocess for every test is
overkill. We poke at ``client._http`` and ``client._state`` directly —
this is a known boundary violation, accepted because the tests live in
the same package and the alternative (extending the fake server with
admin endpoints for body inspection) is more code without more clarity.
"""

from __future__ import annotations

import httpx
import pytest
from PIL import Image

from metascan.core.vlm_client import (
    STATE_IDLE,
    STATE_READY,
    VlmClient,
    VlmError,
)


class _StubHttp:
    """Replaces VlmClient._http for unit testing generate_text."""

    def __init__(self, response_content: str = "generated text"):
        self.calls: list[dict] = []
        self.response_content = response_content
        self.next_status = 200
        self.next_error_message = "stub error"

    async def post(self, path: str, *, json=None, timeout=None):
        assert path == "/v1/chat/completions"
        self.calls.append(json)
        if self.next_status >= 400:
            req = httpx.Request("POST", "http://stub" + path)
            resp = httpx.Response(
                self.next_status,
                json={"error": {"message": self.next_error_message}},
                request=req,
            )
            raise httpx.HTTPStatusError("stub", request=req, response=resp)
        return _StubResp(self.response_content)


class _StubResp:
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def ready_client():
    """VlmClient pre-flipped to READY with a stub http transport."""
    client = VlmClient()
    stub = _StubHttp()
    client._http = stub  # type: ignore[assignment]
    client._state = STATE_READY
    client._model_id = "qwen3vl-4b"
    try:
        yield client, stub
    finally:
        client._http = None
        client._state = STATE_IDLE


@pytest.mark.asyncio
async def test_generate_text_returns_content(ready_client):
    client, _ = ready_client
    text = await client.generate_text(
        system_prompt="You are a prompt engineer.",
        user_prompt="Write a prompt.",
    )
    assert text == "generated text"


@pytest.mark.asyncio
async def test_generate_text_text_only_uses_string_content(ready_client):
    """Without image_path the user message content is a plain string,
    not the image_url-array shape used when grounding on an image."""
    client, stub = ready_client
    await client.generate_text(system_prompt="sys", user_prompt="say hi")
    body = stub.calls[-1]
    assert isinstance(body["messages"][1]["content"], str)
    assert body["messages"][1]["content"] == "say hi"


@pytest.mark.asyncio
async def test_generate_text_with_image_attaches_image_part(ready_client, tmp_path):
    client, stub = ready_client
    img = tmp_path / "x.jpg"
    Image.new("RGB", (4, 4), color="white").save(img, "JPEG")

    await client.generate_text(
        system_prompt="sys", user_prompt="describe", image_path=img
    )
    body = stub.calls[-1]
    parts = body["messages"][1]["content"]
    assert isinstance(parts, list)
    assert any(p.get("type") == "image_url" for p in parts)
    assert any(p.get("type") == "text" and p["text"] == "describe" for p in parts)


@pytest.mark.asyncio
async def test_generate_text_raises_vlm_error_on_500(ready_client):
    """Unlike generate_tags (which swallows errors and returns []),
    generate_text must surface failures so the playground can show them."""
    client, stub = ready_client
    stub.next_status = 500
    stub.next_error_message = "upstream boom"
    with pytest.raises(VlmError, match="500"):
        await client.generate_text(system_prompt="sys", user_prompt="x")


@pytest.mark.asyncio
async def test_generate_text_passes_temperature_and_max_tokens(ready_client):
    client, stub = ready_client
    await client.generate_text(
        system_prompt="sys",
        user_prompt="x",
        temperature=0.9,
        max_tokens=42,
    )
    body = stub.calls[-1]
    assert body["temperature"] == pytest.approx(0.9)
    assert body["max_tokens"] == 42


@pytest.mark.asyncio
async def test_generate_text_raises_when_not_ready():
    """No flip to READY -> immediate VlmError, no HTTP call attempted."""
    client = VlmClient()  # state == IDLE, _http is None
    with pytest.raises(VlmError, match="not ready"):
        await client.generate_text(system_prompt="s", user_prompt="u")
