"""Cost-aware routing provider.

Routes each request to the cheapest tier that can plausibly handle it,
with automatic escalation on rate-limits, server errors, or when the
estimated context exceeds the tier's budget.

Tiers are a dict of ``name -> [providers]``. The canonical ordering is
``cheap -> mid -> premium`` but we honor whatever order the caller
provides via :meth:`_tier_order`. Within a tier we pick the first
provider; the tier itself is the load-balancing unit, not individual
providers. Combine with :class:`FailoverProvider` for per-tier
load-balancing if needed.

Cost estimate math (rough):

  expected_cost = input_tokens * tier_input_price + output_budget * tier_output_price

We use cheap heuristics for token counting (chars / 4) to avoid
pulling in tiktoken — the routing decision doesn't need to be exact,
only monotone.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator

import httpx

from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider

logger = logging.getLogger(__name__)


# Rough per-tier context caps. Used only to pick a tier when the prompt
# obviously exceeds a cheap model's window. Values are intentionally
# conservative; users can override via the ``context_caps`` constructor arg.
_DEFAULT_CONTEXT_CAPS: dict[str, int] = {
    "cheap": 32_000,
    "mid": 128_000,
    "premium": 1_000_000,
}

# Status codes that trigger escalation to the next tier.
_ESCALATE_STATUS_CODES = frozenset({408, 413, 429, 500, 502, 503, 504})


class AllTiersExhaustedError(RuntimeError):
    """Raised when every tier has been tried and all failed."""

class CostAwareRouterProvider(BaseProvider):
    """Route each request to the cheapest tier that can handle it."""

    base_url = ""

    def __init__(
        self,
        tiers: dict[str, list[BaseProvider]],
        *,
        escalate_on_error: bool = True,
        context_caps: dict[str, int] | None = None,
        output_budget_tokens: int = 1024,
    ) -> None:
        if not tiers:
            raise ValueError("CostAwareRouterProvider requires at least one tier")
        for name, provs in tiers.items():
            if not provs:
                raise ValueError(f"tier {name!r} must have at least one provider")
        super().__init__()
        self._tiers = {name: list(provs) for name, provs in tiers.items()}
        self._tier_names = list(tiers.keys())  # preserve insertion order
        self.escalate_on_error = escalate_on_error
        self.context_caps = {**_DEFAULT_CONTEXT_CAPS, **(context_caps or {})}
        self.output_budget_tokens = output_budget_tokens
        self.name = f"router:{','.join(self._tier_names)}"

    # ------------------------------------------------------------------ #
    #  Tier selection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _estimate_input_tokens(messages: list[Message], system_prompt: str | None) -> int:
        """Cheap token estimate: 1 token ~= 4 chars."""
        chars = 0
        if system_prompt:
            chars += len(system_prompt)
        for m in messages:
            if m.content:
                chars += len(m.content)
        return max(1, chars // 4)

    def _tier_order(self) -> list[str]:
        """Return the tier names in insertion order (cheap -> premium)."""
        return list(self._tier_names)

    def _pick_start_tier(self, input_tokens: int) -> str:
        """Pick the first tier whose context cap is large enough."""
        total = input_tokens + self.output_budget_tokens
        for tier in self._tier_order():
            cap = self.context_caps.get(tier, 0)
            if cap and cap >= total:
                return tier
        # Nothing fits — use the highest-capacity tier anyway; the
        # caller at least gets a proper error from the real provider.
        return self._tier_order()[-1]

    def _providers_for_tier(self, tier: str) -> list[BaseProvider]:
        return self._tiers[tier]

    def _higher_tiers(self, tier: str) -> list[str]:
        """Return tier names strictly above *tier* in cost ladder."""
        order = self._tier_order()
        idx = order.index(tier)
        return order[idx + 1 :]

    # ------------------------------------------------------------------ #
    #  Escalation logic
    # ------------------------------------------------------------------ #

    @staticmethod
    def _should_escalate(exc: BaseException) -> bool:
        """Return True if the exception should trigger escalation."""
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in _ESCALATE_STATUS_CODES
        if isinstance(exc, (httpx.TimeoutException, httpx.ConnectError)):
            return True
        return False

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
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> Message:
        input_tokens = self._estimate_input_tokens(messages, system_prompt)
        start = self._pick_start_tier(input_tokens)
        tiers_to_try = [start] + self._higher_tiers(start)

        # Only forward thinking kwargs when actually requested so legacy
        # providers that don't accept the kwargs still work.
        extra: dict[str, Any] = {}
        if thinking or thinking_budget is not None:
            extra["thinking"] = thinking
            extra["thinking_budget"] = thinking_budget

        last_exc: BaseException | None = None
        for tier in tiers_to_try:
            prov = self._providers_for_tier(tier)[0]
            logger.debug("router: trying tier=%s provider=%s (est input=%d)", tier, prov.name, input_tokens)
            try:
                msg = await prov.complete(
                    messages,
                    tools,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **extra,
                )
                self._track_usage(prov.cumulative_usage)
                return msg
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if not self.escalate_on_error or not self._should_escalate(exc):
                    raise
                logger.warning(
                    "router: tier=%s (%s) failed (%s); escalating",
                    tier,
                    prov.name,
                    exc,
                )
        raise AllTiersExhaustedError(f"All router tiers exhausted: {','.join(tiers_to_try)}") from last_exc

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
        input_tokens = self._estimate_input_tokens(messages, system_prompt)
        start = self._pick_start_tier(input_tokens)
        tiers_to_try = [start] + self._higher_tiers(start)

        # Only forward thinking kwargs when actually requested so legacy
        # providers that don't accept the kwargs still work.
        extra: dict[str, Any] = {}
        if thinking or thinking_budget is not None:
            extra["thinking"] = thinking
            extra["thinking_budget"] = thinking_budget

        last_exc: BaseException | None = None
        for tier in tiers_to_try:
            prov = self._providers_for_tier(tier)[0]
            first_event_seen = False
            try:
                async for event in prov.stream(
                    messages,
                    tools,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **extra,
                ):
                    first_event_seen = True
                    yield event
                self._track_usage(prov.cumulative_usage)
                return
            except BaseException as exc:  # noqa: BLE001
                last_exc = exc
                if first_event_seen:
                    raise
                if not self.escalate_on_error or not self._should_escalate(exc):
                    raise
                logger.warning(
                    "router: stream tier=%s (%s) failed (%s); escalating",
                    tier,
                    prov.name,
                    exc,
                )
        raise AllTiersExhaustedError(f"All router tiers exhausted: {','.join(tiers_to_try)}") from last_exc

    async def list_models(self) -> list[ModelInfo]:
        """Union of all tiers' models, deduped by id."""
        seen: dict[str, ModelInfo] = {}
        for tier in self._tier_order():
            for prov in self._providers_for_tier(tier):
                try:
                    for m in await prov.list_models():
                        seen.setdefault(m.id, m)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("router: list_models failed on %s: %s", prov.name, exc)
        return list(seen.values())

    # ------------------------------------------------------------------ #
    #  Observability
    # ------------------------------------------------------------------ #

    @property
    def tier_names(self) -> list[str]:
        return list(self._tier_names)
