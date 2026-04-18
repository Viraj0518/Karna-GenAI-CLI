"""Mixture-of-Agents (MoA) provider — multi-model verification.

Runs the same prompt across N underlying providers and produces a
single synthesized answer. Supported strategies:

* ``synthesis`` (default): all N candidates are passed to an aggregator
  provider which merges them into the final answer.
* ``vote``: candidates are grouped by normalized content; the largest
  cluster wins. Ties fall back to the first candidate.
* ``best-of-n``: all N candidates are passed to an aggregator which is
  asked to pick the single best answer verbatim.

Streaming is implemented by running :meth:`complete` and yielding the
final text as a single ``text`` event followed by ``done``. This keeps
the streaming interface compatible without complicating the multi-model
aggregation.

Usage::

    nellie model set moa:openrouter/claude-sonnet-4-5,openrouter/gpt-4o,openrouter/kimi-k2

The CLI parser constructs the instance list; the provider itself only
cares about already-constructed :class:`BaseProvider` objects.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from typing import Any, AsyncIterator

from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider

logger = logging.getLogger(__name__)


_SUPPORTED_STRATEGIES = frozenset({"synthesis", "vote", "best-of-n"})

_SYNTHESIS_SYSTEM_PROMPT = (
    "You are an aggregator reviewing answers from multiple AI assistants. "
    "Synthesize the strongest, most accurate combined response. "
    "Resolve disagreements in favor of verifiable facts. "
    "Do not mention the aggregation process; emit only the final answer."
)

_BEST_OF_N_SYSTEM_PROMPT = (
    "You are a judge reviewing answers from multiple AI assistants. "
    "Select the single best answer verbatim — do not edit or merge. "
    "Reply with only the chosen answer's text, nothing else."
)


class MoAError(RuntimeError):
    """Raised when MoA cannot produce a result (e.g. all candidates failed)."""


class MixtureOfAgentsProvider(BaseProvider):
    """Run the same prompt across N providers and synthesize the best answer.

    :param instances: list of ``(provider, model)`` tuples. ``model`` is
        informational — providers are expected to be pre-configured with
        the right model.
    :param strategy: one of ``"synthesis"``, ``"vote"``, ``"best-of-n"``.
    :param aggregator: optional provider used for synthesis / best-of-n.
        Defaults to ``instances[0][0]``.
    """

    base_url = ""

    def __init__(
        self,
        instances: list[tuple[BaseProvider, str]],
        *,
        strategy: str = "synthesis",
        aggregator: BaseProvider | None = None,
    ) -> None:
        if not instances:
            raise ValueError("MixtureOfAgentsProvider requires at least one instance")
        if strategy not in _SUPPORTED_STRATEGIES:
            raise ValueError(f"unknown strategy {strategy!r}; expected one of {sorted(_SUPPORTED_STRATEGIES)}")
        super().__init__()
        self._instances: list[tuple[BaseProvider, str]] = list(instances)
        self._strategy = strategy
        self._aggregator = aggregator or instances[0][0]
        model_tag = ",".join(model or prov.name for prov, model in instances)
        self.name = f"moa:{strategy}:{model_tag}"

    # ------------------------------------------------------------------ #
    #  Candidate collection
    # ------------------------------------------------------------------ #

    async def _gather_candidates(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None,
        *,
        system_prompt: str | None,
        max_tokens: int | None,
        temperature: float | None,
    ) -> list[Message]:
        """Run every instance in parallel and return successful candidates."""

        async def _one(prov: BaseProvider) -> Message | BaseException:
            try:
                return await prov.complete(
                    messages,
                    tools,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except BaseException as exc:  # noqa: BLE001 — capture per-instance
                logger.warning("MoA: instance %s failed: %s", prov.name, exc)
                return exc

        results = await asyncio.gather(*[_one(prov) for prov, _ in self._instances])
        candidates = [r for r in results if isinstance(r, Message)]
        if not candidates:
            # Surface the first exception to aid debugging.
            for r in results:
                if isinstance(r, BaseException):
                    raise MoAError("all MoA instances failed") from r
            raise MoAError("all MoA instances failed")
        return candidates

    # ------------------------------------------------------------------ #
    #  Aggregation strategies
    # ------------------------------------------------------------------ #

    async def _synthesize(
        self,
        candidates: list[Message],
        original_messages: list[Message],
    ) -> Message:
        """Ask the aggregator to merge all candidate responses."""
        user_block = _render_original(original_messages)
        numbered = "\n\n".join(f"### Candidate {i + 1}\n{msg.content.strip()}" for i, msg in enumerate(candidates))
        synthesis_prompt = (
            f"{user_block}\n\n---\n\nCandidate answers:\n\n{numbered}\n\nSynthesize the best combined answer."
        )
        agg_msgs = [Message(role="user", content=synthesis_prompt)]
        return await self._aggregator.complete(
            agg_msgs,
            None,
            system_prompt=_SYNTHESIS_SYSTEM_PROMPT,
        )

    def _vote(self, candidates: list[Message]) -> Message:
        """Majority-vote over normalized candidate text."""
        normalized = [(_normalize(c.content), c) for c in candidates]
        counts: Counter[str] = Counter(n for n, _ in normalized)
        top, top_count = counts.most_common(1)[0]
        # Tie-break: when top count is 1 (no real majority), return the
        # first candidate rather than an arbitrary one.
        if top_count == 1:
            return candidates[0]
        for norm, msg in normalized:
            if norm == top:
                return msg
        return candidates[0]  # unreachable

    async def _best_of_n(
        self,
        candidates: list[Message],
        original_messages: list[Message],
    ) -> Message:
        """Ask the aggregator to pick the best candidate verbatim."""
        user_block = _render_original(original_messages)
        numbered = "\n\n".join(f"### Candidate {i + 1}\n{msg.content.strip()}" for i, msg in enumerate(candidates))
        prompt = (
            f"{user_block}\n\n---\n\nCandidate answers:\n\n{numbered}\n\n"
            "Pick the single best candidate and reply with only its text."
        )
        agg_msgs = [Message(role="user", content=prompt)]
        return await self._aggregator.complete(
            agg_msgs,
            None,
            system_prompt=_BEST_OF_N_SYSTEM_PROMPT,
        )

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
        candidates = await self._gather_candidates(
            messages,
            tools,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if self._strategy == "vote":
            return self._vote(candidates)
        if self._strategy == "best-of-n":
            return await self._best_of_n(candidates, messages)
        # Default: synthesis
        return await self._synthesize(candidates, messages)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        # MoA requires all candidates before synthesis, so streaming is
        # simulated: run `complete`, emit the text as one event.
        final = await self.complete(
            messages,
            tools,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if final.content:
            yield StreamEvent(type="text", text=final.content)
        yield StreamEvent(type="done")

    async def list_models(self) -> list[ModelInfo]:
        """Union of every underlying instance's model list, deduped by id."""
        seen: dict[str, ModelInfo] = {}
        for prov, _ in self._instances:
            try:
                for m in await prov.list_models():
                    seen.setdefault(m.id, m)
            except Exception as exc:  # noqa: BLE001
                logger.debug("MoA: list_models failed on %s: %s", prov.name, exc)
        return list(seen.values())


# --------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------- #


def _render_original(messages: list[Message]) -> str:
    """Render the original conversation for the aggregator prompt."""
    parts: list[str] = []
    for m in messages:
        role = m.role.capitalize()
        parts.append(f"{role}: {m.content}")
    return "\n\n".join(parts) if parts else ""


_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Whitespace- and case-normalize for voting."""
    return _WS_RE.sub(" ", (text or "").strip().lower())
