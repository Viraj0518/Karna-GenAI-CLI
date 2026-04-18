"""Tests for the CostAwareRouterProvider."""

from __future__ import annotations

import httpx
import pytest

from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider
from karna.providers.router import AllTiersExhaustedError, CostAwareRouterProvider


class _StubProvider(BaseProvider):
    """Stub that returns a response (or raises) from ``complete``/``stream``."""

    name = "stub"
    base_url = ""

    def __init__(
        self,
        tag: str,
        *,
        exc: BaseException | None = None,
        response: str = "ok",
    ) -> None:
        super().__init__()
        self.name = f"stub-{tag}"
        self.tag = tag
        self._exc = exc
        self._response = response
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(self, messages, tools=None, *, system_prompt=None, max_tokens=None, temperature=None):  # type: ignore[override]
        self.complete_calls += 1
        if self._exc is not None:
            raise self._exc
        return Message(role="assistant", content=self._response)

    async def stream(self, messages, tools=None, *, system_prompt=None, max_tokens=None, temperature=None):  # type: ignore[override]
        self.stream_calls += 1
        if self._exc is not None:
            raise self._exc
        yield StreamEvent(type="text", text=self._response)
        yield StreamEvent(type="done")

    async def list_models(self):  # type: ignore[override]
        return [ModelInfo(id=f"m-{self.tag}", provider=self.name)]


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://x/")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError(f"{status}", request=req, response=resp)


# --------------------------------------------------------------------------- #
#  Construction
# --------------------------------------------------------------------------- #


def test_router_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CostAwareRouterProvider({})


def test_router_rejects_empty_tier() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CostAwareRouterProvider({"cheap": []})


# --------------------------------------------------------------------------- #
#  Tier selection by input size
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_router_picks_cheap_for_small_input() -> None:
    cheap = _StubProvider("cheap", response="cheap-answer")
    mid = _StubProvider("mid", response="mid-answer")
    premium = _StubProvider("prem", response="prem-answer")

    router = CostAwareRouterProvider({"cheap": [cheap], "mid": [mid], "premium": [premium]})
    msg = await router.complete([Message(role="user", content="hi")])
    assert msg.content == "cheap-answer"
    assert cheap.complete_calls == 1
    assert mid.complete_calls == 0
    assert premium.complete_calls == 0


@pytest.mark.asyncio
async def test_router_skips_cheap_when_context_too_large() -> None:
    cheap = _StubProvider("cheap", response="cheap")
    mid = _StubProvider("mid", response="mid")
    premium = _StubProvider("prem", response="prem")

    router = CostAwareRouterProvider(
        {"cheap": [cheap], "mid": [mid], "premium": [premium]},
        context_caps={"cheap": 100, "mid": 1_000_000, "premium": 10_000_000},
    )
    # A message ~500 chars / 4 ~= 125 tokens, > cheap cap (100)
    msg = await router.complete([Message(role="user", content="x" * 500)])
    assert msg.content == "mid"
    assert cheap.complete_calls == 0
    assert mid.complete_calls == 1


# --------------------------------------------------------------------------- #
#  Escalation on error
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_router_escalates_on_429() -> None:
    cheap = _StubProvider("cheap", exc=_http_error(429))
    mid = _StubProvider("mid", response="mid-result")

    router = CostAwareRouterProvider({"cheap": [cheap], "mid": [mid]})
    msg = await router.complete([Message(role="user", content="hi")])
    assert msg.content == "mid-result"
    assert cheap.complete_calls == 1
    assert mid.complete_calls == 1


@pytest.mark.asyncio
async def test_router_escalates_on_timeout() -> None:
    cheap = _StubProvider("cheap", exc=httpx.TimeoutException("slow"))
    mid = _StubProvider("mid", response="mid")

    router = CostAwareRouterProvider({"cheap": [cheap], "mid": [mid]})
    msg = await router.complete([Message(role="user", content="hi")])
    assert msg.content == "mid"


@pytest.mark.asyncio
async def test_router_non_retryable_error_propagates() -> None:
    # 400 is not escalate-worthy -> raise, don't try next tier.
    cheap = _StubProvider("cheap", exc=_http_error(400))
    mid = _StubProvider("mid", response="mid")

    router = CostAwareRouterProvider({"cheap": [cheap], "mid": [mid]})
    with pytest.raises(httpx.HTTPStatusError):
        await router.complete([Message(role="user", content="hi")])
    assert mid.complete_calls == 0


@pytest.mark.asyncio
async def test_router_all_tiers_exhausted() -> None:
    cheap = _StubProvider("cheap", exc=_http_error(503))
    mid = _StubProvider("mid", exc=_http_error(503))

    router = CostAwareRouterProvider({"cheap": [cheap], "mid": [mid]})
    with pytest.raises(AllTiersExhaustedError):
        await router.complete([Message(role="user", content="hi")])
    assert cheap.complete_calls == 1
    assert mid.complete_calls == 1


@pytest.mark.asyncio
async def test_router_escalate_on_error_false() -> None:
    cheap = _StubProvider("cheap", exc=_http_error(429))
    mid = _StubProvider("mid", response="mid")

    router = CostAwareRouterProvider(
        {"cheap": [cheap], "mid": [mid]},
        escalate_on_error=False,
    )
    with pytest.raises(httpx.HTTPStatusError):
        await router.complete([Message(role="user", content="hi")])
    assert mid.complete_calls == 0


# --------------------------------------------------------------------------- #
#  Streaming
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_router_stream_escalates_before_first_event() -> None:
    cheap = _StubProvider("cheap", exc=_http_error(429))
    mid = _StubProvider("mid", response="mid-stream")

    router = CostAwareRouterProvider({"cheap": [cheap], "mid": [mid]})
    events = [ev async for ev in router.stream([Message(role="user", content="hi")])]
    text = "".join(e.text or "" for e in events if e.type == "text")
    assert text == "mid-stream"


# --------------------------------------------------------------------------- #
#  list_models
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_models_union() -> None:
    a = _StubProvider("a")
    b = _StubProvider("b")
    router = CostAwareRouterProvider({"cheap": [a], "premium": [b]})
    models = await router.list_models()
    ids = sorted(m.id for m in models)
    assert ids == ["m-a", "m-b"]
