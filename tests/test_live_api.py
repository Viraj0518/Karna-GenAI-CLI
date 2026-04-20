"""Live API integration tests — exercises Nellie against REAL LLM providers.

These tests require a valid API key. Set OPENROUTER_API_KEY env var.
Tests are marked with @pytest.mark.live so they can be skipped in CI:
    pytest -m "not live"       # skip live tests
    pytest -m live             # run only live tests

Each test makes a real API call, so they:
- Cost money (pennies per run)
- Are slower (2-30s each)
- May fail on rate limits or network issues
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from karna.models import Conversation, Message

# Skip all tests if no API key
pytestmark = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set — skipping live API tests",
)

live = pytest.mark.live


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def provider():
    """Create a real OpenRouter provider."""
    from karna.providers import get_provider, resolve_model

    provider_name, model_name = resolve_model("openrouter:qwen/qwen3-coder")
    p = get_provider(provider_name)
    p.model = model_name
    return p


@pytest.fixture()
def tools():
    """Get all tools."""
    from karna.tools import get_all_tools

    return get_all_tools()


# --------------------------------------------------------------------------- #
#  1. Basic provider connectivity
# --------------------------------------------------------------------------- #


class TestProviderConnectivity:
    """Verify we can connect and get responses from real providers."""

    @live
    @pytest.mark.asyncio
    async def test_simple_completion(self, provider) -> None:
        """Provider should return a non-empty text response."""
        response = await provider.complete(
            [Message(role="user", content="Reply with exactly: PONG")],
            tools=None,
        )
        assert response.content
        assert len(response.content) > 0
        assert "PONG" in response.content.upper()

    @live
    @pytest.mark.asyncio
    async def test_streaming(self, provider) -> None:
        """Provider should stream text deltas."""
        events = []
        async for event in provider.stream(
            [Message(role="user", content="Say hello in one word")],
            tools=None,
        ):
            events.append(event)

        assert len(events) > 0
        text_events = [e for e in events if e.type == "text"]
        assert len(text_events) > 0

    @live
    @pytest.mark.asyncio
    async def test_tool_call_generation(self, provider, tools) -> None:
        """Provider should generate tool calls when appropriate."""
        tool_defs = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ]

        response = await provider.complete(
            [Message(role="user", content="What files are in the current directory? Use the bash tool to run 'ls'.")],
            tools=tool_defs,
        )
        # Should have either tool calls or text
        assert response.content or response.tool_calls


# --------------------------------------------------------------------------- #
#  2. Full agent loop with real provider
# --------------------------------------------------------------------------- #


class TestAgentLoopLive:
    """Run the full agent loop against a real LLM."""

    @live
    @pytest.mark.asyncio
    async def test_agent_text_only(self, provider) -> None:
        """Agent loop should complete with a text response."""
        from karna.agents.loop import agent_loop

        conv = Conversation(
            messages=[
                Message(role="user", content="What is 2+2? Answer with just the number."),
            ]
        )

        events = []
        async for event in agent_loop(provider, conv, [], max_iterations=3):
            events.append(event)

        text = "".join(e.text for e in events if e.type == "text")
        assert "4" in text

    @live
    @pytest.mark.asyncio
    async def test_agent_with_tool_use(self, provider, tools) -> None:
        """Agent should use tools and return results."""
        from karna.agents.loop import agent_loop
        from karna.config import KarnaConfig
        from karna.prompts import build_system_prompt

        config = KarnaConfig()
        system_prompt = build_system_prompt(config, tools)

        conv = Conversation(
            messages=[
                Message(role="user", content="Run 'echo LIVE_TEST_OK' in bash and tell me what it returned."),
            ]
        )

        events = []
        async for event in agent_loop(
            provider,
            conv,
            tools,
            system_prompt=system_prompt,
            max_iterations=5,
        ):
            events.append(event)

        # Should have tool calls and text
        types = {e.type for e in events}
        all_text = "".join(e.text for e in events if e.type == "text")
        assert "LIVE_TEST_OK" in all_text or "tool_call_start" in types

    @live
    @pytest.mark.asyncio
    async def test_agent_reads_file(self, provider, tools, tmp_path: Path) -> None:
        """Agent should be able to read a file we create."""
        from karna.agents.loop import agent_loop
        from karna.config import KarnaConfig
        from karna.prompts import build_system_prompt

        # Create a test file
        test_file = tmp_path / "secret_message.txt"
        test_file.write_text("The secret code is ALPHA-BRAVO-42")

        config = KarnaConfig()
        system_prompt = build_system_prompt(config, tools)

        conv = Conversation(
            messages=[
                Message(role="user", content=f"Read the file at {test_file} and tell me the secret code."),
            ]
        )

        events = []
        async for event in agent_loop(
            provider,
            conv,
            tools,
            system_prompt=system_prompt,
            max_iterations=5,
        ):
            events.append(event)

        all_text = "".join(e.text for e in events if e.type == "text")
        assert "ALPHA-BRAVO-42" in all_text or "alpha" in all_text.lower()

    @live
    @pytest.mark.asyncio
    async def test_agent_writes_and_verifies(self, provider, tools, tmp_path: Path) -> None:
        """Agent should write a file and confirm it exists."""
        from karna.agents.loop import agent_loop
        from karna.config import KarnaConfig
        from karna.prompts import build_system_prompt

        target = tmp_path / "agent_output.txt"

        config = KarnaConfig()
        system_prompt = build_system_prompt(config, tools)

        conv = Conversation(
            messages=[
                Message(role="user", content=f"Write 'AGENT_WROTE_THIS' to {target} and confirm it was written."),
            ]
        )

        events = []
        async for event in agent_loop(
            provider,
            conv,
            tools,
            system_prompt=system_prompt,
            max_iterations=5,
        ):
            events.append(event)

        # File should exist with content
        assert target.exists(), f"Agent should have created {target}"
        assert "AGENT_WROTE_THIS" in target.read_text()


# --------------------------------------------------------------------------- #
#  3. Multi-turn conversation
# --------------------------------------------------------------------------- #


class TestMultiTurnLive:
    """Test multi-turn conversations with context retention."""

    @live
    @pytest.mark.asyncio
    async def test_context_retained(self, provider) -> None:
        """Agent should remember context from earlier turns."""
        from karna.agents.loop import agent_loop

        conv = Conversation(
            messages=[
                Message(role="user", content="My name is TestBot42. Remember this."),
            ]
        )

        # First turn
        async for event in agent_loop(provider, conv, [], max_iterations=2):
            if event.type == "text":
                conv.messages.append(Message(role="assistant", content=event.text))
                break

        # Second turn — ask about the name
        conv.messages.append(Message(role="user", content="What is my name?"))

        events = []
        async for event in agent_loop(provider, conv, [], max_iterations=2):
            events.append(event)

        text = "".join(e.text for e in events if e.type == "text")
        assert "TestBot42" in text or "testbot42" in text.lower()


# --------------------------------------------------------------------------- #
#  4. Error handling with real provider
# --------------------------------------------------------------------------- #


class TestErrorHandlingLive:
    """Test error recovery with real API responses."""

    @live
    @pytest.mark.asyncio
    async def test_invalid_model_error(self) -> None:
        """Using a nonexistent model should produce a clear error."""
        from karna.providers import get_provider

        provider = get_provider("openrouter")
        provider.model = "nonexistent/fake-model-xyz"

        with pytest.raises(Exception) as exc_info:
            await provider.complete(
                [Message(role="user", content="test")],
                tools=None,
            )
        # Should mention the model or return a 4xx error
        assert exc_info.value is not None


# --------------------------------------------------------------------------- #
#  5. Web tools with real network
# --------------------------------------------------------------------------- #


class TestWebToolsLive:
    """Test web search and fetch against real endpoints."""

    @live
    @pytest.mark.asyncio
    async def test_web_search(self) -> None:
        """Web search should return results."""
        from karna.tools.web_search import WebSearchTool

        tool = WebSearchTool()
        result = await tool.execute(query="python programming language")
        assert len(result) > 0
        assert "python" in result.lower()

    @live
    @pytest.mark.asyncio
    async def test_web_fetch(self) -> None:
        """Web fetch should extract content from a URL."""
        from karna.tools.web_fetch import WebFetchTool

        tool = WebFetchTool()
        result = await tool.execute(url="https://httpbin.org/html")
        assert len(result) > 0
