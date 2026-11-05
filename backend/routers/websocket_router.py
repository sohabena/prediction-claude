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
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass  # Already removed by broadcast cleanup
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
            try:
                self.active_connections.remove(conn)
            except ValueError:
                pass


manager = ConnectionManager()


async def _cleanup_pubsub(pubsub: Any) -> None:
    """Safely unsubscribe and close a Redis pubsub connection."""
    try:
        await pubsub.unsubscribe()
        await pubsub.close()
    except Exception:
        pass


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint that streams real-time data.

    Subscribes to Redis channels and forwards events to connected clients.
    """
    await manager.connect(websocket)
    pubsub = None

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
                        channel = message["channel"]
                        if isinstance(channel, bytes):
                            channel = channel.decode("utf-8")
                        data = json.loads(message["data"])
                        await websocket.send_json({
                            "channel": channel,
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

        # Run both listeners concurrently; cancel sibling on first exit
        redis_task = asyncio.create_task(listen_redis())
        client_task = asyncio.create_task(listen_client())
        try:
            done, pending = await asyncio.wait(
                [redis_task, client_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
        except Exception:
            redis_task.cancel()
            client_task.cancel()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("ws_error", error=str(e))
    finally:
        manager.disconnect(websocket)
        if pubsub is not None:
            await _cleanup_pubsub(pubsub)
