"""Bind logical actions to a ``prompt_toolkit`` ``KeyBindings`` object."""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping

from prompt_toolkit.key_binding import KeyBindings

log = logging.getLogger(__name__)


# prompt_toolkit key-spellings differ slightly from the user-facing
# ``ctrl+x`` form. Translate ours into what pt expects.
def _translate(descriptor: str) -> tuple[str, ...]:
    """Convert ``"ctrl+c"`` / ``"ctrl+shift+j"`` to a pt key tuple.

    prompt_toolkit accepts ``"c-c"``, ``"c-j"``, ``"escape"``, etc.
    A single letter is passed through verbatim.
    """
    parts = [p.strip().lower() for p in descriptor.split("+")]
    *mods, key = parts
    pieces: list[str] = []
    for m in mods:
        if m in ("ctrl", "c"):
            pieces.append("c-")
        elif m in ("shift", "s"):
            pieces.append("s-")
        elif m in ("alt", "meta"):
            # prompt_toolkit uses an escape prefix for Alt
            pieces.append("escape ")
    if key == "enter":
        key = "enter"
    elif key in ("esc", "escape"):
        key = "escape"
    return (("".join(pieces) + key).strip(),)


def apply_bindings(
    kb: KeyBindings,
    bindings: Mapping[str, str],
    handlers: Mapping[str, Callable[[Any], None]],
) -> KeyBindings:
    """Wire *handlers* into *kb* according to the descriptor in *bindings*.

    ``handlers`` maps action name -> a function accepting the pt
    ``KeyPressEvent``. Unknown actions in *bindings* are ignored; unknown
    actions in *handlers* are logged but non-fatal.
    """
    for action, fn in handlers.items():
        descriptor = bindings.get(action)
        if descriptor is None:
            log.warning("no binding for action %r; skipping", action)
            continue
        try:
            keys = _translate(descriptor)
            kb.add(*keys)(fn)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("failed to bind %r -> %r: %s", action, descriptor, exc)
    return kb


__all__ = ["apply_bindings"]
