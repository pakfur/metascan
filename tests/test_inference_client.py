"""Tests for metascan.core.inference_client.

Uses a lightweight fake worker script (no CLIP) to exercise the IPC,
request serialization, error handling, and respawn-on-crash paths in the
client without paying a multi-gigabyte model download.
"""

import asyncio
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import List, Optional

import numpy as np

from metascan.core.inference_client import (
    STATE_ERROR,
    STATE_READY,
    STATE_STOPPED,
    InferenceClient,
)


# ----------------------------------------------------------------------
# Fake worker script — emits the same NDJSON protocol as the real worker
# but does not load any model. Runs in a subprocess via sys.executable.
# ----------------------------------------------------------------------

ECHO_WORKER = textwrap.dedent(
    """
    import argparse, json, sys

    ap = argparse.ArgumentParser()
    ap.add_argument("--model-key", default="small")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--slow-load-ms", type=int, default=0)
    ap.add_argument("--crash-after", type=int, default=0)
    ap.add_argument("--fail-ready", action="store_true")
    args = ap.parse_args()

    def emit(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    if args.slow_load_ms:
        import time
        time.sleep(args.slow_load_ms / 1000.0)

    if args.fail_ready:
        emit({"event": "error", "error": "forced failure"})
        sys.exit(2)

    DIM = 4
    emit({"event": "loading", "stage": "instantiating", "percent": 0.0})
    emit({"event": "ready", "model_key": args.model_key, "device": "cpu", "dim": DIM})

    handled = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        kind = req.get("type")
        rid = req.get("id", "")
        if kind == "shutdown":
            break
        if kind == "encode_text":
            emit({"id": rid, "ok": True, "embedding": [1.0, 0.0, 0.0, 0.0], "dim": DIM})
        elif kind == "encode_image":
            emit({"id": rid, "ok": True, "embedding": [0.0, 1.0, 0.0, 0.0], "dim": DIM})
        elif kind == "ping":
            emit({"id": rid, "ok": True, "pong": True})
        else:
            emit({"id": rid, "ok": False, "error": "unknown"})
        handled += 1
        if args.crash_after and handled >= args.crash_after:
            sys.exit(1)
    """
).strip()


def _write_fake_worker() -> Path:
    fd = tempfile.NamedTemporaryFile(
        prefix="fake_inference_worker_", suffix=".py", delete=False, mode="w"
    )
    fd.write(ECHO_WORKER)
    fd.close()
    return Path(fd.name)


class _ClientFixture:
    """Accumulates clients/temp files created during one test so the test
    can delete them all in tearDown without leaking subprocesses."""

    def __init__(self) -> None:
        self.clients: List[InferenceClient] = []
        self.scripts: List[Path] = []

    async def make(
        self, *, extra: Optional[list] = None, wait_ready: bool = True
    ) -> InferenceClient:
        script = _write_fake_worker()
        self.scripts.append(script)
        c = InferenceClient(worker_script=script, extra_worker_args=extra)
        self.clients.append(c)
        await c.start(
            model_key="small",
            device="cpu",
            wait_ready=wait_ready,
            ready_timeout=15.0,
        )
        return c

    async def aclose(self) -> None:
        for c in self.clients:
            try:
                await c.shutdown()
            except Exception:
                pass
        for p in self.scripts:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


class InferenceClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fx = _ClientFixture()

    async def asyncTearDown(self) -> None:
        await self.fx.aclose()

    async def test_ready_and_encode_text(self) -> None:
        c = await self.fx.make()
        self.assertEqual(c.state, STATE_READY)
        self.assertEqual(c.dim, 4)
        vec = await c.encode_text("a cat")
        self.assertIsInstance(vec, np.ndarray)
        self.assertEqual(vec.shape, (4,))
        np.testing.assert_allclose(vec, [1.0, 0.0, 0.0, 0.0])

    async def test_encode_image_returns_distinct_vector(self) -> None:
        c = await self.fx.make()
        with tempfile.NamedTemporaryFile(suffix=".png") as tf:
            vec = await c.encode_image(tf.name)
        np.testing.assert_allclose(vec, [0.0, 1.0, 0.0, 0.0])

    async def test_ping(self) -> None:
        c = await self.fx.make()
        self.assertTrue(await c.ping())

    async def test_lock_serializes_concurrent_requests(self) -> None:
        c = await self.fx.make()
        with tempfile.NamedTemporaryFile(suffix=".png") as tf:
            results = await asyncio.gather(
                c.encode_text("one"),
                c.encode_image(tf.name),
            )
        np.testing.assert_allclose(results[0], [1.0, 0.0, 0.0, 0.0])
        np.testing.assert_allclose(results[1], [0.0, 1.0, 0.0, 0.0])

    async def test_ready_failure_propagates(self) -> None:
        script = _write_fake_worker()
        self.fx.scripts.append(script)
        c = InferenceClient(worker_script=script, extra_worker_args=["--fail-ready"])
        self.fx.clients.append(c)
        with self.assertRaises(RuntimeError):
            await c.start(
                model_key="small",
                device="cpu",
                wait_ready=True,
                ready_timeout=10.0,
            )
        self.assertEqual(c.state, STATE_ERROR)
        self.assertIn("forced failure", c.last_error or "")

    async def test_crash_after_first_request_fails_subsequent(self) -> None:
        c = await self.fx.make(extra=["--crash-after", "1"])
        vec = await c.encode_text("x")
        np.testing.assert_allclose(vec, [1.0, 0.0, 0.0, 0.0])
        # Wait for the supervisor to observe the exit.
        for _ in range(50):
            if c.state != STATE_READY:
                break
            await asyncio.sleep(0.05)
        self.assertNotEqual(c.state, STATE_READY)

    async def test_reload_restarts_worker(self) -> None:
        c = await self.fx.make()
        self.assertEqual(c.model_key, "small")
        await c.reload(model_key="medium", device="cpu")
        self.assertEqual(c.state, STATE_READY)
        self.assertEqual(c.model_key, "medium")
        vec = await c.encode_text("after reload")
        np.testing.assert_allclose(vec, [1.0, 0.0, 0.0, 0.0])

    async def test_shutdown_transitions_state(self) -> None:
        c = await self.fx.make()
        await c.shutdown()
        self.assertEqual(c.state, STATE_STOPPED)


if __name__ == "__main__":
    unittest.main()
