"""Tests for auto-compaction integration into agent loop, slash command, and REPL.

Covers:
- Auto-trigger threshold (80% of context window)
- /compact slash command handler
- Circuit breaker (3 failures -> stop trying)
- Preserved tail messages are never summarized
- Token estimation
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from karna.agents.loop import _estimate_message_tokens, agent_loop, agent_loop_sync
from karna.compaction.compactor import _PRESERVE_TAIL, Compactor, _estimate_tokens
from karna.models import (
    Conversation,
    Message,
    ModelInfo,
    StreamEvent,
)
from karna.providers.base import BaseProvider

# ======================================================================= #
#  Mock provider
# ======================================================================= #


class MockProvider(BaseProvider):
    """Provider that returns scripted responses for testing."""

    name = "mock"

    def __init__(self, responses: list[Message]) -> None:
        super().__init__()
        self._responses = list(responses)
        self.call_count = 0

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
        if not self._responses:
            return Message(role="assistant", content="(no more scripted responses)")
        return self._responses.pop(0)

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
        return []


# ======================================================================= #
#  Tests: auto-compaction trigger in agent loop
# ======================================================================= #


class TestAutoCompactionTrigger:
    """Verify auto-compaction fires at 80% of context window after a turn."""

    @pytest.mark.asyncio
    async def test_compaction_fires_at_80_percent(self):
        """When estimated tokens exceed 80% of context_window, compaction triggers."""
        # Build a conversation with enough content to exceed 80% of a small window.
        # Each message ~400 chars = ~100 tokens. 10 messages = ~1000 tokens.
        # Context window = 1000. 80% threshold = 800.
        messages = [Message(role="user" if i % 2 == 0 else "assistant", content="x" * 400) for i in range(10)]

        # The final assistant response from the agent loop
        provider = MockProvider(
            [
                Message(role="assistant", content="Final answer."),
            ]
        )

        # Summary response from the compactor's summarization call
        summary_provider = MockProvider(
            [
                Message(role="assistant", content="COMPACTED: earlier conversation summary."),
            ]
        )

        compactor = Compactor(summary_provider, threshold=0.80)
        conv = Conversation(messages=messages)
        original_count = len(conv.messages)

        events = []
        async for event in agent_loop(
            provider,
            conv,
            [],
            context_window=1000,
            compactor=compactor,
        ):
            events.append(event)

        # Compaction should have reduced message count
        assert len(conv.messages) < original_count + 1

        # The summary message should be present
        contents = " ".join(m.content for m in conv.messages)
        assert "COMPACTED" in contents

        # The compactor's provider should have been called for summarization
        assert summary_provider.call_count >= 1

    @pytest.mark.asyncio
    async def test_no_compaction_below_threshold(self):
        """When tokens are below 80%, no compaction fires."""
        # 3 short messages = ~15 tokens, well below 80% of 10000
        messages = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there!"),
            Message(role="user", content="How are you?"),
        ]

        provider = MockProvider(
            [
                Message(role="assistant", content="I'm fine."),
            ]
        )

        summary_provider = MockProvider(
            [
                Message(role="assistant", content="SHOULD NOT APPEAR"),
            ]
        )

        compactor = Compactor(summary_provider, threshold=0.80)
        conv = Conversation(messages=messages)

        events = []
        async for event in agent_loop(
            provider,
            conv,
            [],
            context_window=10000,
            compactor=compactor,
        ):
            events.append(event)

        # No compaction should have happened
        assert summary_provider.call_count == 0

        # Conversation should just have the original + final response
        assert conv.messages[-1].content == "I'm fine."

    @pytest.mark.asyncio
    async def test_compaction_in_sync_loop(self):
        """Auto-compaction also works in the non-streaming agent_loop_sync."""
        messages = [Message(role="user" if i % 2 == 0 else "assistant", content="y" * 400) for i in range(10)]

        provider = MockProvider(
            [
                Message(role="assistant", content="Sync final."),
            ]
        )

        summary_provider = MockProvider(
            [
                Message(role="assistant", content="SYNC COMPACTED summary."),
            ]
        )

        compactor = Compactor(summary_provider, threshold=0.80)
        conv = Conversation(messages=messages)

        result = await agent_loop_sync(
            provider,
            conv,
            [],
            context_window=1000,
            compactor=compactor,
        )

        assert result.content == "Sync final."
        # Compaction should have reduced messages
        assert len(conv.messages) < 11
        contents = " ".join(m.content for m in conv.messages)
        assert "SYNC COMPACTED" in contents


# ======================================================================= #
#  Tests: circuit breaker
# ======================================================================= #


class TestCircuitBreaker:
    """Verify the circuit breaker stops compaction after 3 consecutive failures."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_after_3_failures(self):
        """After 3 compaction failures, should_compact returns False."""
        # Create a provider that always fails
        failing_provider = MockProvider([])

        async def _fail(*args, **kwargs):
            raise RuntimeError("Provider down")

        failing_provider.complete = _fail  # type: ignore[assignment]

        compactor = Compactor(failing_provider, threshold=0.50)

        # Build large conversation
        messages = [Message(role="user" if i % 2 == 0 else "assistant", content="z" * 400) for i in range(10)]
        conv = Conversation(messages=messages)

        # Attempt compaction 3 times -- each should fail but not crash
        for _ in range(3):
            result = await compactor.compact(conv, context_window=1000)
            assert result is conv  # returns unchanged on failure

        # Circuit breaker should now be tripped
        assert compactor.circuit_breaker_tripped
        assert not await compactor.should_compact(conv.messages, 1000)

    @pytest.mark.asyncio
    async def test_circuit_breaker_skips_compaction_in_loop(self):
        """When circuit breaker is tripped, agent loop skips compaction."""
        messages = [Message(role="user" if i % 2 == 0 else "assistant", content="w" * 400) for i in range(10)]

        provider = MockProvider(
            [
                Message(role="assistant", content="Answer."),
            ]
        )

        summary_provider = MockProvider([])
        compactor = Compactor(summary_provider, threshold=0.80)
        # Pre-trip the circuit breaker
        compactor.consecutive_failures = 3

        conv = Conversation(messages=messages)

        events = []
        async for event in agent_loop(
            provider,
            conv,
            [],
            context_window=1000,
            compactor=compactor,
        ):
            events.append(event)

        # No compaction should have happened (circuit breaker tripped)
        assert summary_provider.call_count == 0


# ======================================================================= #
#  Tests: preserved tail messages
# ======================================================================= #


class TestPreservedTail:
    """Verify that the last N messages are never summarized."""

    @pytest.mark.asyncio
    async def test_tail_messages_preserved(self):
        """The last _PRESERVE_TAIL messages must survive compaction."""
        # Create messages with identifiable content
        messages = []
        for i in range(15):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append(Message(role=role, content=f"Message-{i} " + "x" * 300))

        summary_provider = MockProvider(
            [
                Message(role="assistant", content="SUMMARY of old messages."),
            ]
        )

        compactor = Compactor(summary_provider, threshold=0.50)
        conv = Conversation(messages=messages)

        result = await compactor.compact(conv, context_window=1000)

        # The last _PRESERVE_TAIL messages should be present
        preserved_contents = [m.content for m in result.messages[-_PRESERVE_TAIL:]]
        for i in range(15 - _PRESERVE_TAIL, 15):
            expected_prefix = f"Message-{i}"
            assert any(expected_prefix in c for c in preserved_contents), (
                f"Message-{i} should have been preserved in tail"
            )

    @pytest.mark.asyncio
    async def test_system_message_preserved(self):
        """System message (if present) is always preserved."""
        messages = [
            Message(role="system", content="You are a helpful assistant."),
        ]
        for i in range(12):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append(Message(role=role, content=f"Msg-{i} " + "a" * 300))

        summary_provider = MockProvider(
            [
                Message(role="assistant", content="SUMMARY."),
            ]
        )

        compactor = Compactor(summary_provider, threshold=0.50)
        conv = Conversation(messages=messages)

        result = await compactor.compact(conv, context_window=1000)

        # System message should be the first message
        assert result.messages[0].role == "system"
        assert result.messages[0].content == "You are a helpful assistant."


# ======================================================================= #
#  Tests: /compact slash command
# ======================================================================= #


class TestCompactSlashCommand:
    """Verify the /compact slash command works end-to-end."""

    def test_compact_command_with_provider(self):
        """The /compact command runs compaction and shows results."""
        from io import StringIO
        from unittest.mock import patch as mock_patch

        from rich.console import Console

        from karna.config import KarnaConfig
        from karna.tui.slash import handle_slash_command

        # Build a conversation large enough to compact (> 6 messages)
        messages = [Message(role="user" if i % 2 == 0 else "assistant", content="x" * 400) for i in range(10)]
        conv = Conversation(messages=messages, model="mock-model", provider="mock")

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        config = KarnaConfig()

        mock_provider = MockProvider(
            [
                Message(role="assistant", content="COMPACT SUMMARY."),
            ]
        )

        with mock_patch("karna.providers.get_provider", return_value=mock_provider):
            with mock_patch("karna.providers.resolve_model", return_value=("mock", "mock-model")):
                handle_slash_command(
                    "/compact",
                    console,
                    config,
                    conv,
                )

        rendered = output.getvalue()
        # Should show some compaction output (before/after or success message)
        assert len(rendered) > 0

    def test_compact_command_too_few_messages(self):
        """The /compact command handles conversations that are too short."""
        from io import StringIO

        from rich.console import Console

        from karna.config import KarnaConfig
        from karna.tui.slash import handle_slash_command

        conv = Conversation(
            messages=[Message(role="user", content="Hi")],
            model="mock-model",
            provider="mock",
        )

        output = StringIO()
        console = Console(file=output, force_terminal=True, width=120)
        config = KarnaConfig()

        handle_slash_command(
            "/compact",
            console,
            config,
            conv,
        )

        rendered = output.getvalue()
        # Should indicate nothing to compact
        assert "Not enough" in rendered


# ======================================================================= #
#  Tests: token estimation
# ======================================================================= #


class TestTokenEstimation:
    """Verify token estimation logic."""

    def test_estimate_tokens_basic(self):
        """Basic token estimation: ~4 chars per token."""
        messages = [Message(role="user", content="a" * 400)]
        assert _estimate_tokens(messages) == 100

    def test_estimate_tokens_with_tool_results(self):
        """Token estimation includes tool result content."""
        from karna.models import ToolResult

        messages = [
            Message(
                role="tool",
                content="",
                tool_results=[ToolResult(tool_call_id="t1", content="b" * 400)],
            )
        ]
        assert _estimate_tokens(messages) == 100

    def test_estimate_tokens_empty(self):
        """Empty message list returns 0."""
        assert _estimate_tokens([]) == 0

    def test_agent_loop_estimate_tokens(self):
        """Agent loop's internal token estimator works."""
        messages = [Message(role="user", content="c" * 400)]
        assert _estimate_message_tokens(messages) == 100


# ======================================================================= #
#  Tests: should_compact
# ======================================================================= #


class TestShouldCompact:
    """Verify should_compact correctly detects the threshold."""

    @pytest.mark.asyncio
    async def test_should_compact_above_threshold(self):
        """should_compact returns True when tokens exceed threshold."""
        provider = MockProvider([])
        compactor = Compactor(provider, threshold=0.50)

        # Large messages -- ~500 tokens each
        messages = [Message(role="user", content="x" * 2000) for _ in range(5)]

        result = await compactor.should_compact(messages, context_window=1000)
        assert result is True

    @pytest.mark.asyncio
    async def test_should_compact_below_threshold(self):
        """should_compact returns False when tokens are below threshold."""
        provider = MockProvider([])
        compactor = Compactor(provider, threshold=0.80)

        messages = [Message(role="user", content="hello")]

        result = await compactor.should_compact(messages, context_window=100000)
        assert result is False

    @pytest.mark.asyncio
    async def test_should_compact_with_tripped_breaker(self):
        """should_compact returns False when circuit breaker is tripped."""
        provider = MockProvider([])
        compactor = Compactor(provider, threshold=0.50)
        compactor.consecutive_failures = 3

        messages = [Message(role="user", content="x" * 2000) for _ in range(5)]

        result = await compactor.should_compact(messages, context_window=1000)
        assert result is False
