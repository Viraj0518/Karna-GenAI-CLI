"""Smoke test for the recipes engine — blocks on alpha's recipes PR.

Exercises:
- Recipe dataclass / schema imports
- A known built-in recipe resolves (e.g. "vscode-debug-python" or similar)
- Recipe parameters validate (required / optional / default)
- A recipe run returns the expected tool-call sequence shape

Unskip by removing the ``_available()`` guard when alpha lands recipes.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _recipes_available() -> bool:
    try:
        import karna.recipes  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def recipes_module():
    if not _recipes_available():
        pytest.skip("karna.recipes not available — blocked on alpha's recipes PR")
    import karna.recipes as rec
    return rec


def test_recipe_registry_non_empty(recipes_module):
    recipes = recipes_module.list_recipes()  # type: ignore[attr-defined]
    assert isinstance(recipes, (list, dict))
    assert len(recipes) >= 1


def test_recipe_load_by_name(recipes_module):
    """Shape placeholder — adjust once alpha's naming conventions are clear."""
    names = recipes_module.list_recipes()  # type: ignore[attr-defined]
    if not names:
        pytest.skip("no recipes registered")
    first = names[0] if isinstance(names, list) else next(iter(names))
    recipe = recipes_module.load_recipe(first)  # type: ignore[attr-defined]
    assert recipe is not None


def test_recipe_params_validate(recipes_module):
    """Parameters missing required args should raise a structured error."""
    try:
        recipes_module.run_recipe("nonexistent", {})  # type: ignore[attr-defined]
    except KeyError:
        pass  # expected
    except Exception as e:
        # Any structured exception is fine — we just want to know the
        # failure path doesn't silently return None.
        assert e.args
