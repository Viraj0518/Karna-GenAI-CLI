"""Load / save / validate ``~/.karna/keybindings.toml``.

The file format is intentionally tiny::

    [bindings]
    cancel      = "ctrl+c"
    submit      = "enter"
    newline     = "ctrl+j"
    history_up  = "up"
    history_down= "down"
    toggle_vim  = "ctrl+v"

Anything unknown in ``[bindings]`` is accepted (custom actions can be
added later); anything missing falls back to the default. Invalid
descriptor strings (e.g. ``"ctrl+ctrl"``) are dropped with a warning and
the default is used instead.

The file format is symmetric: :func:`save_bindings` writes the same
shape. We intentionally use a hand-rolled writer so that we don't depend
on ``tomli_w`` (not guaranteed to be installed).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib  # Py 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

from karna.keybindings.defaults import DEFAULT_BINDINGS

log = logging.getLogger(__name__)

KEYBINDINGS_PATH = Path.home() / ".karna" / "keybindings.toml"

# Accept letters, digits, common named keys, and ``+`` combiners.
# Rejects empty parts ("ctrl++a"), unknown modifiers, and nonsense.
_VALID_MODS = {"ctrl", "shift", "alt", "meta", "c", "s"}
_VALID_NAMED = {
    "enter",
    "tab",
    "space",
    "backspace",
    "delete",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "pageup",
    "pagedown",
    "escape",
    "esc",
}
_KEY_RE = re.compile(r"^[a-z0-9]$|^f[1-9][0-9]?$")


def _valid_descriptor(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    parts = [p.strip().lower() for p in s.split("+")]
    if any(not p for p in parts):
        return False
    *mods, key = parts
    for m in mods:
        if m not in _VALID_MODS:
            return False
    if key in _VALID_NAMED:
        return True
    return bool(_KEY_RE.match(key))


# --------------------------------------------------------------------------- #
#  Data shape
# --------------------------------------------------------------------------- #


@dataclass
class BindingsResult:
    """Outcome of loading a keybindings file.

    ``bindings`` is always populated (defaults fill any gaps).
    ``warnings`` lists human-readable messages about invalid / duplicate
    entries that were rejected. ``source`` is the path read (or ``None``
    if the defaults were used because the file was absent).
    """

    bindings: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    source: Path | None = None


# --------------------------------------------------------------------------- #
#  Load / parse
# --------------------------------------------------------------------------- #


def parse_bindings(data: Mapping[str, Any]) -> BindingsResult:
    """Parse a dict (usually the output of :mod:`tomllib`) into bindings.

    Validates each descriptor, warns on duplicates (last wins), falls
    back to defaults on anything invalid.
    """
    result = BindingsResult(bindings=dict(DEFAULT_BINDINGS))
    section = data.get("bindings") or {}
    if not isinstance(section, dict):
        result.warnings.append("[bindings] is not a table; using defaults")
        return result

    seen_keys: dict[str, str] = {}  # descriptor -> action
    for action, descriptor in section.items():
        if not isinstance(descriptor, str):
            msg = f"binding {action!r}: value must be a string, got {type(descriptor).__name__}"
            result.warnings.append(msg)
            log.warning(msg)
            continue
        if not _valid_descriptor(descriptor):
            msg = f"binding {action!r}: invalid descriptor {descriptor!r}; falling back to default"
            result.warnings.append(msg)
            log.warning(msg)
            continue
        if descriptor in seen_keys and seen_keys[descriptor] != action:
            msg = (
                f"binding {descriptor!r} used for both {seen_keys[descriptor]!r} and {action!r}; last ({action!r}) wins"
            )
            result.warnings.append(msg)
            log.warning(msg)
        seen_keys[descriptor] = action
        result.bindings[action] = descriptor
    return result


def load_bindings(path: Path | None = None) -> BindingsResult:
    """Read *path* (default: ``~/.karna/keybindings.toml``) and parse it.

    Missing file is not an error — the defaults are returned.
    """
    target = path or KEYBINDINGS_PATH
    if not target.exists():
        return BindingsResult(bindings=dict(DEFAULT_BINDINGS), source=None)
    try:
        with target.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        msg = f"failed to read {target}: {exc}; using defaults"
        log.warning(msg)
        return BindingsResult(bindings=dict(DEFAULT_BINDINGS), warnings=[msg], source=target)
    result = parse_bindings(data)
    result.source = target
    return result


# --------------------------------------------------------------------------- #
#  Save
# --------------------------------------------------------------------------- #


def save_bindings(bindings: Mapping[str, str], path: Path | None = None) -> Path:
    """Write *bindings* as a well-formed ``keybindings.toml``.

    Creates parent directories as needed; returns the resolved path.
    """
    target = path or KEYBINDINGS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["[bindings]"]
    for action, descriptor in bindings.items():
        escaped = descriptor.replace('"', '\\"')
        lines.append(f'{action} = "{escaped}"')
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


__all__ = [
    "KEYBINDINGS_PATH",
    "BindingsResult",
    "parse_bindings",
    "load_bindings",
    "save_bindings",
]
