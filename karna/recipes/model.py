"""Recipe dataclasses — the public shape gamma's sub-recipes + web UI
read from.

Kept separate from the loader/runner so they can be imported without
pulling in jinja2 / yaml.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class RecipeParameter:
    """One templated input a recipe expects."""

    name: str
    type: Literal["string", "integer", "number", "boolean"] = "string"
    description: str = ""
    required: bool = False
    default: Any = None

    def validate(self, value: Any) -> Any:
        """Coerce + validate a supplied value against this parameter's type."""
        if value is None:
            if self.required:
                raise ValueError(f"Parameter {self.name!r} is required")
            return self.default
        if self.type == "string":
            return str(value)
        if self.type == "integer":
            return int(value)
        if self.type == "number":
            return float(value)
        if self.type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "y", "on")
            return bool(value)
        raise ValueError(f"Unknown parameter type: {self.type}")


@dataclass
class SubRecipeRef:
    """Reference to a recipe that another recipe can invoke as a sub-task.

    Gamma's G1 implementation reads this list off the parent Recipe and
    dispatches each sub-recipe through the ``task`` tool as a subagent.
    Parent→child input flow is Jinja2 substitution against the parent's
    already-resolved parameter dict; child→parent output flow is the
    sub-agent's final text reply surfaced as ``{{ sub.<name> }}`` in the
    parent's template context.
    """

    name: str
    recipe: str  # path or package-relative identifier of the sub-recipe
    inputs: dict[str, Any] = field(default_factory=dict)
    description: str = ""


@dataclass
class Recipe:
    """Declarative spec for one agent run."""

    name: str
    instructions: str
    description: str = ""
    parameters: list[RecipeParameter] = field(default_factory=list)
    # Allowlist of tool NAMES (matches karna.tools._TOOL_PATHS keys).
    # Empty list = all tools allowed (same as recipe omitting the field).
    extensions: list[str] = field(default_factory=list)
    model: str | None = None
    max_iterations: int = 25
    sub_recipes: list[SubRecipeRef] = field(default_factory=list)
    # Optional schedule — "daily", "@hourly", "0 */2 * * *", etc.
    # When set, the recipe is registrable as a cron job; separate from
    # one-shot ``nellie run --recipe`` invocations.
    schedule: str | None = None

    def resolve_parameters(self, provided: dict[str, Any]) -> dict[str, Any]:
        """Validate + fill defaults for a param dict the user supplied."""
        resolved: dict[str, Any] = {}
        known = {p.name for p in self.parameters}
        extra = set(provided.keys()) - known
        if extra:
            raise ValueError(
                f"Unknown parameter(s) for recipe {self.name!r}: {sorted(extra)}"
            )
        for p in self.parameters:
            resolved[p.name] = p.validate(provided.get(p.name))
        return resolved
