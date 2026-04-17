"""Multiline input handling for the Karna REPL.

Uses ``prompt_toolkit`` when available (full readline / history / multiline),
falling back to a bare ``input()`` loop otherwise.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from rich.console import Console

# --------------------------------------------------------------------------- #
#  prompt_toolkit setup (optional but strongly preferred)
# --------------------------------------------------------------------------- #

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    _HAS_PROMPT_TOOLKIT = True
except ImportError:  # pragma: no cover
    _HAS_PROMPT_TOOLKIT = False


def _make_session(prompt_str: str) -> "PromptSession":
    """Build a ``PromptSession`` with multiline key-bindings.

    * Enter  → submit (single-line)
    * Escape+Enter or ``\\`` at EOL → insert newline (continue editing)
    * Ctrl-C → cancel current input (raises ``KeyboardInterrupt``)
    * Ctrl-D → exit (raises ``EOFError``)
    """
    bindings = KeyBindings()
    history = InMemoryHistory()

    @bindings.add(Keys.Escape, Keys.Enter)
    def _insert_newline(event):  # type: ignore[no-untyped-def]
        event.current_buffer.insert_text("\n")

    return PromptSession(
        message=HTML(f"<style fg='#87CEEB' bold='true'>{prompt_str}</style>"),
        history=history,
        key_bindings=bindings,
        multiline=False,  # Enter submits; Esc-Enter inserts newline
        enable_history_search=True,
    )


# Global session, lazily created
_session: Optional["PromptSession"] = None


async def get_multiline_input(console: Console, prompt_str: str = "karna> ") -> str:
    """Read (possibly multiline) user input.

    Returns the final string.

    Raises:
        EOFError:  when the user presses Ctrl-D (caller should exit).
        KeyboardInterrupt: when the user presses Ctrl-C (caller should
            clear the current input and re-prompt).
    """
    global _session

    if _HAS_PROMPT_TOOLKIT:
        if _session is None or _session.message != HTML(f"<style fg='#87CEEB' bold='true'>{prompt_str}</style>"):
            _session = _make_session(prompt_str)

        # prompt_toolkit is sync — run in executor so we don't block the loop.
        loop = asyncio.get_running_loop()
        text: str = await loop.run_in_executor(None, _session.prompt)

        # Support trailing backslash continuation (same UX as shells)
        while text.endswith("\\"):
            text = text[:-1] + "\n"
            continuation: str = await loop.run_in_executor(
                None,
                lambda: _session.prompt(HTML("<style fg='#87CEEB'>  ... </style>")),  # type: ignore[union-attr]
            )
            text += continuation

        return text.strip()

    # ── Fallback: plain input() ─────────────────────────────────────────
    console.print(f"[bold #87CEEB]{prompt_str}[/]", end="")
    lines: list[str] = []
    while True:
        line = await asyncio.get_running_loop().run_in_executor(None, input)
        if line.endswith("\\"):
            lines.append(line[:-1])
            console.print("[#87CEEB]  ... [/]", end="")
            continue
        lines.append(line)
        break
    return "\n".join(lines).strip()
