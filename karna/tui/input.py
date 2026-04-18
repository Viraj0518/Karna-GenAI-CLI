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


def _make_session(prompt_str: str) -> "PromptSession":
    """Build a ``PromptSession`` with multiline key-bindings and styling.

    * Enter                       -> submit
    * Escape+Enter                -> insert newline (continue editing)
    * Trailing ``\\`` at EOL      -> shell-style continuation
    * Ctrl-C                      -> cancel current input
    * Ctrl-D                      -> exit
    """
    bindings = KeyBindings()
    history = InMemoryHistory()

    @bindings.add(Keys.Escape, Keys.Enter)
    def _insert_newline(event):  # type: ignore[no-untyped-def]
        event.current_buffer.insert_text("\n")

    return PromptSession(
        message=_format_prompt(prompt_str),
        history=history,
        key_bindings=bindings,
        multiline=False,  # Enter submits; Esc-Enter inserts newline
        enable_history_search=True,
        style=_pt_style(),
        placeholder=HTML("<placeholder>Ask anything...</placeholder>"),
        bottom_toolbar=_bottom_toolbar_factory(),
        include_default_pygments_style=False,
    )


# Global session, lazily created / rebuilt when the prompt label changes.
_session: Optional["PromptSession"] = None
_session_label: Optional[str] = None


async def get_multiline_input(console: Console, prompt_str: str = "karna> ") -> str:
    """Read (possibly multiline) user input.

    Returns the final string.

    Raises:
        EOFError:          when the user presses Ctrl-D (caller should exit).
        KeyboardInterrupt: when the user presses Ctrl-C (caller should
                           clear the current input and re-prompt).
    """
    global _session, _session_label

    if _HAS_PROMPT_TOOLKIT:
        if _session is None or _session_label != prompt_str:
            _session = _make_session(prompt_str)
            _session_label = prompt_str

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
