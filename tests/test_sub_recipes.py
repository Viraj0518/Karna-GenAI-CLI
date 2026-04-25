"""Tests for the sub-recipe execution engine (karna.recipes.sub).

Covers:
- Simple sub-recipe invocation
- Parameter substitution (parent -> sub)
- Return value flow (sub -> parent)
- Nested recipes (3 levels)
- Missing sub-recipe file error
- Invalid parameter error
- Max depth exceeded error
- Sequential execution of repeated sub-recipes
- Path resolution (absolute and relative)
- Input rendering with Jinja2 fallback
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from karna.recipes.model import Recipe, SubRecipeRef
from karna.recipes.sub import (
    MAX_SUB_RECIPE_DEPTH,
    MaxDepthExceededError,
    SubRecipeNotFoundError,
    SubRecipeParameterError,
    _render_inputs,
    _resolve_sub_recipe_path,
    run_all_sub_recipes,
    run_sub_recipe,
)

# ============================================================== #
#  Path resolution
# ============================================================== #


def test_resolve_relative_path_with_parent():
    parent = Path("/recipes/parent.yaml")
    resolved = _resolve_sub_recipe_path(parent, "child.yaml")
    assert resolved == Path("/recipes/child.yaml")


def test_resolve_relative_path_subdir():
    parent = Path("/recipes/main/parent.yaml")
    resolved = _resolve_sub_recipe_path(parent, "sub/child.yaml")
    assert resolved == Path("/recipes/main/sub/child.yaml")


def test_resolve_absolute_path_ignores_parent():
    parent = Path("/recipes/parent.yaml")
    resolved = _resolve_sub_recipe_path(parent, "/absolute/child.yaml")
    assert resolved == Path("/absolute/child.yaml")


def test_resolve_path_no_parent():
    resolved = _resolve_sub_recipe_path(None, "child.yaml")
    assert resolved == Path.cwd() / "child.yaml"


# ============================================================== #
#  Input rendering
# ============================================================== #


def test_render_inputs_simple():
    inputs = {"query": "{{ topic }}", "limit": "{{ count }}"}
    ctx = {"topic": "vaccines", "count": "10"}
    rendered = _render_inputs(inputs, ctx)
    assert rendered == {"query": "vaccines", "limit": "10"}


def test_render_inputs_non_string_passthrough():
    inputs = {"n": 42, "flag": True, "query": "{{ topic }}"}
    ctx = {"topic": "test"}
    rendered = _render_inputs(inputs, ctx)
    assert rendered["n"] == 42
    assert rendered["flag"] is True
    assert rendered["query"] == "test"


def test_render_inputs_missing_variable_raises():
    inputs = {"query": "{{ undefined_var }}"}
    ctx = {"topic": "test"}
    with pytest.raises(SubRecipeParameterError, match="undefined_var"):
        _render_inputs(inputs, ctx)


# ============================================================== #
#  Simple sub-recipe invocation
# ============================================================== #


@pytest.mark.asyncio
async def test_simple_sub_recipe_invocation(tmp_path):
    """A basic sub-recipe loads, renders, spawns subagent, returns text."""
    # Write a sub-recipe YAML
    sub_yaml = tmp_path / "summarize.yaml"
    sub_yaml.write_text(
        "name: summarize\n"
        "description: Summarise a document\n"
        "instructions: Summarise the following topic - {{ topic }}\n"
        "parameters:\n"
        "  - name: topic\n"
        "    type: string\n"
        "    required: true\n",
        encoding="utf-8",
    )

    parent = Recipe(
        name="main",
        instructions="Use the summary: {{ sub.research }}",
        sub_recipes=[
            SubRecipeRef(
                name="research",
                recipe="summarize.yaml",
                inputs={"topic": "{{ query }}"},
            )
        ],
    )

    mock_provider = MagicMock()
    mock_tools: list = []

    with (
        patch("karna.agents.subagent.spawn_subagent", new_callable=AsyncMock) as mock_spawn,
        patch("karna.config.load_config") as mock_config,
    ):
        mock_spawn.return_value = "Summary: vaccines are important"
        mock_config.return_value = MagicMock()

        result = await run_sub_recipe(
            parent_recipe=parent,
            sub_ref=parent.sub_recipes[0],
            parent_context={"query": "vaccines"},
            provider=mock_provider,
            tools=mock_tools,
            parent_recipe_path=tmp_path / "parent.yaml",
        )

    assert result == "Summary: vaccines are important"
    mock_spawn.assert_called_once()
    # Verify the prompt passed to subagent contains rendered instructions
    call_args = mock_spawn.call_args
    assert "vaccines" in call_args.args[0]


# ============================================================== #
#  Parameter substitution (parent -> sub)
# ============================================================== #


@pytest.mark.asyncio
async def test_parameter_substitution_parent_to_sub(tmp_path):
    """Parent context values flow into sub-recipe parameters via Jinja2."""
    sub_yaml = tmp_path / "analyze.yaml"
    sub_yaml.write_text(
        "name: analyze\n"
        "instructions: Analyze {{ item }} at priority {{ prio }}\n"
        "parameters:\n"
        "  - name: item\n"
        "    type: string\n"
        "    required: true\n"
        "  - name: prio\n"
        "    type: string\n"
        "    default: normal\n",
        encoding="utf-8",
    )

    parent = Recipe(
        name="parent",
        instructions="Result: {{ sub.analysis }}",
        sub_recipes=[
            SubRecipeRef(
                name="analysis",
                recipe="analyze.yaml",
                inputs={"item": "{{ ticket_id }}", "prio": "{{ priority }}"},
            )
        ],
    )

    with (
        patch("karna.agents.subagent.spawn_subagent", new_callable=AsyncMock) as mock_spawn,
        patch("karna.config.load_config") as mock_config,
    ):
        mock_spawn.return_value = "Analysis complete"
        mock_config.return_value = MagicMock()

        result = await run_sub_recipe(
            parent_recipe=parent,
            sub_ref=parent.sub_recipes[0],
            parent_context={"ticket_id": "CDC-4021", "priority": "high"},
            provider=MagicMock(),
            tools=[],
            parent_recipe_path=tmp_path / "parent.yaml",
        )

    assert result == "Analysis complete"
    # Verify rendered instructions contain substituted values
    prompt = mock_spawn.call_args.args[0]
    assert "CDC-4021" in prompt
    assert "high" in prompt


# ============================================================== #
#  Return value flow (sub -> parent)
# ============================================================== #


@pytest.mark.asyncio
async def test_return_value_flow_sub_to_parent(tmp_path):
    """run_all_sub_recipes collects results keyed by sub-recipe name."""
    sub_yaml = tmp_path / "fetch.yaml"
    sub_yaml.write_text(
        "name: fetch\n"
        "instructions: Fetch data for {{ query }}\n"
        "parameters:\n"
        "  - name: query\n"
        "    type: string\n"
        "    required: true\n",
        encoding="utf-8",
    )

    parent = Recipe(
        name="main",
        instructions="Here is the data: {{ sub.fetcher }}",
        sub_recipes=[
            SubRecipeRef(
                name="fetcher",
                recipe="fetch.yaml",
                inputs={"query": "{{ search_term }}"},
            )
        ],
    )

    with (
        patch("karna.agents.subagent.spawn_subagent", new_callable=AsyncMock) as mock_spawn,
        patch("karna.config.load_config") as mock_config,
    ):
        mock_spawn.return_value = "Found 42 results"
        mock_config.return_value = MagicMock()

        results = await run_all_sub_recipes(
            parent,
            {"search_term": "covid"},
            MagicMock(),
            [],
            recipe_path=tmp_path / "parent.yaml",
        )

    assert results == {"fetcher": "Found 42 results"}


# ============================================================== #
#  Nested recipes (3 levels)
# ============================================================== #


@pytest.mark.asyncio
async def test_nested_recipes_three_levels(tmp_path):
    """Three-level nesting: parent -> sub -> sub-sub all execute correctly."""
    # Level 3 (deepest)
    level3 = tmp_path / "level3.yaml"
    level3.write_text(
        "name: level3\n"
        "instructions: Deep task for {{ item }}\n"
        "parameters:\n"
        "  - name: item\n"
        "    type: string\n"
        "    required: true\n",
        encoding="utf-8",
    )

    # Level 2 (middle) — references level3
    level2 = tmp_path / "level2.yaml"
    level2.write_text(
        "name: level2\n"
        "instructions: Middle task. Deep result - {{ sub.deep }}\n"
        "parameters:\n"
        "  - name: item\n"
        "    type: string\n"
        "    required: true\n"
        "sub_recipes:\n"
        "  - name: deep\n"
        "    recipe: level3.yaml\n"
        "    inputs:\n"
        "      item: '{{ item }}'\n",
        encoding="utf-8",
    )

    # Level 1 (top) — references level2
    parent = Recipe(
        name="level1",
        instructions="Top task. Middle result: {{ sub.middle }}",
        sub_recipes=[
            SubRecipeRef(
                name="middle",
                recipe="level2.yaml",
                inputs={"item": "{{ topic }}"},
            )
        ],
    )

    call_count = 0

    async def mock_spawn_fn(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # This is the level3 (deepest) subagent call
            return "deep-result"
        else:
            # This is the level2 subagent call (with level3 result in prompt)
            return "middle-result"

    with (
        patch("karna.agents.subagent.spawn_subagent", side_effect=mock_spawn_fn),
        patch("karna.config.load_config") as mock_config,
    ):
        mock_config.return_value = MagicMock()

        result = await run_sub_recipe(
            parent_recipe=parent,
            sub_ref=parent.sub_recipes[0],
            parent_context={"topic": "vaccines"},
            provider=MagicMock(),
            tools=[],
            parent_recipe_path=tmp_path / "parent.yaml",
            current_depth=1,
        )

    assert result == "middle-result"
    assert call_count == 2  # level3 + level2 both executed


# ============================================================== #
#  Missing sub-recipe file error
# ============================================================== #


@pytest.mark.asyncio
async def test_missing_sub_recipe_file(tmp_path):
    """Referencing a non-existent sub-recipe file raises SubRecipeNotFoundError."""
    parent = Recipe(
        name="main",
        instructions="placeholder",
        sub_recipes=[
            SubRecipeRef(
                name="ghost",
                recipe="nonexistent.yaml",
                inputs={},
            )
        ],
    )

    with pytest.raises(SubRecipeNotFoundError, match="nonexistent.yaml"):
        await run_sub_recipe(
            parent_recipe=parent,
            sub_ref=parent.sub_recipes[0],
            parent_context={},
            provider=MagicMock(),
            tools=[],
            parent_recipe_path=tmp_path / "parent.yaml",
        )


# ============================================================== #
#  Invalid parameter error
# ============================================================== #


@pytest.mark.asyncio
async def test_invalid_parameter_error(tmp_path):
    """Passing an unknown parameter to a sub-recipe raises SubRecipeParameterError."""
    sub_yaml = tmp_path / "strict.yaml"
    sub_yaml.write_text(
        "name: strict\ninstructions: Do something\nparameters:\n  - name: allowed\n    type: string\n",
        encoding="utf-8",
    )

    parent = Recipe(
        name="main",
        instructions="placeholder",
        sub_recipes=[
            SubRecipeRef(
                name="strict_sub",
                recipe="strict.yaml",
                inputs={"allowed": "ok", "forbidden": "nope"},
            )
        ],
    )

    with pytest.raises(SubRecipeParameterError, match="Unknown parameter"):
        await run_sub_recipe(
            parent_recipe=parent,
            sub_ref=parent.sub_recipes[0],
            parent_context={},
            provider=MagicMock(),
            tools=[],
            parent_recipe_path=tmp_path / "parent.yaml",
        )


# ============================================================== #
#  Max depth exceeded error
# ============================================================== #


@pytest.mark.asyncio
async def test_max_depth_exceeded(tmp_path):
    """Exceeding MAX_SUB_RECIPE_DEPTH raises MaxDepthExceededError."""
    sub_yaml = tmp_path / "any.yaml"
    sub_yaml.write_text(
        "name: any\ninstructions: anything\n",
        encoding="utf-8",
    )

    parent = Recipe(
        name="too_deep",
        instructions="placeholder",
        sub_recipes=[SubRecipeRef(name="child", recipe="any.yaml", inputs={})],
    )

    with pytest.raises(MaxDepthExceededError, match="exceeds maximum"):
        await run_sub_recipe(
            parent_recipe=parent,
            sub_ref=parent.sub_recipes[0],
            parent_context={},
            provider=MagicMock(),
            tools=[],
            parent_recipe_path=tmp_path / "parent.yaml",
            current_depth=MAX_SUB_RECIPE_DEPTH + 1,
        )


# ============================================================== #
#  Sequential execution of repeated sub-recipes
# ============================================================== #


@pytest.mark.asyncio
async def test_sequential_execution_multiple_sub_recipes(tmp_path):
    """Multiple sub-recipes execute sequentially, each getting correct params."""
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.yaml").write_text(
            f"name: {name}\n"
            f"instructions: Task {name} for {{{{ item }}}}\n"
            "parameters:\n"
            "  - name: item\n"
            "    type: string\n"
            "    required: true\n",
            encoding="utf-8",
        )

    parent = Recipe(
        name="multi",
        instructions="Results: {{ sub.a }}, {{ sub.b }}, {{ sub.c }}",
        sub_recipes=[
            SubRecipeRef(name="a", recipe="a.yaml", inputs={"item": "{{ x }}"}),
            SubRecipeRef(name="b", recipe="b.yaml", inputs={"item": "{{ x }}"}),
            SubRecipeRef(name="c", recipe="c.yaml", inputs={"item": "{{ x }}"}),
        ],
    )

    call_order: list[str] = []

    async def mock_spawn_fn(prompt, **kwargs):
        # Extract which sub-recipe is being called from the prompt
        for letter in ("a", "b", "c"):
            if f"Task {letter}" in prompt:
                call_order.append(letter)
                return f"result-{letter}"
        return "unknown"

    with (
        patch("karna.agents.subagent.spawn_subagent", side_effect=mock_spawn_fn),
        patch("karna.config.load_config") as mock_config,
    ):
        mock_config.return_value = MagicMock()

        results = await run_all_sub_recipes(
            parent,
            {"x": "test_item"},
            MagicMock(),
            [],
            recipe_path=tmp_path / "parent.yaml",
        )

    # All three executed in order
    assert call_order == ["a", "b", "c"]
    assert results == {"a": "result-a", "b": "result-b", "c": "result-c"}


# ============================================================== #
#  run_all_sub_recipes returns empty dict when no sub-recipes
# ============================================================== #


@pytest.mark.asyncio
async def test_no_sub_recipes_returns_empty():
    """A recipe with no sub_recipes returns an empty dict."""
    recipe = Recipe(name="solo", instructions="do stuff")
    results = await run_all_sub_recipes(recipe, {}, MagicMock(), [], recipe_path=None)
    assert results == {}


# ============================================================== #
#  Depth constant is 3
# ============================================================== #


def test_max_depth_constant():
    """Verify the max depth constant is set to 3."""
    assert MAX_SUB_RECIPE_DEPTH == 3
