"""OpenRouter provider — the primary backend for Karna.

Talks to ``https://openrouter.ai/api/v1`` using an httpx async client.
API key is read from ``~/.karna/credentials/openrouter.token.json``
(field ``api_key``) or the ``$OPENROUTER_API_KEY`` environment variable.

Supports:
- Non-streaming and streaming chat completions
- Tool use (function calling via OpenAI-compatible format)
- Model listing via ``/api/v1/models``
- Model aliases (e.g. ``gpt-oss-120b`` -> ``openai/gpt-oss-120b``)
- Cost tracking per call

Portions adapted from cc-src (Claude Code) and hermes-agent (MIT).
See NOTICES.md for attribution.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent, ToolCall, Usage
from karna.providers.base import BaseProvider

# Model aliases: short names -> full OpenRouter model IDs
MODEL_ALIASES: dict[str, str] = {
    "gpt-oss-120b": "openai/gpt-oss-120b",
    "gpt-4o": "openai/gpt-4o",
    "gpt-4o-mini": "openai/gpt-4o-mini",
    "gpt-4.1": "openai/gpt-4.1",
    "gpt-4.1-mini": "openai/gpt-4.1-mini",
    "gpt-4.1-nano": "openai/gpt-4.1-nano",
    "o3": "openai/o3",
    "o3-mini": "openai/o3-mini",
    "claude-opus-4": "anthropic/claude-opus-4-20250514",
    "claude-sonnet-4": "anthropic/claude-sonnet-4-20250514",
    "claude-3.5-sonnet": "anthropic/claude-3-5-sonnet-20241022",
    "deepseek-chat": "deepseek/deepseek-chat",
    "deepseek-reasoner": "deepseek/deepseek-reasoner",
    "gemini-2.5-pro": "google/gemini-2.5-pro",
    "gemini-2.5-flash": "google/gemini-2.5-flash",
    "llama-3.3-70b": "meta-llama/llama-3.3-70b-instruct",
}


def _resolve_alias(model: str) -> str:
    """Resolve a short alias to a full OpenRouter model ID."""
    return MODEL_ALIASES.get(model, model)


class OpenRouterProvider(BaseProvider):
    """OpenRouter chat-completions provider."""

    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        model: str = "openrouter/auto",
        *,
        max_retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(max_retries=max_retries, timeout=timeout)
        self.model = _resolve_alias(model)
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
            "X-Title": "Nellie",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    # ------------------------------------------------------------------ #
    #  Message serialization
    # ------------------------------------------------------------------ #

    @staticmethod
    def _serialize_messages(
        messages: list[Message],
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        """Convert Message objects to OpenAI-format dicts."""
        result: list[dict[str, Any]] = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            if m.tool_results:
                # Tool results are sent as separate tool-role messages
                for tr in m.tool_results:
                    result.append(
                        {
                            "role": "tool",
                            "tool_call_id": tr.tool_call_id,
                            "content": tr.content,
                        }
                    )
                continue
            result.append(msg)
        return result

    @staticmethod
    def _parse_tool_calls(raw_tool_calls: list[dict[str, Any]]) -> list[ToolCall]:
        """Parse OpenAI-format tool calls from a response."""
        tool_calls: list[ToolCall] = []
        for tc in raw_tool_calls:
            args = tc["function"]["arguments"]
            tool_calls.append(
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=json.loads(args) if isinstance(args, str) else args,
                )
            )
        return tool_calls

    @staticmethod
    def _extract_usage(data: dict[str, Any], model: str) -> Usage:
        """Extract usage from an OpenAI-format response."""
        raw = data.get("usage", {})
        input_tokens = raw.get("prompt_tokens", 0)
        output_tokens = raw.get("completion_tokens", 0)

        # OpenRouter may include cost directly
        cost: float | None = None
        if "total_cost" in raw:
            try:
                cost = float(raw["total_cost"])
            except (TypeError, ValueError):
                pass

        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    # ------------------------------------------------------------------ #
    #  Interface
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #  Thinking-mode support
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_thinking(payload: dict[str, Any], *, thinking: bool, thinking_budget: int | None) -> None:
        """Attach OpenRouter's ``reasoning`` passthrough block.

        OpenRouter routes ``reasoning`` to whichever underlying provider
        supports it (Anthropic thinking, OpenAI reasoning, Gemini thinking,
        etc.). The ``max_tokens`` sub-field becomes the reasoning budget.
        Silently no-ops when thinking is off so upstream models that don't
        support reasoning aren't affected.
        """
        if not thinking:
            return
        budget = thinking_budget if thinking_budget and thinking_budget > 0 else 10000
        payload["reasoning"] = {"enabled": True, "max_tokens": int(budget)}

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> Message:
        """Non-streaming chat completion."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(messages, system_prompt),
        }
        if tools:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_thinking(payload, thinking=thinking, thinking_budget=thinking_budget)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._request_with_retry(
                client,
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            data = resp.json()

        choice = data["choices"][0]["message"]
        tool_calls = self._parse_tool_calls(choice.get("tool_calls", []))
        usage = self._extract_usage(data, self.model)
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
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Streaming chat completion -- yields StreamEvent objects."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(messages, system_prompt),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_thinking(payload, thinking=thinking, thinking_budget=thinking_budget)

        # Track accumulated tool calls during streaming
        tool_call_buffers: dict[int, dict[str, Any]] = {}

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

                    # Text content
                    if content := delta.get("content"):
                        yield StreamEvent(type="text", text=content)

                    # OpenRouter surfaces reasoning tokens as ``delta.reasoning``
                    # when the upstream provider (Anthropic/OpenAI/Gemini)
                    # returns them. We fold them into the text stream so the
                    # renderer can display them without a dedicated event type.
                    if reasoning := delta.get("reasoning"):
                        yield StreamEvent(type="text", text=reasoning)

                    # Tool calls in streaming
                    for tc_delta in delta.get("tool_calls", []):
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_call_buffers:
                            # New tool call starting
                            tool_call_buffers[idx] = {
                                "id": tc_delta.get("id", ""),
                                "name": tc_delta.get("function", {}).get("name", ""),
                                "arguments": "",
                            }
                            yield StreamEvent(
                                type="tool_call_start",
                                tool_call=ToolCall(
                                    id=tool_call_buffers[idx]["id"],
                                    name=tool_call_buffers[idx]["name"],
                                    arguments={},
                                ),
                            )

                        # Accumulate argument fragments
                        arg_delta = tc_delta.get("function", {}).get("arguments", "")
                        if arg_delta:
                            tool_call_buffers[idx]["arguments"] += arg_delta
                            yield StreamEvent(type="tool_call_delta", text=arg_delta)

                    # Usage in the final chunk
                    if "usage" in chunk and chunk["usage"]:
                        usage = self._extract_usage(chunk, self.model)
                        self._track_usage(usage)

        # Emit tool_call_end events for completed tool calls
        for buf in tool_call_buffers.values():
            try:
                args = json.loads(buf["arguments"]) if buf["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            yield StreamEvent(
                type="tool_call_end",
                tool_call=ToolCall(
                    id=buf["id"],
                    name=buf["name"],
                    arguments=args,
                ),
            )

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
