"""Recipe runner — given a Recipe + a params dict, run one agent turn.

Kept separate from the loader so tests can instantiate in-memory Recipe
objects without round-tripping through YAML. Isolated from FastAPI /
MCP server code so any transport (CLI ``nellie run``, REST
``/v1/recipes/{name}/run``, cron scheduler tick) shares the same
runner.
"""

from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any

from karna.agents.loop import agent_loop
from karna.config import load_config
from karna.models import Conversation, Message
from karna.prompts import build_system_prompt
from karna.providers import get_provider, resolve_model
from karna.recipes.model import Recipe
from karna.tools import _TOOL_PATHS  # type: ignore[attr-defined]


def _instantiate_tools_for_recipe(recipe: Recipe, workspace: str | None) -> list:
    """Instantiate only the tools the recipe's extensions list allows.

    Empty extensions list = all tools allowed (parity with Goose's
    "recipe omitting extensions = full trust" default).
    """
    allowed_roots = [Path(workspace)] if workspace else None
    allowed_names = set(recipe.extensions) if recipe.extensions else None
    tools = []
    for name, (module_path, class_name) in _TOOL_PATHS.items():
        if allowed_names is not None and name not in allowed_names:
            continue
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        if allowed_roots and name in ("write", "edit"):
            instance = cls(allowed_roots=allowed_roots)
        else:
            instance = cls()
        if workspace and name == "bash" and hasattr(instance, "_cwd"):
            instance._cwd = workspace  # type: ignore[attr-defined]
        tools.append(instance)
    return tools


def _render_template(template: str, context: dict[str, Any]) -> str:
    """Render Jinja2 template against ``context``.

    Use Jinja2 when available; fall back to a minimal ``{{ var }}``
    substitution that handles the 90% case so recipes still run on
    an install without the optional dep.
    """
    try:
        import jinja2  # type: ignore[import-untyped]
    except ImportError:
        pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")

        def repl(m):
            key = m.group(1)
            return str(context.get(key, m.group(0)))

        return pattern.sub(repl, template)

    env = jinja2.Environment(
        # StrictUndefined: fail loudly on typos in recipe templates
        # rather than silently producing "None" strings.
        undefined=jinja2.StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    return env.from_string(template).render(**context)


async def run_recipe(
    recipe: Recipe,
    params: dict[str, Any] | None = None,
    *,
    workspace: str | None = None,
    system_extension: str | None = None,
    recipe_path: Path | str | None = None,
) -> dict[str, Any]:
    """Execute a recipe end-to-end.

    Returns ``{text, halt, errors, events_count, rendered_instructions}``.

    Parameters
    ----------
    recipe_path
        Path to the recipe YAML file (used for resolving relative
        sub-recipe paths). If None, sub-recipes with relative paths
        resolve against cwd.
    """
    params = params or {}
    resolved = recipe.resolve_parameters(params)

    # --- Sub-recipe execution (before main instructions) ---
    # Sub-recipes run first so their outputs can be referenced in the
    # parent's instructions template as {{ sub.<name> }}.
    sub_results: dict[str, str] = {}
    if recipe.sub_recipes:
        from karna.recipes.sub import run_all_sub_recipes

        config = load_config()
        model_spec = recipe.model or f"{config.active_provider}:{config.active_model}"
        provider_name, model_name = resolve_model(model_spec)
        sub_provider = get_provider(provider_name)
        sub_provider.model = model_name
        sub_tools = _instantiate_tools_for_recipe(recipe, workspace)

        rp = Path(recipe_path) if recipe_path else None
        sub_results = await run_all_sub_recipes(
            recipe,
            resolved,
            sub_provider,
            sub_tools,
            recipe_path=rp,
            workspace=workspace,
        )

    # Merge sub-recipe results into the template context
    template_context = dict(resolved)
    if sub_results:
        template_context["sub"] = sub_results

    rendered = _render_template(recipe.instructions, template_context)

    config = load_config()
    model_spec = recipe.model or f"{config.active_provider}:{config.active_model}"
    provider_name, model_name = resolve_model(model_spec)
    provider = get_provider(provider_name)
    provider.model = model_name

    if workspace:
        os.makedirs(workspace, exist_ok=True)
    tools = _instantiate_tools_for_recipe(recipe, workspace)

    conversation = Conversation()
    conversation.messages.append(Message(role="user", content=rendered))

    system_prompt = build_system_prompt(config, tools)
    if system_extension:
        system_prompt = f"{system_prompt}\n\n{system_extension}"

    text_parts: list[str] = []
    errors: list[str] = []
    saw_done = False
    event_count = 0

    async for event in agent_loop(
        provider=provider,
        conversation=conversation,
        tools=tools,
        system_prompt=system_prompt,
        max_iterations=recipe.max_iterations,
    ):
        event_count += 1
        if event.type == "text" and event.text:
            text_parts.append(event.text)
        elif event.type == "error":
            err = event.error or event.text
            if err:
                errors.append(err)
        elif event.type == "done":
            saw_done = True

    text = "".join(text_parts).strip()
    if errors and not text:
        halt = "error"
    elif not text and not saw_done:
        halt = "max_iterations"
    elif not text:
        halt = "empty_reply"
    else:
        halt = "done"

    return {
        "text": text,
        "halt": halt,
        "errors": errors,
        "event_count": event_count,
        "rendered_instructions": rendered,
        "resolved_params": resolved,
        "model": model_spec,
    }
