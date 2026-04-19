"""Tests for skills integration — system prompt injection, trigger matching, and slash commands.

Covers:
- Skills are injected into system prompt via build_system_prompt()
- Trigger matching works for slash commands and keywords
- Disabled skills are excluded from prompt and trigger matching
- Token budget trimming applies to skills section
- /skills slash command lists, enables, and disables skills
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from karna.config import KarnaConfig
from karna.prompts.system import build_system_prompt
from karna.skills.loader import SkillManager
from karna.tools.base import BaseTool

# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


class _FakeTool(BaseTool):
    name = "fake_tool"
    description = "A fake tool for testing."
    parameters = {
        "type": "object",
        "properties": {"input": {"type": "string", "description": "input"}},
        "required": ["input"],
    }

    async def execute(self, **kwargs):
        return "ok"


@pytest.fixture
def fake_tools() -> list[BaseTool]:
    return [_FakeTool()]


@pytest.fixture
def default_config() -> KarnaConfig:
    return KarnaConfig(
        active_model="openrouter/auto",
        active_provider="openrouter",
    )


@pytest.fixture
def tmp_skills_dir(tmp_path: Path) -> Path:
    """Create a temporary skills directory with sample skill files."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Skill 1: commit helper
    (skills_dir / "commit.md").write_text(
        textwrap.dedent("""\
        ---
        name: commit
        description: Generate commit messages
        triggers: ["/commit", "commit message"]
        ---

        When the user asks to commit, generate a conventional commit message.
        Use the format: type(scope): description
    """)
    )

    # Skill 2: review helper
    (skills_dir / "review.md").write_text(
        textwrap.dedent("""\
        ---
        name: review
        description: Code review assistant
        triggers: ["/review", "review this"]
        ---

        Provide a thorough code review with focus on:
        - Correctness
        - Security
        - Performance
    """)
    )

    # Skill 3: disabled skill
    (skills_dir / "disabled-skill.md").write_text(
        textwrap.dedent("""\
        ---
        name: disabled-skill
        description: This skill is disabled
        triggers: ["/disabled"]
        enabled: false
        ---

        This should not appear anywhere.
    """)
    )

    return skills_dir


@pytest.fixture
def skill_manager(tmp_skills_dir: Path) -> SkillManager:
    """Return a SkillManager loaded with test skills."""
    mgr = SkillManager(skills_dir=tmp_skills_dir)
    mgr.load_all()
    return mgr


# ------------------------------------------------------------------ #
#  System prompt injection tests
# ------------------------------------------------------------------ #


class TestSkillsInSystemPrompt:
    """Test that skills are injected into the system prompt."""

    def test_skills_section_present(self, default_config, fake_tools, skill_manager):
        prompt = build_system_prompt(default_config, fake_tools, skill_manager=skill_manager)
        assert "# Skills" in prompt or "Available Skills" in prompt
        assert "commit" in prompt
        assert "review" in prompt

    def test_skill_instructions_injected(self, default_config, fake_tools, skill_manager):
        prompt = build_system_prompt(default_config, fake_tools, skill_manager=skill_manager)
        assert "conventional commit" in prompt
        assert "Correctness" in prompt

    def test_disabled_skills_excluded(self, default_config, fake_tools, skill_manager):
        prompt = build_system_prompt(default_config, fake_tools, skill_manager=skill_manager)
        assert "disabled-skill" not in prompt
        assert "This should not appear" not in prompt

    def test_no_skills_no_section(self, default_config, fake_tools):
        """When no SkillManager is provided, prompt still builds fine."""
        prompt = build_system_prompt(default_config, fake_tools)
        assert isinstance(prompt, str)
        assert len(prompt) > 100
        # Should not contain a stray Skills header
        assert "# Skills" not in prompt

    def test_empty_skills_dir(self, default_config, fake_tools, tmp_path):
        """Empty skills dir produces no skills section."""
        empty_dir = tmp_path / "empty_skills"
        empty_dir.mkdir()
        mgr = SkillManager(skills_dir=empty_dir)
        mgr.load_all()
        prompt = build_system_prompt(default_config, fake_tools, skill_manager=mgr)
        assert "# Skills" not in prompt


# ------------------------------------------------------------------ #
#  Token budget trimming tests
# ------------------------------------------------------------------ #


class TestSkillsTokenBudget:
    """Test that skills respect the token budget."""

    def test_skills_trimmed_under_budget(self, default_config, fake_tools, tmp_path):
        """When skills are large, they should be trimmed to fit budget."""
        skills_dir = tmp_path / "big_skills"
        skills_dir.mkdir()

        # Create a very large skill
        (skills_dir / "huge.md").write_text(
            '---\nname: huge\ndescription: A huge skill\ntriggers: ["/huge"]\n---\n\n' + "x" * 50000 + "\n"
        )
        mgr = SkillManager(skills_dir=skills_dir)
        mgr.load_all()

        prompt = build_system_prompt(
            default_config,
            fake_tools,
            skill_manager=mgr,
            max_tokens=2000,
        )
        # The prompt should still build without error
        assert isinstance(prompt, str)
        # The huge content should have been trimmed
        assert len(prompt) < 50000


# ------------------------------------------------------------------ #
#  Trigger matching tests
# ------------------------------------------------------------------ #


class TestTriggerMatching:
    """Test SkillManager.match_trigger integration."""

    def test_slash_trigger_match(self, skill_manager):
        matched = skill_manager.match_trigger("/commit fix the bug")
        names = [s.name for s in matched]
        assert "commit" in names

    def test_keyword_trigger_match(self, skill_manager):
        matched = skill_manager.match_trigger("can you review this PR?")
        names = [s.name for s in matched]
        assert "review" in names

    def test_no_match(self, skill_manager):
        matched = skill_manager.match_trigger("hello world")
        assert matched == []

    def test_disabled_skill_not_matched(self, skill_manager):
        matched = skill_manager.match_trigger("/disabled")
        assert matched == []

    def test_multiple_triggers(self, skill_manager):
        matched = skill_manager.match_trigger("commit message for review this")
        names = [s.name for s in matched]
        assert "commit" in names
        assert "review" in names


# ------------------------------------------------------------------ #
#  Slash command tests
# ------------------------------------------------------------------ #


class TestSkillsSlashCommand:
    """Test /skills slash command."""

    def test_skills_command_registered(self):
        from karna.tui.slash import COMMANDS

        assert "skills" in COMMANDS

    def test_skills_lists_loaded_skills(self, skill_manager):
        from io import StringIO

        from rich.console import Console

        from karna.tui.slash import handle_slash_command

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        config = KarnaConfig()
        from karna.models import Conversation

        conversation = Conversation()

        handle_slash_command(
            "/skills",
            console,
            config,
            conversation,
            skill_manager=skill_manager,
        )
        output = buf.getvalue()
        assert "commit" in output
        assert "review" in output

    def test_skills_enable(self, skill_manager):
        from io import StringIO

        from rich.console import Console

        from karna.tui.slash import handle_slash_command

        # First disable it
        skill_manager.disable_skill("commit")
        assert not skill_manager.get_skill_by_name("commit").enabled

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        config = KarnaConfig()
        from karna.models import Conversation

        conversation = Conversation()

        handle_slash_command(
            "/skills enable commit",
            console,
            config,
            conversation,
            skill_manager=skill_manager,
        )
        output = buf.getvalue()
        assert "enabled" in output.lower() or "Enabled" in output
        assert skill_manager.get_skill_by_name("commit").enabled

    def test_skills_disable(self, skill_manager):
        from io import StringIO

        from rich.console import Console

        from karna.tui.slash import handle_slash_command

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        config = KarnaConfig()
        from karna.models import Conversation

        conversation = Conversation()

        handle_slash_command(
            "/skills disable review",
            console,
            config,
            conversation,
            skill_manager=skill_manager,
        )
        output = buf.getvalue()
        assert "disabled" in output.lower() or "Disabled" in output
        assert not skill_manager.get_skill_by_name("review").enabled

    def test_skills_enable_unknown(self, skill_manager):
        from io import StringIO

        from rich.console import Console

        from karna.tui.slash import handle_slash_command

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        config = KarnaConfig()
        from karna.models import Conversation

        conversation = Conversation()

        handle_slash_command(
            "/skills enable nonexistent",
            console,
            config,
            conversation,
            skill_manager=skill_manager,
        )
        output = buf.getvalue()
        assert "not found" in output.lower() or "unknown" in output.lower()
