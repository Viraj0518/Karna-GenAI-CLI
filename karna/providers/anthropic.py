"""Anthropic provider -- native Messages API client.

Targets ``https://api.anthropic.com/v1/messages`` with the
``x-api-key`` + ``anthropic-version`` header pattern.

Supports:
- Non-streaming and streaming completions
- Tool use via Anthropic's native format (not OpenAI-compatible)
- System prompt as top-level parameter (not in messages)
- SSE streaming with message_start, content_block_delta, message_delta
- Cost tracking per call

Portions adapted from cc-src (Claude Code) and hermes-agent (MIT).
See NOTICES.md for attribution.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent, ToolCall, Usage, estimate_cost
from karna.providers.base import BaseProvider
from karna.providers.caching import PromptCache

# Anthropic API version
ANTHROPIC_VERSION = "2023-06-01"

# Default max output tokens per model family (ported from hermes-agent)
_OUTPUT_LIMITS: dict[str, int] = {
    "claude-opus-4": 128_000,
    "claude-sonnet-4": 64_000,
    "claude-3-5-sonnet": 8_192,
    "claude-3-5-haiku": 8_192,
    "claude-3-opus": 4_096,
    "claude-3-haiku": 4_096,
}
_DEFAULT_OUTPUT_LIMIT = 16_384


def _get_max_output_tokens(model: str) -> int:
    """Determine the max output tokens for an Anthropic model.

    Uses longest-prefix matching against the output limits table.
    """
    model_lower = model.lower().replace(".", "-")
    best_key = ""
    best_val = _DEFAULT_OUTPUT_LIMIT
    for key, val in _OUTPUT_LIMITS.items():
        if key in model_lower and len(key) > len(best_key):
            best_key = key
            best_val = val
    return best_val


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API provider."""

    name = "anthropic"
    base_url = "https://api.anthropic.com/v1"

    def __init__(
        self,
        model: str = "claude-sonnet-4-20250514",
        *,
        max_retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(max_retries=max_retries, timeout=timeout)
        self.model = model
        self._cache = PromptCache()
        cred = self._load_credential()
        self._api_key = cred.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")

    # ------------------------------------------------------------------ #
    #  Headers
    # ------------------------------------------------------------------ #

    def _headers(self) -> dict[str, str]:
        key = self._require_api_key()
        return {
            "Content-Type": "application/json",
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    # ------------------------------------------------------------------ #
    #  Message serialization (Anthropic native format)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _serialize_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Message objects to Anthropic's message format.

        Anthropic requires strict role alternation (user/assistant).
        System messages are handled separately as a top-level param.
        Tool results go into user messages with tool_result blocks.
        """
        result: list[dict[str, Any]] = []

        for m in messages:
            if m.role == "system":
                continue

            if m.role == "assistant":
                content: list[dict[str, Any]] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                for tc in m.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": tc.arguments,
                        }
                    )
                result.append(
                    {
                        "role": "assistant",
                        "content": content or [{"type": "text", "text": ""}],
                    }
                )
                continue

            if m.tool_results:
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": tr.content or "(no output)",
                        **({"is_error": True} if tr.is_error else {}),
                    }
                    for tr in m.tool_results
                ]
                if result and result[-1]["role"] == "user" and isinstance(result[-1]["content"], list):
                    result[-1]["content"].extend(blocks)
                else:
                    result.append({"role": "user", "content": blocks})
                continue

            # Regular user message
            result.append({"role": "user", "content": m.content or "(empty)"})

        return result

    @staticmethod
    def _serialize_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-format tool definitions to Anthropic format."""
        anthropic_tools: list[dict[str, Any]] = []
        for t in tools:
            fn = t.get("function", {})
            anthropic_tools.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return anthropic_tools

    # ------------------------------------------------------------------ #
    #  Response parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> tuple[str, list[ToolCall]]:
        """Parse content and tool calls from an Anthropic response."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block["id"],
                        name=block["name"],
                        arguments=block.get("input", {}),
                    )
                )

        return "\n".join(text_parts), tool_calls

    @staticmethod
    def _extract_usage(data: dict[str, Any], model: str) -> Usage:
        """Extract usage from an Anthropic response.

        Adjusts cost estimate for cached tokens:
        - cache_read tokens are billed at 10% of the normal input rate
        - cache_creation tokens are billed at 125% of the normal input rate
        - base input_tokens excludes cached tokens (already counted separately)
        """
        raw = data.get("usage", {})
        input_tokens = raw.get("input_tokens", 0)
        output_tokens = raw.get("output_tokens", 0)
        cache_read = raw.get("cache_read_input_tokens", 0)
        cache_write = raw.get("cache_creation_input_tokens", 0)

        # Base cost for non-cached input + output
        cost = estimate_cost("anthropic", model, input_tokens, output_tokens)

        # Adjust for cached tokens if we have pricing data
        if cost is not None and (cache_read or cache_write):
            # Get per-token input cost from the base estimate
            base_input_cost = estimate_cost("anthropic", model, 1_000_000, 0)
            if base_input_cost is not None and base_input_cost > 0:
                per_token = base_input_cost / 1_000_000
                # cache reads at 10%, cache writes at 125%
                cost += cache_read * per_token * 0.1
                cost += cache_write * per_token * 1.25

        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost,
        )

    # ------------------------------------------------------------------ #
    #  Interface
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #  Thinking-mode support
    # ------------------------------------------------------------------ #

    # Model substrings that support Anthropic's extended-thinking parameter.
    # Kept permissive — newer claude-4 / 4-5 / opus-4-7 families all support it.
    _THINKING_SUPPORTED_SUBSTRINGS: tuple[str, ...] = (
        "claude-sonnet-4",
        "claude-opus-4",
        "claude-haiku-4",
    )

    def _supports_thinking(self) -> bool:
        lowered = self.model.lower().replace(".", "-")
        return any(sub in lowered for sub in self._THINKING_SUPPORTED_SUBSTRINGS)

    def _apply_thinking(self, payload: dict[str, Any], *, thinking: bool, thinking_budget: int | None) -> None:
        """Attach Anthropic's ``thinking`` block to *payload* when enabled.

        No-op when thinking is off or the model doesn't support it. Extended
        thinking is incompatible with ``temperature`` overrides, so we drop
        any caller-supplied temperature once thinking is turned on.
        """
        if not thinking or not self._supports_thinking():
            return
        budget = thinking_budget if thinking_budget and thinking_budget > 0 else 10000
        payload["thinking"] = {"type": "enabled", "budget_tokens": int(budget)}
        payload.pop("temperature", None)

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
        """Non-streaming Messages API call."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(messages),
            "max_tokens": max_tokens or _get_max_output_tokens(self.model),
        }
        if system_prompt:
            # Use structured system with cache_control for prompt caching
            payload["system"] = PromptCache.prepare_anthropic_system(system_prompt)
        if tools:
            serialized = self._serialize_tools(tools)
            # Mark last tool with cache_control for prompt caching
            payload["tools"] = PromptCache.mark_anthropic_tools(serialized)
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_thinking(payload, thinking=thinking, thinking_budget=thinking_budget)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._request_with_retry(
                client,
                "POST",
                f"{self.base_url}/messages",
                headers=self._headers(),
                json=payload,
            )
            data = resp.json()

        text, tool_calls = self._parse_response(data)
        usage = self._extract_usage(data, self.model)
        self._track_usage(usage)
        self._cache.record_usage(usage.cache_read_tokens, usage.cache_write_tokens)

        return Message(
            role="assistant",
            content=text,
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
        """Streaming Messages API -- yields StreamEvent objects.

        Parses Anthropic's SSE events:
        - message_start: message metadata
        - content_block_start: new text/tool_use block
        - content_block_delta: incremental content
        - content_block_stop: block complete
        - message_delta: final stop reason + usage
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._serialize_messages(messages),
            "max_tokens": max_tokens or _get_max_output_tokens(self.model),
            "stream": True,
        }
        if system_prompt:
            # Use structured system with cache_control for prompt caching
            payload["system"] = PromptCache.prepare_anthropic_system(system_prompt)
        if tools:
            serialized = self._serialize_tools(tools)
            # Mark last tool with cache_control for prompt caching
            payload["tools"] = PromptCache.mark_anthropic_tools(serialized)
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_thinking(payload, thinking=thinking, thinking_budget=thinking_budget)

        # State for tracking in-flight tool calls across SSE events.
        # Anthropic streams tool arguments incrementally via input_json_delta
        # events, so we accumulate fragments until content_block_stop.
        current_tool: dict[str, Any] | None = None
        # Accumulate usage from message_start (input tokens) and
        # message_delta (output tokens) — merged at end.
        usage_data: dict[str, Any] = {}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/messages",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()

                # Anthropic SSE format: "event: <type>\ndata: <json>\n\n"
                # We track event_type across lines since event and data
                # arrive on separate lines.
                event_type: str | None = None
                async for line in resp.aiter_lines():
                    # Parse the "event:" line to know what the next data line means
                    if line.startswith("event: "):
                        event_type = line.removeprefix("event: ").strip()
                        continue

                    # Skip non-data lines (blank lines, comments)
                    if not line.startswith("data: "):
                        continue

                    raw = line.removeprefix("data: ").strip()
                    if not raw:
                        continue

                    data = json.loads(raw)

                    # --- message_start: contains input token usage ---
                    if event_type == "message_start":
                        msg = data.get("message", {})
                        usage_data = msg.get("usage", {})

                    # --- content_block_start: new text or tool_use block ---
                    elif event_type == "content_block_start":
                        block = data.get("content_block", {})
                        if block.get("type") == "tool_use":
                            # Begin accumulating a new tool call
                            current_tool = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "arguments": "",
                            }
                            yield StreamEvent(
                                type="tool_call_start",
                                tool_call=ToolCall(
                                    id=current_tool["id"],
                                    name=current_tool["name"],
                                    arguments={},
                                ),
                            )

                    # --- content_block_delta: incremental content ---
                    elif event_type == "content_block_delta":
                        delta = data.get("delta", {})
                        if delta.get("type") == "text_delta":
                            # Regular text token — stream to caller immediately
                            yield StreamEvent(type="text", text=delta.get("text", ""))
                        elif delta.get("type") in ("thinking_delta", "signature_delta"):
                            # Extended-thinking reasoning token — surfaced as a
                            # dedicated "thinking" event so the TUI renderer can
                            # stream it live with distinct styling.
                            txt = delta.get("thinking") or delta.get("text") or ""
                            if txt:
                                yield StreamEvent(type="thinking", text=txt)
                        elif delta.get("type") == "input_json_delta":
                            # Tool argument fragment — accumulate until block stops
                            partial = delta.get("partial_json", "")
                            if current_tool is not None and partial:
                                current_tool["arguments"] += partial
                                yield StreamEvent(type="tool_call_delta", text=partial)

                    # --- content_block_stop: block complete ---
                    elif event_type == "content_block_stop":
                        if current_tool is not None:
                            # Parse accumulated JSON arguments into a dict
                            try:
                                args = json.loads(current_tool["arguments"]) if current_tool["arguments"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield StreamEvent(
                                type="tool_call_end",
                                tool_call=ToolCall(
                                    id=current_tool["id"],
                                    name=current_tool["name"],
                                    arguments=args,
                                ),
                            )
                            # Reset — ready for the next tool call block
                            current_tool = None

                    # --- message_delta: final stop reason + output token usage ---
                    elif event_type == "message_delta":
                        delta_usage = data.get("usage", {})
                        usage_data.update(delta_usage)

                    # Reset event_type so stale values don't affect next data line
                    event_type = None

        # Emit final usage event with cache-adjusted cost calculation.
        # _extract_usage handles the Anthropic-specific pricing for
        # cache reads (10% of input rate) and cache writes (125%).
        usage = self._extract_usage({"usage": usage_data}, self.model)
        self._track_usage(usage)
        self._cache.record_usage(usage.cache_read_tokens, usage.cache_write_tokens)
        yield StreamEvent(type="done", usage=usage)

    async def list_models(self) -> list[ModelInfo]:
        """List available models via the Anthropic /v1/models endpoint."""
        key = self._require_api_key()
        headers = {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base_url}/models?limit=1000",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        return [
            ModelInfo(
                id=m["id"],
                name=m.get("display_name", m["id"]),
                provider="anthropic",
                context_window=m.get("max_input_tokens"),
            )
            for m in data.get("data", [])
        ]
