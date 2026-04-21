"""Abstract base class for all Karna model providers.

Every concrete provider (OpenRouter, OpenAI, ...) inherits from
``BaseProvider`` and implements the async methods.

Includes:
- Credential loading from ``~/.karna/credentials/<provider>.token.json``
- Retry with jittered exponential backoff (ported from hermes-agent, MIT)
- Rate-limit (429) handling with Retry-After parsing
- Per-call cost tracking

Security invariants:
- HTTPS enforced for all provider URLs except localhost/127.0.0.1
- TLS certificate verification is always on (``verify=True``)
- Request/response bodies are NEVER logged (contain user conversations)
- Only model name, token count, latency, and cost are logged

Two retry layers -- WHY THEY COEXIST
------------------------------------

Karna has two separate retry surfaces:

1. **Transport-level retry** -- ``BaseProvider._request_with_retry`` in
   this file. Wraps a single ``httpx`` request. Retries on 429 + 5xx
   + timeouts, parses the ``Retry-After`` header, surfaces failures
   as raised exceptions. This is the right layer for provider
   implementations (``openai.py``, ``anthropic.py``, ...) that make a
   single HTTP call per provider method invocation.

2. **Agent-loop-level retry** -- ``_call_provider_with_retry`` in
   ``karna/agents/loop.py``. Wraps a full ``provider.stream`` call.
   Retries on the same transient classes but also emits a
   ``StreamEvent`` of type ``"error"`` so the UI can show a
   "retrying..." notice mid-stream. This is the right layer for
   streaming providers that may fail part-way through.

Both layers share the same jittered-exponential-backoff math via
``karna.providers._retry.jittered_backoff`` so the timing semantics
stay in lock-step.

Portions adapted from hermes-agent retry_utils.py (MIT).
See NOTICES.md for attribution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import urlparse

import httpx

from karna.auth.pool import CredentialPool
from karna.models import Message, ModelInfo, StreamEvent, Usage, estimate_cost
from karna.providers._retry import (
    DEFAULT_BASE_DELAY,
    DEFAULT_JITTER_RATIO,
    DEFAULT_MAX_DELAY,
)
from karna.providers._retry import (
    jittered_backoff as _shared_jittered_backoff,
)

logger = logging.getLogger(__name__)

CREDENTIALS_DIR = Path.home() / ".karna" / "credentials"

# Retry defaults (ported from hermes-agent retry_utils.py; canonical
# values live in ``karna.providers._retry``).
DEFAULT_MAX_RETRIES = 3

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

    Thin wrapper around :func:`karna.providers._retry.jittered_backoff`
    kept for backwards-compat import paths.
    """
    return _shared_jittered_backoff(
        attempt,
        base_delay=base_delay,
        max_delay=max_delay,
        jitter_ratio=jitter_ratio,
    )


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Extract a delay from the Retry-After header, if present."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return max(float(raw), 0.5)
    except (TypeError, ValueError):
        return None


# Shared across every provider so no caller silently hits a 4K output cap
# when their model supports 128K. Caller-specified ``requested`` wins (but
# still clamped to what the model actually accepts); otherwise we pick a
# generous-but-not-wasteful default rather than always reserving the full
# cap. Port of OpenClaw's ``resolveAnthropicVertexMaxTokens`` with the same
# 32K soft ceiling it uses for un-requested generations.
_DEFAULT_SOFT_CEILING = 32_000
_FALLBACK_WHEN_UNKNOWN = 4_096


def resolve_max_tokens(
    requested: int | None,
    model_max: int | None,
    *,
    fallback: int = _FALLBACK_WHEN_UNKNOWN,
    soft_ceiling: int = _DEFAULT_SOFT_CEILING,
) -> int:
    """Resolve the effective ``max_tokens`` for an API call.

    - If the caller passed ``requested``, clamp it to ``model_max`` and return.
    - Otherwise, if we know the model's cap, default to
      ``min(model_max, soft_ceiling)`` so big-context models like Opus-4
      (128K output) don't waste the full budget on small turns.
    - If we know neither, return ``fallback``.
    """
    req = int(requested) if requested and requested > 0 else None
    mmax = int(model_max) if model_max and model_max > 0 else None
    if req is not None:
        return min(req, mmax) if mmax is not None else req
    if mmax is not None:
        return min(mmax, soft_ceiling)
    return fallback


def lookup_model_max_output(provider: str, model: str) -> int | None:
    """Consult the canonical registry (beta's ``canonical_models.json``) for
    a model's authoritative max_output cap.

    Returns ``None`` when the registry isn't loaded yet (pre-PR #49) OR the
    model isn't catalogued — callers should fall back to their local
    per-family table in either case. Single entry point here keeps the
    fallback story consistent across all 7 providers.
    """
    try:
        # Imported lazily to avoid a circular import at module load.
        from karna.providers import model_capabilities  # type: ignore[attr-defined]
    except ImportError:
        return None
    spec = f"{provider}:{model}" if ":" not in model else model
    caps = model_capabilities(spec)
    if caps is None:
        return None
    raw = caps.get("max_output")
    if raw is None or not isinstance(raw, (int, float)) or raw <= 0:
        return None
    return int(raw)


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
        self.credential_pool: CredentialPool | None = None
        self.max_retries = max_retries
        self.timeout = timeout
        # Cumulative usage across calls for this provider instance
        self._cumulative_usage = Usage()
        # Validate provider URL security at init time
        self._validate_url_security()

    # ------------------------------------------------------------------ #
    #  URL security
    # ------------------------------------------------------------------ #

    def _validate_url_security(self) -> None:
        """Enforce HTTPS for all provider URLs except localhost/127.0.0.1.

        Called at init time. Local providers (localhost, 127.0.0.1) are
        allowed to use HTTP for development convenience.
        """
        if not self.base_url:
            return  # Azure sets URL dynamically; validated at call time

        parsed = urlparse(self.base_url)
        host = parsed.hostname or ""

        # Allow HTTP for local development servers.
        # nosec B104 — this is a host *comparison*, not a socket bind.
        if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):  # nosec B104
            return

        if parsed.scheme != "https":
            raise ValueError(
                f"Provider {self.name}: base_url must use HTTPS for "
                f"non-local endpoints (got {self.base_url!r}). "
                f"Only localhost/127.0.0.1 may use HTTP."
            )

    def _make_client(self, **kwargs: Any) -> httpx.AsyncClient:
        """Create an httpx.AsyncClient with TLS verification enforced.

        Never disables certificate verification. Timeout is set from
        the provider's configured timeout.
        """
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("verify", True)  # Explicitly enforce TLS verification
        return httpx.AsyncClient(**kwargs)

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

        Security: NEVER logs request or response bodies (they contain
        user conversations and potentially sensitive generated content).
        Only logs: provider name, status code, and retry timing.
        """
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                t0 = time.monotonic()
                resp = await client.request(method, url, **kwargs)
                if resp.status_code not in _RETRYABLE_STATUS_CODES:
                    elapsed = time.monotonic() - t0
                    logger.debug(
                        "%s: %s %s -> %d (%.1fs)",
                        self.name,
                        method,
                        url,
                        resp.status_code,
                        elapsed,
                    )
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
                        self.name,
                        delay,
                        attempt,
                        self.max_retries,
                    )
                else:
                    delay = _jittered_backoff(attempt)
                    logger.warning(
                        "%s: server error %d, retrying in %.1fs (attempt %d/%d)",
                        self.name,
                        resp.status_code,
                        delay,
                        attempt,
                        self.max_retries,
                    )

                await asyncio.sleep(delay)

            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    raise
                delay = _jittered_backoff(attempt)
                logger.warning(
                    "%s: timeout, retrying in %.1fs (attempt %d/%d)",
                    self.name,
                    delay,
                    attempt,
                    self.max_retries,
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
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> Message:
        """Send *messages* and return a single assistant ``Message``.

        ``thinking`` toggles extended-reasoning mode on providers that
        support it (Anthropic extended thinking, OpenAI o-series, OpenRouter
        reasoning, Vertex Gemini 2.5 thinking, Bedrock Claude thinking).
        Providers that don't support it MUST silently ignore the kwarg.
        ``thinking_budget`` is the requested reasoning token budget (ignored
        when unsupported by the target provider).
        """
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
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield ``StreamEvent`` objects from the model.

        See :meth:`complete` for ``thinking`` / ``thinking_budget`` semantics.
        """
        ...
        yield StreamEvent(type="done")  # type: ignore[misc]  # pragma: no cover

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Return info for all models this provider exposes."""
        ...
