"""Azure OpenAI provider — stub for Phase 2.

Will target the Azure-specific ``/openai/deployments/`` endpoint with
``api-version`` query parameter and ``api-key`` header auth.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider


class AzureOpenAIProvider(BaseProvider):
    """Azure OpenAI chat-completions provider (Phase 2)."""

    name = "azure"
    base_url = ""  # Resolved from AZURE_OPENAI_ENDPOINT at runtime

    def __init__(self, model: str = "gpt-4o") -> None:
        super().__init__()
        self.model = model
        cred = self._load_credential()
        self._api_key = cred.get("api_key") or os.environ.get("AZURE_OPENAI_API_KEY")
        self.base_url = cred.get("endpoint") or os.environ.get("AZURE_OPENAI_ENDPOINT", "")

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
