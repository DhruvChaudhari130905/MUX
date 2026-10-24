"""Room WebSocket (docs/06-event-catalog.md). Streams event envelopes from ?since=seq and carries ephemeral presence.

    GET /rooms/{id}/ws?since={seq}
    first client message: {"type": "auth", "payload": {"token": "<supabase jwt>"}}

The token may also come as ?token= or an Authorization header. The server first sends a JSON array
with every envelope after `since`, then one envelope per message.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from mux.auth.supabase import User, display_name, verify_supabase_token
from mux.events.bus import event_bus
from mux.events.models import BaseEvent
from mux.events.wire import ephemeral, to_envelope, user_view
from mux.rooms.actor import ROOM_ID_PATTERN, RoomActor
from mux.rooms.registry import get_registry

logger = logging.getLogger(__name__)

router = APIRouter()

AUTH_TIMEOUT_SECONDS = 10


def _user_from_token(token: str) -> Optional[User]:
    """Same verification as the REST API (handles Supabase's aud claim)."""
    payload = verify_supabase_token(token)
    if payload is None or not payload.get("sub"):
        return None
    return User(id=payload["sub"], email=payload.get("email"), role=payload.get("role", "authenticated"), name=display_name(payload))


async def _authenticate(websocket: WebSocket, token: Optional[str]) -> Optional[User]:
    """Token from ?token=, the Authorization header, or the first message ({"type": "auth"})."""
    if not token:
        header = websocket.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token = header[7:]
    if not token:
        try:
            first = json.loads(await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT_SECONDS))
        except (asyncio.TimeoutError, ValueError, WebSocketDisconnect):
            return None
        if isinstance(first, dict) and first.get("type") == "auth":
            token = (first.get("payload") or {}).get("token")
    return _user_from_token(token) if token else None


@router.websocket("/rooms/{room_id}/ws")
async def room_websocket(
    websocket: WebSocket,
    room_id: str,
    since: int = Query(0, description="Last seq the client has; everything after it is replayed"),
    token: Optional[str] = Query(None),
):
    await websocket.accept()

    user = await _authenticate(websocket, token)
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing or invalid authentication token")
        return

    # Look up (or rehydrate) the room; WebSockets never create rooms
    actor = await get_registry().get_room_or_rehydrate(room_id) if ROOM_ID_PATTERN.match(room_id) else None
    if actor is None or actor.role_of(user.id) is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Room not found")
        return

    conn = None

    async def dump_and_subscribe(events: list[BaseEvent]) -> None:
        nonlocal conn
        envelopes = [env for env in map(to_envelope, events) if env is not None]
        await websocket.send_text(json.dumps(envelopes, default=str))
        conn = event_bus.connect(room_id, websocket, last_seq=events[-1].sequence if events else since)

    try:
        await actor.subscribe_since(since, dump_and_subscribe)
        existing = await actor.get_all_presence()
        snapshot = [
            {
                "user_id": user_id,
                "user": user_view(user_id, value.get("user_name")),
                "tab": value.get("active_tab") or "feed",
                "active": value.get("status") != "offline",
                "typing": value.get("typing", False),
                "last_seen": _now(),
            }
            for user_id, value in existing.items()
        ]
        await websocket.send_text(json.dumps(ephemeral(
            room_id, actor.sequence, "presence.snapshot", user.id, {"users": snapshot}, _now(),
        )))
        # Records presence + sitting activity; reaches everyone (this client too) as presence.join
        await actor.user_join(user_id=user.id)

        while True:
            try:
                message = json.loads(await websocket.receive_text())
                if not isinstance(message, dict):
                    raise ValueError("Message must be a JSON object")
                await handle_client_message(actor, message, user.id, websocket)
            except WebSocketDisconnect:
                break
            except ValueError as e:  # includes json.JSONDecodeError
                await send_error(websocket, actor, str(e))
            except Exception as e:
                logger.exception(f"Error handling WebSocket message: {e}")
                await send_error(websocket, actor, "Internal error")
    except WebSocketDisconnect:
        pass
    finally:
        if conn is not None:
            event_bus.disconnect(room_id, conn)
            await actor.user_leave(user.id)


async def handle_client_message(actor: RoomActor, message: dict[str, Any], user_id: str, websocket: WebSocket) -> None:
    """presence.tab / presence.typing (as sent by web/src/lib/socket.ts), ping, and a late auth message."""
    msg_type = message.get("type")
    payload = message.get("payload") or {}

    if msg_type == "presence.typing":
        # The actor records it and it reaches everyone as presence.typing
        await actor.set_typing(user_id, bool(payload.get("typing")))
    elif msg_type == "presence.tab":
        tab = payload.get("tab")
        if tab not in ("feed", "preview", "code", "cards"):
            raise ValueError(f"Invalid tab: {tab!r}")
        await actor.set_active_tab(user_id, tab)
        await event_bus.publish_json(actor.room_id, ephemeral(
            actor.room_id, actor.sequence, "presence.tab", user_id, {"user_id": user_id, "tab": tab}, _now(),
        ))
    elif msg_type == "ping":
        await websocket.send_text(json.dumps({"type": "pong", "seq": actor.sequence}))
    elif msg_type == "auth":
        pass  # already authenticated
    else:
        raise ValueError(f"Unknown message type: {msg_type}")


async def send_error(websocket: WebSocket, actor: RoomActor, message: str) -> None:
    """Errors carry the current seq so a client that tracks seq from every message keeps its place."""
    try:
        await websocket.send_text(json.dumps({"type": "error", "message": message, "seq": actor.sequence}))
    except Exception:
        pass


async def emit_event(room_id: str, event: BaseEvent) -> None:
    """Send an actor event to the room's sockets as its catalog envelope (events with none are dropped)."""
    envelope = to_envelope(event)
    if envelope is not None:
        await event_bus.publish_envelope(room_id, envelope)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/ws/health")
async def ws_health():
    return {"status": "healthy", "connections": event_bus.connection_count()}
