"""Local / self-hosted provider — stub for Phase 2.

Will target an OpenAI-compatible endpoint (e.g. llama.cpp server,
Ollama, vLLM) at a user-configured base URL.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider


class LocalProvider(BaseProvider):
    """Local OpenAI-compatible provider (Phase 2)."""

    name = "local"
    base_url = "http://localhost:8080/v1"

    def __init__(self, model: str = "default") -> None:
        super().__init__()
        self.model = model
        cred = self._load_credential()
        self.base_url = cred.get("base_url", self.base_url)

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Message:
        raise NotImplementedError("Phase 2")

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        raise NotImplementedError("Phase 2")
        yield StreamEvent(type="done")  # pragma: no cover

    async def list_models(self) -> list[ModelInfo]:
        raise NotImplementedError("Phase 2")
