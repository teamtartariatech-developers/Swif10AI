from __future__ import annotations

import asyncio
import types
import pytest

from AI.app.controllers import controller as ctrl
from AI.app.models.chat import ChatRequest, Message


class DummyEventBus:
    async def emit_message_received(self, *args, **kwargs): pass
    async def emit_agent_status(self, *args, **kwargs): pass
    async def emit_agent_think(self, *args, **kwargs): pass
    async def emit_agent_tool_start(self, *args, **kwargs): pass
    async def emit_agent_tool_result(self, *args, **kwargs): pass
    async def emit_done(self, *args, **kwargs): pass
    async def emit_error(self, *args, **kwargs): pass
    async def emit_token(self, *args, **kwargs): pass


@pytest.mark.asyncio
async def test_small_talk(monkeypatch):
    # Patch event bus
    monkeypatch.setattr(ctrl, "event_bus", DummyEventBus())

    # Patch LLM-1 classify -> small_talk
    async def fake_classify(messages):
        return {"intent": "small_talk", "tool": None, "params": None}
    monkeypatch.setattr(ctrl, "_llm1_classify", fake_classify)

    # Patch llm_sarvam.chat -> returns response
    class R: 
        def __init__(self, r): self.response = r
    async def fake_chat(messages, **kwargs):
        return R("Hello there!")
    monkeypatch.setattr(ctrl.llm_sarvam, "chat", fake_chat)

    # Patch summarizer passthrough
    monkeypatch.setattr(ctrl, "_llm1_summarize", lambda x: asyncio.sleep(0, result=x))

    req = ChatRequest(messages=[Message(role="user", content="hi")])
    text = await ctrl.handle_http_chat(req)
    assert "Hello" in text


@pytest.mark.asyncio
async def test_info_read(monkeypatch):
    monkeypatch.setattr(ctrl, "event_bus", DummyEventBus())

    async def fake_classify(messages):
        return {"intent": "info_read", "tool": "rooms_list", "params": {}}
    monkeypatch.setattr(ctrl, "_llm1_classify", fake_classify)

    async def fake_rooms_list(**kwargs):
        return {"rooms": [{"id": 1, "name": "101"}]}
    monkeypatch.setattr(ctrl.mcp_tools, "rooms_list", fake_rooms_list)

    class R: 
        def __init__(self, r): self.response = r
    async def fake_chat(messages, **kwargs):
        return R("Rooms available: 101")
    monkeypatch.setattr(ctrl.llm_sarvam, "chat", fake_chat)
    monkeypatch.setattr(ctrl, "_llm1_summarize", lambda x: asyncio.sleep(0, result=x))

    req = ChatRequest(messages=[Message(role="user", content="list rooms")])
    text = await ctrl.handle_http_chat(req)
    assert "Rooms available" in text


@pytest.mark.asyncio
async def test_task_write(monkeypatch):
    monkeypatch.setattr(ctrl, "event_bus", DummyEventBus())

    async def fake_classify(messages):
        return {"intent": "task_write", "tool": None, "params": None}
    monkeypatch.setattr(ctrl, "_llm1_classify", fake_classify)

    def fake_agent_chat(messages, stream=False):
        return "Booked successfully."
    monkeypatch.setattr(ctrl.llm_openrouter, "agent_chat", fake_agent_chat)
    monkeypatch.setattr(ctrl, "_llm1_summarize", lambda x: asyncio.sleep(0, result=x))

    req = ChatRequest(messages=[Message(role="user", content="book a room for tonight")])
    text = await ctrl.handle_http_chat(req)
    assert "Booked" in text


