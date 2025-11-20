from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import ValidationError


from ..controllers import controller
from ..models.chat import ChatRequest

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat_endpoint(request: ChatRequest) -> dict[str, Any]:
    try:
        text = await controller.handle_http_chat(request)
        return {"response": text}
    except Exception as exc:  # pragma: no cover - bubble as HTTP error
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/agent")
async def agent_endpoint(request: ChatRequest) -> dict[str, Any]:
    """Optional endpoint for agent runs without Socket.IO."""
    text = await controller.handle_http_chat(request)
    return {"response": text}


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            try:
                request = ChatRequest(**payload)
            except ValidationError as exc:
                await websocket.send_json({"error": "invalid_payload", "detail": exc.errors()})
                continue

            try:
                text = await controller.handle_http_chat(request)
            except Exception as exc:  # pragma: no cover - surface runtime errors to client
                await websocket.send_json({"error": "processing_failed", "detail": str(exc)})
                continue

            await websocket.send_json({"response": text})
    except WebSocketDisconnect:
        return
