"""OpenAI provider -- standard chat completions API.

Uses httpx async to ``https://api.openai.com/v1``.
Supports:
- Non-streaming and streaming chat completions
- Tool use (function calling)
- Streaming SSE
- Cost tracking per call

API key from ``~/.karna/credentials/openai.token.json`` or ``$OPENAI_API_KEY``.

Portions adapted from upstream (upstream reference) and hermes-agent (MIT).
See NOTICES.md for attribution.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent, ToolCall, Usage, estimate_cost
from karna.providers.base import BaseProvider, lookup_model_max_output, resolve_max_tokens


class OpenAIProvider(BaseProvider):
    """OpenAI chat-completions provider."""

    name = "openai"
    base_url = "https://api.openai.com/v1"

    # Per-family max output caps. Longest-prefix match; unknown → None
    # (resolver falls back to the conservative default).
    _OUTPUT_LIMITS: dict[str, int] = {
        "o1": 100_000,
        "o3": 100_000,
        "gpt-4o": 16_384,
        "gpt-4-turbo": 4_096,
        "gpt-4": 8_192,
        "gpt-3.5": 4_096,
    }

    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        max_retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(max_retries=max_retries, timeout=timeout)
        self.model = model
        cred = self._load_credential()
        self._api_key = cred.get("api_key") or os.environ.get("OPENAI_API_KEY")

    def _max_output(self) -> int | None:
        """Resolve the current model's max output cap.

        Canonical registry wins when present (beta's PR #49 — 1,359 models
        with per-model ``max_output``). Falls back to the local prefix
        table so this provider keeps working even if the registry file is
        missing or hasn't been loaded yet.
        """
        cap = lookup_model_max_output("openai", self.model)
        if cap is not None:
            return cap
        ml = self.model.lower()
        best_key = ""
        best_val: int | None = None
        for key, val in self._OUTPUT_LIMITS.items():
            if ml.startswith(key) and len(key) > len(best_key):
                best_key = key
                best_val = val
        return best_val

    # ------------------------------------------------------------------ #
    #  Headers
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        key = self._require_api_key()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }

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

    @staticmethod
    def _extract_usage(data: dict[str, Any], model: str) -> Usage:
        """Extract usage from an OpenAI response."""
        raw = data.get("usage", {})
        input_tokens = raw.get("prompt_tokens", 0)
        output_tokens = raw.get("completion_tokens", 0)
        cost = estimate_cost("openai", model, input_tokens, output_tokens)

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

    # Model substrings that accept OpenAI's ``reasoning_effort`` param.
    _REASONING_MODEL_HINTS: tuple[str, ...] = (
        "o1",
        "o3",
        "gpt-oss",
    )

    def _supports_reasoning(self) -> bool:
        lowered = self.model.lower()
        return any(hint in lowered for hint in self._REASONING_MODEL_HINTS)

    def _apply_thinking(self, payload: dict[str, Any], *, thinking: bool) -> None:
        """Attach OpenAI's ``reasoning_effort`` when thinking is on.

        OpenAI doesn't expose a raw budget, so we map the boolean to
        ``reasoning_effort="high"``. Silently no-ops for non-reasoning models.
        """
        if thinking and self._supports_reasoning():
            payload["reasoning_effort"] = "high"

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
        # Always send a resolved cap so un-specified calls still get the
        # full headroom the model supports (up to the 32K soft ceiling).
        payload["max_tokens"] = resolve_max_tokens(max_tokens, self._max_output())
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_thinking(payload, thinking=thinking)

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
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        # Always send a resolved cap so un-specified calls still get the
        # full headroom the model supports (up to the 32K soft ceiling).
        payload["max_tokens"] = resolve_max_tokens(max_tokens, self._max_output())
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_thinking(payload, thinking=thinking)

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

                    # Handle choices
                    choices = chunk.get("choices", [])
                    if choices:
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

                    # Usage in final chunk
                    if "usage" in chunk and chunk["usage"]:
                        usage = self._extract_usage(chunk, self.model)
                        self._track_usage(usage)

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
        """List available models via the OpenAI /v1/models endpoint."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/models",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        return [
            ModelInfo(
                id=m["id"],
                name=m.get("id", ""),
                provider="openai",
            )
            for m in data.get("data", [])
        ]
