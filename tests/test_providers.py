"""Tests for the provider registry."""

import pytest

from karna.providers import get_provider
from karna.providers.base import BaseProvider


@pytest.mark.parametrize(
    "name,expected_class_name",
    [
        ("openrouter", "OpenRouterProvider"),
        ("openai", "OpenAIProvider"),
        ("azure", "AzureOpenAIProvider"),
        ("anthropic", "AnthropicProvider"),
        ("local", "LocalProvider"),
    ],
)
def test_registry_returns_correct_class(name: str, expected_class_name: str) -> None:
    cls = get_provider(name)
    assert cls.__name__ == expected_class_name
    assert issubclass(cls, BaseProvider)


def test_registry_case_insensitive() -> None:
    assert get_provider("OpenRouter") == get_provider("openrouter")


def test_registry_unknown_raises() -> None:
    with pytest.raises(KeyError, match="Unknown provider"):
        get_provider("nonexistent")
