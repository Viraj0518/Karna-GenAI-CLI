"""Prompt caching support for Karna providers.

Enables provider-level prompt caching to reduce token costs:
- Anthropic: adds ``cache_control`` markers to system prompt + tool defs
- OpenRouter: passes through cache markers for Anthropic-backed models
- OpenAI: ensures prefix stability (system first, tools sorted by name)

Anthropic's prompt caching bills cache-read tokens at 10% of the normal
input rate, and cache-write (creation) tokens at 25% above normal.
For long system prompts + tool definitions that stay stable across turns,
the savings are substantial.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


class PromptCache:
    """Track cache-stable message prefixes to enable provider-level caching.

    For Anthropic: adds cache_control markers to system prompt + tool defs.
    For OpenRouter: passes through markers when targeting anthropic/ models.
    For OpenAI: ensures message prefix stability (auto-cached by backend).
    """

    def __init__(self) -> None:
        self._system_hash: str | None = None
        self._tools_hash: str | None = None
        self._cache_reads: int = 0
        self._cache_writes: int = 0
        self._total_calls: int = 0

    # ------------------------------------------------------------------ #
    #  Anthropic cache markers
    # ------------------------------------------------------------------ #

    @staticmethod
    def prepare_anthropic_system(system_prompt: str) -> list[dict[str, Any]]:
        """Wrap a system prompt string into Anthropic's block format with
        ``cache_control`` set to ephemeral.

        Returns a list suitable for the ``system`` field in the Messages API.
        """
        return [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @staticmethod
    def mark_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add ``cache_control`` to the last tool definition.

        Anthropic caches everything up to the last cache_control breakpoint,
        so marking the final tool captures the entire tool list.
        Returns a new list (does not mutate the input).
        """
        if not tools:
            return tools

        # Shallow-copy the list, deep-copy only the last element
        result = list(tools)
        last = dict(result[-1])
        last["cache_control"] = {"type": "ephemeral"}
        result[-1] = last
        return result

    # ------------------------------------------------------------------ #
    #  Prefix stability helpers (OpenAI / generic)
    # ------------------------------------------------------------------ #

    @staticmethod
    def sort_tools_by_name(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Sort tool definitions by function name for prefix stability.

        OpenAI automatically caches identical request prefixes, so keeping
        tool definitions in a deterministic order maximises cache hits.

        Works with both OpenAI-format (``{"type":"function","function":{...}}``)
        and Anthropic-format (``{"name":...}``) tools.
        """
        def _tool_sort_key(t: dict[str, Any]) -> str:
            # OpenAI format
            fn = t.get("function", {})
            if fn:
                return fn.get("name", "")
            # Anthropic format
            return t.get("name", "")

        return sorted(tools, key=_tool_sort_key)

    # ------------------------------------------------------------------ #
    #  Cache statistics
    # ------------------------------------------------------------------ #

    def record_usage(
        self,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> None:
        """Record cache hit/miss tokens from a provider response."""
        self._total_calls += 1
        self._cache_reads += cache_read_tokens
        self._cache_writes += cache_write_tokens

    def get_cache_stats(self) -> dict[str, Any]:
        """Return cache hit/miss stats accumulated so far."""
        return {
            "total_calls": self._total_calls,
            "cache_read_tokens": self._cache_reads,
            "cache_write_tokens": self._cache_writes,
            "cache_hit_rate": (
                self._cache_reads / (self._cache_reads + self._cache_writes)
                if (self._cache_reads + self._cache_writes) > 0
                else 0.0
            ),
        }

    # ------------------------------------------------------------------ #
    #  Hash tracking (detect when cache should invalidate)
    # ------------------------------------------------------------------ #

    def update_hashes(self, system_prompt: str, tools: list[dict[str, Any]]) -> bool:
        """Update internal hashes and return True if either changed.

        Useful for logging / debugging cache invalidation.
        """
        sys_hash = hashlib.md5(system_prompt.encode()).hexdigest()[:12]
        tools_hash = hashlib.md5(
            json.dumps(tools, sort_keys=True).encode()
        ).hexdigest()[:12]

        changed = sys_hash != self._system_hash or tools_hash != self._tools_hash
        self._system_hash = sys_hash
        self._tools_hash = tools_hash
        return changed
