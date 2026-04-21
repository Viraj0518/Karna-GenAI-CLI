"""Recipe YAML loader + parser.

Understands two source shapes:

1. A file path — ``load_recipe(Path("foo.yaml"))`` reads + parses
2. A dict — ``load_recipe_from_dict({...})`` for in-memory / testing

Both return a fully-validated :class:`Recipe` dataclass.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from karna.recipes.model import Recipe, RecipeParameter, SubRecipeRef


class RecipeLoadError(ValueError):
    """Raised when a recipe YAML is malformed or references unknown keys."""


_REQUIRED_FIELDS = ("name", "instructions")
_KNOWN_FIELDS = {
    "name",
    "description",
    "instructions",
    "parameters",
    "extensions",
    "model",
    "max_iterations",
    "sub_recipes",
    "schedule",
    "version",  # reserved for forward-compatible schema evolution
}


def load_recipe_from_dict(raw: dict[str, Any]) -> Recipe:
    """Build a Recipe from a parsed YAML dict."""
    if not isinstance(raw, dict):
        raise RecipeLoadError("Recipe root must be a mapping")
    for required in _REQUIRED_FIELDS:
        if required not in raw:
            raise RecipeLoadError(f"Recipe missing required field: {required!r}")

    unknown = set(raw.keys()) - _KNOWN_FIELDS
    if unknown:
        raise RecipeLoadError(
            f"Recipe contains unknown fields: {sorted(unknown)}. "
            f"Supported fields: {sorted(_KNOWN_FIELDS)}."
        )

    parameters = [
        _parse_parameter(p) for p in (raw.get("parameters") or [])
    ]
    sub_recipes = [
        _parse_sub_recipe(s) for s in (raw.get("sub_recipes") or [])
    ]
    extensions = raw.get("extensions") or []
    # Extensions may be listed as `[{"name": "db"}, {"name": "bash"}]` (Goose
    # shape with future fields) or `["db", "bash"]` (shorthand). Normalise.
    extension_names: list[str] = []
    for ext in extensions:
        if isinstance(ext, str):
            extension_names.append(ext)
        elif isinstance(ext, dict) and "name" in ext:
            extension_names.append(str(ext["name"]))
        else:
            raise RecipeLoadError(f"Extension entry must be str or {{name: str}}: {ext!r}")

    try:
        max_iters = int(raw.get("max_iterations", 25))
    except (TypeError, ValueError) as exc:
        raise RecipeLoadError(f"max_iterations must be an integer: {exc}") from exc

    return Recipe(
        name=str(raw["name"]),
        description=str(raw.get("description") or ""),
        instructions=str(raw["instructions"]),
        parameters=parameters,
        extensions=extension_names,
        model=raw.get("model"),
        max_iterations=max_iters,
        sub_recipes=sub_recipes,
        schedule=raw.get("schedule"),
    )


def _parse_parameter(raw: Any) -> RecipeParameter:
    if not isinstance(raw, dict):
        raise RecipeLoadError(f"Parameter entry must be a mapping: {raw!r}")
    if "name" not in raw:
        raise RecipeLoadError(f"Parameter missing 'name': {raw!r}")
    ptype = raw.get("type", "string")
    if ptype not in ("string", "integer", "number", "boolean"):
        raise RecipeLoadError(
            f"Parameter {raw['name']!r} has unsupported type {ptype!r}. "
            "Allowed: string, integer, number, boolean."
        )
    return RecipeParameter(
        name=str(raw["name"]),
        type=ptype,
        description=str(raw.get("description") or ""),
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
    )


def _parse_sub_recipe(raw: Any) -> SubRecipeRef:
    if not isinstance(raw, dict):
        raise RecipeLoadError(f"sub_recipes entry must be a mapping: {raw!r}")
    if "name" not in raw or "recipe" not in raw:
        raise RecipeLoadError(
            f"sub_recipes entry needs 'name' + 'recipe': {raw!r}"
        )
    inputs = raw.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise RecipeLoadError(f"sub_recipes.inputs must be a mapping: {raw!r}")
    return SubRecipeRef(
        name=str(raw["name"]),
        recipe=str(raw["recipe"]),
        inputs=inputs,
        description=str(raw.get("description") or ""),
    )


def load_recipe(path: Path | str) -> Recipe:
    """Read + parse a recipe YAML file."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RuntimeError(
            "pyyaml not installed. Recipe support requires it: "
            "pip install pyyaml (or `pip install 'karna[cron]'` which already "
            "includes pyyaml)"
        ) from exc

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Recipe not found: {p}")
    with p.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return load_recipe_from_dict(raw)
