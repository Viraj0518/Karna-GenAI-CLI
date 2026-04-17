"""Abstract base class for all Karna model providers.

Every concrete provider (OpenRouter, OpenAI, ...) inherits from
``BaseProvider`` and implements the async methods.

Includes:
- Credential loading from ``~/.karna/credentials/<provider>.token.json``
- Retry with jittered exponential backoff (ported from hermes-agent, MIT)
- Rate-limit (429) handling with Retry-After parsing
- Per-call cost tracking

Portions adapted from hermes-agent retry_utils.py (MIT).
See NOTICES.md for attribution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent, Usage, estimate_cost

logger = logging.getLogger(__name__)

CREDENTIALS_DIR = Path.home() / ".karna" / "credentials"

# Retry defaults (ported from hermes-agent retry_utils.py)
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY = 2.0
DEFAULT_MAX_DELAY = 60.0
DEFAULT_JITTER_RATIO = 0.5

# HTTP status codes that trigger a retry
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _jittered_backoff(
    attempt: int,
    *,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
) -> float:
    """Compute a jittered exponential backoff delay.

    Ported from hermes-agent retry_utils.py (MIT).
    Decorrelates concurrent retries to avoid thundering-herd spikes.
    """
    exponent = max(0, attempt - 1)
    if exponent >= 63 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2**exponent), max_delay)

    seed = (time.time_ns() ^ (id(asyncio.current_task()) * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    jitter = rng.uniform(0, jitter_ratio * delay)
    return delay + jitter


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Extract a delay from the Retry-After header, if present."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(float(raw), 0.5)
    except (TypeError, ValueError):
        return None


class BaseProvider(ABC):
    """Base class for model providers."""

    name: str = "base"
    base_url: str = ""

    def __init__(
        self,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        timeout: float = 120.0,
    ) -> None:
        self._api_key: str | None = None
        self.max_retries = max_retries
        self.timeout = timeout
        # Cumulative usage across calls for this provider instance
        self._cumulative_usage = Usage()

    # ------------------------------------------------------------------ #
    #  Credential helpers
    # ------------------------------------------------------------------ #

    def _credential_path(self) -> Path:
        """Return the path to this provider's token file."""
        return CREDENTIALS_DIR / f"{self.name}.token.json"

    def _load_credential(self) -> dict[str, Any]:
        """Load credentials from the JSON token file.

        Returns an empty dict when the file doesn't exist.
        """
        path = self._credential_path()
        if path.exists():
            return json.loads(path.read_text())
        return {}

    def _require_api_key(self) -> str:
        """Return the API key or raise a clear error."""
        if not self._api_key:
            raise ValueError(
                f"No API key configured for {self.name}. "
                f"Set ${self.name.upper()}_API_KEY or create "
                f"{self._credential_path()}"
            )
        return self._api_key

    # ------------------------------------------------------------------ #
    #  Cost tracking
    # ------------------------------------------------------------------ #

    @property
    def cumulative_usage(self) -> Usage:
        """Cumulative token usage and cost across all calls on this instance."""
        return self._cumulative_usage

    def _track_usage(self, usage: Usage) -> None:
        """Add a call's usage to the cumulative total."""
        self._cumulative_usage.input_tokens += usage.input_tokens
        self._cumulative_usage.output_tokens += usage.output_tokens
        self._cumulative_usage.cache_read_tokens += usage.cache_read_tokens
        self._cumulative_usage.cache_write_tokens += usage.cache_write_tokens
        if usage.cost_usd is not None:
            if self._cumulative_usage.cost_usd is None:
                self._cumulative_usage.cost_usd = 0.0
            self._cumulative_usage.cost_usd += usage.cost_usd

    def _make_usage(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        *,
        model: str = "",
    ) -> Usage:
        """Create a Usage with estimated cost from the pricing table."""
        cost = estimate_cost(self.name, model, input_tokens, output_tokens)
        return Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )

    # ------------------------------------------------------------------ #
    #  Retry wrapper
    # ------------------------------------------------------------------ #

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request with retries and backoff for transient errors.

        Handles 429 (rate limit) with Retry-After header parsing, and
        retries on 5xx server errors.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = await client.request(method, url, **kwargs)
                if resp.status_code not in _RETRYABLE_STATUS_CODES:
                    resp.raise_for_status()
                    return resp

                # Retryable status code
                if attempt >= self.max_retries:
                    resp.raise_for_status()
                    return resp  # unreachable, raise_for_status throws

                if resp.status_code == 429:
                    delay = _parse_retry_after(resp) or _jittered_backoff(attempt)
                    logger.warning(
                        "%s: rate limited (429), retrying in %.1fs (attempt %d/%d)",
                        self.name, delay, attempt, self.max_retries,
                    )
                else:
                    delay = _jittered_backoff(attempt)
                    logger.warning(
                        "%s: server error %d, retrying in %.1fs (attempt %d/%d)",
                        self.name, resp.status_code, delay, attempt, self.max_retries,
                    )

                await asyncio.sleep(delay)

            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise
                delay = _jittered_backoff(attempt)
                logger.warning(
                    "%s: timeout, retrying in %.1fs (attempt %d/%d)",
                    self.name, delay, attempt, self.max_retries,
                )
                await asyncio.sleep(delay)

            except httpx.HTTPStatusError:
                raise

        # Should not reach here, but satisfy the type checker
        raise last_exc or RuntimeError("Retry loop exited unexpectedly")

    # ------------------------------------------------------------------ #
    #  Abstract interface
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Message:
        """Send *messages* and return a single assistant ``Message``."""
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield ``StreamEvent`` objects from the model."""
        ...
        yield StreamEvent(type="done")  # type: ignore[misc]  # pragma: no cover

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Return info for all models this provider exposes."""
        ...
