"""Google Vertex AI provider.

Supports Gemini 2.5 models, Anthropic models on Vertex, and third-party
model garden entries via the REST ``generateContent`` endpoint.

Auth uses Application Default Credentials (ADC) through the lightweight
``google-auth`` library. The heavier ``google-cloud-aiplatform`` SDK is
intentionally NOT pulled in. ``google-auth`` is an optional dependency
(extras: ``vertex``); it is imported lazily so the rest of Karna still
works without it.

Config file: ``~/.karna/credentials/vertex.token.json``::

    {"project_id": "my-project", "region": "us-central1"}

Env overrides: ``GOOGLE_CLOUD_PROJECT``, ``GOOGLE_CLOUD_REGION``,
``GOOGLE_APPLICATION_CREDENTIALS`` (service-account JSON path).

REST endpoint shape:
    https://{region}-aiplatform.googleapis.com/v1/projects/{project}/
        locations/{region}/publishers/google/models/{model}:generateContent
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent, ToolCall, Usage
from karna.providers.base import BaseProvider, lookup_model_max_output, resolve_max_tokens

_GOOGLE_AUTH_IMPORT_ERROR = (
    "The 'google-auth' package is required for the Vertex AI provider. "
    "Install it with:  pip install 'karna[vertex]'  (or pip install google-auth)"
)

# Hardcoded fallback model list — covers the most common Gemini + Anthropic
# publishers. Used when ``list_models`` cannot reach the discovery endpoint.
_FALLBACK_MODELS: list[tuple[str, str, int | None]] = [
    # (id, publisher, context_window)
    ("gemini-2.5-pro", "google", 2_000_000),
    ("gemini-2.5-flash", "google", 1_000_000),
    ("gemini-2.0-pro", "google", 2_000_000),
    ("gemini-2.0-flash", "google", 1_000_000),
    ("gemini-1.5-pro", "google", 2_000_000),
    ("gemini-1.5-flash", "google", 1_000_000),
    ("claude-opus-4@20250514", "anthropic", 200_000),
    ("claude-sonnet-4@20250514", "anthropic", 200_000),
    ("claude-3-5-sonnet-v2@20241022", "anthropic", 200_000),
]


def _load_google_auth() -> tuple[Any, Any]:
    """Import ``google.auth`` lazily.

    Returned as ``(google.auth, google.auth.transport.requests)`` to avoid
    paying the import cost (and hard dependency) unless the user actually
    instantiates the Vertex provider.
    """
    try:
        import google.auth  # type: ignore[import-not-found]
        import google.auth.transport.requests  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - exercised via test
        raise ImportError(_GOOGLE_AUTH_IMPORT_ERROR) from exc
    return google.auth, google.auth.transport.requests


class VertexProvider(BaseProvider):
    """Google Vertex AI provider (Gemini + model-garden models)."""

    name = "vertex"
    # base_url is constructed dynamically from region+project; leave blank
    # so BaseProvider._validate_url_security doesn't trip.
    base_url = ""

    def __init__(
        self,
        model: str = "gemini-2.5-pro",
        *,
        project_id: str | None = None,
        region: str | None = None,
        publisher: str = "google",
        max_retries: int = 3,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(max_retries=max_retries, timeout=timeout)
        self.model = model
        self.publisher = publisher

        cred = self._load_credential()
        self.project_id = project_id or cred.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT") or ""
        self.region = region or cred.get("region") or os.environ.get("GOOGLE_CLOUD_REGION") or "us-central1"

        # Bearer token is fetched lazily on first use. Cached here between
        # calls; refreshed on 401 or when google-auth reports expiry.
        self._bearer_token: str | None = None
        self._google_credentials: Any = None

    # ------------------------------------------------------------------ #
    #  Auth
    # ------------------------------------------------------------------ #

    def _fetch_token(self) -> str:
        """Obtain a fresh OAuth bearer token via ADC."""
        google_auth, google_requests = _load_google_auth()
        if self._google_credentials is None:
            # ``google.auth.default()`` honours GOOGLE_APPLICATION_CREDENTIALS,
            # metadata server, gcloud user creds — full ADC chain.
            creds, project = google_auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            if not self.project_id and project:
                self.project_id = project
            self._google_credentials = creds
        request = google_requests.Request()
        self._google_credentials.refresh(request)
        token = self._google_credentials.token
        if not token:
            raise RuntimeError("Vertex: google-auth returned an empty token")
        self._bearer_token = token
        return token

    def _headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        if force_refresh or self._bearer_token is None:
            self._fetch_token()
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._bearer_token}",
        }

    def _require_project(self) -> str:
        if not self.project_id:
            raise ValueError(
                "Vertex: no project_id configured. Set GOOGLE_CLOUD_PROJECT, "
                "pass project_id=..., or write "
                f"{self._credential_path()}."
            )
        return self.project_id

    # ------------------------------------------------------------------ #
    #  URL construction
    # ------------------------------------------------------------------ #

    def _endpoint(self, *, streaming: bool = False) -> str:
        project = self._require_project()
        method = "streamGenerateContent" if streaming else "generateContent"
        return (
            f"https://{self.region}-aiplatform.googleapis.com/v1/"
            f"projects/{project}/locations/{self.region}/"
            f"publishers/{self.publisher}/models/{self.model}:{method}"
        )

    # ------------------------------------------------------------------ #
    #  Serialization — Vertex/Gemini contents format
    # ------------------------------------------------------------------ #

    @staticmethod
    def _serialize_messages(messages: list[Message]) -> list[dict[str, Any]]:
        """Convert Messages to Gemini ``contents`` format.

        Gemini uses roles "user" and "model" (no "assistant"), and system
        messages must be promoted to ``system_instruction`` at the top level.
        This helper skips system messages — the caller handles them.
        """
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            role = "model" if m.role == "assistant" else "user"
            parts: list[dict[str, Any]] = []
            if m.content:
                parts.append({"text": m.content})
            for tc in m.tool_calls:
                parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
            for tr in m.tool_results:
                parts.append(
                    {
                        "functionResponse": {
                            "name": tr.tool_call_id,
                            "response": {"content": tr.content},
                        }
                    }
                )
            if not parts:
                parts.append({"text": ""})
            contents.append({"role": role, "parts": parts})
        return contents

    @staticmethod
    def _serialize_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI-style tool defs to Gemini ``tools`` format."""
        fns: list[dict[str, Any]] = []
        for t in tools:
            fn = t.get("function", {})
            fns.append(
                {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return [{"function_declarations": fns}]

    @staticmethod
    def _parse_candidates(data: dict[str, Any]) -> tuple[str, list[ToolCall]]:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for cand in data.get("candidates", []):
            parts = cand.get("content", {}).get("parts", [])
            for idx, part in enumerate(parts):
                if "text" in part:
                    text_parts.append(part["text"])
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(
                        ToolCall(
                            id=f"call_{idx}",
                            name=fc.get("name", ""),
                            arguments=fc.get("args", {}) or {},
                        )
                    )
        return "".join(text_parts), tool_calls

    @staticmethod
    def _extract_usage(data: dict[str, Any]) -> Usage:
        meta = data.get("usageMetadata", {}) or {}
        return Usage(
            input_tokens=meta.get("promptTokenCount", 0),
            output_tokens=meta.get("candidatesTokenCount", 0),
        )

    # ------------------------------------------------------------------ #
    #  Thinking-mode support
    # ------------------------------------------------------------------ #

    def _supports_thinking(self) -> bool:
        """Gemini 2.5 family exposes the ``thinkingConfig`` generation knob."""
        lowered = self.model.lower()
        return "gemini-2.5" in lowered or "gemini-3" in lowered

    # Per-family output caps for Vertex Gemini.
    _VERTEX_OUTPUT_LIMITS: dict[str, int] = {
        "gemini-3": 65_536,  # speculative — Gemini 3 will likely match Claude
        "gemini-2.5-pro": 65_536,
        "gemini-2.5-flash": 65_536,
        "gemini-2.0-flash": 8_192,
        "gemini-1.5-pro": 8_192,
        "gemini-1.5-flash": 8_192,
    }

    def _max_output(self) -> int | None:
        """Canonical registry wins; falls back to the prefix table."""
        cap = lookup_model_max_output("vertex", self.model)
        if cap is not None:
            return cap
        ml = self.model.lower() if self.model else ""
        best_key = ""
        best_val: int | None = None
        for key, val in self._VERTEX_OUTPUT_LIMITS.items():
            if ml.startswith(key) and len(key) > len(best_key):
                best_key = key
                best_val = val
        return best_val

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
        payload: dict[str, Any] = {
            "contents": self._serialize_messages(messages),
        }
        # Extract system prompt from messages if not passed explicitly.
        system_text = system_prompt
        if system_text is None:
            for m in messages:
                if m.role == "system" and m.content:
                    system_text = m.content
                    break
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        if tools:
            payload["tools"] = self._serialize_tools(tools)
        gen_config: dict[str, Any] = {}
        # Gemini 1.5/2.0 family: pro = 8192, flash = 8192; 1.5-pro-latest = 8192.
        # Always send a resolved value so the caller gets their requested
        # budget clamped to the family cap.
        gen_config["maxOutputTokens"] = resolve_max_tokens(max_tokens, self._max_output())
        if temperature is not None:
            gen_config["temperature"] = temperature
        if thinking and self._supports_thinking():
            budget = thinking_budget if thinking_budget and thinking_budget > 0 else 10000
            # Vertex Gemini exposes ``thinkingConfig`` inside ``generationConfig``.
            gen_config["thinkingConfig"] = {"thinkingBudget": int(budget)}
        if gen_config:
            payload["generationConfig"] = gen_config
        return payload

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
        url = self._endpoint(streaming=False)

        async with self._make_client() as client:
            headers = self._headers()
            resp = await client.post(url, headers=headers, json=payload)
            # One-shot refresh on 401 (expired token).
            if resp.status_code == 401:
                resp = await client.post(url, headers=self._headers(force_refresh=True), json=payload)
            resp.raise_for_status()
            data = resp.json()

        text, tool_calls = self._parse_candidates(data)
        usage = self._extract_usage(data)
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
        # Vertex REST supports SSE via ``?alt=sse``.
        url = self._endpoint(streaming=True) + "?alt=sse"

        cumulative_usage = Usage()
        async with self._make_client() as client:
            headers = self._headers()
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code == 401:
                    # Retry once with a refreshed token.
                    await resp.aclose()
                    headers = self._headers(force_refresh=True)
                    async with client.stream("POST", url, headers=headers, json=payload) as resp2:
                        async for ev in self._iter_sse(resp2, cumulative_usage):
                            yield ev
                else:
                    resp.raise_for_status()
                    async for ev in self._iter_sse(resp, cumulative_usage):
                        yield ev

        self._track_usage(cumulative_usage)
        yield StreamEvent(type="done", usage=cumulative_usage)

    async def _iter_sse(self, resp: httpx.Response, cumulative_usage: Usage) -> AsyncIterator[StreamEvent]:
        """Parse SSE stream. Each ``data:`` line is a JSON chunk containing
        a partial ``candidates`` / ``usageMetadata`` payload."""
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == "[DONE]":
                continue
            try:
                chunk = json.loads(raw)
            except json.JSONDecodeError:
                continue
            text, tool_calls = self._parse_candidates(chunk)
            if text:
                yield StreamEvent(type="text", text=text)
            for tc in tool_calls:
                yield StreamEvent(type="tool_call_end", tool_call=tc)
            usage = self._extract_usage(chunk)
            # Gemini sends cumulative counts in each chunk — overwrite, not add.
            if usage.input_tokens:
                cumulative_usage.input_tokens = usage.input_tokens
            if usage.output_tokens:
                cumulative_usage.output_tokens = usage.output_tokens

    async def list_models(self) -> list[ModelInfo]:
        """Return Vertex model info.

        Vertex's discovery endpoint requires the ``aiplatform.googleapis.com``
        ``publisherModels.list`` API. We attempt to call it; if it fails (no
        auth, no permissions, etc.), we fall back to the hardcoded list.
        """
        fallback = [
            ModelInfo(
                id=mid,
                name=mid,
                provider="vertex",
                context_window=ctx,
            )
            for (mid, _pub, ctx) in _FALLBACK_MODELS
        ]
        if not self.project_id:
            return fallback
        try:
            # Publisher model listing is a global (not regional) endpoint.
            url = f"https://aiplatform.googleapis.com/v1/publishers/{self.publisher}/models"
            async with self._make_client() as client:
                resp = await client.get(url, headers=self._headers())
                if resp.status_code != 200:
                    return fallback
                data = resp.json()
        except Exception:
            return fallback

        models: list[ModelInfo] = []
        for m in data.get("publisherModels", []):
            name = m.get("name", "")
            # name like "publishers/google/models/gemini-2.5-pro"
            model_id = name.rsplit("/", 1)[-1] if "/" in name else name
            if not model_id:
                continue
            models.append(ModelInfo(id=model_id, name=model_id, provider="vertex"))
        return models or fallback
