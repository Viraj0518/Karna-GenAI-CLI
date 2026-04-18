"""Tests for karna.compaction.compactor.auto_compact."""

from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest

from karna.compaction.compactor import (
    CompactionError,
    auto_compact,
    should_compact,
)
from karna.models import Conversation, Message, ModelInfo, StreamEvent, ToolResult
from karna.providers.base import BaseProvider


class _ScriptedProvider(BaseProvider):
    """Provider that returns a scripted sequence of completion results.

    Each item may be a str (returned as Message.content) or an
    Exception (raised).  ``.calls`` records every (messages, kwargs)
    tuple passed in so tests can inspect the scrubbed prompt.
    """

    name = "scripted"

    def __init__(self, script: list[Any]) -> None:
        super().__init__()
        self._script = list(script)
        self.calls: list[tuple[list[Message], dict[str, Any]]] = []

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
        self.calls.append((list(messages), {"max_tokens": max_tokens}))
        if not self._script:
            return Message(role="assistant", content="(default)")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return Message(role="assistant", content=item)

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
        msg = await self.complete(messages, tools)
        yield StreamEvent(type="text", text=msg.content)
        yield StreamEvent(type="done")

    async def list_models(self) -> list[ModelInfo]:
        return []


def _big_conv(n_middle: int = 20, filler: str = "x" * 2000) -> Conversation:
    """Build a conversation with head + middle + tail zones."""
    msgs = [
        Message(role="system", content="you are a helpful agent"),
        Message(role="user", content="initial task: build thing"),
    ]
    for i in range(n_middle):
        role = "assistant" if i % 2 == 0 else "user"
        msgs.append(Message(role=role, content=f"turn {i} {filler}"))
    for i in range(8):
        role = "assistant" if i % 2 == 0 else "user"
        msgs.append(Message(role=role, content=f"tail {i}"))
    return Conversation(messages=msgs)


@pytest.mark.asyncio
async def test_under_budget_returns_unchanged() -> None:
    conv = Conversation(
        messages=[
            Message(role="system", content="sys"),
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
    )
    provider = _ScriptedProvider([])
    before = [m.content for m in conv.messages]
    result = await auto_compact(conv, provider, model="gpt-4o", budget_tokens=100_000)
    after = [m.content for m in result.messages]
    assert before == after
    assert provider.calls == []  # no summariser call made


@pytest.mark.asyncio
async def test_over_budget_compresses_middle() -> None:
    conv = _big_conv(n_middle=20)
    provider = _ScriptedProvider(["<summary of middle>"])
    original_head = conv.messages[:2]
    original_tail = conv.messages[-8:]

    result = await auto_compact(
        conv,
        provider,
        model="gpt-4o",
        budget_tokens=2_000,  # way under actual usage
        head_turns_to_keep=2,
        tail_turns_to_keep=8,
    )

    # head preserved
    assert [m.content for m in result.messages[:2]] == [m.content for m in original_head]
    # tail preserved
    assert [m.content for m in result.messages[-8:]] == [m.content for m in original_tail]
    # middle replaced with a single system summary
    middle_msgs = result.messages[2:-8]
    assert len(middle_msgs) == 1
    assert middle_msgs[0].role == "system"
    assert "Compacted summary" in middle_msgs[0].content
    assert "<summary of middle>" in middle_msgs[0].content
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_summary_failure_three_times_raises() -> None:
    conv = _big_conv(n_middle=20)
    provider = _ScriptedProvider(
        [
            RuntimeError("boom-1"),
            RuntimeError("boom-2"),
            RuntimeError("boom-3"),
        ]
    )
    with patch("asyncio.sleep"):  # don't actually sleep between retries
        with pytest.raises(CompactionError):
            await auto_compact(
                conv,
                provider,
                model="gpt-4o",
                budget_tokens=2_000,
            )
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_retries_until_success() -> None:
    conv = _big_conv(n_middle=20)
    provider = _ScriptedProvider([RuntimeError("flaky"), "<recovered summary>"])
    with patch("asyncio.sleep"):
        result = await auto_compact(
            conv,
            provider,
            model="gpt-4o",
            budget_tokens=2_000,
        )
    middle_msgs = result.messages[2:-8]
    assert len(middle_msgs) == 1
    assert "<recovered summary>" in middle_msgs[0].content
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_secret_in_middle_is_scrubbed_before_summary_call() -> None:
    """A leaked API key in a tool result must NOT be sent to the summariser."""
    leaked_key = "sk-ant-api03-" + "A" * 60
    msgs = [
        Message(role="system", content="sys"),
        Message(role="user", content="task"),
    ]
    # 20 middle turns — one of them carries a leaked key
    for i in range(20):
        if i == 5:
            msgs.append(
                Message(
                    role="tool",
                    tool_results=[
                        ToolResult(
                            tool_call_id="t1",
                            content=f"API response: {leaked_key} plus " + "x" * 1500,
                            is_error=False,
                        )
                    ],
                )
            )
        else:
            msgs.append(Message(role="assistant", content="turn " + "x" * 2000))
    for i in range(8):
        msgs.append(Message(role="user", content=f"tail {i}"))

    conv = Conversation(messages=msgs)
    provider = _ScriptedProvider(["<summary>"])

    await auto_compact(
        conv,
        provider,
        model="gpt-4o",
        budget_tokens=2_000,
    )

    assert len(provider.calls) == 1
    sent_msgs, _ = provider.calls[0]
    combined = "\n".join(m.content for m in sent_msgs)
    assert leaked_key not in combined, "Leaked secret was shipped to summariser!"
    assert "<REDACTED_SECRET>" in combined


def test_should_compact_threshold() -> None:
    small = Conversation(messages=[Message(role="user", content="hi")])
    assert should_compact(small, budget=1000) is False

    huge = Conversation(messages=[Message(role="user", content="x" * 100_000)])
    assert should_compact(huge, budget=1000) is True


def test_should_compact_zero_budget() -> None:
    conv = Conversation(messages=[Message(role="user", content="hi")])
    assert should_compact(conv, budget=0) is False
