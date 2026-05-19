from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    message: Any
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def _get_client():
    global _client
    try:
        return _client
    except NameError:
        from openai import OpenAI
        _client = OpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY or "missing-key",
        )
        return _client


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str | dict | None = None,
    temperature: float = 0.2,
) -> LLMResponse:
    client = _get_client()
    kwargs: dict = {
        "model": settings.LLM_MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

    response = client.chat.completions.create(**kwargs)
    choice = response.choices[0]
    usage = getattr(response, "usage", None)
    return LLMResponse(
        message=choice.message,
        finish_reason=choice.finish_reason,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        total_tokens=getattr(usage, "total_tokens", 0) or 0,
    )


def message_to_dict(message: Any) -> dict:
    """Convert an OpenAI message object back to the JSON shape the API expects
    when we feed conversation history back in."""
    out: dict = {"role": message.role}
    if getattr(message, "content", None) is not None:
        out["content"] = message.content
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    return out

