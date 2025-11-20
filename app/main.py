"""Application entrypoint: FastAPI + Socket.IO setup."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import socketio

from .config import settings
from .controllers import controller
from .routers import chat as chat_router
from .routers import rag as rag_router
from .routers import inAppAI as inAppAI_router
from .services import event_bus, token_storage


def create_app() -> tuple[FastAPI, socketio.AsyncServer, socketio.ASGIApp]:
    """Create FastAPI app, configure CORS, mount routers, and initialise Socket.IO."""

    fastapi_app = FastAPI(title=settings.app_name)

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins=settings.cors_origins,
        logger=False,
        engineio_logger=False,
        ping_timeout=60,
        ping_interval=25,
        max_http_buffer_size=1e6,  # 1MB max message size
        allow_upgrades=True,
        transports=['polling', 'websocket'],  # Explicitly allow both transports for Render.com
    )

    event_bus.init(sio)

    sio_app = socketio.ASGIApp(sio, fastapi_app, socketio_path="/socket.io")

    fastapi_app.include_router(chat_router.router)
    fastapi_app.include_router(rag_router.router)
    fastapi_app.include_router(inAppAI_router.router)
    @fastapi_app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok"}

    return fastapi_app, sio, sio_app


app, sio, socketio_app = create_app()


@sio.event
async def connect(sid, environ, auth):  # type: ignore[valid-type]
    """Handle Socket.IO connection and store client token if provided."""
    # Extract token from auth data (sent by client during connection)
    if auth and isinstance(auth, dict):
        token = auth.get("token") or auth.get("backend_token")
        if token:
            token_storage.store_token(sid, token)
            print(f"Token stored for session {sid[:8]}...")
        else:
            print(f"Warning: No token provided in auth for session {sid[:8]}...")
    else:
        print(f"Warning: No auth data provided for session {sid[:8]}...")
    await event_bus.emit_connected(sid)


@sio.event
async def disconnect(sid):  # type: ignore[valid-type]
    """Clean up client token on disconnect."""
    token_storage.remove_token(sid)


@sio.on("message")
async def handle_message(sid, data):  # type: ignore[valid-type]
    await controller.handle_socket_message(sid, data)


@sio.on("set_token")
async def handle_set_token(sid, data):  # type: ignore[valid-type]
    """Handle token update from client."""
    if isinstance(data, dict):
        token = data.get("token") or data.get("backend_token")
        if token:
            token_storage.store_token(sid, token)
            await sio.emit("token_set", {"status": "ok"}, room=sid)
        else:
            await sio.emit("token_set", {"status": "error", "message": "Token not provided"}, room=sid)





if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        socketio_app,
        host="0.0.0.0",
        port=settings.port,
        log_level="info",
    )

