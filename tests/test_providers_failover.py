"""Tests for the FailoverProvider.

Covers:
- Empty instance list rejected
- First-call success short-circuits to instance 0
- 429 on instance 0 rotates to instance 1
- Auth error (401) rotates
- All instances in cooldown raises AllInstancesExhaustedError
- Non-failover exception propagates (not rotated)
- list_models merges & dedupes
- stream() first-event-seen locks to the instance (mid-stream failure surfaces)
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest

from karna.models import Message, ModelInfo, StreamEvent, Usage
from karna.providers.base import BaseProvider
from karna.providers.failover import (
    AllInstancesExhaustedError,
    FailoverProvider,
    _is_failover_exception,
)


class _StubProvider(BaseProvider):
    """A BaseProvider stub for failover tests.

    ``complete`` returns ``response`` unless ``exc`` is set, in which case
    it raises. ``stream`` yields ``events`` unless ``exc`` is set.
    """

    name = "stub"
    base_url = ""

    def __init__(
        self,
        tag: str,
        *,
        exc: BaseException | None = None,
        response: str = "ok",
        models: list[ModelInfo] | None = None,
        stream_events: list[StreamEvent] | None = None,
        exc_after_events: int = 0,
    ) -> None:
        super().__init__()
        self.name = f"stub-{tag}"
        self.tag = tag
        self._exc = exc
        self._response = response
        self._models = models or [ModelInfo(id=f"m-{tag}", provider=self.name)]
        self._stream_events = stream_events
        self._exc_after_events = exc_after_events
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(self, messages, tools=None, *, system_prompt=None, max_tokens=None, temperature=None):  # type: ignore[override]
        self.complete_calls += 1
        if self._exc is not None:
            raise self._exc
        return Message(role="assistant", content=self._response)

    async def stream(self, messages, tools=None, *, system_prompt=None, max_tokens=None, temperature=None):  # type: ignore[override]
        self.stream_calls += 1
        events = self._stream_events or [
            StreamEvent(type="text", text=self._response),
            StreamEvent(type="done"),
        ]
        for i, ev in enumerate(events):
            if self._exc is not None and i == self._exc_after_events:
                raise self._exc
            yield ev

    async def list_models(self) -> list[ModelInfo]:
        if self._exc is not None:
            raise self._exc
        return self._models


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://x/")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError(f"{status}", request=req, response=resp)


# --------------------------------------------------------------------------- #
#  Construction
# --------------------------------------------------------------------------- #


def test_failover_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FailoverProvider([])


def test_failover_name_prefix() -> None:
    fp = FailoverProvider([_StubProvider("a")])
    assert fp.name == "failover:stub-a"


# --------------------------------------------------------------------------- #
#  _is_failover_exception
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503, 504])
def test_is_failover_exception_http_status(status: int) -> None:
    assert _is_failover_exception(_http_error(status)) is True


def test_is_failover_exception_skips_400() -> None:
    assert _is_failover_exception(_http_error(400)) is False


def test_is_failover_exception_timeout() -> None:
    assert _is_failover_exception(httpx.ReadTimeout("slow")) is True


def test_is_failover_exception_skips_value_error() -> None:
    assert _is_failover_exception(ValueError("no key")) is False


# --------------------------------------------------------------------------- #
#  complete() rotation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_complete_first_instance_success() -> None:
    a = _StubProvider("a", response="from-a")
    b = _StubProvider("b", response="from-b")
    fp = FailoverProvider([a, b])
    msg = await fp.complete([Message(role="user", content="x")])
    assert msg.content == "from-a"
    assert a.complete_calls == 1
    assert b.complete_calls == 0


@pytest.mark.asyncio
async def test_complete_rotates_on_429() -> None:
    a = _StubProvider("a", exc=_http_error(429))
    b = _StubProvider("b", response="from-b")
    fp = FailoverProvider([a, b])
    msg = await fp.complete([Message(role="user", content="x")])
    assert msg.content == "from-b"
    assert a.complete_calls == 1
    assert b.complete_calls == 1
    # A's cooldown is now > 0.
    assert fp.cooldown_status[0] > 0
    assert fp.cooldown_status[1] == 0


@pytest.mark.asyncio
async def test_complete_rotates_on_auth_error() -> None:
    a = _StubProvider("a", exc=_http_error(401))
    b = _StubProvider("b", response="from-b")
    fp = FailoverProvider([a, b])
    msg = await fp.complete([Message(role="user", content="x")])
    assert msg.content == "from-b"


@pytest.mark.asyncio
async def test_complete_all_exhausted_raises() -> None:
    a = _StubProvider("a", exc=_http_error(503))
    b = _StubProvider("b", exc=_http_error(503))
    fp = FailoverProvider([a, b])
    with pytest.raises(AllInstancesExhaustedError):
        await fp.complete([Message(role="user", content="x")])
    # Both cooldowns should now be active.
    assert fp.cooldown_status[0] > 0
    assert fp.cooldown_status[1] > 0


@pytest.mark.asyncio
async def test_complete_non_failover_exc_propagates() -> None:
    a = _StubProvider("a", exc=ValueError("misconfigured"))
    b = _StubProvider("b")
    fp = FailoverProvider([a, b])
    with pytest.raises(ValueError, match="misconfigured"):
        await fp.complete([Message(role="user", content="x")])
    # b never tried — config errors don't rotate.
    assert b.complete_calls == 0


# --------------------------------------------------------------------------- #
#  stream()
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_stream_rotates_before_first_event() -> None:
    a = _StubProvider("a", exc=_http_error(429), exc_after_events=0)
    b = _StubProvider("b", response="from-b")
    fp = FailoverProvider([a, b])
    events = [e async for e in fp.stream([Message(role="user", content="x")])]
    texts = [e.text for e in events if e.type == "text"]
    assert "from-b" in texts


@pytest.mark.asyncio
async def test_stream_mid_stream_exception_surfaces() -> None:
    # Instance a yields one event then fails. Failover must NOT silently
    # swap — the partial output is already visible.
    a = _StubProvider(
        "a",
        exc=_http_error(429),
        exc_after_events=1,
        stream_events=[StreamEvent(type="text", text="partial"), StreamEvent(type="done")],
    )
    b = _StubProvider("b")
    fp = FailoverProvider([a, b])
    collected: list[StreamEvent] = []
    with pytest.raises(httpx.HTTPStatusError):
        async for ev in fp.stream([Message(role="user", content="x")]):
            collected.append(ev)
    assert any(e.text == "partial" for e in collected if e.type == "text")
    assert b.stream_calls == 0


# --------------------------------------------------------------------------- #
#  list_models merge
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_models_merges_and_dedupes() -> None:
    shared = ModelInfo(id="shared-model", provider="stub")
    a = _StubProvider("a", models=[shared, ModelInfo(id="only-a", provider="stub")])
    b = _StubProvider("b", models=[shared, ModelInfo(id="only-b", provider="stub")])
    fp = FailoverProvider([a, b])
    models = await fp.list_models()
    ids = {m.id for m in models}
    assert ids == {"shared-model", "only-a", "only-b"}


@pytest.mark.asyncio
async def test_list_models_skips_cooldown_instances() -> None:
    a = _StubProvider("a", models=[ModelInfo(id="from-a", provider="stub")])
    b = _StubProvider("b", models=[ModelInfo(id="from-b", provider="stub")])
    fp = FailoverProvider([a, b])
    # Force cooldown on instance 0.
    fp._mark_failure(0)
    models = await fp.list_models()
    ids = {m.id for m in models}
    assert "from-b" in ids
    assert "from-a" not in ids
