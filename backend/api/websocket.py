"""WebSocket endpoint for real-time communication."""

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.ws.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Single multiplexed WebSocket for all real-time channels."""
    await ws_manager.connect(websocket)
    try:
        while True:
            text = await websocket.receive_text()
            try:
                data = json.loads(text)
                await ws_manager.handle_message(data)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from WebSocket: {text[:100]}")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
