"""WebSocket connection manager with channel multiplexing."""

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and broadcasts messages on channels."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []
        self._handlers: Dict[str, Callable[..., Coroutine]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the main event loop so sync code on worker threads can
        schedule broadcasts via run_coroutine_threadsafe."""
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(
            f"WebSocket connected. Active connections: {len(self.active_connections)}"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(
            f"WebSocket disconnected. Active connections: {len(self.active_connections)}"
        )

    async def broadcast(self, channel: str, event: str, data: Any = None) -> None:
        """Broadcast a message to all connected clients."""
        message = json.dumps({"channel": channel, "event": event, "data": data})
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)

    def broadcast_sync(self, channel: str, event: str, data: Any = None) -> None:
        """Broadcast from synchronous code (including worker threads).

        Uses the loop captured via attach_loop() so this works from threads
        without a running event loop (e.g. asyncio.to_thread workers).
        """
        if self._loop is not None and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self.broadcast(channel, event, data), self._loop
            )
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast(channel, event, data))
        except RuntimeError:
            pass

    def register_handler(self, action: str, handler: Callable[..., Coroutine]) -> None:
        """Register a handler for client-to-server actions."""
        self._handlers[action] = handler

    async def handle_message(self, data: dict) -> None:
        """Route an incoming client message to the appropriate handler."""
        action = data.get("action")
        if action and action in self._handlers:
            await self._handlers[action](data.get("data"))
        else:
            logger.warning(f"Unknown WebSocket action: {action}")


# Singleton instance
ws_manager = ConnectionManager()
