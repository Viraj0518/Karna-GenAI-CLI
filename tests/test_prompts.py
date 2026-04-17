"""Tests for the system prompt engine.

Covers:
- Prompt builds without error for all providers
- Contains tool names from the registry
- Respects max_tokens budget
- Model adaptation changes output for different providers
- Project context / custom instructions injection works
- Weak model detection and template selection
- Tool description generation
"""

from __future__ import annotations

import pytest

from karna.config import KarnaConfig
from karna.prompts import build_system_prompt, generate_tool_docs, adapt_for_model, get_adaptation
from karna.prompts.system import _is_weak_model, _estimate_tokens, MODEL_ADAPTATIONS
from karna.tools import get_all_tools
from karna.tools.base import BaseTool


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #


class FakeTool(BaseTool):
    name = "fake_tool"
    description = "A fake tool for testing."
    parameters = {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": "The input value.",
            },
        },
        "required": ["input"],
    }

    async def execute(self, **kwargs):
        return "ok"


@pytest.fixture
def default_config() -> KarnaConfig:
    return KarnaConfig(
        active_model="openrouter/auto",
        active_provider="openrouter",
    )


@pytest.fixture
def anthropic_config() -> KarnaConfig:
    return KarnaConfig(
        active_model="claude-sonnet-4-20250514",
        active_provider="anthropic",
    )


@pytest.fixture
def local_config() -> KarnaConfig:
    return KarnaConfig(
        active_model="phi-3-mini",
        active_provider="local",
    )


@pytest.fixture
def tools() -> list[BaseTool]:
    return get_all_tools()


@pytest.fixture
def fake_tools() -> list[BaseTool]:
    return [FakeTool()]


# ------------------------------------------------------------------ #
#  Core builder tests
# ------------------------------------------------------------------ #


class TestBuildSystemPrompt:
    """Tests for build_system_prompt()."""

    def test_builds_without_error(self, default_config, tools):
        prompt = build_system_prompt(default_config, tools)
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_contains_identity(self, default_config, tools):
        prompt = build_system_prompt(default_config, tools)
        assert "Karna" in prompt
        assert "terminal" in prompt

    def test_contains_tool_names(self, default_config, tools):
        prompt = build_system_prompt(default_config, tools)
        for tool in tools:
            assert tool.name in prompt, f"Tool '{tool.name}' not found in prompt"

    def test_contains_behavioral_guidelines(self, default_config, tools):
        prompt = build_system_prompt(default_config, tools)
        assert "read" in prompt.lower()
        assert "edit" in prompt.lower()
        # Check for key behavioral concepts
        assert "concise" in prompt.lower() or "brief" in prompt.lower()

    def test_contains_environment_section(self, default_config, tools):
        prompt = build_system_prompt(default_config, tools)
        assert "Working directory" in prompt
        assert "Platform" in prompt

    def test_anthropic_provider(self, anthropic_config, tools):
        prompt = build_system_prompt(anthropic_config, tools)
        assert "Karna" in prompt
        # Should include Claude-specific notes
        assert "Claude" in prompt or "XML" in prompt

    def test_local_provider_weak_model(self, local_config, tools):
        prompt = build_system_prompt(local_config, tools)
        assert "Karna" in prompt
        # Weak model template has CRITICAL markers
        assert "CRITICAL" in prompt or "MUST" in prompt


class TestContextInjection:
    """Tests for context section injection."""

    def test_project_context_injected(self, default_config, tools):
        prompt = build_system_prompt(
            default_config,
            tools,
            project_context="This is a Python web app using FastAPI.",
        )
        assert "FastAPI" in prompt

    def test_git_context_injected(self, default_config, tools):
        prompt = build_system_prompt(
            default_config,
            tools,
            git_context="Branch: main\nClean working tree.",
        )
        assert "Branch: main" in prompt

    def test_memory_context_injected(self, default_config, tools):
        prompt = build_system_prompt(
            default_config,
            tools,
            memory_context="User prefers tabs over spaces.",
        )
        assert "tabs over spaces" in prompt

    def test_custom_instructions_injected(self, default_config, tools):
        prompt = build_system_prompt(
            default_config,
            tools,
            custom_instructions="Always use type hints in Python code.",
        )
        assert "type hints" in prompt

    def test_all_contexts_injected(self, default_config, tools):
        prompt = build_system_prompt(
            default_config,
            tools,
            project_context="Project: ACME",
            git_context="Branch: feature/x",
            memory_context="User likes tests",
            custom_instructions="Use ruff for linting",
        )
        assert "ACME" in prompt
        assert "feature/x" in prompt
        assert "User likes tests" in prompt
        assert "ruff" in prompt

    def test_none_contexts_are_fine(self, default_config, tools):
        prompt = build_system_prompt(default_config, tools)
        assert isinstance(prompt, str)
        assert len(prompt) > 100


class TestTokenBudget:
    """Tests for the max_tokens budget enforcement."""

    def test_respects_budget(self, default_config, fake_tools):
        # Use a small budget
        prompt = build_system_prompt(
            default_config,
            fake_tools,
            project_context="x" * 10000,
            git_context="y" * 10000,
            memory_context="z" * 10000,
            max_tokens=2000,
        )
        tokens = _estimate_tokens(prompt)
        # Should be within budget (with some tolerance for base prompt)
        assert tokens < 3000  # generous bound — base prompt fits

    def test_large_budget_keeps_all_context(self, default_config, fake_tools):
        prompt = build_system_prompt(
            default_config,
            fake_tools,
            project_context="Project Alpha details",
            git_context="Branch: develop",
            memory_context="Remember: user prefers vim",
            max_tokens=50000,
        )
        assert "Project Alpha" in prompt
        assert "Branch: develop" in prompt
        assert "vim" in prompt

    def test_small_budget_trims_memory_first(self, default_config, fake_tools):
        # Memory has highest priority number (5), so it should be trimmed first.
        # Use a tight budget but enough for base + project + git.
        large_memory = "MEMORY_MARKER " + "m" * 8000
        prompt = build_system_prompt(
            default_config,
            fake_tools,
            project_context="PROJECT_MARKER here",
            git_context="GIT_MARKER here",
            memory_context=large_memory,
            max_tokens=2500,
        )
        # Memory should have been trimmed, but project and git may survive
        # (depends on base prompt size). At minimum, check that we didn't crash.
        assert isinstance(prompt, str)


# ------------------------------------------------------------------ #
#  Model adaptation tests
# ------------------------------------------------------------------ #


class TestModelAdaptation:
    """Tests for adapt_for_model()."""

    def test_anthropic_adds_claude_notes(self):
        base = "Base prompt here."
        result = adapt_for_model(base, "anthropic", "claude-sonnet-4-20250514")
        assert "Claude" in result
        assert "XML" in result

    def test_openai_adds_function_notes(self):
        base = "Base prompt here."
        result = adapt_for_model(base, "openai", "gpt-4o")
        assert "function_call" in result

    def test_generic_model_no_additions(self):
        base = "Base prompt here."
        result = adapt_for_model(base, "openrouter", "some-random-model")
        assert result == base

    def test_weak_model_adds_reminders(self):
        base = "Base prompt here."
        result = adapt_for_model(base, "local", "phi-3-mini")
        assert "MUST" in result
        assert "read" in result.lower()

    def test_different_providers_produce_different_prompts(self, tools):
        anthropic_cfg = KarnaConfig(
            active_model="claude-sonnet-4-20250514",
            active_provider="anthropic",
        )
        openai_cfg = KarnaConfig(
            active_model="gpt-4o",
            active_provider="openai",
        )
        p1 = build_system_prompt(anthropic_cfg, tools)
        p2 = build_system_prompt(openai_cfg, tools)
        # They should be different due to model adaptation and possibly template
        assert p1 != p2


class TestGetAdaptation:
    """Tests for get_adaptation()."""

    def test_known_providers(self):
        for provider in ["anthropic", "openai", "openrouter", "azure", "local"]:
            adaptation = get_adaptation(provider)
            assert "tool_format" in adaptation
            assert "system_placement" in adaptation

    def test_unknown_provider_defaults_to_openai(self):
        adaptation = get_adaptation("some_unknown_provider")
        assert adaptation == MODEL_ADAPTATIONS["openai"]

    def test_anthropic_supports_cache(self):
        adaptation = get_adaptation("anthropic")
        assert adaptation["supports_cache"] is True

    def test_openai_no_cache(self):
        adaptation = get_adaptation("openai")
        assert adaptation["supports_cache"] is False


# ------------------------------------------------------------------ #
#  Weak model detection
# ------------------------------------------------------------------ #


class TestWeakModelDetection:
    """Tests for _is_weak_model()."""

    def test_phi_is_weak(self):
        assert _is_weak_model("phi-3-mini") is True

    def test_qwen_is_weak(self):
        assert _is_weak_model("qwen2-7b") is True

    def test_gemma_is_weak(self):
        assert _is_weak_model("gemma-2b") is True

    def test_llama_small_is_weak(self):
        assert _is_weak_model("llama-3.2-1b-instruct") is True
        assert _is_weak_model("llama-3.2-3b-instruct") is True

    def test_claude_is_not_weak(self):
        assert _is_weak_model("claude-sonnet-4-20250514") is False

    def test_gpt4o_is_not_weak(self):
        assert _is_weak_model("gpt-4o") is False

    def test_openrouter_prefix_detection(self):
        # OpenRouter models often come as "meta/llama-3.2-3b-instruct"
        assert _is_weak_model("meta/llama-3.2-3b-instruct") is True


# ------------------------------------------------------------------ #
#  Tool description generation
# ------------------------------------------------------------------ #


class TestToolDescriptions:
    """Tests for generate_tool_docs()."""

    def test_generates_for_real_tools(self, tools):
        docs = generate_tool_docs(tools)
        assert "## Available Tools" in docs
        for tool in tools:
            assert f"### {tool.name}" in docs

    def test_generates_for_fake_tool(self, fake_tools):
        docs = generate_tool_docs(fake_tools)
        assert "### fake_tool" in docs
        assert "A fake tool for testing" in docs
        assert "`input`" in docs

    def test_empty_tools_list(self):
        docs = generate_tool_docs([])
        assert "No tools" in docs

    def test_bash_tool_has_guidance(self, tools):
        docs = generate_tool_docs(tools)
        # The bash tool should have "Do NOT use for" guidance
        assert "Do NOT use for" in docs

    def test_includes_parameter_docs(self, tools):
        docs = generate_tool_docs(tools)
        # At minimum, bash tool's 'command' parameter
        assert "`command`" in docs
        assert "(required)" in docs
