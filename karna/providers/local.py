"""Local / self-hosted provider -- OpenAI-compatible endpoint.

Targets OpenAI-compatible servers: llama.cpp, vLLM, Ollama, LM Studio.
Configurable ``base_url`` (default ``http://localhost:8080/v1``).
No auth required (optional key support).

Configuration from ``~/.karna/credentials/local.token.json`` with fields:
- ``base_url``: Server URL (default ``http://localhost:8080/v1``)
- ``api_key``: Optional API key

Or environment variables:
- ``$LOCAL_API_KEY``
- ``$LOCAL_BASE_URL``

Portions adapted from cc-src (Claude Code) and hermes-agent (MIT).
See NOTICES.md for attribution.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent, ToolCall, Usage
from karna.providers.base import BaseProvider, resolve_max_tokens

DEFAULT_BASE_URL = "http://localhost:8080/v1"


class LocalProvider(BaseProvider):
    """Local OpenAI-compatible provider."""

    name = "local"
    base_url = DEFAULT_BASE_URL

    def __init__(
        self,
        model: str = "default",
        *,
        base_url: str | None = None,
        max_retries: int = 2,
        timeout: float = 300.0,  # Local models can be slow
    ) -> None:
        super().__init__(max_retries=max_retries, timeout=timeout)
        self.model = model

        cred = self._load_credential()
        self.base_url = (base_url or cred.get("base_url") or os.environ.get("LOCAL_BASE_URL", DEFAULT_BASE_URL)).rstrip(
            "/"
        )
        self._api_key = cred.get("api_key") or os.environ.get("LOCAL_API_KEY")
        # Resolved from ``/v1/models`` on first list_models call (vLLM
        # reports ``max_model_len``; LM Studio reports ``context_length``).
        self._max_output: int | None = None

    # ------------------------------------------------------------------ #
    #  Headers
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    # ------------------------------------------------------------------ #
    #  Message serialization (same as OpenAI)
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
        """Parse OpenAI-format tool calls."""
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

    # ------------------------------------------------------------------ #
    #  Interface
    # ------------------------------------------------------------------ #

    @staticmethod
    def _apply_thinking(payload: dict[str, Any], *, thinking: bool, thinking_budget: int | None) -> None:
        """Pass ``reasoning`` hints through to the local endpoint.

        Many OpenAI-compatible local servers (vLLM, LM Studio, llama.cpp with
        the reasoning hint) accept a ``reasoning`` passthrough. Servers that
        don't recognise it simply ignore the extra field, so this is safe as
        an always-off default.
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
        """Non-streaming chat completion against local server."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(messages, system_prompt),
        }
        if tools:
            payload["tools"] = tools
        # Local servers (llama.cpp, vLLM, LM Studio) vary wildly on default
        # cap — some use 128, some use ctx_len. Always send the resolved
        # value so the caller's intent is respected.
        payload["max_tokens"] = resolve_max_tokens(max_tokens, self._max_output)
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

        # Local servers may or may not report usage
        raw_usage = data.get("usage", {})
        usage = Usage(
            input_tokens=raw_usage.get("prompt_tokens", 0),
            output_tokens=raw_usage.get("completion_tokens", 0),
            cost_usd=None,  # Local = no cost
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
        # Local servers (llama.cpp, vLLM, LM Studio) vary wildly on default
        # cap — some use 128, some use ctx_len. Always send the resolved
        # value so the caller's intent is respected.
        payload["max_tokens"] = resolve_max_tokens(max_tokens, self._max_output)
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_thinking(payload, thinking=thinking, thinking_budget=thinking_budget)

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
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue

                    delta = choices[0].get("delta", {})

                    # Text content
                    if content := delta.get("content"):
                        yield StreamEvent(type="text", text=content)

                    # Tool calls
                    for tc_delta in delta.get("tool_calls", []):
                        idx = tc_delta.get("index", 0)
                        if idx not in tool_call_buffers:
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
                        arg_delta = tc_delta.get("function", {}).get("arguments", "")
                        if arg_delta:
                            tool_call_buffers[idx]["arguments"] += arg_delta
                            yield StreamEvent(type="tool_call_delta", text=arg_delta)

        # Emit tool_call_end events
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
        """List models available on the local server."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        return [
            ModelInfo(
                id=m.get("id", "unknown"),
                name=m.get("id", "unknown"),
                provider="local",
                context_window=m.get("context_length") or m.get("max_model_len"),
            )
            for m in data.get("data", [])
        ]
