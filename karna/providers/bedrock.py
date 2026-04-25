"""AWS Bedrock provider.

Supports Claude, Llama, and Titan foundation models hosted on AWS Bedrock.

Uses ``boto3`` (optional dep, extras ``bedrock``) for both credential
resolution and the actual invocations — signing SigV4 by hand adds a lot of
code for little gain when boto3 already ships with essentially every AWS
install anyway. ``boto3`` is imported lazily so Karna works without it.

Credential resolution uses the standard AWS SDK chain:
- Explicit ``access_key_id`` / ``secret_access_key`` in the config file
  ``~/.karna/credentials/bedrock.token.json`` OR passed to ``__init__``
- Otherwise: env vars, ``~/.aws/credentials``, IAM role — whatever boto3
  resolves on its own.

Bedrock calls are synchronous (boto3 isn't async), so we run them on the
default executor via ``asyncio.to_thread`` to keep the ``BaseProvider``
async contract. Streaming iterates chunks from the EventStream and yields
``StreamEvent``s.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Iterator

from karna.models import Message, ModelInfo, StreamEvent, ToolCall, Usage
from karna.providers.base import BaseProvider, resolve_max_tokens

_BOTO3_IMPORT_ERROR = (
    "The 'boto3' package is required for the Bedrock provider. "
    "Install it with:  pip install 'karna[bedrock]'  (or pip install boto3)"
)

# Hardcoded fallback model list used when ``list_foundation_models`` is
# unavailable (no boto3) or the caller lacks ``ListFoundationModels`` perms.
_FALLBACK_MODELS: list[tuple[str, int | None]] = [
    # (model_id, context_window)
    ("anthropic.claude-opus-4-20250514-v1:0", 200_000),
    ("anthropic.claude-sonnet-4-20250514-v1:0", 200_000),
    ("anthropic.claude-3-5-sonnet-20241022-v2:0", 200_000),
    ("anthropic.claude-3-5-haiku-20241022-v1:0", 200_000),
    ("meta.llama3-1-70b-instruct-v1:0", 128_000),
    ("meta.llama3-1-8b-instruct-v1:0", 128_000),
    ("amazon.titan-text-premier-v1:0", 32_000),
    ("amazon.titan-text-express-v1", 8_000),
]


def _load_boto3() -> Any:
    """Import ``boto3`` lazily. Raises a helpful ImportError otherwise."""
    try:
        import boto3  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised via test
        raise ImportError(_BOTO3_IMPORT_ERROR) from exc
    return boto3


def _is_anthropic_model(model_id: str) -> bool:
    return model_id.startswith("anthropic.")


def _is_llama_model(model_id: str) -> bool:
    return model_id.startswith("meta.llama") or model_id.startswith("meta.")


def _is_titan_model(model_id: str) -> bool:
    return model_id.startswith("amazon.titan")


class BedrockProvider(BaseProvider):
    """AWS Bedrock provider."""

    name = "bedrock"
    # Bedrock endpoint varies by region; set dynamically, skip URL validation.
    base_url = ""

    def __init__(
        self,
        model: str = "anthropic.claude-sonnet-4-20250514-v1:0",
        *,
        region: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        session_token: str | None = None,
        max_retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(max_retries=max_retries, timeout=timeout)
        self.model = model

        cred = self._load_credential()
        self.region = (
            region
            or cred.get("region")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        self._access_key_id = access_key_id or cred.get("access_key_id")
        self._secret_access_key = secret_access_key or cred.get("secret_access_key")
        self._session_token = session_token or cred.get("session_token")

        # Lazy boto3 clients; created on first call.
        self._runtime_client: Any = None
        self._control_client: Any = None

    # ------------------------------------------------------------------ #
    #  boto3 client helpers
    # ------------------------------------------------------------------ #

    def _make_boto3_session(self) -> Any:
        boto3 = _load_boto3()
        kwargs: dict[str, Any] = {"region_name": self.region}
        if self._access_key_id and self._secret_access_key:
            kwargs["aws_access_key_id"] = self._access_key_id
            kwargs["aws_secret_access_key"] = self._secret_access_key
            if self._session_token:
                kwargs["aws_session_token"] = self._session_token
        return boto3.session.Session(**kwargs)

    def _get_runtime_client(self) -> Any:
        if self._runtime_client is None:
            self._runtime_client = self._make_boto3_session().client("bedrock-runtime")
        return self._runtime_client

    def _get_control_client(self) -> Any:
        if self._control_client is None:
            self._control_client = self._make_boto3_session().client("bedrock")
        return self._control_client

    # ------------------------------------------------------------------ #
    #  Per-model payload shaping
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_anthropic_payload(
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        system_prompt: str | None,
        max_tokens: int | None,
        temperature: float | None,
        *,
        thinking: bool = False,
        thinking_budget: int | None = None,
        model_max_tokens: int | None = None,
    ) -> dict[str, Any]:
        # Bedrock Claude uses the Messages API shape with
        # ``anthropic_version`` instead of a server-side version header.
        anth_messages: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            role = m.role if m.role in ("user", "assistant") else "user"
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
            for tr in m.tool_results:
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": tr.content or "(no output)",
                    }
                )
            anth_messages.append({"role": role, "content": content or [{"type": "text", "text": ""}]})

        payload: dict[str, Any] = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": anth_messages,
            # Use the shared resolver so a caller asking for 64K on Opus-4
            # isn't silently capped at 4K, and un-requested calls get the
            # full model headroom up to the 32K soft ceiling.
            "max_tokens": resolve_max_tokens(max_tokens, model_max_tokens),
        }
        if system_prompt:
            payload["system"] = system_prompt
        if tools:
            payload["tools"] = [
                {
                    "name": t.get("function", {}).get("name", ""),
                    "description": t.get("function", {}).get("description", ""),
                    "input_schema": t.get("function", {}).get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
        if temperature is not None:
            payload["temperature"] = temperature
        if thinking:
            budget = thinking_budget if thinking_budget and thinking_budget > 0 else 10000
            payload["thinking"] = {"type": "enabled", "budget_tokens": int(budget)}
            # Extended thinking is incompatible with a user-set temperature.
            payload.pop("temperature", None)
        return payload

    @staticmethod
    def _build_llama_payload(
        messages: list[Message],
        system_prompt: str | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        # Llama on Bedrock uses a flat ``prompt`` string.
        parts: list[str] = []
        if system_prompt:
            parts.append(f"<<SYS>>\n{system_prompt}\n<</SYS>>")
        for m in messages:
            if m.role == "system":
                continue
            tag = "User" if m.role == "user" else "Assistant"
            parts.append(f"{tag}: {m.content}")
        parts.append("Assistant:")
        payload: dict[str, Any] = {"prompt": "\n".join(parts)}
        if max_tokens is not None:
            payload["max_gen_len"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    @staticmethod
    def _build_titan_payload(
        messages: list[Message],
        system_prompt: str | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        parts: list[str] = []
        if system_prompt:
            parts.append(system_prompt)
        for m in messages:
            if m.role == "system":
                continue
            parts.append(m.content)
        payload: dict[str, Any] = {"inputText": "\n".join(parts)}
        text_cfg: dict[str, Any] = {}
        if max_tokens is not None:
            text_cfg["maxTokenCount"] = max_tokens
        if temperature is not None:
            text_cfg["temperature"] = temperature
        if text_cfg:
            payload["textGenerationConfig"] = text_cfg
        return payload

    def _build_payload(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        system_prompt: str | None,
        max_tokens: int | None,
        temperature: float | None,
        *,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> dict[str, Any]:
        if _is_anthropic_model(self.model):
            return self._build_anthropic_payload(
                messages,
                tools,
                system_prompt,
                max_tokens,
                temperature,
                thinking=thinking,
                thinking_budget=thinking_budget,
                model_max_tokens=self._get_model_max_tokens(),
            )
        if _is_llama_model(self.model):
            # Llama/Titan don't expose thinking knobs on Bedrock — silently ignore.
            return self._build_llama_payload(messages, system_prompt, max_tokens, temperature)
        if _is_titan_model(self.model):
            return self._build_titan_payload(messages, system_prompt, max_tokens, temperature)
        # Unknown family — best-effort fall through as Anthropic shape.
        return self._build_anthropic_payload(
            messages,
            tools,
            system_prompt,
            max_tokens,
            temperature,
            model_max_tokens=self._get_model_max_tokens(),
            thinking=thinking,
            thinking_budget=thinking_budget,
        )

    def _get_model_max_tokens(self) -> int | None:
        """Look up the model's max output; falls back to the Anthropic
        provider's prefix table for Claude models on Bedrock."""
        try:
            from karna.providers.anthropic import _get_max_output_tokens
        except ImportError:
            return None
        if _is_anthropic_model(self.model):
            return _get_max_output_tokens(self.model)
        # Llama/Titan have their own caps; the resolver will fall back to
        # the conservative default when this returns None.
        return None

    # ------------------------------------------------------------------ #
    #  Response parsing
    # ------------------------------------------------------------------ #

    def _parse_response(self, data: dict[str, Any]) -> tuple[str, list[ToolCall], Usage]:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage = Usage()

        if _is_anthropic_model(self.model):
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            arguments=block.get("input", {}) or {},
                        )
                    )
            u = data.get("usage", {})
            usage = Usage(
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
            )
        elif _is_llama_model(self.model):
            text_parts.append(data.get("generation", ""))
            usage = Usage(
                input_tokens=data.get("prompt_token_count", 0),
                output_tokens=data.get("generation_token_count", 0),
            )
        elif _is_titan_model(self.model):
            results = data.get("results", [])
            for r in results:
                text_parts.append(r.get("outputText", ""))
            usage = Usage(
                input_tokens=data.get("inputTextTokenCount", 0),
                output_tokens=sum(r.get("tokenCount", 0) for r in results),
            )
        return "".join(text_parts), tool_calls, usage

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
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> Message:
        payload = self._build_payload(
            messages,
            tools,
            system_prompt,
            max_tokens,
            temperature,
            thinking=thinking,
            thinking_budget=thinking_budget,
        )
        body = json.dumps(payload).encode("utf-8")

        def _invoke() -> dict[str, Any]:
            client = self._get_runtime_client()
            resp = client.invoke_model(
                modelId=self.model,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            raw = resp["body"].read()
            return json.loads(raw)

        data = await asyncio.to_thread(_invoke)
        text, tool_calls, usage = self._parse_response(data)
        self._track_usage(usage)
        return Message(role="assistant", content=text, tool_calls=tool_calls)

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
        payload = self._build_payload(
            messages,
            tools,
            system_prompt,
            max_tokens,
            temperature,
            thinking=thinking,
            thinking_budget=thinking_budget,
        )
        body = json.dumps(payload).encode("utf-8")

        def _invoke_stream() -> Iterator[dict[str, Any]]:
            client = self._get_runtime_client()
            resp = client.invoke_model_with_response_stream(
                modelId=self.model,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            for event in resp["body"]:
                chunk = event.get("chunk", {})
                raw = chunk.get("bytes")
                if not raw:
                    continue
                yield json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)

        # Drain the stream in a worker thread — boto3 EventStream is sync.
        # We collect all chunks up-front since bridging sync iterators to
        # async iterators is painful and the chunks themselves are small.
        chunks: list[dict[str, Any]] = await asyncio.to_thread(lambda: list(_invoke_stream()))

        cumulative = Usage()
        for chunk in chunks:
            if _is_anthropic_model(self.model):
                async for ev in self._iter_anthropic_chunk(chunk, cumulative):
                    yield ev
            elif _is_llama_model(self.model):
                text = chunk.get("generation", "")
                if text:
                    yield StreamEvent(type="text", text=text)
                if "generation_token_count" in chunk:
                    cumulative.output_tokens = chunk["generation_token_count"]
                if "prompt_token_count" in chunk:
                    cumulative.input_tokens = chunk["prompt_token_count"]
            elif _is_titan_model(self.model):
                text = chunk.get("outputText", "")
                if text:
                    yield StreamEvent(type="text", text=text)
                if "inputTextTokenCount" in chunk:
                    cumulative.input_tokens = chunk["inputTextTokenCount"]
                if "totalOutputTextTokenCount" in chunk:
                    cumulative.output_tokens = chunk["totalOutputTextTokenCount"]

        self._track_usage(cumulative)
        yield StreamEvent(type="done", usage=cumulative)

    async def _iter_anthropic_chunk(self, chunk: dict[str, Any], cumulative: Usage) -> AsyncIterator[StreamEvent]:
        """Parse one Bedrock/Anthropic streaming chunk into StreamEvents."""
        ctype = chunk.get("type")
        if ctype == "content_block_delta":
            delta = chunk.get("delta", {})
            if delta.get("type") == "text_delta":
                yield StreamEvent(type="text", text=delta.get("text", ""))
            elif delta.get("type") in ("thinking_delta", "signature_delta"):
                # Surface reasoning tokens as a dedicated "thinking" event
                # so the TUI can stream them live with distinct styling.
                txt = delta.get("thinking") or delta.get("text") or ""
                if txt:
                    yield StreamEvent(type="thinking", text=txt)
        elif ctype == "message_start":
            u = chunk.get("message", {}).get("usage", {})
            cumulative.input_tokens = u.get("input_tokens", cumulative.input_tokens)
        elif ctype == "message_delta":
            u = chunk.get("usage", {})
            if "output_tokens" in u:
                cumulative.output_tokens = u["output_tokens"]

    async def list_models(self) -> list[ModelInfo]:
        fallback = [
            ModelInfo(id=mid, name=mid, provider="bedrock", context_window=ctx) for (mid, ctx) in _FALLBACK_MODELS
        ]
        try:

            def _list() -> list[dict[str, Any]]:
                client = self._get_control_client()
                resp = client.list_foundation_models()
                return resp.get("modelSummaries", [])

            summaries = await asyncio.to_thread(_list)
        except ImportError:
            # boto3 not installed — return only the hardcoded list.
            return fallback
        except Exception:
            return fallback

        models: list[ModelInfo] = []
        for s in summaries:
            mid = s.get("modelId")
            if not mid:
                continue
            models.append(
                ModelInfo(
                    id=mid,
                    name=s.get("modelName", mid),
                    provider="bedrock",
                )
            )
        return models or fallback
