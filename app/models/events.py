"""Socket.IO event payload schemas."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel


class ConnectedEvent(BaseModel):
    status: str


class MessageReceivedEvent(BaseModel):
    message_id: str


class AssistantTokenEvent(BaseModel):
    message_id: str
    token: str


class AssistantDoneEvent(BaseModel):
    message_id: str
    text: str


AgentStage = Literal["planning", "retrieving", "tool_call", "waiting_user", "done"]


class AgentStatusEvent(BaseModel):
    stage: AgentStage
    detail: Optional[str] = None


class AgentThinkEvent(BaseModel):
    message_id: str
    thought: str


class AgentToolStartEvent(BaseModel):
    tool: str
    args: dict


class AgentToolResultEvent(BaseModel):
    tool: str
    result: dict


ChecklistStatus = Literal["pending", "done", "error"]


class ChecklistItem(BaseModel):
    id: str
    label: str
    status: ChecklistStatus = "pending"


class DoerChecklistEvent(BaseModel):
    items: List[ChecklistItem]


class DoerUpdateEvent(BaseModel):
    id: str
    status: ChecklistStatus
    note: Optional[str] = None


class AskParamField(BaseModel):
    name: str
    type: str
    hint: Optional[str] = None
    required: bool = True


class AskParamsEvent(BaseModel):
    fields: List[AskParamField]
    correlation_id: str


class ProvideParamsEvent(BaseModel):
    correlation_id: str
    values: dict


class ErrorEvent(BaseModel):
    message_id: Optional[str]
    error: str

