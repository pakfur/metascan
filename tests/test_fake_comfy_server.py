"""The fake ComfyUI must behave before anything is tested against it.

These tests pin the protocol details the client used to get wrong: the
frame ordering around end-of-prompt, that /history is written last, that
/interrupt is scoped by prompt_id, and that an interrupted prompt reports
`execution_interrupted` rather than `execution_error`.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import websockets

from tests._fake_comfy_server import fake_comfy  # noqa: F401

SAVE_GRAPH = {
    "prompt": {
        "9": {
            "class_type": "SaveImage",
            "inputs": {},
            "_meta": {"title": "MS_SAVE"},
        }
    }
}


async def _drain(ws, count, timeout=5):
    out = []
    for _ in range(count):
        out.append(json.loads(await asyncio.wait_for(ws.recv(), timeout)))
    return out


async def test_submit_returns_a_prompt_id(fake_comfy):  # noqa: F811
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{fake_comfy.base_url}/prompt", json={"prompt": {}})
    assert r.status_code == 200
    assert r.json()["prompt_id"]


async def test_execution_emits_ws_events_and_populates_history(
    fake_comfy,  # noqa: F811
):
    async with websockets.connect(f"ws://127.0.0.1:{fake_comfy._port}/ws") as ws:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
            prompt_id = r.json()["prompt_id"]
            events = await _drain(ws, 5)

    assert [e["type"] for e in events] == [
        "execution_start",
        "executing",
        "executed",
        "execution_success",
        "executing",
    ]
    # The end-of-prompt frame is `executing` with node: null.
    assert events[-1]["data"]["node"] is None
    assert events[-1]["data"]["prompt_id"] == prompt_id

    async with httpx.AsyncClient() as client:
        h = await client.get(f"{fake_comfy.base_url}/history/{prompt_id}")
    images = h.json()[prompt_id]["outputs"]["9"]["images"]
    assert len(images) == 2


async def test_history_is_absent_until_the_prompt_ends(fake_comfy):  # noqa: F811
    """The C2 race, reproduced at the fixture level.

    While a post-save node is still executing, `executed` has already
    fired for MS_SAVE but /history has nothing at all.
    """
    fake_comfy.post_save_delay = 0.4
    fake_comfy.trailing_output_node = True

    async with websockets.connect(f"ws://127.0.0.1:{fake_comfy._port}/ws") as ws:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
            prompt_id = r.json()["prompt_id"]

            events = await _drain(ws, 3)
            assert [e["type"] for e in events] == [
                "execution_start",
                "executing",
                "executed",
            ]
            h = await client.get(f"{fake_comfy.base_url}/history/{prompt_id}")
            assert h.json() == {}  # nothing yet — the prompt is still running

            rest = await _drain(ws, 3)
            assert [e["type"] for e in rest] == [
                "executed",
                "execution_success",
                "executing",
            ]

            h = await client.get(f"{fake_comfy.base_url}/history/{prompt_id}")
            assert h.json()[prompt_id]["outputs"]["9"]["images"]


async def test_history_is_absent_between_execution_success_and_the_null_frame(
    fake_comfy,  # noqa: F811
):
    """Pins that `/history` is still empty strictly between the two
    end-of-prompt signals, not just before `executed`.

    `_end_of_prompt` writes `self._history[prompt_id]` and only then
    broadcasts the trailing `executing {node: null}` frame -- mirroring
    real ComfyUI, where `execution_success` is emitted from *inside*
    `PromptExecutor.execute()` while the history entry is only recorded
    afterwards by `PromptQueue.task_done()`. If the write were moved
    earlier -- e.g. to just before `execution_success` is broadcast --
    every other fixture test would still pass (they only check history
    before `executed`, or after the whole sequence), silently erasing
    the reason `ComfyClient._await_history` exists at all. Uses
    `post_success_hold` to pin the fixture in that exact window
    deterministically, rather than racing real socket I/O.
    """
    fake_comfy.post_success_hold = asyncio.Event()
    async with websockets.connect(f"ws://127.0.0.1:{fake_comfy._port}/ws") as ws:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
            prompt_id = r.json()["prompt_id"]

            events = await _drain(ws, 4)
            assert [e["type"] for e in events] == [
                "execution_start",
                "executing",
                "executed",
                "execution_success",
            ]

            # execution_success is out, but the fixture is held before
            # writing history -- the entry must not exist yet.
            h = await client.get(f"{fake_comfy.base_url}/history/{prompt_id}")
            assert h.json() == {}

            fake_comfy.post_success_hold.set()
            rest = await _drain(ws, 1)
            assert rest[0]["type"] == "executing"
            assert rest[0]["data"]["node"] is None

            h = await client.get(f"{fake_comfy.base_url}/history/{prompt_id}")
            assert h.json()[prompt_id]["outputs"]["9"]["images"]


async def test_fail_with_emits_execution_error(fake_comfy):  # noqa: F811
    fake_comfy.fail_with = "value not in list: ckpt_name"
    async with websockets.connect(f"ws://127.0.0.1:{fake_comfy._port}/ws") as ws:
        async with httpx.AsyncClient() as client:
            await client.post(f"{fake_comfy.base_url}/prompt", json={"prompt": {}})
        events = await _drain(ws, 4)

    assert [e["type"] for e in events] == [
        "execution_start",
        "executing",
        "execution_error",
        "executing",
    ]


async def test_only_one_prompt_runs_at_a_time(fake_comfy):  # noqa: F811
    # These tests assert on *which* prompt is running, so they hold the
    # prompt open with a never-set gate rather than betting that an
    # execution_delay out-lasts the assertions. A wall-clock margin does
    # not survive a loaded box; the fixture's stop() cancels the worker.
    fake_comfy.hold = asyncio.Event()
    async with httpx.AsyncClient() as client:
        first = (
            await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
        ).json()["prompt_id"]
        second = (
            await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
        ).json()["prompt_id"]

    for _ in range(50):
        await asyncio.sleep(0.01)
        if fake_comfy.running_prompt == first:
            break
    assert fake_comfy.running_prompt == first
    assert fake_comfy.pending_prompts == [second]


async def test_a_bodyless_interrupt_kills_whatever_is_running(
    fake_comfy,  # noqa: F811
):
    fake_comfy.hold = asyncio.Event()
    async with httpx.AsyncClient() as client:
        running = (
            await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
        ).json()["prompt_id"]
        pending = (
            await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
        ).json()["prompt_id"]

        for _ in range(100):
            await asyncio.sleep(0.01)
            if fake_comfy.running_prompt == running:
                break

        await client.post(f"{fake_comfy.base_url}/interrupt")

    assert fake_comfy.interrupted_prompts == [running]
    assert pending in fake_comfy.pending_prompts


async def test_an_interrupt_naming_a_pending_prompt_is_a_no_op(
    fake_comfy,  # noqa: F811
):
    fake_comfy.hold = asyncio.Event()
    async with httpx.AsyncClient() as client:
        running = (
            await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
        ).json()["prompt_id"]
        pending = (
            await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
        ).json()["prompt_id"]

        for _ in range(100):
            await asyncio.sleep(0.01)
            if fake_comfy.running_prompt == running:
                break

        await client.post(
            f"{fake_comfy.base_url}/interrupt", json={"prompt_id": pending}
        )

    assert fake_comfy.interrupted == 1
    assert fake_comfy.interrupted_prompts == []
    assert fake_comfy.running_prompt == running


async def test_an_interrupted_prompt_reports_execution_interrupted(
    fake_comfy,  # noqa: F811
):
    fake_comfy.hold = asyncio.Event()
    async with websockets.connect(f"ws://127.0.0.1:{fake_comfy._port}/ws") as ws:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
            prompt_id = r.json()["prompt_id"]
            await _drain(ws, 2)  # execution_start, executing

            for _ in range(100):
                await asyncio.sleep(0.01)
                if fake_comfy.running_prompt == prompt_id:
                    break
            await client.post(
                f"{fake_comfy.base_url}/interrupt", json={"prompt_id": prompt_id}
            )
            events = await _drain(ws, 2)

    assert [e["type"] for e in events] == ["execution_interrupted", "executing"]
    assert events[0]["data"]["prompt_id"] == prompt_id


async def test_queue_delete_drops_a_pending_prompt(fake_comfy):  # noqa: F811
    fake_comfy.hold = asyncio.Event()
    async with httpx.AsyncClient() as client:
        running = (
            await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
        ).json()["prompt_id"]
        pending = (
            await client.post(f"{fake_comfy.base_url}/prompt", json=SAVE_GRAPH)
        ).json()["prompt_id"]

        for _ in range(100):
            await asyncio.sleep(0.01)
            if fake_comfy.running_prompt == running:
                break

        await client.post(f"{fake_comfy.base_url}/queue", json={"delete": [pending]})

    assert fake_comfy.pending_prompts == []
    assert fake_comfy.running_prompt == running


async def test_view_returns_png_bytes(fake_comfy):  # noqa: F811
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{fake_comfy.base_url}/view", params={"filename": "a.png"}
        )
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
