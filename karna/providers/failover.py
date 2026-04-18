"""Multi-credential failover wrapper.

Wraps N instances of the same underlying provider (each configured with
its own credential / API key) and rotates to the next instance on
rate-limit, auth, or transient 5xx errors. Each instance has an
independent cooldown timestamp so the rotation respects Retry-After hints.

Intended usage::

    inst1 = OpenRouterProvider(model="x", api_key="k1")
    inst2 = OpenRouterProvider(model="x", api_key="k2")
    provider = FailoverProvider([inst1, inst2])
    await provider.complete(messages)

Or via the registry string syntax consumed elsewhere:

    nellie model failover openrouter:key1,key2,key3

which produces three OpenRouter instances, one per key.

Cooldown strategy: exponential — first failure cools an instance for 5s,
next for 10s, then 20s ... capped at ``_MAX_COOLDOWN``. Successful calls
reset the instance's failure counter.

When every instance is in cooldown, the last captured exception is
re-raised.
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider

logger = logging.getLogger(__name__)

# Cooldown math
_BASE_COOLDOWN = 5.0
_MAX_COOLDOWN = 300.0
_COOLDOWN_MULTIPLIER = 2.0

# HTTP status codes that should trigger a rotate-and-cool on the current
# instance. 401/403 = credential issue; 429 = rate limit; 5xx = server.
_FAILOVER_STATUS_CODES = frozenset({401, 403, 429, 500, 502, 503, 504})


class AllInstancesExhaustedError(RuntimeError):
    """Raised when every instance is in cooldown and no call can proceed."""


def _is_failover_exception(exc: BaseException) -> bool:
    """Return True if *exc* should trigger rotation to the next instance."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _FAILOVER_STATUS_CODES
    if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    # Auth failures that providers raise as ValueError (no key configured)
    # shouldn't rotate — they're a config bug, not a transient issue.
    return False


class FailoverProvider(BaseProvider):
    """Wraps N instances of the same provider for credential rotation.

    The wrapper IS a ``BaseProvider`` so it drops into anywhere a regular
    provider is used. ``name`` is prefixed with ``failover:`` so logs and
    cost-tracking can distinguish it.
    """

    base_url = ""  # inherited from wrapped instances; not validated here

    def __init__(self, instances: list[BaseProvider]) -> None:
        if not instances:
            raise ValueError("FailoverProvider requires at least one instance")
        # Defer BaseProvider.__init__ — we don't want its URL validation.
        # But we do need the attributes it sets, so call it with a safe URL.
        super().__init__()
        self.name = f"failover:{instances[0].name}"
        self._instances: list[BaseProvider] = list(instances)
        self._current: int = 0
        self._cooldowns: dict[int, float] = {i: 0.0 for i in range(len(instances))}
        self._failure_counts: dict[int, int] = {i: 0 for i in range(len(instances))}
        # Surface the first instance's model for observability.
        self.model = getattr(instances[0], "model", "")

    # ------------------------------------------------------------------ #
    #  Rotation bookkeeping
    # ------------------------------------------------------------------ #

    def _next_ready_index(self, start: int) -> int | None:
        """Return the next index whose cooldown has elapsed, or None."""
        now = time.monotonic()
        n = len(self._instances)
        for offset in range(n):
            idx = (start + offset) % n
            if self._cooldowns[idx] <= now:
                return idx
        return None

    def _mark_failure(self, idx: int, retry_after: float | None = None) -> None:
        """Advance the cooldown for instance *idx* and rotate away from it."""
        self._failure_counts[idx] += 1
        count = self._failure_counts[idx]
        if retry_after is not None:
            delay = min(retry_after, _MAX_COOLDOWN)
        else:
            delay = min(
                _BASE_COOLDOWN * (_COOLDOWN_MULTIPLIER ** (count - 1)),
                _MAX_COOLDOWN,
            )
        self._cooldowns[idx] = time.monotonic() + delay
        logger.warning(
            "failover[%s]: instance %d cooldown for %.1fs (failure #%d)",
            self._instances[idx].name,
            idx,
            delay,
            count,
        )

    def _mark_success(self, idx: int) -> None:
        """Reset the failure counter after a successful call."""
        self._failure_counts[idx] = 0

    @staticmethod
    def _retry_after(exc: BaseException) -> float | None:
        if isinstance(exc, httpx.HTTPStatusError):
            raw = exc.response.headers.get("retry-after")
            if raw:
                try:
                    return max(float(raw), 0.5)
                except (TypeError, ValueError):
                    return None
        return None

    # ------------------------------------------------------------------ #
    #  BaseProvider interface
    # ------------------------------------------------------------------ #

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Message:
        last_exc: BaseException | None = None
        start = self._current
        for _ in range(len(self._instances)):
            idx = self._next_ready_index(start)
            if idx is None:
                break
            inst = self._instances[idx]
            try:
                result = await inst.complete(
                    messages,
                    tools,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                self._current = idx
                self._mark_success(idx)
                # Roll up usage from the wrapped instance for observability.
                self._track_usage(inst.cumulative_usage)
                return result
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if not _is_failover_exception(exc):
                    raise
                self._mark_failure(idx, self._retry_after(exc))
                # Advance the start pointer so the next attempt skips idx.
                start = (idx + 1) % len(self._instances)
        raise AllInstancesExhaustedError(
            f"All {len(self._instances)} failover instances are in cooldown for provider {self._instances[0].name!r}"
        ) from last_exc

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        # For streaming we try each instance in turn, yielding from the first
        # one that produces at least one event. If an error happens after the
        # first yield, we surface it — we don't silently swap mid-stream
        # because that would corrupt the user's output.
        last_exc: BaseException | None = None
        start = self._current
        for _ in range(len(self._instances)):
            idx = self._next_ready_index(start)
            if idx is None:
                break
            inst = self._instances[idx]
            first_event_seen = False
            try:
                async for event in inst.stream(
                    messages,
                    tools,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ):
                    first_event_seen = True
                    yield event
                self._current = idx
                self._mark_success(idx)
                self._track_usage(inst.cumulative_usage)
                return
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if first_event_seen or not _is_failover_exception(exc):
                    raise
                self._mark_failure(idx, self._retry_after(exc))
                start = (idx + 1) % len(self._instances)
        raise AllInstancesExhaustedError(
            f"All {len(self._instances)} failover instances are in cooldown for provider {self._instances[0].name!r}"
        ) from last_exc

    async def list_models(self) -> list[ModelInfo]:
        """Merge and deduplicate models across all instances.

        Instances in cooldown are skipped. If every instance is down, we
        return the union of whatever the first instance's list_models
        produces — callers shouldn't get an empty list just because of a
        transient outage, so we still attempt instance 0.
        """
        seen: dict[str, ModelInfo] = {}
        for idx, inst in enumerate(self._instances):
            if self._cooldowns[idx] > time.monotonic():
                continue
            try:
                for m in await inst.list_models():
                    if m.id not in seen:
                        seen[m.id] = m
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "failover: list_models failed on instance %d (%s): %s",
                    idx,
                    inst.name,
                    exc,
                )
                continue
        if not seen and self._instances:
            # All instances errored; try the first one without swallowing.
            for m in await self._instances[0].list_models():
                seen[m.id] = m
        return list(seen.values())

    # ------------------------------------------------------------------ #
    #  Observability
    # ------------------------------------------------------------------ #

    @property
    def cooldown_status(self) -> dict[int, float]:
        """Remaining cooldown seconds per instance index (0 when ready)."""
        now = time.monotonic()
        return {idx: max(0.0, self._cooldowns[idx] - now) for idx in self._cooldowns}

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"FailoverProvider(n={len(self._instances)}, current={self._current}, name={self.name!r})"
