"""Anthropic provider — stub for Phase 2.

Will target ``https://api.anthropic.com/v1/messages`` with the
``x-api-key`` + ``anthropic-version`` header pattern.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider


class AnthropicProvider(BaseProvider):
    """Anthropic Messages API provider (Phase 2)."""

    name = "anthropic"
    base_url = "https://api.anthropic.com/v1"

    def __init__(self, model: str = "claude-sonnet-4-20250514") -> None:
        super().__init__()
        self.model = model
        cred = self._load_credential()
        self._api_key = cred.get("api_key") or os.environ.get("ANTHROPIC_API_KEY")

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
