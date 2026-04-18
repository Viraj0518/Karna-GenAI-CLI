"""Tests for the MixtureOfAgentsProvider.

All providers are stubs — no HTTP. Covers:
- Construction guards (empty list, bad strategy)
- ``synthesis`` strategy passes all candidates to the aggregator
- ``vote`` strategy picks the majority answer
- ``best-of-n`` strategy asks the aggregator to pick one
- Partial failures: a crashing instance doesn't take down the run
- All-failed: raises MoAError
"""

from __future__ import annotations

import pytest

from karna.models import Message, ModelInfo
from karna.providers.base import BaseProvider
from karna.providers.moa import MixtureOfAgentsProvider, MoAError


class _StubProvider(BaseProvider):
    """Minimal stub: returns a preset response (or raises a preset exception)."""

    name = "stub"
    base_url = ""

    def __init__(
        self,
        tag: str,
        response: str,
        *,
        exc: BaseException | None = None,
    ) -> None:
        super().__init__()
        self.name = f"stub-{tag}"
        self.tag = tag
        self.response = response
        self._exc = exc
        self.received_prompts: list[str] = []

    async def complete(self, messages, tools=None, *, system_prompt=None, max_tokens=None, temperature=None):  # type: ignore[override]
        if self._exc is not None:
            raise self._exc
        if messages:
            self.received_prompts.append(messages[-1].content)
        return Message(role="assistant", content=self.response)

    async def stream(self, messages, tools=None, *, system_prompt=None, max_tokens=None, temperature=None):  # type: ignore[override]
        yield  # pragma: no cover

    async def list_models(self):  # type: ignore[override]
        return [ModelInfo(id=f"m-{self.tag}", provider=self.name)]


def _user(text: str) -> list[Message]:
    return [Message(role="user", content=text)]


# --------------------------------------------------------------------------- #
#  Construction
# --------------------------------------------------------------------------- #


def test_moa_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        MixtureOfAgentsProvider([])


def test_moa_rejects_bad_strategy() -> None:
    a = _StubProvider("a", "x")
    with pytest.raises(ValueError, match="strategy"):
        MixtureOfAgentsProvider([(a, "m")], strategy="nope")


# --------------------------------------------------------------------------- #
#  Strategy: synthesis
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_synthesis_passes_all_candidates_to_aggregator() -> None:
    a = _StubProvider("a", "answer A")
    b = _StubProvider("b", "answer B")
    c = _StubProvider("c", "answer C")
    aggregator = _StubProvider("agg", "SYNTHESIS RESULT")

    moa = MixtureOfAgentsProvider(
        [(a, "ma"), (b, "mb"), (c, "mc")],
        strategy="synthesis",
        aggregator=aggregator,
    )
    result = await moa.complete(_user("what is X?"))
    assert result.content == "SYNTHESIS RESULT"

    # Aggregator saw all three candidate texts in its combined prompt.
    assert len(aggregator.received_prompts) == 1
    combined = aggregator.received_prompts[0]
    assert "answer A" in combined
    assert "answer B" in combined
    assert "answer C" in combined
    assert "what is X?" in combined  # original question echoed in


# --------------------------------------------------------------------------- #
#  Strategy: vote
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_vote_picks_majority() -> None:
    # Two say "blue", one says "red" -> majority wins.
    a = _StubProvider("a", "blue")
    b = _StubProvider("b", "BLUE")  # normalizer should treat same as blue
    c = _StubProvider("c", "red")

    moa = MixtureOfAgentsProvider(
        [(a, "ma"), (b, "mb"), (c, "mc")],
        strategy="vote",
    )
    result = await moa.complete(_user("color?"))
    assert result.content.lower() == "blue"


@pytest.mark.asyncio
async def test_vote_no_majority_falls_back_to_first() -> None:
    a = _StubProvider("a", "alpha")
    b = _StubProvider("b", "beta")
    c = _StubProvider("c", "gamma")
    moa = MixtureOfAgentsProvider(
        [(a, "ma"), (b, "mb"), (c, "mc")],
        strategy="vote",
    )
    result = await moa.complete(_user("pick"))
    assert result.content == "alpha"  # tie -> first candidate


# --------------------------------------------------------------------------- #
#  Strategy: best-of-n
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_best_of_n_uses_aggregator() -> None:
    a = _StubProvider("a", "candidate A")
    b = _StubProvider("b", "candidate B")
    aggregator = _StubProvider("agg", "candidate B")  # judge picks B

    moa = MixtureOfAgentsProvider(
        [(a, "ma"), (b, "mb")],
        strategy="best-of-n",
        aggregator=aggregator,
    )
    result = await moa.complete(_user("q"))
    assert result.content == "candidate B"
    assert aggregator.received_prompts, "aggregator must be called"


# --------------------------------------------------------------------------- #
#  Failure handling
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_partial_failure_still_synthesizes() -> None:
    good = _StubProvider("good", "the answer")
    bad = _StubProvider("bad", "", exc=RuntimeError("boom"))
    aggregator = _StubProvider("agg", "MERGED")

    moa = MixtureOfAgentsProvider(
        [(good, "m1"), (bad, "m2")],
        strategy="synthesis",
        aggregator=aggregator,
    )
    result = await moa.complete(_user("q"))
    assert result.content == "MERGED"


@pytest.mark.asyncio
async def test_all_failed_raises() -> None:
    a = _StubProvider("a", "", exc=RuntimeError("x"))
    b = _StubProvider("b", "", exc=RuntimeError("y"))
    moa = MixtureOfAgentsProvider([(a, "ma"), (b, "mb")])
    with pytest.raises(MoAError):
        await moa.complete(_user("q"))


# --------------------------------------------------------------------------- #
#  list_models dedupes across instances
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_models_dedupes() -> None:
    a = _StubProvider("a", "x")
    b = _StubProvider("a", "y")  # same tag -> same model id
    moa = MixtureOfAgentsProvider([(a, "ma"), (b, "mb")])
    models = await moa.list_models()
    ids = [m.id for m in models]
    assert ids == ["m-a"]
