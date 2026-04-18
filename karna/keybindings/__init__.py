"""User-customizable keybindings for the Nellie prompt.

Public API::

    from karna.keybindings import load_bindings, apply_bindings, DEFAULT_BINDINGS

``load_bindings()`` reads ``~/.karna/keybindings.toml`` (falling back to the
defaults), and ``apply_bindings(kb, bindings, handlers)`` wires a
``prompt_toolkit.key_binding.KeyBindings`` object with the user's spellings.
"""

from karna.keybindings.defaults import DEFAULT_BINDINGS
from karna.keybindings.manager import (
    KEYBINDINGS_PATH,
    BindingsResult,
    load_bindings,
    parse_bindings,
    save_bindings,
)

try:
    from karna.keybindings.apply import apply_bindings  # noqa: F401
except ImportError:  # pragma: no cover - prompt_toolkit optional
    apply_bindings = None  # type: ignore[assignment]

__all__ = [
    "DEFAULT_BINDINGS",
    "KEYBINDINGS_PATH",
    "BindingsResult",
    "load_bindings",
    "parse_bindings",
    "save_bindings",
    "apply_bindings",
]
