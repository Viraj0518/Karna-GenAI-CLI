"""Tests for karna.tui — banner, slash commands, output rendering."""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from karna.config import KarnaConfig
from karna.models import Conversation, Message
from karna.tui.banner import print_banner
from karna.tui.output import EventKind, OutputRenderer, StreamEvent
from karna.tui.slash import COMMANDS, SessionCost, handle_slash_command

# --------------------------------------------------------------------------- #
#  Banner
# --------------------------------------------------------------------------- #


def test_banner_renders_without_error() -> None:
    """print_banner should complete without raising."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    config = KarnaConfig(active_provider="openrouter", active_model="test-model")
    print_banner(console, config, tool_names=["bash", "read", "edit"])
    output = buf.getvalue()
    assert "karna" in output.lower()
    assert "test-model" in output


def test_banner_shows_tool_count() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    config = KarnaConfig()
    print_banner(console, config, tool_names=["a", "b", "c", "d", "e"])
    assert "5 loaded" in buf.getvalue()


# --------------------------------------------------------------------------- #
#  Slash commands — parsing
# --------------------------------------------------------------------------- #


def test_slash_commands_registered() -> None:
    """All expected commands should be present in the COMMANDS table."""
    expected = {
        "help",
        "model",
        "clear",
        "history",
        "cost",
        "exit",
        "quit",
        "compact",
        "tools",
        "system",
        "resume",
        "paste",
        "copy",
        "sessions",
        "memory",
        "skills",
        # Advanced mode (autonomous loop + plan-first + task management)
        "loop",
        "plan",
        "do",
        "tasks",
    }
    assert expected == set(COMMANDS.keys())


@pytest.mark.asyncio
async def test_help_lists_commands() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=100)
    config = KarnaConfig()
    conversation = Conversation()
    await handle_slash_command("/help", console, config, conversation)
    output = buf.getvalue()
    # Should mention most commands
    assert "/model" in output
    assert "/clear" in output
    assert "/cost" in output
    assert "/tools" in output


@pytest.mark.asyncio
async def test_unknown_command_shows_error() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    config = KarnaConfig()
    conversation = Conversation()
    await handle_slash_command("/notarealcommand", console, config, conversation)
    output = buf.getvalue()
    assert "unknown command" in output.lower()


@pytest.mark.asyncio
async def test_model_switch() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    config = KarnaConfig(active_provider="openrouter", active_model="old-model")
    conversation = Conversation(provider="openrouter", model="old-model")
    await handle_slash_command("/model anthropic:claude-3-opus", console, config, conversation)
    assert config.active_provider == "anthropic"
    assert config.active_model == "claude-3-opus"
    assert conversation.provider == "anthropic"
    assert "Switched" in buf.getvalue()


@pytest.mark.asyncio
async def test_clear_resets_conversation() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    config = KarnaConfig()
    conversation = Conversation(messages=[Message(role="user", content="hi")])
    assert len(conversation.messages) == 1
    await handle_slash_command("/clear", console, config, conversation)
    assert len(conversation.messages) == 0


@pytest.mark.asyncio
async def test_exit_raises_system_exit() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    config = KarnaConfig()
    conversation = Conversation()
    try:
        await handle_slash_command("/exit", console, config, conversation)
        assert False, "Should have raised SystemExit"
    except SystemExit:
        pass


@pytest.mark.asyncio
async def test_cost_shows_session_totals() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    config = KarnaConfig()
    conversation = Conversation()
    cost = SessionCost(prompt_tokens=100, completion_tokens=50, total_usd=0.0025)
    await handle_slash_command("/cost", console, config, conversation, session_cost=cost)
    output = buf.getvalue()
    assert "100" in output
    assert "50" in output


@pytest.mark.asyncio
async def test_tools_lists_tool_names() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    config = KarnaConfig()
    conversation = Conversation()
    await handle_slash_command("/tools", console, config, conversation, tool_names=["bash", "read"])
    output = buf.getvalue()
    assert "bash" in output
    assert "read" in output


@pytest.mark.asyncio
async def test_system_shows_and_sets_prompt() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=100)
    config = KarnaConfig(system_prompt="You are helpful.")
    conversation = Conversation()
    # Show current
    await handle_slash_command("/system", console, config, conversation)
    assert "You are helpful." in buf.getvalue()

    # Set new
    buf2 = StringIO()
    console2 = Console(file=buf2, force_terminal=True, width=100)
    await handle_slash_command("/system Be concise.", console2, config, conversation)
    assert config.system_prompt == "Be concise."


# --------------------------------------------------------------------------- #
#  Output renderer
# --------------------------------------------------------------------------- #


def test_renderer_handles_text_deltas() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    renderer = OutputRenderer(console)
    renderer.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="Hello "))
    renderer.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="world"))
    renderer.handle(StreamEvent(kind=EventKind.DONE))
    renderer.finish()
    output = buf.getvalue()
    assert "Hello" in output
    assert "world" in output


def test_renderer_handles_error() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    renderer = OutputRenderer(console)
    renderer.handle(StreamEvent(kind=EventKind.ERROR, data="something broke"))
    renderer.finish()
    assert "something broke" in buf.getvalue()


def test_renderer_handles_tool_call() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=100)
    renderer = OutputRenderer(console)
    renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "bash", "id": "1"}))
    renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"command": "ls"}'))
    renderer.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    renderer.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={"content": "file.txt\n", "is_error": False}))
    renderer.finish()
    output = buf.getvalue()
    # Hermes-style: tool verb "terminal" replaces raw name "bash"
    assert "terminal" in output
    assert "ls" in output
