"""Tests for the Recipes engine (loader + model + template rendering).

Runner.run_recipe is not tested here — it drives agent_loop which
needs a live provider. Runner integration tests live in the beta
B4 suite when they land.
"""

from __future__ import annotations

import pytest

from karna.recipes import Recipe, RecipeParameter, SubRecipeRef
from karna.recipes.loader import (
    RecipeLoadError,
    load_recipe,
    load_recipe_from_dict,
)
from karna.recipes.runner import _render_template


# ============================================================== #
#  Loader
# ============================================================== #


def test_minimal_recipe_loads():
    r = load_recipe_from_dict({"name": "x", "instructions": "do the thing"})
    assert r.name == "x"
    assert r.instructions == "do the thing"
    assert r.parameters == []
    assert r.extensions == []
    assert r.max_iterations == 25


def test_loader_rejects_missing_required():
    with pytest.raises(RecipeLoadError, match="instructions"):
        load_recipe_from_dict({"name": "x"})
    with pytest.raises(RecipeLoadError, match="name"):
        load_recipe_from_dict({"instructions": "y"})


def test_loader_rejects_unknown_fields():
    with pytest.raises(RecipeLoadError, match="unknown fields"):
        load_recipe_from_dict(
            {"name": "x", "instructions": "y", "secret_option": True}
        )


def test_loader_rejects_non_dict_root():
    with pytest.raises(RecipeLoadError, match="mapping"):
        load_recipe_from_dict(["name", "x"])  # type: ignore[arg-type]


def test_parameters_parse():
    r = load_recipe_from_dict({
        "name": "x",
        "instructions": "y",
        "parameters": [
            {"name": "ticket", "type": "string", "required": True},
            {"name": "prio", "default": "normal"},
            {"name": "n", "type": "integer", "default": 5},
        ],
    })
    assert len(r.parameters) == 3
    assert r.parameters[0].required is True
    assert r.parameters[1].type == "string"
    assert r.parameters[2].type == "integer"


def test_unsupported_parameter_type_rejected():
    with pytest.raises(RecipeLoadError, match="unsupported type"):
        load_recipe_from_dict({
            "name": "x",
            "instructions": "y",
            "parameters": [{"name": "p", "type": "widget"}],
        })


def test_extensions_normalise_both_forms():
    r = load_recipe_from_dict({
        "name": "x",
        "instructions": "y",
        "extensions": ["db", {"name": "bash"}, "web_fetch"],
    })
    assert r.extensions == ["db", "bash", "web_fetch"]


def test_sub_recipes_parse():
    r = load_recipe_from_dict({
        "name": "parent",
        "instructions": "y",
        "sub_recipes": [
            {"name": "research", "recipe": "research.yaml",
             "inputs": {"query": "{{ topic }}"}},
        ],
    })
    assert len(r.sub_recipes) == 1
    sr = r.sub_recipes[0]
    assert isinstance(sr, SubRecipeRef)
    assert sr.name == "research"
    assert sr.recipe == "research.yaml"
    assert sr.inputs == {"query": "{{ topic }}"}


def test_load_from_yaml_file(tmp_path):
    path = tmp_path / "r.yaml"
    path.write_text(
        "name: yaml_test\n"
        "instructions: hello {{ who }}\n"
        "parameters:\n"
        "  - name: who\n"
        "    default: world\n",
        encoding="utf-8",
    )
    r = load_recipe(path)
    assert r.name == "yaml_test"
    assert r.parameters[0].name == "who"


def test_load_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_recipe("/tmp/does-not-exist-nellie.yaml")


# ============================================================== #
#  Parameter validation
# ============================================================== #


def test_required_parameter_missing():
    r = Recipe(
        name="x",
        instructions="y",
        parameters=[RecipeParameter(name="who", required=True)],
    )
    with pytest.raises(ValueError, match="required"):
        r.resolve_parameters({})


def test_parameter_defaults_apply():
    r = Recipe(
        name="x",
        instructions="y",
        parameters=[RecipeParameter(name="who", default="world")],
    )
    assert r.resolve_parameters({})["who"] == "world"


def test_type_coercion():
    r = Recipe(
        name="x",
        instructions="y",
        parameters=[
            RecipeParameter(name="n", type="integer"),
            RecipeParameter(name="ok", type="boolean"),
        ],
    )
    out = r.resolve_parameters({"n": "42", "ok": "true"})
    assert out["n"] == 42
    assert out["ok"] is True


def test_unknown_parameter_rejected():
    r = Recipe(name="x", instructions="y", parameters=[])
    with pytest.raises(ValueError, match="Unknown parameter"):
        r.resolve_parameters({"extra": "nope"})


# ============================================================== #
#  Template rendering
# ============================================================== #


def test_render_simple_substitution():
    assert _render_template("hello {{ who }}", {"who": "world"}) == "hello world"


def test_render_strict_undefined_raises_or_passes_through():
    """With jinja2 installed, missing variable raises.
    Without jinja2, the fallback passes the literal through."""
    try:
        import jinja2  # noqa: F401

        with pytest.raises(Exception):
            _render_template("hello {{ missing }}", {})
    except ImportError:
        out = _render_template("hello {{ missing }}", {})
        assert "{{ missing }}" in out


def test_render_multiple_variables():
    out = _render_template(
        "ticket {{ id }} priority {{ prio }}",
        {"id": "CDC-1", "prio": "high"},
    )
    assert out == "ticket CDC-1 priority high"
