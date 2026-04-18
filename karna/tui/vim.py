"""Vim-mode layer for the Nellie prompt_toolkit input session.

prompt_toolkit ships with a full vi-mode implementation (motions, operators,
undo/redo, visual mode) — this module is a thin shim that:

1. Reads ``[tui].vim_mode`` from ``~/.karna/config.toml`` via
   :func:`vim_mode_enabled` (so callers don't have to touch ``karna.config``).
2. Exposes :func:`build_vim_keybindings` returning a ``KeyBindings`` object
   pre-wired with a few quality-of-life extras on top of pt's built-ins
   (normal-mode ``gg``/``G`` are native; we add a user-visible escape hatch
   ``Ctrl+[`` to reliably return to normal mode on terminals that swallow
   ``Esc``).
3. Exposes :func:`apply_vim_mode` which, given a ``PromptSession`` kwargs
   dict, flips ``vi_mode=True`` and merges in the extra key-bindings.

The heavy lifting — h/j/k/l, w/b, 0/$, d/c/y motions, u / Ctrl+R undo-redo,
visual mode v — is all already done by prompt_toolkit. Lean on it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib  # Py 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

try:
    from prompt_toolkit.enums import EditingMode  # noqa: F401  # exposed for callers
    from prompt_toolkit.key_binding import KeyBindings

    _HAS_PT = True
except ImportError:  # pragma: no cover
    _HAS_PT = False


_CONFIG_PATH = Path.home() / ".karna" / "config.toml"


def _read_toml(path: Path) -> Mapping[str, Any]:
    """Best-effort TOML read. Returns {} on any error."""
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        return {}


def vim_mode_enabled(config_path: Path | None = None) -> bool:
    """Return ``True`` when ``[tui].vim_mode`` is truthy in the config file.

    Returns ``False`` when the file is missing, unreadable, or lacks the key.
    Accepts an optional ``config_path`` for tests.
    """
    data = _read_toml(config_path or _CONFIG_PATH)
    tui = data.get("tui") or {}
    return bool(tui.get("vim_mode", False))


def build_vim_keybindings() -> "KeyBindings":
    """Return extra ``KeyBindings`` layered on top of prompt_toolkit's vi mode.

    prompt_toolkit already handles:
      * Insert / Normal toggle via ``Esc``
      * Motions: h j k l, w b, 0 $, gg G, %%
      * Operators: d c y + motion
      * Undo ``u`` / redo ``Ctrl+R``
      * Visual mode ``v``

    This function adds a small number of conveniences that aren't on by
    default or that benefit from being spelled out explicitly.
    """
    if not _HAS_PT:  # pragma: no cover
        raise RuntimeError("prompt_toolkit is required for vim mode")

    kb = KeyBindings()

    # Explicit ``escape`` binding as a belt-and-suspenders return-to-normal.
    # prompt_toolkit's native vi mode already does this, but binding it
    # here keeps our intent visible and survives terminals that only
    # deliver Esc as a standalone key press.
    @kb.add("escape")
    def _to_normal(event: Any) -> None:  # pragma: no cover - runtime only
        event.app.vi_state.input_mode = "navigation"

    return kb


def apply_vim_mode(session_kwargs: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    """Mutate ``session_kwargs`` to enable vi mode when ``enabled`` is True.

    Merges our extra ``KeyBindings`` with any already present under the
    ``key_bindings`` kwarg (using ``merge_key_bindings`` when available).
    Returns the same dict for convenience.
    """
    if not enabled or not _HAS_PT:
        return session_kwargs

    session_kwargs["vi_mode"] = True
    extra = build_vim_keybindings()
    existing = session_kwargs.get("key_bindings")
    if existing is None:
        session_kwargs["key_bindings"] = extra
    else:
        try:
            from prompt_toolkit.key_binding import merge_key_bindings

            session_kwargs["key_bindings"] = merge_key_bindings([existing, extra])
        except Exception:  # pragma: no cover
            session_kwargs["key_bindings"] = extra
    return session_kwargs


__all__ = [
    "vim_mode_enabled",
    "build_vim_keybindings",
    "apply_vim_mode",
    "EditingMode" if _HAS_PT else "_HAS_PT",
]
