from __future__ import annotations

from typing import Any

import socketio

from . import events
from .config import SETTINGS
from .room_manager import RoomManager


socket_cors_origins: str | list[str]
if SETTINGS.cors_origins == ("*",):
    socket_cors_origins = "*"
else:
    socket_cors_origins = list(SETTINGS.cors_origins)

sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins=socket_cors_origins)
room_manager = RoomManager(
    db_path=SETTINGS.db_path,
    hint_penalty=SETTINGS.hint_penalty,
    inter_round_delay_seconds=SETTINGS.inter_round_delay_seconds,
)
room_manager.bind_socket_server(sio)


def _ok(payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **payload}


def _error(message: str) -> dict[str, Any]:
    return {"ok": False, "error": message}


@sio.event
async def connect(sid: str, environ: dict, auth: dict | None):
    return True


@sio.event
async def disconnect(sid: str):
    await room_manager.mark_disconnected(sid)


@sio.on(events.ROOM_CREATE)
async def on_room_create(sid: str, payload: dict[str, Any]):
    try:
        result = await room_manager.create_room(sid, payload or {})
        await sio.enter_room(sid, result["session"]["roomCode"])
        await room_manager.emit_room_state(result["session"]["roomCode"])
        return _ok(result)
    except ValueError as exc:
        return _error(str(exc))


@sio.on(events.ROOM_JOIN)
async def on_room_join(sid: str, payload: dict[str, Any]):
    try:
        result = await room_manager.join_room(sid, payload or {})
        await sio.enter_room(sid, result["session"]["roomCode"])
        return _ok(result)
    except ValueError as exc:
        return _error(str(exc))


@sio.on(events.PLAYER_RECONNECT)
async def on_player_reconnect(sid: str, payload: dict[str, Any]):
    try:
        result = await room_manager.reconnect_player(sid, payload or {})
        await sio.enter_room(sid, result["session"]["roomCode"])
        return _ok(result)
    except ValueError as exc:
        return _error(str(exc))


@sio.on(events.GAME_START)
async def on_game_start(sid: str, payload: dict[str, Any]):
    _ = payload
    try:
        await room_manager.start_game(sid)
        return _ok({"started": True})
    except ValueError as exc:
        return _error(str(exc))


@sio.on(events.ROUND_GUESS_SUBMIT)
async def on_submit_guess(sid: str, payload: dict[str, Any]):
    try:
        result = await room_manager.submit_guess(sid, payload or {})
        return _ok(result)
    except ValueError as exc:
        return _error(str(exc))


@sio.on(events.ROUND_HINT_REQUEST)
async def on_request_hint(sid: str, payload: dict[str, Any]):
    try:
        result = await room_manager.request_hint(sid, payload or {})
        return _ok(result)
    except ValueError as exc:
        return _error(str(exc))


@sio.on(events.ROUND_NEXT)
async def on_next_round(sid: str, payload: dict[str, Any]):
    _ = payload
    try:
        result = await room_manager.start_next_round(sid)
        return _ok(result)
    except ValueError as exc:
        return _error(str(exc))
