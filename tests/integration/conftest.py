"""Shared fixtures for E2E integration tests.

Exposes a reusable ``MockProvider`` and a ``mock_karna_home`` fixture
that redirects ``~/.karna/`` to a fresh ``tmp_path``. No test in this
package touches real credentials or real ``~/.karna`` state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Callable

import pytest

from karna.models import Message, ModelInfo, StreamEvent
from karna.providers.base import BaseProvider


class MockProvider(BaseProvider):
    """Scripted provider for deterministic end-to-end tests.

    *responses* is either a list of ``Message`` objects or a list of
    factories that take the current ``messages`` list and return a
    ``Message``. Factories let tests assert that later turns see
    earlier tool results / memory injection.

    Set *raise_once* to a callable-or-exception to inject a one-shot
    failure (used by the 401-retry test).
    """

    name = "mock"

    def __init__(
        self,
        responses: list[Message] | list[Callable[[list[Message]], Message]],
        *,
        raise_once: BaseException | None = None,
    ) -> None:
        super().__init__()
        self._responses: list[Any] = list(responses)
        self._raise_once = raise_once
        self.call_count = 0
        self.seen_system_prompts: list[str | None] = []
        self.seen_message_counts: list[int] = []

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Message:
        self.call_count += 1
        self.seen_system_prompts.append(system_prompt)
        self.seen_message_counts.append(len(messages))

        if self._raise_once is not None:
            exc = self._raise_once
            self._raise_once = None
            raise exc

        if not self._responses:
            return Message(role="assistant", content="(no more scripted responses)")

        nxt = self._responses.pop(0)
        if callable(nxt):
            return nxt(messages)
        return nxt

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        msg = await self.complete(
            messages,
            tools,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        if msg.content:
            yield StreamEvent(type="text", text=msg.content)

        for tc in msg.tool_calls:
            yield StreamEvent(type="tool_call_start", tool_call=tc)
            yield StreamEvent(type="tool_call_end", tool_call=tc)

        yield StreamEvent(type="done")

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock-1", name="mock-1", provider="mock")]


@pytest.fixture
def mock_karna_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``Path.home()`` so ``~/.karna`` lives in *tmp_path*.

    Subsystems that compute ``Path.home() / ".karna" / ...`` at call
    time (sessions, memory, skills, credentials) transparently land in
    the temp directory. Subsystems that cached the resolved path at
    import time are unaffected -- those take explicit path args in the
    tests below.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # ``Path.home`` reads HOME first on POSIX and USERPROFILE on
    # Windows; set both for cross-platform runs.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".karna").mkdir()
    return home / ".karna"
