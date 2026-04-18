"""Multiline input handling for the Karna REPL.

Uses ``prompt_toolkit`` when available (full readline / history / multiline
with cursor styling, placeholder text, and a first-run hint), falling back
to a bare ``input()`` loop otherwise.

Public API (unchanged for the REPL):

    async def get_multiline_input(console, prompt_str="karna> ") -> str
"""

from __future__ import annotations

import asyncio
from typing import Optional

from rich.console import Console

from karna.tui.design_tokens import SEMANTIC

# --------------------------------------------------------------------------- #
#  prompt_toolkit setup (optional but strongly preferred)
# --------------------------------------------------------------------------- #

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.styles import Style as PTStyle

    _HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False

# --------------------------------------------------------------------------- #
#  Icons (optional; authored by a sibling agent — degrade gracefully)
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - trivial import guard
    from karna.tui import icons as _icons  # type: ignore

    _CHEVRON = (
        getattr(_icons, "CHEVRON_RIGHT", None) or getattr(_icons, "chevron_right", None) or "\u276f"  # ❯
    )
except Exception:  # pragma: no cover - fallback
    _CHEVRON = "\u276f"


# --------------------------------------------------------------------------- #
#  Style helpers
# --------------------------------------------------------------------------- #


def _pt_style() -> "PTStyle":
    """Build the prompt_toolkit ``Style`` from semantic design tokens."""
    brand = SEMANTIC.get("accent.brand", "#3C73BD")
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    tertiary = SEMANTIC.get("text.tertiary", "#5F6472")
    primary = SEMANTIC.get("text.primary", "#E6E8EC")

    return PTStyle.from_dict(
        {
            # Prompt parts
            "prompt.chevron": f"{brand} bold",
            "prompt.text": f"{cyan} bold",
            "prompt.cont": f"{tertiary}",
            # Input body + cursor
            "": f"{primary}",
            "cursor": f"{brand}",
            # Placeholder + bottom toolbar
            "placeholder": f"{tertiary} italic",
            "bottom-toolbar": f"{tertiary}",
            "bottom-toolbar.key": f"{cyan}",
        }
    )


def _format_prompt(prompt_str: str) -> "HTML":
    """Render the main prompt prefix: chevron + brand-coloured label."""
    # Strip a trailing '>' or '> ' from the caller-supplied label so we can
    # use our own chevron glyph consistently without doubling up.
    label = prompt_str.rstrip()
    if label.endswith(">"):
        label = label[:-1].rstrip()
    return HTML(f"<prompt.chevron>{_CHEVRON}</prompt.chevron> <prompt.text>{label}</prompt.text> ")


def _format_continuation() -> "HTML":
    """Continuation prompt shown after a trailing ``\\`` line."""
    return HTML("<prompt.cont>  ... </prompt.cont>")


# --------------------------------------------------------------------------- #
#  Session construction
# --------------------------------------------------------------------------- #

# First-run hint is shown once per process, not on every prompt.
_HINT_SHOWN = False


def _bottom_toolbar_factory():
    """Return a bottom-toolbar callable that fires only on the first prompt."""

    def _toolbar():
        global _HINT_SHOWN
        if _HINT_SHOWN:
            return None
        _HINT_SHOWN = True
        return HTML(
            "<bottom-toolbar>"
            "<bottom-toolbar.key>enter</bottom-toolbar.key> to send  -  "
            "<bottom-toolbar.key>esc+enter</bottom-toolbar.key> for newline  -  "
            "<bottom-toolbar.key>ctrl+d</bottom-toolbar.key> to exit"
            "</bottom-toolbar>"
        )

    return _toolbar


def _make_session(prompt_str: str, *, vim_mode: bool = False) -> "PromptSession":
    """Build a ``PromptSession`` with multiline key-bindings and styling.

    * Enter                       -> submit
    * Escape+Enter                -> insert newline (continue editing)
    * Trailing ``\\`` at EOL      -> shell-style continuation
    * Ctrl-C                      -> cancel current input
    * Ctrl-D                      -> exit

    When ``vim_mode`` is ``True``, prompt_toolkit's native vi editing mode
    is enabled and extra vim-specific key-bindings are layered on top.
    """
    bindings = KeyBindings()
    history = InMemoryHistory()

    @bindings.add(Keys.Escape, Keys.Enter)
    def _insert_newline(event):  # type: ignore[no-untyped-def]
        event.current_buffer.insert_text("\n")

    session_kwargs: dict = {
        "message": _format_prompt(prompt_str),
        "history": history,
        "key_bindings": bindings,
        "multiline": False,  # Enter submits; Esc-Enter inserts newline
        "enable_history_search": True,
        "style": _pt_style(),
        "placeholder": HTML("<placeholder>Ask anything...</placeholder>"),
        "bottom_toolbar": _bottom_toolbar_factory(),
        "include_default_pygments_style": False,
    }

    if vim_mode:
        try:  # pragma: no cover - exercised via tests/test_vim_mode
            from karna.tui.vim import apply_vim_mode

            apply_vim_mode(session_kwargs, enabled=True)
        except Exception:
            pass

    return PromptSession(**session_kwargs)


# Global session, lazily created / rebuilt when the prompt label changes.
_session: Optional["PromptSession"] = None
# Cache key: legacy code stored a bare label string; when vim-mode plumbing
# is in use it becomes a ``(label, vim_mode)`` tuple. Both forms compare
# correctly against the stored value.
_session_label: Optional[object] = None


async def get_multiline_input(
    console: Console,
    prompt_str: str = "karna> ",
    *,
    vim_mode: bool = False,
) -> str:
    """Read (possibly multiline) user input.

    Returns the final string.

    Args:
        console: Rich console for fallback rendering.
        prompt_str: Prompt label (e.g. ``"karna> "``).
        vim_mode: When True, enable prompt_toolkit's vi editing mode for
            this session (h/j/k/l, d/c/y + motion, u/Ctrl+R undo, etc.).
            Default off for backward compatibility.

    Raises:
        EOFError:          when the user presses Ctrl-D (caller should exit).
        KeyboardInterrupt: when the user presses Ctrl-C (caller should
                           clear the current input and re-prompt).
    """
    global _session, _session_label

    if _HAS_PROMPT_TOOLKIT:
        # Rebuild session if label changed or if we need to switch vim mode.
        label_key = (prompt_str, bool(vim_mode))
        if _session is None or _session_label != label_key:
            _session = _make_session(prompt_str, vim_mode=vim_mode)
            _session_label = label_key  # type: ignore[assignment]

        # prompt_toolkit is sync — run in executor so we don't block the loop.
        loop = asyncio.get_running_loop()
        text: str = await loop.run_in_executor(None, _session.prompt)

        # Support trailing backslash continuation (same UX as shells)
        while text.endswith("\\"):
            text = text[:-1] + "\n"
            continuation: str = await loop.run_in_executor(
                None,
                lambda: _session.prompt(_format_continuation()),  # type: ignore[union-attr]
            )
            text += continuation

        return text.strip()

    # ── Fallback: plain input() ─────────────────────────────────────────
    brand = SEMANTIC.get("accent.brand", "#3C73BD")
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    label = prompt_str.rstrip().rstrip(">").rstrip()
    console.print(
        f"[bold {brand}]{_CHEVRON}[/] [bold {cyan}]{label}[/] ",
        end="",
    )
    lines: list[str] = []
    while True:
        line = await asyncio.get_running_loop().run_in_executor(None, input)
        if line.endswith("\\"):
            lines.append(line[:-1])
            console.print(f"[{SEMANTIC.get('text.tertiary', '#5F6472')}]  ... [/]", end="")
            continue
        lines.append(line)
        break
    return "\n".join(lines).strip()


__all__ = ["get_multiline_input"]
