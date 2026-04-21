"""Recipes — declarative YAML agent workflows, Jinja2-templated.

Goose-parity. A recipe is a reusable specification for an agent run:
instructions + parameter schema + tool/extension allowlist + optional
schedule. Recipes live as ``.yaml`` files under ``~/.karna/recipes/``
or inline in the user's project.

Example::

    name: triage_cdc_ticket
    description: Summarise an inbound CDC ticket + recommend triage
    parameters:
      - name: ticket_id
        type: string
        required: true
      - name: priority
        type: string
        default: normal
    extensions:
      - name: db
      - name: web_fetch
    instructions: |
      You are triaging ticket {{ ticket_id }} at {{ priority }} priority.
      Read the ticket from the database. Summarise in 3 bullets. If it
      involves vaccine safety, flag for immediate review.
    model: openrouter:anthropic/claude-haiku-4.5
    max_iterations: 20

Run::

    nellie run --recipe triage_cdc_ticket.yaml --param ticket_id=CDC-4021

Public API::

    from karna.recipes import Recipe, load_recipe, run_recipe
"""

from __future__ import annotations

from karna.recipes.loader import load_recipe, load_recipe_from_dict
from karna.recipes.model import Recipe, RecipeParameter, SubRecipeRef
from karna.recipes.runner import run_recipe

__all__ = [
    "Recipe",
    "RecipeParameter",
    "SubRecipeRef",
    "load_recipe",
    "load_recipe_from_dict",
    "run_recipe",
]
