"""Azure OpenAI provider -- first-class Azure support.

Targets the Azure-specific endpoint:
``{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={version}``

Uses ``api-key`` header auth. Same message/tool format as OpenAI.

API configuration from ``~/.karna/credentials/azure.token.json`` with fields:
- ``endpoint``: Azure OpenAI endpoint URL
- ``api_key``: Azure API key
- ``api_version``: API version (default ``2024-06-01``)
- ``deployment``: Deployment name (default: model name)

Or environment variables:
- ``$AZURE_OPENAI_API_KEY``
- ``$AZURE_OPENAI_ENDPOINT``
- ``$AZURE_OPENAI_API_VERSION``
- ``$AZURE_OPENAI_DEPLOYMENT``

Portions adapted from cc-src (Claude Code) and hermes-agent (MIT).
See NOTICES.md for attribution.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent, ToolCall, Usage, estimate_cost
from karna.providers.base import BaseProvider, resolve_max_tokens

DEFAULT_API_VERSION = "2024-06-01"


class AzureOpenAIProvider(BaseProvider):
    def _max_output(self) -> int | None:
        """Delegate to OpenAIProvider's family table — Azure deployments
        are GPT under the hood."""
        from karna.providers.openai import OpenAIProvider

        ml = self.model.lower() if self.model else ""
        best = None
        best_len = 0
        for key, val in OpenAIProvider._OUTPUT_LIMITS.items():
            if ml.startswith(key) and len(key) > best_len:
                best_len = len(key)
                best = val
        return best

    """Azure OpenAI chat-completions provider."""

    name = "azure"
    base_url = ""  # Resolved from credentials at runtime

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
        self._api_key = cred.get("api_key") or os.environ.get("AZURE_OPENAI_API_KEY")
        self.endpoint = (cred.get("endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")).rstrip("/")
        self.api_version = cred.get("api_version") or os.environ.get("AZURE_OPENAI_API_VERSION", DEFAULT_API_VERSION)
        self.deployment = cred.get("deployment") or os.environ.get("AZURE_OPENAI_DEPLOYMENT", model)

    # ------------------------------------------------------------------ #
    #  URL and Headers
    # ------------------------------------------------------------------ #

    def _completions_url(self) -> str:
        if not self.endpoint:
            raise ValueError(
                "No Azure endpoint configured. Set $AZURE_OPENAI_ENDPOINT or "
                "add 'endpoint' to ~/.karna/credentials/azure.token.json"
            )
        return f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions?api-version={self.api_version}"

    def _headers(self) -> dict[str, str]:
        key = self._require_api_key()
        return {
            "Content-Type": "application/json",
            "api-key": key,
        }

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

    @staticmethod
    def _extract_usage(data: dict[str, Any], model: str) -> Usage:
        """Extract usage from an Azure response."""
        raw = data.get("usage", {})
        input_tokens = raw.get("prompt_tokens", 0)
        output_tokens = raw.get("completion_tokens", 0)
        # Azure uses OpenAI pricing
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

    # Azure deployments of OpenAI's o-series accept the same reasoning param.
    _REASONING_MODEL_HINTS: tuple[str, ...] = ("o1", "o3", "gpt-oss")

    def _supports_reasoning(self) -> bool:
        lowered = f"{self.model} {self.deployment}".lower()
        return any(hint in lowered for hint in self._REASONING_MODEL_HINTS)

    def _apply_thinking(self, payload: dict[str, Any], *, thinking: bool) -> None:
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
        """Non-streaming chat completion via Azure."""
        payload: dict[str, Any] = {
            "messages": self._serialize_messages(messages, system_prompt),
        }
        if tools:
            payload["tools"] = tools
        # Azure deployments inherit the underlying GPT model's cap; reuse
        # OpenAIProvider's family table via the shared resolver.
        payload["max_tokens"] = resolve_max_tokens(max_tokens, self._max_output())
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_thinking(payload, thinking=thinking)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await self._request_with_retry(
                client,
                "POST",
                self._completions_url(),
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
        """Streaming chat completion via Azure -- yields StreamEvent objects."""
        payload: dict[str, Any] = {
            "messages": self._serialize_messages(messages, system_prompt),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        # Azure deployments inherit the underlying GPT model's cap; reuse
        # OpenAIProvider's family table via the shared resolver.
        payload["max_tokens"] = resolve_max_tokens(max_tokens, self._max_output())
        if temperature is not None:
            payload["temperature"] = temperature
        self._apply_thinking(payload, thinking=thinking)

        tool_call_buffers: dict[int, dict[str, Any]] = {}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                self._completions_url(),
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
        """List available deployments.

        Azure doesn't have a standard model listing endpoint like OpenAI,
        so we return the configured deployment as the only available model.
        """
        return [
            ModelInfo(
                id=self.deployment,
                name=self.deployment,
                provider="azure",
            )
        ]
