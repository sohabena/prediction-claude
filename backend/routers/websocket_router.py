"""WebSocket endpoint for real-time data streaming."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from shared.constants import (
    CHANNEL_MATCH_EVENTS,
    CHANNEL_RL_ACTIONS,
    CHANNEL_TRAINING_PROGRESS,
    CHANNEL_VIRTUAL_OUTCOMES,
)
from shared.logging import setup_logging
from shared.redis_client import get_redis

router = APIRouter()
logger = setup_logging("websocket")


class ConnectionManager:
    """Manage active WebSocket connections."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("ws_connected", total=len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.remove(websocket)
        logger.info("ws_disconnected", total=len(self.active_connections))

    async def broadcast(self, message: dict[str, Any]) -> None:
        """Send message to all connected clients."""
        disconnected: list[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)

        for conn in disconnected:
            self.active_connections.remove(conn)


manager = ConnectionManager()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint that streams real-time data.

    Subscribes to Redis channels and forwards events to connected clients.
    """
    await manager.connect(websocket)

    try:
        redis = await get_redis()
        pubsub = await redis.subscribe(
            CHANNEL_MATCH_EVENTS,
            CHANNEL_RL_ACTIONS,
            CHANNEL_VIRTUAL_OUTCOMES,
            CHANNEL_TRAINING_PROGRESS,
        )

        async def listen_redis() -> None:
            """Listen for Redis messages and forward to WebSocket."""
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await websocket.send_json({
                            "channel": message["channel"],
                            "data": data,
                        })
                    except json.JSONDecodeError:
                        logger.debug("ws_json_decode_error", channel=message.get("channel"))
                    except Exception as e:
                        logger.debug("ws_forward_error", error=str(e))
                        break

        async def listen_client() -> None:
            """Listen for client messages (keep-alive, commands)."""
            while True:
                data = await websocket.receive_text()
                # Handle client commands if needed
                if data == "ping":
                    await websocket.send_json({"type": "pong"})

        # Run both listeners concurrently
        await asyncio.gather(
            listen_redis(),
            listen_client(),
        )

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("ws_error", error=str(e))
        manager.disconnect(websocket)
