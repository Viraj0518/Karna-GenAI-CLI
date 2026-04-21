"""Sub-recipe execution engine — invoke child recipes as subagents.

Parent recipes declare sub-recipes via ``SubRecipeRef`` entries. This
module resolves the sub-recipe YAML, substitutes parent context values
into sub-recipe inputs via Jinja2, spawns a subagent to execute the
sub-recipe's prompt, and returns the output text to the parent context.

Nesting is supported up to 3 levels deep (recipe -> sub -> sub-sub).
Parameter flow: parent -> sub via Jinja2 template substitution;
sub -> parent via structured return injected as ``{{ sub.<name> }}``.

Usage::

    result = await run_sub_recipe(
        parent_recipe=recipe,
        sub_ref=recipe.sub_recipes[0],
        parent_context=resolved_params,
        provider=provider,
        tools=tools,
    )
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from karna.recipes.model import Recipe, SubRecipeRef

if TYPE_CHECKING:
    from karna.providers.base import BaseProvider
    from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Maximum nesting depth for sub-recipe invocations.
MAX_SUB_RECIPE_DEPTH = 3


class SubRecipeError(RuntimeError):
    """Raised when sub-recipe execution fails."""

class MaxDepthExceededError(SubRecipeError):
    """Raised when sub-recipe nesting exceeds the maximum allowed depth."""

class SubRecipeNotFoundError(SubRecipeError, FileNotFoundError):
    """Raised when a sub-recipe YAML file cannot be found."""

class SubRecipeParameterError(SubRecipeError, ValueError):
    """Raised when parameter substitution or validation fails for a sub-recipe."""

def _resolve_sub_recipe_path(
    parent_recipe_path: Path | None,
    sub_recipe_ref: str,
) -> Path:
    """Resolve a sub-recipe path relative to the parent recipe directory.

    If parent_recipe_path is None (in-memory recipe), treats the ref as
    an absolute path or relative to cwd.
    """
    ref_path = Path(sub_recipe_ref)
    if ref_path.is_absolute():
        return ref_path
    if parent_recipe_path is not None:
        return parent_recipe_path.parent / ref_path
    return Path.cwd() / ref_path


def _render_inputs(
    inputs: dict[str, Any],
    parent_context: dict[str, Any],
) -> dict[str, Any]:
    """Render Jinja2 templates in sub-recipe input values against parent context.

    Each input value that is a string is treated as a Jinja2 template and
    rendered against the parent context. Non-string values pass through
    unchanged.
    """
    try:
        import jinja2
    except ImportError:
        jinja2 = None  # type: ignore[assignment]

    rendered: dict[str, Any] = {}
    for key, value in inputs.items():
        if isinstance(value, str):
            if jinja2 is not None:
                env = jinja2.Environment(
                    undefined=jinja2.StrictUndefined,
                    autoescape=False,
                )
                try:
                    rendered[key] = env.from_string(value).render(**parent_context)
                except jinja2.UndefinedError as exc:
                    raise SubRecipeParameterError(f"Failed to render input {key!r} for sub-recipe: {exc}") from exc
            else:
                # Minimal fallback without jinja2
                import re

                pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

                def repl(m: re.Match) -> str:
                    k = m.group(1)
                    if k not in parent_context:
                        raise SubRecipeParameterError(f"Undefined variable {k!r} in sub-recipe input {key!r}")
                    return str(parent_context[k])

                rendered[key] = pattern.sub(repl, value)
        else:
            rendered[key] = value
    return rendered


async def run_sub_recipe(
    parent_recipe: Recipe,
    sub_ref: SubRecipeRef,
    parent_context: dict[str, Any],
    provider: "BaseProvider",  # noqa: F821
    tools: list["BaseTool"],  # noqa: F821
    *,
    parent_recipe_path: Path | None = None,
    current_depth: int = 1,
    workspace: str | None = None,
) -> str:
    """Execute a sub-recipe as a subagent and return its output.

    Parameters
    ----------
    parent_recipe
        The Recipe that declares this sub-recipe invocation.
    sub_ref
        The SubRecipeRef describing which sub-recipe to invoke and with
        what inputs.
    parent_context
        The resolved parameter dict from the parent recipe (used for
        Jinja2 substitution into sub-recipe inputs).
    provider
        The LLM provider instance to use for the subagent.
    tools
        Tools available to the subagent.
    parent_recipe_path
        Filesystem path to the parent recipe YAML. Used to resolve
        relative sub-recipe paths. None for in-memory recipes.
    current_depth
        Current nesting depth (1 = first sub-recipe level). Used to
        enforce MAX_SUB_RECIPE_DEPTH.
    workspace
        Optional workspace directory for filesystem-isolated execution.

    Returns
    -------
    str
        The subagent's final text output.

    Raises
    ------
    MaxDepthExceededError
        If nesting would exceed MAX_SUB_RECIPE_DEPTH.
    SubRecipeNotFoundError
        If the sub-recipe YAML file cannot be found.
    SubRecipeParameterError
        If parameter substitution or validation fails.
    """
    # Deferred imports to avoid circular dependencies at module load time.
    # These are imported here so they resolve at call time.
    from karna.agents.subagent import spawn_subagent  # noqa: PLC0415
    from karna.config import load_config  # noqa: PLC0415
    from karna.recipes.loader import load_recipe  # noqa: PLC0415
    from karna.recipes.runner import _render_template  # noqa: PLC0415

    # Enforce depth limit
    if current_depth > MAX_SUB_RECIPE_DEPTH:
        raise MaxDepthExceededError(
            f"Sub-recipe nesting depth {current_depth} exceeds maximum "
            f"of {MAX_SUB_RECIPE_DEPTH}. Recipe chain: {parent_recipe.name} -> {sub_ref.name}"
        )

    # Resolve and load the sub-recipe YAML
    sub_path = _resolve_sub_recipe_path(parent_recipe_path, sub_ref.recipe)
    if not sub_path.exists():
        raise SubRecipeNotFoundError(
            f"Sub-recipe file not found: {sub_path} (referenced by {parent_recipe.name!r} sub_recipe {sub_ref.name!r})"
        )

    sub_recipe = load_recipe(sub_path)

    # Render sub-recipe inputs from parent context via Jinja2
    rendered_inputs = _render_inputs(sub_ref.inputs, parent_context)

    # Validate and resolve sub-recipe parameters
    try:
        resolved_sub_params = sub_recipe.resolve_parameters(rendered_inputs)
    except ValueError as exc:
        raise SubRecipeParameterError(f"Parameter validation failed for sub-recipe {sub_ref.name!r}: {exc}") from exc

    # If the sub-recipe itself has sub-recipes, run them first
    # (depth-first execution) to populate `sub.*` context variables
    # before rendering the instructions template.
    sub_context = dict(resolved_sub_params)
    if sub_recipe.sub_recipes:
        sub_results: dict[str, str] = {}
        for nested_ref in sub_recipe.sub_recipes:
            nested_result = await run_sub_recipe(
                parent_recipe=sub_recipe,
                sub_ref=nested_ref,
                parent_context=sub_context,
                provider=provider,
                tools=tools,
                parent_recipe_path=sub_path,
                current_depth=current_depth + 1,
                workspace=workspace,
            )
            sub_results[nested_ref.name] = nested_result

        # Add nested sub-recipe results to context
        sub_context["sub"] = sub_results

    # Render the sub-recipe instructions with full context (params + sub results)
    rendered_instructions = _render_template(sub_recipe.instructions, sub_context)

    # Spawn a subagent to execute the sub-recipe
    config = load_config()
    logger.info(
        "Running sub-recipe %r (depth=%d) for parent %r",
        sub_ref.name,
        current_depth,
        parent_recipe.name,
    )

    result = await spawn_subagent(
        rendered_instructions,
        parent_config=config,
        parent_provider=provider,
        tools=tools,
        model=sub_recipe.model,
        max_iterations=sub_recipe.max_iterations,
        system_prompt=(
            f"You are executing sub-recipe '{sub_recipe.name}'. "
            f"{sub_recipe.description or 'Complete the task and report back.'}"
        ),
    )

    return result


async def run_all_sub_recipes(
    recipe: Recipe,
    parent_context: dict[str, Any],
    provider: "BaseProvider",  # noqa: F821
    tools: list["BaseTool"],  # noqa: F821
    *,
    recipe_path: Path | None = None,
    workspace: str | None = None,
) -> dict[str, str]:
    """Execute all sub-recipes declared in a recipe and return results.

    Results are keyed by sub-recipe name for injection into the parent
    context as ``{{ sub.<name> }}``.

    Sub-recipes declared with ``sequential_when_repeated`` are executed
    in order. By default, all sub-recipes run sequentially to maintain
    deterministic output (parallel execution is a future enhancement).

    Parameters
    ----------
    recipe
        The parent Recipe containing sub_recipes declarations.
    parent_context
        Resolved parameters from the parent recipe.
    provider
        LLM provider for subagent execution.
    tools
        Tools available to subagents.
    recipe_path
        Path to the parent recipe YAML (for relative path resolution).
    workspace
        Optional workspace for filesystem isolation.

    Returns
    -------
    dict[str, str]
        Mapping of sub-recipe name -> subagent output text.
    """
    if not recipe.sub_recipes:
        return {}

    results: dict[str, str] = {}
    for sub_ref in recipe.sub_recipes:
        result = await run_sub_recipe(
            parent_recipe=recipe,
            sub_ref=sub_ref,
            parent_context=parent_context,
            provider=provider,
            tools=tools,
            parent_recipe_path=recipe_path,
            current_depth=1,
            workspace=workspace,
        )
        results[sub_ref.name] = result

    return results
