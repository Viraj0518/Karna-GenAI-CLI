"""Shared backoff helper used by both provider-transport and agent-loop retry layers.

Karna has two separate retry surfaces that need backoff with jitter:

1.  **Transport-level retry** -- ``karna.providers.base.BaseProvider._request_with_retry``
    wraps a single ``httpx`` request. It retries on 429 and 5xx status
    codes and on ``httpx.TimeoutException``. Failures surface as raised
    exceptions. No ``StreamEvent`` is produced.

2.  **Agent-loop-level retry** -- ``karna.agents.loop._call_provider_with_retry``
    wraps a full provider ``stream`` call. It retries on the same
    transient classes, but each retry also emits a ``StreamEvent`` of
    type ``"error"`` so the UI can show a "retrying..." notice in real
    time.

Both layers share the same jittered exponential backoff math; this
module is the single source of truth for that computation. See the
docstrings on the call sites for why the two layers exist side-by-side
rather than being collapsed into one.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


DEFAULT_BASE_DELAY = 2.0
DEFAULT_MAX_DELAY = 60.0
DEFAULT_JITTER_RATIO = 0.5


def jittered_backoff(
    attempt: int,
    *,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    jitter_ratio: float = DEFAULT_JITTER_RATIO,
) -> float:
    """Compute a jittered exponential backoff delay for *attempt* (1-indexed).

    Ported from hermes-agent retry_utils.py (MIT). Decorrelates
    concurrent retries so N racing tasks do not all wake up at the same
    moment and re-stampede the provider.
    """
    exponent = max(0, attempt - 1)
    if exponent >= 63 or base_delay <= 0:
        delay = max_delay
    else:
        delay = min(base_delay * (2**exponent), max_delay)

    try:
        task_id = id(asyncio.current_task()) if asyncio.get_event_loop().is_running() else 0
    except RuntimeError:
        task_id = 0
    seed = (time.time_ns() ^ (task_id * 0x9E3779B9)) & 0xFFFFFFFF
    rng = random.Random(seed)
    jitter = rng.uniform(0, jitter_ratio * delay)
    return delay + jitter


async def with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = DEFAULT_BASE_DELAY,
    should_retry: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[int, float, BaseException], None] | None = None,
) -> T:
    """Run *fn* with jittered exponential backoff on transient failures.

    The caller controls which exceptions are retryable via
    *should_retry* (default: retry every exception). *on_retry* is
    called before each sleep with ``(attempt, delay, exc)`` so callers
    can surface their own telemetry (e.g., emit ``StreamEvent`` objects
    from the agent-loop retry layer).

    Kept intentionally small -- both the transport-level retry (around
    one httpx request) and the loop-level retry (around a full stream)
    call this with their own *fn* closure.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except BaseException as exc:  # noqa: BLE001 -- caller decides what's retryable
            last_exc = exc
            if should_retry is not None and not should_retry(exc):
                raise
            if attempt >= attempts:
                raise
            delay = jittered_backoff(attempt, base_delay=base_delay)
            if on_retry is not None:
                try:
                    on_retry(attempt, delay, exc)
                except Exception:  # pragma: no cover -- telemetry must not break retry
                    pass
            await asyncio.sleep(delay)

    # Unreachable, but keeps the type checker happy.
    assert last_exc is not None
    raise last_exc
