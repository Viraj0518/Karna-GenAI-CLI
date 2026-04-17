"""OpenRouter provider — the primary backend for Karna.

Talks to ``https://openrouter.ai/api/v1`` using an httpx async client.
API key is read from ``~/.karna/credentials/openrouter.token.json``
(field ``api_key``) or the ``$OPENROUTER_API_KEY`` environment variable.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent, ToolCall
from karna.providers.base import BaseProvider


class OpenRouterProvider(BaseProvider):
    """OpenRouter chat-completions provider."""

    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"

    def __init__(self, model: str = "openrouter/auto") -> None:
        super().__init__()
        self.model = model
        self._api_key = self._resolve_key()

    # ------------------------------------------------------------------ #
    #  Key resolution
    # ------------------------------------------------------------------ #

    def _resolve_key(self) -> str | None:
        cred = self._load_credential()
        if key := cred.get("api_key"):
            return key
        return os.environ.get("OPENROUTER_API_KEY")

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Viraj0518/Karna-GenAI-CLI",
            "X-Title": "Karna",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    # ------------------------------------------------------------------ #
    #  Interface
    # ------------------------------------------------------------------ #

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Message:
        """Non-streaming chat completion."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._request_with_retry(
                client, "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            data = resp.json()

        choice = data["choices"][0]["message"]
        tool_calls: list[ToolCall] = []
        for tc in choice.get("tool_calls", []):
            tool_calls.append(
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=(
                        json.loads(tc["function"]["arguments"])
                        if isinstance(tc["function"]["arguments"], str)
                        else tc["function"]["arguments"]
                    ),
                )
            )

        # Track usage if present
        if usage_data := data.get("usage"):
            usage = self._make_usage(
                input_tokens=usage_data.get("prompt_tokens", 0),
                output_tokens=usage_data.get("completion_tokens", 0),
                model=self.model,
            )
            self._track_usage(usage)

        return Message(
            role="assistant",
            content=choice.get("content", "") or "",
            tool_calls=tool_calls,
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat completion — yields StreamEvent objects."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line.removeprefix("data: ").strip()
                    if raw == "[DONE]":
                        break
                    chunk = json.loads(raw)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    if content := delta.get("content"):
                        yield StreamEvent(type="text", text=content)

        yield StreamEvent(type="done")

    async def list_models(self) -> list[ModelInfo]:
        """Fetch available models from the OpenRouter catalogue."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base_url}/models", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
        return [
            ModelInfo(
                id=m["id"],
                name=m.get("name", m["id"]),
                provider="openrouter",
                context_window=m.get("context_length"),
                pricing=m.get("pricing"),
            )
            for m in data.get("data", [])
        ]
