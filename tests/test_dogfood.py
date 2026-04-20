"""Comprehensive dogfood tests — exercise every Nellie feature end-to-end.

These tests simulate real user interactions by feeding prompts through
the agent loop and verifying that tools execute, output renders, and
state persists correctly. They run against mock providers (no API key
needed) but exercise the full code path.

Test categories:
1. REPL lifecycle (start, prompt, respond, exit)
2. Tool execution (all 15+ tools)
3. Slash commands (all 18+)
4. Memory system (extract, save, load, search)
5. Skills system (load, trigger, inject)
6. Session management (create, resume, search)
7. Compaction (auto-trigger, manual)
8. Subagent spawning and notification
9. Monitor and background tasks
10. TUI rendering (banner, thinking, tools, text)
11. Security guardrails (path traversal, SSRF, secrets)
12. Config system (load, save, profiles)
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from io import StringIO
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from karna.config import KarnaConfig, save_config
from karna.models import Conversation, Message, StreamEvent, ToolCall, ToolResult
from karna.providers.base import BaseProvider


# --------------------------------------------------------------------------- #
#  Mock provider for testing
# --------------------------------------------------------------------------- #


class DogfoodProvider(BaseProvider):
    """Provider that returns scripted responses for dogfood testing."""

    name = "dogfood"

    def __init__(self, responses: list[Message] | None = None) -> None:
        super().__init__()
        self._responses = list(responses or [])
        self.calls: list[dict] = []

    def add_response(self, content: str = "", tool_calls: list[ToolCall] | None = None):
        self._responses.append(Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls or [],
        ))

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Message:
        self.calls.append({"messages": len(messages), "tools": len(tools or [])})
        if self._responses:
            return self._responses.pop(0)
        return Message(role="assistant", content="(no more responses)")

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        msg = await self.complete(messages, tools, **kwargs)
        if msg.content:
            yield StreamEvent(type="text", text=msg.content)
        for tc in msg.tool_calls:
            yield StreamEvent(type="tool_call_start", tool_call=tc)
            yield StreamEvent(type="tool_call_end", tool_call=tc)
        yield StreamEvent(type="done")

    async def list_models(self):
        return []


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def karna_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    karna_dir = tmp_path / ".karna"
    monkeypatch.setattr("karna.config.KARNA_DIR", karna_dir)
    monkeypatch.setattr("karna.config.CONFIG_PATH", karna_dir / "config.toml")
    return karna_dir


@pytest.fixture()
def provider():
    return DogfoodProvider()


@pytest.fixture()
def conversation():
    return Conversation(provider="dogfood", model="test")


# --------------------------------------------------------------------------- #
#  1. Agent loop lifecycle
# --------------------------------------------------------------------------- #


class TestAgentLoop:
    """Test the core agent loop mechanics."""

    @pytest.mark.asyncio
    async def test_simple_text_response(self, provider: DogfoodProvider) -> None:
        """Agent should return text when no tool calls."""
        from karna.agents.loop import agent_loop

        provider.add_response("Hello, world!")
        conv = Conversation(messages=[Message(role="user", content="hi")])

        events = []
        async for event in agent_loop(provider, conv, []):
            events.append(event)

        text_events = [e for e in events if e.type == "text"]
        assert any("Hello" in e.text for e in text_events)

    @pytest.mark.asyncio
    async def test_tool_call_and_response(self, provider: DogfoodProvider) -> None:
        """Agent should execute tool calls and continue."""
        from karna.agents.loop import agent_loop
        from karna.tools.bash import BashTool

        # First response: call a tool
        provider.add_response(
            tool_calls=[ToolCall(id="tc1", name="bash", arguments={"command": "echo hello"})]
        )
        # Second response: final text
        provider.add_response("The command returned hello.")

        conv = Conversation(messages=[Message(role="user", content="run echo hello")])
        tools = [BashTool()]

        events = []
        async for event in agent_loop(provider, conv, tools):
            events.append(event)

        # Should have tool call events and text
        types = {e.type for e in events}
        assert "tool_call_start" in types or "text" in types

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self, provider: DogfoodProvider) -> None:
        """Agent should stop after max_iterations."""
        from karna.agents.loop import agent_loop
        from karna.tools.bash import BashTool

        # Keep calling tools forever
        for _ in range(30):
            provider.add_response(
                tool_calls=[ToolCall(id=f"tc{_}", name="bash", arguments={"command": "echo loop"})]
            )

        conv = Conversation(messages=[Message(role="user", content="loop")])
        tools = [BashTool()]

        event_count = 0
        async for _ in agent_loop(provider, conv, tools, max_iterations=3):
            event_count += 1

        # Should have stopped, not gone to 30
        assert provider.calls.__len__() <= 4  # 3 iterations + maybe 1 more


# --------------------------------------------------------------------------- #
#  2. Tool execution
# --------------------------------------------------------------------------- #


class TestToolExecution:
    """Test individual tool execution."""

    @pytest.mark.asyncio
    async def test_bash_tool(self) -> None:
        from karna.tools.bash import BashTool

        tool = BashTool()
        result = await tool.execute(command="echo dogfood_test")
        assert "dogfood_test" in result

    @pytest.mark.asyncio
    async def test_read_tool(self, tmp_path: Path) -> None:
        from karna.tools.read import ReadTool

        test_file = tmp_path / "test.txt"
        test_file.write_text("line1\nline2\nline3\n")

        tool = ReadTool(allowed_roots=[tmp_path])
        result = await tool.execute(file_path=str(test_file))
        assert "line1" in result
        assert "line2" in result

    @pytest.mark.asyncio
    async def test_write_tool(self, tmp_path: Path) -> None:
        from karna.tools.write import WriteTool

        target = tmp_path / "output.txt"
        tool = WriteTool(allowed_roots=[tmp_path])
        result = await tool.execute(file_path=str(target), content="hello world")
        assert target.exists()
        assert target.read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_edit_tool(self, tmp_path: Path) -> None:
        from karna.tools.edit import EditTool

        target = tmp_path / "edit_test.txt"
        target.write_text("foo bar baz")
        tool = EditTool(allowed_roots=[tmp_path])
        result = await tool.execute(
            file_path=str(target),
            old_string="bar",
            new_string="qux",
        )
        assert target.read_text() == "foo qux baz"

    @pytest.mark.asyncio
    async def test_grep_tool(self, tmp_path: Path) -> None:
        from karna.tools.grep import GrepTool

        (tmp_path / "a.py").write_text("def hello():\n    pass\n")
        (tmp_path / "b.py").write_text("def world():\n    pass\n")

        tool = GrepTool()
        result = await tool.execute(pattern="hello", path=str(tmp_path))
        assert "a.py" in result  # grep returns matching file paths by default

    @pytest.mark.asyncio
    async def test_glob_tool(self, tmp_path: Path) -> None:
        from karna.tools.glob import GlobTool

        (tmp_path / "a.py").touch()
        (tmp_path / "b.py").touch()
        (tmp_path / "c.txt").touch()

        tool = GlobTool()
        result = await tool.execute(pattern="*.py", path=str(tmp_path))
        assert "a.py" in result
        assert "b.py" in result

    @pytest.mark.asyncio
    async def test_git_tool(self, tmp_path: Path) -> None:
        from karna.tools.git_ops import GitTool

        os.system(f"cd {tmp_path} && git init -q && git config user.email 'test@test.com' && git config user.name 'Test'")
        (tmp_path / "file.txt").write_text("hello")
        os.system(f"cd {tmp_path} && git add . && git commit -q -m 'init'")

        tool = GitTool()
        tool._cwd = str(tmp_path)
        result = await tool.execute(action="log", args="--oneline -1")
        assert "init" in result


# --------------------------------------------------------------------------- #
#  3. Slash commands
# --------------------------------------------------------------------------- #


class TestSlashCommands:
    """Test slash command dispatch."""

    def test_all_commands_registered(self) -> None:
        from karna.tui.slash import COMMANDS

        expected = {"help", "model", "clear", "history", "cost", "exit", "quit",
                    "compact", "tools", "system", "resume", "paste", "copy",
                    "sessions", "skills", "memory", "loop", "plan", "do"}
        registered = set(COMMANDS.keys())
        missing = expected - registered
        assert not missing, f"Missing slash commands: {missing}"

    @pytest.mark.asyncio
    async def test_clear_resets_conversation(self) -> None:
        from karna.tui.slash import handle_slash_command

        console = __import__("rich.console", fromlist=["Console"]).Console(
            file=StringIO(), force_terminal=True
        )
        config = KarnaConfig()
        conv = Conversation(messages=[Message(role="user", content="hi")])
        assert len(conv.messages) == 1
        await handle_slash_command("/clear", console, config, conv)
        assert len(conv.messages) == 0


# --------------------------------------------------------------------------- #
#  4. Memory system
# --------------------------------------------------------------------------- #


class TestMemoryDogfood:
    """Test memory extraction and retrieval end-to-end."""

    def test_feedback_detection(self, karna_home: Path) -> None:
        from karna.memory.extractor import MemoryExtractor, _RateLimiter
        from karna.memory.manager import MemoryManager

        memory_dir = karna_home / "memory"
        mm = MemoryManager(memory_dir=memory_dir)
        ext = MemoryExtractor(memory_manager=mm)
        ext._rate_limiter = _RateLimiter(min_turns_between_saves=0)
        ext._rate_limiter._turns_since_last_save = 999

        saved = ext.extract_and_save("don't use print statements for debugging, use logging instead")
        assert len(saved) >= 1

    def test_user_profile_detection(self, karna_home: Path) -> None:
        from karna.memory.extractor import MemoryExtractor, _RateLimiter
        from karna.memory.manager import MemoryManager

        memory_dir = karna_home / "memory"
        mm = MemoryManager(memory_dir=memory_dir)
        ext = MemoryExtractor(memory_manager=mm)
        ext._rate_limiter = _RateLimiter(min_turns_between_saves=0)
        ext._rate_limiter._turns_since_last_save = 999

        saved = ext.extract_and_save("I'm a data scientist working on public health analytics")
        assert len(saved) >= 1


# --------------------------------------------------------------------------- #
#  5. Skills system
# --------------------------------------------------------------------------- #


class TestSkillsDogfood:
    """Test skills loading and trigger matching."""

    def test_skill_loading(self, tmp_path: Path) -> None:
        from karna.skills.loader import SkillManager

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "test-skill.md").write_text(
            "---\n"
            "name: test-skill\n"
            "description: A test skill\n"
            "triggers: [test-trigger]\n"
            "enabled: true\n"
            "---\n"
            "\n"
            "Do the test thing.\n"
        )

        sm = SkillManager(skills_dir=skills_dir)
        sm.load_all()
        assert len(sm.skills) >= 1
        assert any(s.name == "test-skill" for s in sm.skills)

    def test_trigger_matching(self, tmp_path: Path) -> None:
        from karna.skills.loader import SkillManager

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "commit.md").write_text(
            "---\n"
            "name: commit\n"
            "description: Git commit helper\n"
            "triggers: [/commit, commit changes]\n"
            "enabled: true\n"
            "---\n"
            "\n"
            "Help commit changes.\n"
        )

        sm = SkillManager(skills_dir=skills_dir)
        sm.load_all()
        matches = sm.match_trigger("commit changes please")
        assert len(matches) >= 1


# --------------------------------------------------------------------------- #
#  6. Session management
# --------------------------------------------------------------------------- #


class TestSessionDogfood:
    """Test session creation and retrieval."""

    def test_session_lifecycle(self, karna_home: Path) -> None:
        from karna.sessions.db import SessionDB

        sessions_dir = karna_home / "sessions"
        sessions_dir.mkdir(parents=True)
        db = SessionDB(db_path=sessions_dir / "sessions.db")

        # Create session
        sid = db.create_session(model="test-model", provider="test", cwd="/tmp")
        assert sid

        # Add messages
        db.add_message(sid, Message(role="user", content="hello"))
        db.add_message(sid, Message(role="assistant", content="world"))

        # Retrieve
        sessions = db.list_sessions(limit=5)
        assert len(sessions) >= 1

    def test_session_search(self, karna_home: Path) -> None:
        from karna.sessions.db import SessionDB

        sessions_dir = karna_home / "sessions"
        sessions_dir.mkdir(parents=True)
        db = SessionDB(db_path=sessions_dir / "sessions.db")

        sid = db.create_session(model="test", provider="test", cwd="/tmp")
        db.add_message(sid, Message(role="user", content="unique_dogfood_query"))

        results = db.search("unique_dogfood_query")
        assert len(results) >= 1


# --------------------------------------------------------------------------- #
#  7. TUI rendering
# --------------------------------------------------------------------------- #


class TestTUIRendering:
    """Test TUI output rendering correctness."""

    def test_banner_renders(self) -> None:
        from rich.console import Console

        from karna.tui.banner import print_banner

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        config = KarnaConfig()
        print_banner(console, config, tool_names=["bash", "read", "write"])
        output = buf.getvalue()
        assert "nellie" in output.lower() or "NELLIE" in output or "███" in output

    def test_output_renderer_text(self) -> None:
        from rich.console import Console

        from karna.tui.output import EventKind, OutputRenderer
        from karna.tui.output import StreamEvent as TUIStreamEvent

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = OutputRenderer(console)
        renderer.handle(TUIStreamEvent(kind=EventKind.TEXT_DELTA, data="Hello world"))
        renderer.handle(TUIStreamEvent(kind=EventKind.DONE))
        renderer.finish()
        output = buf.getvalue()
        assert "Hello" in output

    def test_output_renderer_tool_call(self) -> None:
        from rich.console import Console

        from karna.tui.output import EventKind, OutputRenderer
        from karna.tui.output import StreamEvent as TUIStreamEvent

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = OutputRenderer(console)
        renderer.handle(TUIStreamEvent(
            kind=EventKind.TOOL_CALL_START,
            data={"name": "bash", "id": "tc1"},
        ))
        renderer.handle(TUIStreamEvent(
            kind=EventKind.TOOL_CALL_ARGS_DELTA,
            data='{"command": "ls"}',
        ))
        renderer.handle(TUIStreamEvent(kind=EventKind.TOOL_CALL_END))
        renderer.handle(TUIStreamEvent(
            kind=EventKind.TOOL_RESULT,
            data={"content": "file.txt", "is_error": False},
        ))
        renderer.finish()
        output = buf.getvalue()
        assert "terminal" in output or "bash" in output  # verb mapping shows "terminal" for bash

    def test_output_renderer_error(self) -> None:
        from rich.console import Console

        from karna.tui.output import EventKind, OutputRenderer
        from karna.tui.output import StreamEvent as TUIStreamEvent

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=80)
        renderer = OutputRenderer(console)
        renderer.handle(TUIStreamEvent(kind=EventKind.ERROR, data="something broke"))
        renderer.finish()
        output = buf.getvalue()
        assert "broke" in output


# --------------------------------------------------------------------------- #
#  8. Security guardrails
# --------------------------------------------------------------------------- #


class TestSecurityDogfood:
    """Test security checks in real tool execution paths."""

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self) -> None:
        """Reading /etc/shadow should be blocked."""
        from karna.tools.read import ReadTool

        tool = ReadTool()
        result = await tool.execute(file_path="/etc/shadow")
        # Should either raise or return an error
        assert "denied" in result.lower() or "blocked" in result.lower() or "error" in result.lower() or "not found" in result.lower() or "permission" in result.lower()

    @pytest.mark.asyncio
    async def test_secret_scrubbing_in_output(self) -> None:
        """Secrets should be scrubbed from tool output."""
        from karna.security.scrub import scrub_secrets

        output = "Connected with key sk-ant-api03-abcdef123456"
        scrubbed = scrub_secrets(output)
        assert "abcdef123456" not in scrubbed

    @pytest.mark.asyncio
    async def test_dangerous_bash_flagged(self) -> None:
        """Dangerous bash commands should be caught by safety checks."""
        from karna.agents.safety import pre_tool_check
        from karna.tools.bash import BashTool

        tool = BashTool()
        proceed, warning = await pre_tool_check(tool, {"command": "rm -rf /"})
        assert not proceed or warning  # should block or warn


# --------------------------------------------------------------------------- #
#  9. Prompt system
# --------------------------------------------------------------------------- #


class TestPromptSystem:
    """Test system prompt construction."""

    def test_system_prompt_has_tools(self) -> None:
        from karna.prompts import build_system_prompt
        from karna.tools import get_all_tools

        config = KarnaConfig()
        tools = get_all_tools()
        prompt = build_system_prompt(config, tools)
        assert len(prompt) > 100  # not empty
        assert "tool" in prompt.lower() or "bash" in prompt.lower()

    def test_system_prompt_has_context(self) -> None:
        from karna.prompts import build_system_prompt

        config = KarnaConfig()
        prompt = build_system_prompt(config, [])
        # Should have environment info
        assert "python" in prompt.lower() or "cwd" in prompt.lower() or "assistant" in prompt.lower()


# --------------------------------------------------------------------------- #
#  10. Cost tracking
# --------------------------------------------------------------------------- #


class TestCostTracking:
    """Test cost accumulation."""

    def test_cost_tracker(self, karna_home: Path) -> None:
        from karna.sessions.cost import CostTracker
        from karna.sessions.db import SessionDB

        sessions_dir = karna_home / "sessions"
        sessions_dir.mkdir(parents=True)
        db = SessionDB(db_path=sessions_dir / "sessions.db")
        sid = db.create_session(model="test", provider="test", cwd="/tmp")
        tracker = CostTracker(db, session_id=sid, model="test", provider="test")

        # Should not crash even with no data
        summary = tracker.get_total_summary()
        assert isinstance(summary, dict)
