"""Lifecycle tests for VlmClient: start, /health, shutdown."""

import pytest

from metascan.core.vlm_client import (
    STATE_LOADING,
    STATE_READY,
    STATE_STOPPED,
    VlmClient,
)
from tests._fake_llama_server import FakeLlamaServer


async def test_start_then_ready():
    async with FakeLlamaServer(load_ms=100) as fake:
        client = VlmClient(spawn_override=lambda model_id: fake.base_url)
        try:
            await client.start("qwen3vl-2b", wait_ready=True, ready_timeout=5.0)
            assert client.state == STATE_READY
            assert client.model_id == "qwen3vl-2b"
        finally:
            await client.shutdown()
        assert client.state == STATE_STOPPED


async def test_ensure_started_is_idempotent():
    async with FakeLlamaServer() as fake:
        client = VlmClient(spawn_override=lambda model_id: fake.base_url)
        try:
            await client.ensure_started("qwen3vl-2b")
            await client.ensure_started("qwen3vl-2b")
            assert client.state == STATE_READY
        finally:
            await client.shutdown()


async def test_start_times_out_when_health_never_ready():
    async with FakeLlamaServer(health_fails_forever=True) as fake:
        client = VlmClient(spawn_override=lambda model_id: fake.base_url)
        try:
            with pytest.raises(TimeoutError):
                await client.start("qwen3vl-2b", wait_ready=True, ready_timeout=1.0)
        finally:
            await client.shutdown()


async def test_snapshot_returns_state():
    async with FakeLlamaServer() as fake:
        client = VlmClient(spawn_override=lambda model_id: fake.base_url)
        try:
            await client.start("qwen3vl-2b")
            snap = client.snapshot()
            assert snap["state"] == STATE_READY
            assert snap["model_id"] == "qwen3vl-2b"
        finally:
            await client.shutdown()


async def test_status_callback_fires_on_state_change():
    events = []
    async with FakeLlamaServer() as fake:
        client = VlmClient(spawn_override=lambda model_id: fake.base_url)
        client.on_status(lambda state, payload: events.append(state))
        try:
            await client.start("qwen3vl-2b")
        finally:
            await client.shutdown()
    assert STATE_LOADING in events
    assert STATE_READY in events
    assert STATE_STOPPED in events
