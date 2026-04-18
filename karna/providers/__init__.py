"""Provider registry for Karna.

Maps provider names to their implementation classes and exposes a
``get_provider()`` lookup helper and ``resolve_model()`` parser.
"""

from __future__ import annotations

import importlib
from typing import Any

from karna.providers.base import BaseProvider

# Lazy imports keep startup fast and avoid circular-import issues.
_PROVIDER_PATHS: dict[str, tuple[str, str]] = {
    "openrouter": ("karna.providers.openrouter", "OpenRouterProvider"),
    "openai": ("karna.providers.openai", "OpenAIProvider"),
    "azure": ("karna.providers.azure", "AzureOpenAIProvider"),
    "anthropic": ("karna.providers.anthropic", "AnthropicProvider"),
    "local": ("karna.providers.local", "LocalProvider"),
    "vertex": ("karna.providers.vertex", "VertexProvider"),
    "bedrock": ("karna.providers.bedrock", "BedrockProvider"),
    "failover": ("karna.providers.failover", "FailoverProvider"),
}

# Convenience re-export so callers can do ``from karna.providers import PROVIDERS``
PROVIDERS: dict[str, tuple[str, str]] = dict(_PROVIDER_PATHS)

# Default provider when none specified
DEFAULT_PROVIDER = "openrouter"


def get_provider(name: str, **kwargs: Any) -> BaseProvider:
    """Return an *instantiated* provider for *name* (case-insensitive).

    Any extra ``**kwargs`` are forwarded to the provider constructor.
    Raises ``KeyError`` if the provider is not registered.
    """
    key = name.lower()
    if key not in _PROVIDER_PATHS:
        raise KeyError(f"Unknown provider: {name!r}. Available: {', '.join(_PROVIDER_PATHS)}")
    module_path, class_name = _PROVIDER_PATHS[key]
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    return cls(**kwargs)


def get_provider_class(name: str) -> type[BaseProvider]:
    """Return the provider **class** for *name* (case-insensitive).

    Raises ``KeyError`` if the provider is not registered.
    """
    key = name.lower()
    if key not in _PROVIDER_PATHS:
        raise KeyError(f"Unknown provider: {name!r}. Available: {', '.join(_PROVIDER_PATHS)}")
    module_path, class_name = _PROVIDER_PATHS[key]
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def resolve_model(model_string: str) -> tuple[str, str]:
    """Parse a ``provider:model`` string into ``(provider_name, model_id)``.

    Examples::

        >>> resolve_model("openrouter:gpt-oss-120b")
        ('openrouter', 'gpt-oss-120b')
        >>> resolve_model("anthropic:claude-sonnet-4-20250514")
        ('anthropic', 'claude-sonnet-4-20250514')
        >>> resolve_model("gpt-4o")
        ('openrouter', 'gpt-4o')

    When no provider prefix is given, defaults to ``openrouter``.
    """
    if ":" in model_string:
        provider, _, model = model_string.partition(":")
        provider = provider.strip().lower()
        model = model.strip()
        if provider in _PROVIDER_PATHS:
            return provider, model
    # No recognized prefix -- default to openrouter
    return DEFAULT_PROVIDER, model_string.strip()
