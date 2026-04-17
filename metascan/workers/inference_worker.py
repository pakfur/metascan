#!/usr/bin/env python3
"""
Inference Worker Process

Long-running subprocess that holds a single CLIP model and answers
encode_text / encode_image requests over NDJSON stdio.

Started lazily (or eagerly at server startup) by ``InferenceClient``. All
live CLIP queries from the FastAPI server go through this process so that
(a) the model is loaded exactly once regardless of concurrent requests,
and (b) VRAM is not duplicated between the server and the batch
embedding worker.

Protocol — one JSON object per line on stdin/stdout:

Requests (server → worker):
    {"type": "encode_text", "id": "<str>", "text": "<str>"}
    {"type": "encode_image", "id": "<str>", "path": "<absolute path>"}
    {"type": "encode_video", "id": "<str>", "path": "<absolute path>", "num_keyframes": int}
    {"type": "ping", "id": "<str>"}
    {"type": "shutdown"}

Responses (worker → server, keyed by request id):
    {"id": "<id>", "ok": true,  "embedding": [float, ...], "dim": int}
    {"id": "<id>", "ok": false, "error": "<str>"}
    {"id": "<id>", "ok": true,  "pong": true}

Unsolicited events (worker → server):
    {"event": "loading", "stage": "downloading|instantiating|ready", "percent": float}
    {"event": "ready", "model_key": "<str>", "device": "<str>", "dim": int}
    {"event": "error", "error": "<str>"}

Stderr is used only for logs; the server may surface or ignore it.

Usage:
    python -m metascan.workers.inference_worker --model-key small --device auto
"""

import argparse
import json
import logging
import os
import platform
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional

# Pre-load PyTorch c10.dll on Windows to prevent DLL loading errors
if platform.system() == "Windows":
    import ctypes
    from importlib.util import find_spec

    try:
        if (
            (spec := find_spec("torch"))
            and spec.origin
            and os.path.exists(
                dll_path := os.path.join(os.path.dirname(spec.origin), "lib", "c10.dll")
            )
        ):
            ctypes.CDLL(os.path.normpath(dll_path))
    except Exception:
        pass

# Add the project root to sys.path so we can import metascan modules.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from metascan.core.embedding_manager import EmbeddingManager  # noqa: E402

logger = logging.getLogger("inference_worker")


class InferenceWorker:
    """Holds a single ``EmbeddingManager`` and serves NDJSON requests."""

    def __init__(self, model_key: str, device: str) -> None:
        self.model_key = model_key
        self.device = device
        self._mgr: Optional[EmbeddingManager] = None
        # Protects stdout writes so unsolicited events don't interleave with
        # response bytes when emitted from a background thread.
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------
    # IO helpers
    # ------------------------------------------------------------------

    def _emit(self, payload: Dict[str, Any]) -> None:
        line = json.dumps(payload, separators=(",", ":"))
        with self._write_lock:
            sys.stdout.write(line)
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _respond(self, req_id: str, ok: bool, **extra: Any) -> None:
        payload: Dict[str, Any] = {"id": req_id, "ok": ok}
        payload.update(extra)
        self._emit(payload)

    def _event(self, event: str, **data: Any) -> None:
        payload: Dict[str, Any] = {"event": event}
        payload.update(data)
        self._emit(payload)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _load_model(self) -> None:
        self._event("loading", stage="instantiating", percent=0.0)
        mgr = EmbeddingManager(model_key=self.model_key, device=self.device)
        # Force the model to load now so the first real query doesn't pay
        # the cost — and so the server sees the 'ready' event before
        # accepting searches.
        mgr._ensure_model_loaded()
        self._mgr = mgr
        assert mgr._device is not None
        self._event(
            "ready",
            model_key=self.model_key,
            device=mgr._device,
            dim=mgr.embedding_dim,
        )

    # ------------------------------------------------------------------
    # Request handlers
    # ------------------------------------------------------------------

    def _handle_encode_text(self, req: Dict[str, Any]) -> None:
        req_id = str(req.get("id", ""))
        text = req.get("text")
        if not isinstance(text, str) or not text.strip():
            self._respond(req_id, False, error="text must be a non-empty string")
            return
        assert self._mgr is not None
        vec = self._mgr.compute_text_embedding(text)
        if vec is None:
            self._respond(req_id, False, error="compute_text_embedding returned None")
            return
        self._respond(
            req_id,
            True,
            embedding=vec.astype(float).tolist(),
            dim=int(vec.shape[0]),
        )

    def _handle_encode_image(self, req: Dict[str, Any]) -> None:
        req_id = str(req.get("id", ""))
        path = req.get("path")
        if not isinstance(path, str) or not path:
            self._respond(req_id, False, error="path must be a non-empty string")
            return
        if not os.path.exists(path):
            self._respond(req_id, False, error=f"file not found: {path}")
            return
        assert self._mgr is not None
        vec = self._mgr.compute_image_embedding(path)
        if vec is None:
            self._respond(req_id, False, error="compute_image_embedding returned None")
            return
        self._respond(
            req_id,
            True,
            embedding=vec.astype(float).tolist(),
            dim=int(vec.shape[0]),
        )

    def _handle_encode_video(self, req: Dict[str, Any]) -> None:
        req_id = str(req.get("id", ""))
        path = req.get("path")
        num_keyframes = int(req.get("num_keyframes", 4))
        if not isinstance(path, str) or not path:
            self._respond(req_id, False, error="path must be a non-empty string")
            return
        if not os.path.exists(path):
            self._respond(req_id, False, error=f"file not found: {path}")
            return
        assert self._mgr is not None
        vec = self._mgr.compute_video_embedding(path, num_keyframes=num_keyframes)
        if vec is None:
            self._respond(req_id, False, error="compute_video_embedding returned None")
            return
        self._respond(
            req_id,
            True,
            embedding=vec.astype(float).tolist(),
            dim=int(vec.shape[0]),
        )

    def _handle_ping(self, req: Dict[str, Any]) -> None:
        self._respond(str(req.get("id", "")), True, pong=True)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> int:
        try:
            self._load_model()
        except Exception as e:
            logger.exception("Model load failed")
            self._event("error", error=f"load failed: {e}")
            return 2

        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("Malformed request (not JSON): %s", e)
                continue

            kind = req.get("type")
            try:
                if kind == "encode_text":
                    self._handle_encode_text(req)
                elif kind == "encode_image":
                    self._handle_encode_image(req)
                elif kind == "encode_video":
                    self._handle_encode_video(req)
                elif kind == "ping":
                    self._handle_ping(req)
                elif kind == "shutdown":
                    return 0
                else:
                    self._respond(
                        str(req.get("id", "")),
                        False,
                        error=f"unknown request type: {kind!r}",
                    )
            except Exception as e:
                logger.exception("Request handler crashed")
                self._respond(str(req.get("id", "")), False, error=str(e))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="inference_worker")
    parser.add_argument("--model-key", default="small")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    worker = InferenceWorker(model_key=args.model_key, device=args.device)
    return worker.run()


if __name__ == "__main__":
    sys.exit(main())
