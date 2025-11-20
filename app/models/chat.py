"""Chat-related Pydantic models."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    role: Role
    content: str


class ChatRequest(BaseModel):
    messages: List[Message]
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None

