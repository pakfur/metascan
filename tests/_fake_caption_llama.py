"""A stand-in for llama-server for the caption-classifier tests (stdlib only).

Run as a script. Answers GET /health (503 until --load-ms has passed) and
POST /v1/chat/completions with the canned answer from --response, including
its logprobs. Failure modes: --fail-first N answers the first N requests with
--fail-status; --exit-after N kills the process on request N+1 without
answering. --requests-log appends every request body as a JSON line.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List

FAKE_SCRIPT = Path(__file__).resolve()


def fake_command(**flags: Any) -> Callable[[int], List[str]]:
    """A LlamaServer command factory that launches this fake."""

    def command(port: int) -> List[str]:
        cmd = [sys.executable, str(FAKE_SCRIPT), "--port", str(port)]
        for key, value in flags.items():
            cmd += [f"--{key.replace('_', '-')}", str(value)]
        return cmd

    return command


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--response", default="")
    ap.add_argument("--load-ms", type=int, default=0)
    ap.add_argument("--fail-first", type=int, default=0)
    ap.add_argument("--fail-status", type=int, default=500)
    ap.add_argument("--exit-after", type=int, default=0)
    ap.add_argument("--requests-log", default="")
    args = ap.parse_args()

    payload: Dict[str, Any] = {"content": "", "tokens": []}
    if args.response:
        payload = json.loads(Path(args.response).read_text(encoding="utf-8"))
    started = time.monotonic()
    lock = threading.Lock()
    state = {"served": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def _send(self, status: int, body: Dict[str, Any]) -> None:
            data = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            if self.path != "/health":
                self._send(404, {})
                return
            ready = (time.monotonic() - started) * 1000 >= args.load_ms
            self._send(200 if ready else 503, {"status": "ok" if ready else "loading"})

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            with lock:
                state["served"] += 1
                served = state["served"]
                if args.requests_log:
                    with open(args.requests_log, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(body) + "\n")
            if args.exit_after and served > args.exit_after:
                os._exit(1)
            if served <= args.fail_first:
                self._send(
                    args.fail_status,
                    {
                        "error": {
                            "message": "the request exceeds the available context size"
                        }
                    },
                )
                return
            self._send(
                200,
                {
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": payload["content"],
                            },
                            "logprobs": {"content": payload["tokens"]},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

    sys.stderr.write("fake llama-server listening\n")
    sys.stderr.flush()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
