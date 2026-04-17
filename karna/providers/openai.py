"""OpenAI provider — stub for Phase 2.

Will use the ``openai`` Python SDK or raw httpx against
``https://api.openai.com/v1``.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider


class OpenAIProvider(BaseProvider):
    """OpenAI chat-completions provider (Phase 2)."""

    name = "openai"
    base_url = "https://api.openai.com/v1"

    def __init__(self, model: str = "gpt-4o") -> None:
        super().__init__()
        self.model = model
        cred = self._load_credential()
        self._api_key = cred.get("api_key") or os.environ.get("OPENAI_API_KEY")

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
