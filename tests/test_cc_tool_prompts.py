"""Regression guards for the CC tool-prompt port.

Guarantees that the verbatim Claude Code prompts from
``karna/prompts/cc_tool_prompts.py`` are wired into the 10 Nellie tools
and that they flow through the model-facing surfaces (API schemas +
system prompt).
"""

from __future__ import annotations

import pytest

from karna.prompts.cc_tool_prompts import CC_TOOL_PROMPTS
from karna.prompts.tool_descriptions import generate_tool_docs
from karna.tools.bash import BashTool
from karna.tools.edit import EditTool
from karna.tools.glob import GlobTool
from karna.tools.grep import GrepTool
from karna.tools.notebook import NotebookTool
from karna.tools.read import ReadTool
from karna.tools.task import TaskTool
from karna.tools.web_fetch import WebFetchTool
from karna.tools.web_search import WebSearchTool
from karna.tools.write import WriteTool


@pytest.fixture
def all_cc_tools():
    return [
        BashTool(),
        ReadTool(),
        WriteTool(),
        EditTool(),
        GrepTool(),
        GlobTool(),
        NotebookTool(),
        TaskTool(),
        WebFetchTool(),
        WebSearchTool(),
    ]


def test_every_cc_tool_has_a_cc_prompt(all_cc_tools):
    """Each CC-mapped tool exposes the verbatim CC prompt."""
    for tool in all_cc_tools:
        assert tool.cc_prompt, f"{tool.name}: cc_prompt is empty"
        assert tool.cc_prompt == CC_TOOL_PROMPTS[tool.name], (
            f"{tool.name}: cc_prompt doesn't match central registry"
        )


def test_model_facing_description_prefers_cc_prompt(all_cc_tools):
    """The model-facing surface returns the rich CC prompt, not the short description."""
    for tool in all_cc_tools:
        assert tool.model_facing_description == tool.cc_prompt, (
            f"{tool.name}: model_facing_description should return cc_prompt when set"
        )
        assert tool.model_facing_description != tool.description


def test_api_schemas_ship_cc_prompt_to_the_model(all_cc_tools):
    """OpenAI and Anthropic tool schemas both carry the CC prompt verbatim."""
    for tool in all_cc_tools:
        openai_schema = tool.to_openai_tool()
        anthropic_schema = tool.to_anthropic_tool()
        assert openai_schema["function"]["description"] == tool.cc_prompt, (
            f"{tool.name}: OpenAI schema description is not cc_prompt"
        )
        assert anthropic_schema["description"] == tool.cc_prompt, (
            f"{tool.name}: Anthropic schema description is not cc_prompt"
        )


def test_system_prompt_includes_cc_prompt(all_cc_tools):
    """generate_tool_docs() inlines the full cc_prompt into the system prompt."""
    docs = generate_tool_docs(all_cc_tools)
    for tool in all_cc_tools:
        assert tool.cc_prompt in docs, (
            f"{tool.name}: cc_prompt body missing from generated tool docs"
        )


def test_short_description_still_populated_for_ui(all_cc_tools):
    """The short ``description`` field survives — still needed for web/TUI/slash UIs."""
    for tool in all_cc_tools:
        assert tool.description, f"{tool.name}: short description is empty"
        assert len(tool.description) < len(tool.cc_prompt), (
            f"{tool.name}: short description should be shorter than cc_prompt"
        )


def test_scraping_framing_reaches_model_via_web_fetch_cc_prompt():
    """Web-fetch CC prompt contains the capability language that blocks scraping refusals."""
    tool = WebFetchTool()
    prompt = tool.model_facing_description
    # CC's verbatim wording
    assert "Fetches content from a specified URL" in prompt
    assert "retrieve and analyze web content" in prompt


def test_web_search_cc_prompt_interpolates_current_month_year():
    """The runtime-rendered CC WebSearch prompt carries an anchor date."""
    tool = WebSearchTool()
    prompt = tool.model_facing_description
    # The template embeds `getLocalMonthYear()`-style output, e.g. "April 2026"
    assert "current month is" in prompt
    assert "You MUST use this year" in prompt


def test_nellie_tool_names_are_lowercase_in_cc_prompts():
    """CC uses 'Read' / 'Bash'; we rewrite to Nellie's lowercase registry names."""
    # Spot-check a few that reference other tools
    assert "Use glob (NOT find or ls)" in CC_TOOL_PROMPTS["bash"]
    assert "Use read (NOT cat/head/tail)" in CC_TOOL_PROMPTS["bash"]
    assert "Use edit (NOT sed/awk)" in CC_TOOL_PROMPTS["bash"]
    assert "multiple bash tool calls" in CC_TOOL_PROMPTS["bash"]
    assert "must use your `read` tool" in CC_TOOL_PROMPTS["edit"]
    assert "ALWAYS use grep" in CC_TOOL_PROMPTS["grep"]
    assert "Use the task tool" in CC_TOOL_PROMPTS["grep"]
    # Capitalised CC tool names should NOT appear
    assert "use the Bash tool" not in CC_TOOL_PROMPTS["grep"]
    assert "Use Read" not in CC_TOOL_PROMPTS["bash"]
