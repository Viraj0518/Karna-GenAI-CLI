"""Provider registry for Karna.

Maps provider names to their implementation classes and exposes a
``get_provider()`` lookup helper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from karna.providers.base import BaseProvider

# Lazy imports keep startup fast and avoid circular-import issues.
_PROVIDER_PATHS: dict[str, tuple[str, str]] = {
    "openrouter": ("karna.providers.openrouter", "OpenRouterProvider"),
    "openai": ("karna.providers.openai", "OpenAIProvider"),
    "azure": ("karna.providers.azure", "AzureOpenAIProvider"),
    "anthropic": ("karna.providers.anthropic", "AnthropicProvider"),
    "local": ("karna.providers.local", "LocalProvider"),
}


def get_provider(name: str) -> type["BaseProvider"]:
    """Return the provider **class** for *name* (case-insensitive).

    Raises ``KeyError`` if the provider is not registered.
    """
    key = name.lower()
    if key not in _PROVIDER_PATHS:
        raise KeyError(f"Unknown provider: {name!r}. Available: {', '.join(_PROVIDER_PATHS)}")
    module_path, class_name = _PROVIDER_PATHS[key]
    import importlib

    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


# Convenience re-export so callers can do ``from karna.providers import PROVIDERS``
PROVIDERS: dict[str, tuple[str, str]] = dict(_PROVIDER_PATHS)
