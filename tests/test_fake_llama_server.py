"""Tests that the fake llama-server fixture itself behaves correctly.

These are necessary because the rest of Phase 2 trusts this fixture to
emulate the real binary closely enough."""

import asyncio

import httpx

from tests._fake_llama_server import FakeLlamaServer


async def test_health_returns_ok_after_load_delay():
    async with FakeLlamaServer(load_ms=50) as fake:
        async with httpx.AsyncClient() as client:
            r = None
            for _ in range(40):
                try:
                    r = await client.get(f"{fake.base_url}/health")
                    if r.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.025)
            assert r is not None and r.status_code == 200


async def test_chat_completions_returns_canned_tags():
    async with FakeLlamaServer(canned_response='["red dress", "outdoor"]') as fake:
        await fake.wait_ready()
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{fake.base_url}/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "x"}]},
            )
            assert r.status_code == 200
            content = r.json()["choices"][0]["message"]["content"]
            assert content == '["red dress", "outdoor"]'


async def test_can_force_crash():
    fake = FakeLlamaServer(crash_after_n_requests=1)
    async with fake:
        await fake.wait_ready()
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{fake.base_url}/v1/chat/completions",
                json={"messages": []},
            )
            await asyncio.sleep(0.5)
        assert fake.process_returncode() is not None
