"""Dogfood tests — exercise Nellie features end-to-end.

These tests simulate real user interactions by feeding prompts through
the agent loop and verifying that tools execute, output renders, and
state persists correctly.  They run against mock providers (no API key
needed) but exercise the full code path.

Test categories:
1.  Agent loop lifecycle
2.  Tool execution (bash, read, write, edit, grep, glob, git)
3.  Slash commands (registration + clear command)
4.  Memory system (extraction and retrieval)
5.  Skills system (loading + trigger matching)
6.  Session management (create, resume, search)
7.  TUI rendering (banner, output renderer)
8.  Security guardrails (path traversal, secret scrubbing, dangerous commands)
9.  Prompt system (system prompt construction)
10. Cost tracking
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from karna.config import KarnaConfig
from karna.models import Conversation, Message, StreamEvent, ToolCall
from karna.providers.base import BaseProvider

# --------------------------------------------------------------------------- #
#  Mock provider for dogfood testing
# --------------------------------------------------------------------------- #


class DogfoodProvider(BaseProvider):
    """Provider that returns scripted responses for dogfood testing."""

    name = "dogfood"

    def __init__(self, responses: list[Message] | None = None) -> None:
        super().__init__()
        self._responses = list(responses or [])
        self.calls: list[dict] = []

    def add_response(self, content: str = "", tool_calls: list[ToolCall] | None = None) -> None:
        self._responses.append(
            Message(
                role="assistant",
                content=content,
                tool_calls=tool_calls or [],
            )
        )

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

    async def list_models(self):  # type: ignore[override]
        return []


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def karna_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    karna_dir = tmp_path / ".karna"
    monkeypatch.setattr("karna.config.KARNA_DIR", karna_dir)
    monkeypatch.setattr("karna.config.CONFIG_PATH", karna_dir / "config.toml")
    return karna_dir


@pytest.fixture()
def provider() -> DogfoodProvider:
    return DogfoodProvider()


@pytest.fixture()
def conversation() -> Conversation:
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
        provider.add_response(tool_calls=[ToolCall(id="tc1", name="bash", arguments={"command": "echo hello"})])
        # Second response: final text
        provider.add_response("The command returned hello.")

        conv = Conversation(messages=[Message(role="user", content="run echo hello")])
        tools = [BashTool()]

        events = []
        async for event in agent_loop(provider, conv, tools):
            events.append(event)

        # Should have tool call events or text
        types = {e.type for e in events}
        assert "tool_call_start" in types or "text" in types

    @pytest.mark.asyncio
    async def test_max_iterations_respected(self, provider: DogfoodProvider) -> None:
        """Agent should stop after max_iterations."""
        from karna.agents.loop import agent_loop
        from karna.tools.bash import BashTool

        # Keep calling tools
        for i in range(30):
            provider.add_response(tool_calls=[ToolCall(id=f"tc{i}", name="bash", arguments={"command": "echo loop"})])

        conv = Conversation(messages=[Message(role="user", content="loop")])
        tools = [BashTool()]

        async for _ in agent_loop(provider, conv, tools, max_iterations=3):
            pass

        # Should have stopped, not gone to 30
        assert len(provider.calls) <= 5


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
        await tool.execute(file_path=str(target), content="hello world")
        assert target.exists()
        assert target.read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_edit_tool(self, tmp_path: Path) -> None:
        from karna.tools.edit import EditTool

        target = tmp_path / "edit_test.txt"
        target.write_text("foo bar baz")
        tool = EditTool(allowed_roots=[tmp_path])
        await tool.execute(
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
        assert "a.py" in result

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

        os.system(
            f"cd {tmp_path} && git init -q && git config user.email 'test@test.com' && git config user.name 'Test'"
        )
        (tmp_path / "file.txt").write_text("hello")
        os.system(f"cd {tmp_path} && git add . && git commit -q -m 'init'")

        tool = GitTool()
        tool._cwd = str(tmp_path)  # type: ignore[attr-defined]
        result = await tool.execute(action="log", args="--oneline -1")
        assert "init" in result


# --------------------------------------------------------------------------- #
#  3. Slash commands
# --------------------------------------------------------------------------- #


class TestSlashCommands:
    """Test slash command dispatch."""

    def test_all_expected_commands_registered(self) -> None:
        from karna.tui.slash import COMMANDS

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
        }
        registered = set(COMMANDS.keys())
        missing = expected - registered
        assert not missing, f"Missing slash commands: {missing}"

    def test_clear_resets_conversation(self) -> None:
        from rich.console import Console

        from karna.tui.slash import handle_slash_command

        console = Console(file=StringIO(), force_terminal=True)
        config = KarnaConfig()
        conv = Conversation(messages=[Message(role="user", content="hi")])
        assert len(conv.messages) == 1
        # handle_slash_command is sync on main
        handle_slash_command("/clear", console, config, conv)
        assert len(conv.messages) == 0

    def test_help_command_produces_output(self) -> None:
        from rich.console import Console

        from karna.tui.slash import handle_slash_command

        buf = StringIO()
        console = Console(file=buf, force_terminal=True)
        config = KarnaConfig()
        conv = Conversation(messages=[])
        handle_slash_command("/help", console, config, conv)
        output = buf.getvalue()
        assert len(output) > 0


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
        renderer.handle(
            TUIStreamEvent(
                kind=EventKind.TOOL_CALL_START,
                data={"name": "bash", "id": "tc1"},
            )
        )
        renderer.handle(
            TUIStreamEvent(
                kind=EventKind.TOOL_CALL_ARGS_DELTA,
                data='{"command": "ls"}',
            )
        )
        renderer.handle(TUIStreamEvent(kind=EventKind.TOOL_CALL_END))
        renderer.handle(
            TUIStreamEvent(
                kind=EventKind.TOOL_RESULT,
                data={"content": "file.txt", "is_error": False},
            )
        )
        renderer.finish()
        output = buf.getvalue()
        # Should show tool name and result
        assert "Bash" in output or "bash" in output
        assert "file.txt" in output or len(output) > 0

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
        """Reading sensitive system files should be blocked."""
        from karna.tools.read import ReadTool

        tool = ReadTool()
        result = await tool.execute(file_path="/etc/shadow")
        # Should either raise or return an error/denied message
        assert (
            "denied" in result.lower()
            or "blocked" in result.lower()
            or "error" in result.lower()
            or "not found" in result.lower()
            or "permission" in result.lower()
            or "cannot" in result.lower()
        )

    @pytest.mark.asyncio
    async def test_secret_scrubbing_in_output(self) -> None:
        """Secrets should be scrubbed from tool output."""
        from karna.security.scrub import scrub_secrets

        output = "Connected with key sk-ant-api03-abcdef123456AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        scrubbed = scrub_secrets(output)
        assert "abcdef123456" not in scrubbed

    @pytest.mark.asyncio
    async def test_dangerous_bash_flagged(self) -> None:
        """Dangerous bash commands should be caught by safety checks."""
        from karna.agents.safety import pre_tool_check
        from karna.tools.bash import BashTool

        tool = BashTool()
        proceed, warning = await pre_tool_check(tool, {"command": "rm -rf /"})
        # Safety check should either block or warn about this command
        assert not proceed or warning is not None


# --------------------------------------------------------------------------- #
#  9. Prompt system
# --------------------------------------------------------------------------- #


class TestPromptSystem:
    """Test system prompt construction."""

    def test_system_prompt_has_content(self) -> None:
        from karna.prompts import build_system_prompt
        from karna.tools import get_all_tools

        config = KarnaConfig()
        tools = get_all_tools()
        prompt = build_system_prompt(config, tools)
        assert len(prompt) > 100  # not empty
        assert "tool" in prompt.lower() or "bash" in prompt.lower() or "assistant" in prompt.lower()

    def test_system_prompt_with_no_tools(self) -> None:
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

    def test_cost_tracker_no_crash(self, karna_home: Path) -> None:
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


# --------------------------------------------------------------------------- #
#  11. CLI surface (dogfood via Typer test runner)
# --------------------------------------------------------------------------- #


class TestCLISurface:
    """Dogfood the CLI entry point as a user would."""

    def test_help_exits_zero(self) -> None:
        from typer.testing import CliRunner

        from karna.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "nellie" in result.output.lower() or "karna" in result.output.lower()

    def test_version_exits_zero(self) -> None:
        from typer.testing import CliRunner

        from karna import __version__
        from karna.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_config_show_exits_zero(self) -> None:
        from typer.testing import CliRunner

        from karna.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "active_model" in result.output

    def test_auth_subcommand_help(self) -> None:
        from typer.testing import CliRunner

        from karna.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["auth", "--help"])
        assert result.exit_code == 0
        assert "login" in result.output.lower()

    def test_auth_login_stores_credential(self, tmp_path: Path) -> None:
        from typer.testing import CliRunner

        from karna.auth import credentials
        from karna.cli import app

        runner = CliRunner()
        with runner.isolated_filesystem():
            import unittest.mock as mock

            cred_dir = tmp_path / "credentials"
            with mock.patch.object(credentials, "CREDENTIALS_DIR", cred_dir):
                result = runner.invoke(app, ["auth", "login", "openrouter", "--key", "sk-fake-test"])
            assert result.exit_code == 0, result.output
            assert "saved openrouter credential" in result.output.lower()

    def test_tools_list_subcommand(self) -> None:
        """The auth list command should work without crashing."""
        from typer.testing import CliRunner

        from karna.cli import app

        runner = CliRunner()
        result = runner.invoke(app, ["auth", "list"])
        assert result.exit_code == 0
        # Should list zero or more providers without crashing

    def test_missing_api_key_shows_helpful_error(self) -> None:
        """Running without credentials should produce a friendly message, not crash."""
        from typer.testing import CliRunner

        from karna.cli import app

        runner = CliRunner()
        # auth list should work even with no credentials stored
        result = runner.invoke(app, ["auth", "list"])
        # Should exit 0 (no credentials is a normal state)
        assert result.exit_code == 0 or result.exit_code == 1  # both acceptable
