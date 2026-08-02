"""The fake ComfyUI must behave before anything is tested against it."""

from __future__ import annotations

import asyncio
import json

import httpx
import websockets

from tests._fake_comfy_server import fake_comfy  # noqa: F401


async def test_submit_returns_a_prompt_id(fake_comfy):  # noqa: F811
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{fake_comfy.base_url}/prompt", json={"prompt": {}})
    assert r.status_code == 200
    assert r.json()["prompt_id"]


async def test_execution_emits_ws_events_and_populates_history(
    fake_comfy,  # noqa: F811
):
    events = []
    async with websockets.connect(f"ws://127.0.0.1:{fake_comfy._port}/ws") as ws:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{fake_comfy.base_url}/prompt",
                json={
                    "prompt": {
                        "9": {
                            "class_type": "SaveImage",
                            "inputs": {},
                            "_meta": {"title": "MS_SAVE"},
                        }
                    }
                },
            )
            prompt_id = r.json()["prompt_id"]

            for _ in range(2):
                events.append(json.loads(await asyncio.wait_for(ws.recv(), 5)))

    assert [e["type"] for e in events] == ["execution_start", "executed"]

    async with httpx.AsyncClient() as client:
        h = await client.get(f"{fake_comfy.base_url}/history/{prompt_id}")
    images = h.json()[prompt_id]["outputs"]["9"]["images"]
    assert len(images) == 2


async def test_fail_with_emits_execution_error(fake_comfy):  # noqa: F811
    fake_comfy.fail_with = "value not in list: ckpt_name"
    async with websockets.connect(f"ws://127.0.0.1:{fake_comfy._port}/ws") as ws:
        async with httpx.AsyncClient() as client:
            await client.post(f"{fake_comfy.base_url}/prompt", json={"prompt": {}})
        types = []
        for _ in range(2):
            types.append(json.loads(await asyncio.wait_for(ws.recv(), 5))["type"])

    assert types == ["execution_start", "execution_error"]


async def test_view_returns_png_bytes(fake_comfy):  # noqa: F811
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{fake_comfy.base_url}/view", params={"filename": "a.png"}
        )
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
