"""Socket.IO event helpers."""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..models import events as event_models


_sio: Optional[Any] = None


def init(server: Any) -> None:
    global _sio
    _sio = server


async def _emit(event: str, payload: Dict[str, Any], sid: Optional[str] = None) -> None:
    if not _sio:
        raise RuntimeError("Socket.IO server not initialised")
    await _sio.emit(event, payload, room=sid)


async def emit_connected(sid: str) -> None:
    await _emit("connected", event_models.ConnectedEvent(status="ok").model_dump(), sid)


async def emit_message_received(sid: str, message_id: str) -> None:
    await _emit("message_received", event_models.MessageReceivedEvent(message_id=message_id).model_dump(), sid)


async def emit_token(sid: str, message_id: str, token: str) -> None:
    await _emit("assistant_token", event_models.AssistantTokenEvent(message_id=message_id, token=token).model_dump(), sid)


async def emit_done(sid: str, message_id: str, text: str) -> None:
    await _emit("assistant_done", event_models.AssistantDoneEvent(message_id=message_id, text=text).model_dump(), sid)


async def emit_agent_status(sid: str, stage: event_models.AgentStage, detail: Optional[str] = None) -> None:
    await _emit("agent_status", event_models.AgentStatusEvent(stage=stage, detail=detail).model_dump(), sid)


async def emit_agent_think(sid: str, message_id: str, thought: str) -> None:
    await _emit("agent_think", event_models.AgentThinkEvent(message_id=message_id, thought=thought).model_dump(), sid)


async def emit_agent_tool_start(sid: str, tool: str, args: Dict[str, Any]) -> None:
    await _emit("agent_tool_start", event_models.AgentToolStartEvent(tool=tool, args=args).model_dump(), sid)


async def emit_agent_tool_result(sid: str, tool: str, result: Dict[str, Any]) -> None:
    await _emit("agent_tool_result", event_models.AgentToolResultEvent(tool=tool, result=result).model_dump(), sid)


async def emit_doer_checklist(sid: str, items) -> None:
    event = event_models.DoerChecklistEvent(items=items)
    await _emit("doer_checklist", event.model_dump(), sid)


async def emit_doer_update(sid: str, item_id: str, status: event_models.ChecklistStatus, note: Optional[str] = None) -> None:
    await _emit("doer_update", event_models.DoerUpdateEvent(id=item_id, status=status, note=note).model_dump(), sid)


async def emit_ask_params(sid: str, correlation_id: str, fields) -> None:
    event = event_models.AskParamsEvent(fields=fields, correlation_id=correlation_id)
    await _emit("ask_params", event.model_dump(), sid)


async def emit_error(sid: Optional[str], message_id: Optional[str], error: str) -> None:
    target = sid if sid else None
    await _emit("error", event_models.ErrorEvent(message_id=message_id, error=error).model_dump(), target)

