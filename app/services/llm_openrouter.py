"""OpenRouter MiniMax-M2 client wrapper."""

from __future__ import annotations

from typing import Dict, Iterable, List

from openai import OpenAI

from ..config import settings


_client = OpenAI(base_url=settings.openrouter_base_url, api_key=settings.openrouter_api_key)


def agent_chat(messages: List[Dict[str, str]], stream: bool = True) -> Iterable[str] | str:
    params: Dict[str, object] = {"model": settings.openrouter_agent_model, "messages": messages}

    if stream:
        response = _client.chat.completions.create(stream=True, **params)
        for chunk in response:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
        return

    response = _client.chat.completions.create(**params)
    return response.choices[0].message.content

