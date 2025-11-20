"""Sarvam LLM client wrappers."""

from __future__ import annotations
from typing import Dict, List, Optional
from openai import AsyncOpenAI
import httpx
from ..config import settings
from pydantic import BaseModel
from typing import AsyncIterable

class ChatRequest(BaseModel):
    messages: List[Dict[str, str]]
    reasoning_level: Optional[str] = None
    max_completion_tokens: Optional[int] = 8192

class ChatResponse(BaseModel):
    response: str

# Create HTTP client with timeout
_http_client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
_client = AsyncOpenAI(
    base_url=settings.sarvam_base_url,
    api_key=settings.sarvam_api_key,
    http_client=_http_client
)


async def chat(messages: List[Dict[str, str]], *, reasoning_level: Optional[str] = None, max_completion_tokens: Optional[int] = 8192) -> ChatResponse:
    """
    Non-streaming chat completion. Returns the full response content.
    """
    try:
        params: Dict[str, object] = {
            "model": settings.sarvam_model,
            "messages": messages,
        }
        if reasoning_level:
            params["reasoning_effort"] = reasoning_level
        if max_completion_tokens is not None:
            params["max_completion_tokens"] = max_completion_tokens

        print(f"DEBUG: Calling Sarvam API for chat completion...")
        response = await _client.chat.completions.create(**params)
        content = response.choices[0].message.content
        # Handle None or empty content
        if content is None:
            content = ""
        print(f"DEBUG: Sarvam API response received, length: {len(content)}")
        return ChatResponse(response=content)
    except Exception as e:
        print(f"ERROR: Sarvam API call failed: {str(e)}")
        raise


async def chat_stream(messages: List[Dict[str, str]], *, reasoning_level: Optional[str] = None, max_completion_tokens: Optional[int] = 8192) -> AsyncIterable[str]:
    """
    Streaming chat completion. Yields content chunks as they arrive.
    """
    try:
        params: Dict[str, object] = {
            "model": settings.sarvam_model,
            "messages": messages,
            "stream": True,
        }
        if reasoning_level:
            params["reasoning_effort"] = reasoning_level
        if max_completion_tokens is not None:
            params["max_completion_tokens"] = max_completion_tokens

        print(f"DEBUG: Calling Sarvam API for streaming chat...")
        stream = await _client.chat.completions.create(**params)
        async for chunk in stream:
            delta = getattr(chunk.choices[0], "delta", None)
            if delta and getattr(delta, "content", None):
                yield delta.content
    except Exception as e:
        print(f"ERROR: Sarvam API streaming failed: {str(e)}")
        raise

