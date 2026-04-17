"""Tests for the agent loop.

Uses a mock provider to verify:
- Tool call -> execution -> result appended to conversation
- Termination on no tool calls
- Max iteration guard
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest

from karna.agents.loop import agent_loop, agent_loop_sync
from karna.models import (
    Conversation,
    Message,
    ModelInfo,
    StreamEvent,
    ToolCall,
    Usage,
)
from karna.providers.base import BaseProvider
from karna.tools.base import BaseTool


# ======================================================================= #
#  Mock provider
# ======================================================================= #


class MockProvider(BaseProvider):
    """Provider that returns a scripted sequence of responses.

    *responses* is a list of ``Message`` objects.  Each call to
    ``complete`` pops the first one.  ``stream`` is implemented via
    ``complete`` — it yields the appropriate events.
    """

    name = "mock"

    def __init__(self, responses: list[Message]) -> None:
        super().__init__()
        self._responses = list(responses)

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Message:
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

        # Emit text
        if msg.content:
            yield StreamEvent(type="text", text=msg.content)

        # Emit tool calls
        for tc in msg.tool_calls:
            yield StreamEvent(type="tool_call_start", tool_call=tc)
            yield StreamEvent(type="tool_call_end", tool_call=tc)

        yield StreamEvent(type="done")

    async def list_models(self) -> list[ModelInfo]:
        return []


# ======================================================================= #
#  Mock tool
# ======================================================================= #


class MockTool(BaseTool):
    """Simple tool that returns a fixed result."""

    name = "mock_tool"
    description = "A mock tool for testing"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "input": {"type": "string"},
        },
        "required": ["input"],
    }

    def __init__(self, return_value: str = "mock result") -> None:
        super().__init__()
        self._return_value = return_value
        self.call_count = 0
        self.last_args: dict[str, Any] = {}

    async def execute(self, **kwargs: Any) -> str:
        self.call_count += 1
        self.last_args = kwargs
        return self._return_value


# ======================================================================= #
#  Tests: streaming agent loop
# ======================================================================= #


class TestAgentLoopStreaming:
    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        """Provider returns text with no tool calls -> loop terminates."""
        provider = MockProvider([
            Message(role="assistant", content="Hello, I'm done."),
        ])
        conv = Conversation(messages=[
            Message(role="user", content="Hi"),
        ])

        events = []
        async for event in agent_loop(provider, conv, []):
            events.append(event)

        # Should have text + done events
        text_events = [e for e in events if e.type == "text"]
        done_events = [e for e in events if e.type == "done"]
        assert len(text_events) >= 1
        assert text_events[0].text == "Hello, I'm done."
        assert len(done_events) == 1

        # Conversation should have the assistant message appended
        assert conv.messages[-1].role == "assistant"
        assert conv.messages[-1].content == "Hello, I'm done."

    @pytest.mark.asyncio
    async def test_tool_call_and_response(self):
        """Provider makes a tool call, then responds with text."""
        tool = MockTool(return_value="tool output here")

        provider = MockProvider([
            # First response: tool call
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="tc_1", name="mock_tool", arguments={"input": "test"})
                ],
            ),
            # Second response: final text
            Message(role="assistant", content="Done using the tool."),
        ])

        conv = Conversation(messages=[
            Message(role="user", content="Use the tool"),
        ])

        events = []
        async for event in agent_loop(provider, conv, [tool]):
            events.append(event)

        # Tool should have been called
        assert tool.call_count == 1
        assert tool.last_args == {"input": "test"}

        # Conversation should have:
        # [user, assistant(tool_call), tool(result), assistant(text)]
        assert len(conv.messages) == 4
        assert conv.messages[0].role == "user"
        assert conv.messages[1].role == "assistant"
        assert len(conv.messages[1].tool_calls) == 1
        assert conv.messages[2].role == "tool"
        assert len(conv.messages[2].tool_results) == 1
        assert conv.messages[2].tool_results[0].content == "tool output here"
        assert conv.messages[3].role == "assistant"
        assert conv.messages[3].content == "Done using the tool."

    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """Provider requests an unknown tool -> error result appended."""
        provider = MockProvider([
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="tc_1", name="nonexistent", arguments={})
                ],
            ),
            Message(role="assistant", content="Sorry about that."),
        ])

        conv = Conversation(messages=[
            Message(role="user", content="Try unknown tool"),
        ])

        events = []
        async for event in agent_loop(provider, conv, []):
            events.append(event)

        # The tool result should be an error
        tool_msg = conv.messages[2]
        assert tool_msg.role == "tool"
        assert tool_msg.tool_results[0].is_error
        assert "unknown tool" in tool_msg.tool_results[0].content.lower()

    @pytest.mark.asyncio
    async def test_max_iterations(self):
        """Loop stops after max_iterations even if provider keeps making tool calls."""
        tool = MockTool()
        # Use different arguments each time to avoid triggering loop detection
        tc_messages = [
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id=f"tc_{i}", name="mock_tool", arguments={"input": f"iter_{i}"})
                ],
            )
            for i in range(10)
        ]

        provider = MockProvider(tc_messages)

        conv = Conversation(messages=[
            Message(role="user", content="Loop forever"),
        ])

        events = []
        async for event in agent_loop(provider, conv, [tool], max_iterations=3):
            events.append(event)

        # Should have stopped after 3 iterations
        assert tool.call_count == 3


# ======================================================================= #
#  Tests: non-streaming agent loop
# ======================================================================= #


class TestAgentLoopSync:
    @pytest.mark.asyncio
    async def test_simple_completion(self):
        provider = MockProvider([
            Message(role="assistant", content="Done."),
        ])
        conv = Conversation(messages=[
            Message(role="user", content="Hi"),
        ])

        result = await agent_loop_sync(provider, conv, [])
        assert result.content == "Done."

    @pytest.mark.asyncio
    async def test_tool_call_cycle(self):
        tool = MockTool(return_value="42")

        provider = MockProvider([
            Message(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="tc_1", name="mock_tool", arguments={"input": "calc"})
                ],
            ),
            Message(role="assistant", content="The answer is 42."),
        ])

        conv = Conversation(messages=[
            Message(role="user", content="What is 6*7?"),
        ])

        result = await agent_loop_sync(provider, conv, [tool])
        assert result.content == "The answer is 42."
        assert tool.call_count == 1
