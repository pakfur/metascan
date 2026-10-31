"""When the owner last used the server (spec 10).

Hygiene waits for a quiet moment: ``IDLE_AFTER_S`` since the last request
that changes something. Reads (GET, HEAD, OPTIONS) do not count -- the grid
polls and the panel refetches on its own. The counting is a pure ASGI
middleware: ``BaseHTTPMiddleware`` buffers a streaming response, which the
chat streams must not suffer.
"""

from __future__ import annotations

import time
from typing import Any, Callable

IDLE_AFTER_S = 120.0
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class IdleTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last = clock()  # the server's start counts as the last use

    def note_request(self) -> None:
        self._last = self._clock()

    def idle_for(self) -> float:
        return max(0.0, self._clock() - self._last)


# One per server process; the middleware feeds it and Hygiene reads it.
idle_tracker = IdleTracker()


class ActivityMiddleware:
    """Notes every non-read HTTP request on an ``IdleTracker``."""

    def __init__(self, app: Any, tracker: IdleTracker = idle_tracker) -> None:
        self.app = app
        self.tracker = tracker

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope.get("method") not in READ_METHODS:
            self.tracker.note_request()
        await self.app(scope, receive, send)
