"""Small modal dialogs ported from Claude Code's TUI.

Mirrors the one-shot confirmation widgets CC exposes:

* ``PressEnterToContinue.tsx``  -> :func:`press_enter_to_continue`
* ``ExitFlow.tsx`` + ``WorktreeExitDialog`` -> :func:`exit_flow`
* ``IdleReturnDialog.tsx``      -> :func:`idle_return`
* ``ThinkingToggle.tsx``        -> :func:`render_thinking_toggle`
* ``CtrlOToExpand.tsx``         -> :func:`render_ctrl_o_to_expand`
* ``wizard/*``                  -> :func:`run_wizard`
* ``KeybindingWarnings.tsx``    -> :func:`render_keybinding_warnings`

Design notes
------------
* Each async function drives a single-shot prompt on stdin and resolves
  with the user's choice. IO uses ``asyncio.get_running_loop().run_in_executor``
  to avoid blocking the event loop — matching the pattern used by
  :mod:`karna.tui.input` for multiline input.
* Render helpers (``render_*``) are pure: they return a Rich renderable and
  do not print anything. Consumers print them into their own layout.
* Wizard is a generic "list of step dicts" primitive — each step has
  ``{"key": str, "prompt": str, "type": str, ...}`` and the return value is
  a flat dict keyed by ``step["key"]``. Matches the control-flow semantics of
  ``WizardProvider.tsx`` (linear progression, back-button via history stack,
  cancel on Ctrl-C) without trying to replicate the React context surface.
"""

from __future__ import annotations

import asyncio
import sys
from enum import Enum
from typing import Any, Optional, Sequence

from rich.console import Console, RenderableType
from rich.panel import Panel
from rich.text import Text

from karna.tui.design_tokens import COLORS


# --------------------------------------------------------------------------- #
#  Enums
# --------------------------------------------------------------------------- #


class IdleReturnAction(str, Enum):
    """CC's IdleReturnDialog possible outcomes."""

    CONTINUE = "continue"
    CLEAR = "clear"
    DISMISS = "dismiss"
    NEVER = "never"


# --------------------------------------------------------------------------- #
#  Internal — read a single line off stdin without blocking the loop
# --------------------------------------------------------------------------- #


async def _prompt(message: str) -> str:
    """Print ``message`` then await a line of input via the default executor."""
    loop = asyncio.get_running_loop()

    def _blocking_read() -> str:
        try:
            return input(message)
        except EOFError:
            # Treat EOF as empty line — callers interpret as "default".
            return ""

    return await loop.run_in_executor(None, _blocking_read)


def _brand_text(s: str) -> str:
    """Wrap ``s`` with the brand accent for use in plain-ANSI prompts."""
    return f"\x1b[38;2;60;115;189m{s}\x1b[0m"


# --------------------------------------------------------------------------- #
#  confirm — Y/n prompt
# --------------------------------------------------------------------------- #


async def confirm(message: str, *, default: bool = False) -> bool:
    """Prompt ``message`` with ``[Y/n]`` or ``[y/N]`` suffix.

    * Returns ``default`` on empty input.
    * Accepts y/yes/Y/YES as True and n/no/N/NO as False (matches CC's
      ``Confirm.tsx`` parser).
    """
    suffix = "[Y/n]" if default else "[y/N]"
    prompt = f"{message} {suffix} "
    while True:
        raw = (await _prompt(prompt)).strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        # Invalid input — re-prompt. CC shows an inline error; we just loop.


# --------------------------------------------------------------------------- #
#  press_enter_to_continue
# --------------------------------------------------------------------------- #


async def press_enter_to_continue(
    message: str = "Press Enter to continue\u2026",
) -> None:
    """Block until the user presses Enter (CC's ``PressEnterToContinue``)."""
    await _prompt(message + " ")


def render_press_enter_to_continue(
    message: str = "Press",
) -> Text:
    """Pure-render version (no IO) — useful for embedding in panels."""
    t = Text(message + " ", style=COLORS.text.secondary)
    t.append("Enter", style=f"{COLORS.accent.cyan} bold")
    t.append(" to continue\u2026", style=COLORS.text.secondary)
    return t


# --------------------------------------------------------------------------- #
#  exit_flow — "Really exit?" with an optional unsaved-changes warning
# --------------------------------------------------------------------------- #


_GOODBYE_MESSAGES: tuple[str, ...] = (
    "Goodbye!",
    "See ya!",
    "Bye!",
    "Catch you later!",
)


async def exit_flow(*, unsaved: bool = False) -> bool:
    """Ask the user whether to exit; return True to proceed with shutdown.

    * ``unsaved=True`` prepends a red warning about unsaved changes.
    * Mirrors ``ExitFlow.tsx`` / ``WorktreeExitDialog``: default No when
      unsaved, default Yes otherwise.
    """
    if unsaved:
        warn = Text(
            "You have unsaved changes. Exit anyway?",
            style=f"{COLORS.accent.danger} bold",
        )
        Console(file=sys.stderr).print(warn)
        return await confirm("Really exit?", default=False)
    return await confirm("Really exit?", default=True)


def random_goodbye() -> str:
    """CC's ``getRandomGoodbyeMessage`` — pick one of the goodbye lines."""
    import random

    return random.choice(_GOODBYE_MESSAGES)


# --------------------------------------------------------------------------- #
#  idle_return — "You've been idle" continue-or-clear
# --------------------------------------------------------------------------- #


def _format_idle_duration(minutes: int) -> str:
    """CC's ``formatIdleDuration`` — "< 1m", "42m", "3h", "3h 12m"."""
    if minutes < 1:
        return "< 1m"
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = minutes // 60
    rem = minutes % 60
    return f"{hours}h" if rem == 0 else f"{hours}h {rem}m"


async def idle_return(*, idle_minutes: int) -> bool:
    """Prompt after idle period. ``True`` to continue, ``False`` to clear/reset.

    Simplified from CC's 4-way dialog (continue / clear / dismiss / never)
    down to a boolean — if you need the full enum, call the underlying
    ``_idle_return_4way`` helper.
    """
    formatted = _format_idle_duration(idle_minutes)
    msg = f"You've been away {formatted}. Continue this conversation?"
    return await confirm(msg, default=True)


async def _idle_return_4way(
    *, idle_minutes: int, total_input_tokens: int = 0
) -> IdleReturnAction:
    """Full 4-way variant matching ``IdleReturnDialog.tsx``."""
    formatted = _format_idle_duration(idle_minutes)
    tokens = f"{total_input_tokens:,}" if total_input_tokens else "0"
    Console().print(
        Panel(
            Text(
                f"You've been away {formatted} and this conversation is "
                f"{tokens} tokens.\n\n"
                "If this is a new task, clearing context will save usage "
                "and be faster.",
                style=COLORS.text.primary,
            ),
            title="Idle",
            border_style=COLORS.border.accent,
        )
    )
    print(
        "  [1] Continue this conversation\n"
        "  [2] Send message as a new conversation\n"
        "  [3] Dismiss\n"
        "  [4] Don't ask me again"
    )
    raw = (await _prompt("Choice [1]: ")).strip() or "1"
    return {
        "1": IdleReturnAction.CONTINUE,
        "2": IdleReturnAction.CLEAR,
        "3": IdleReturnAction.DISMISS,
        "4": IdleReturnAction.NEVER,
    }.get(raw, IdleReturnAction.DISMISS)


# --------------------------------------------------------------------------- #
#  render_thinking_toggle — on/off pill for extended thinking
# --------------------------------------------------------------------------- #


def render_thinking_toggle(enabled: bool) -> Text:
    """Small pill: ``\u2726 thinking on`` (success) or ``\u2726 thinking off`` (dim).

    Matches the "Extended thinking" indicator CC renders in
    ``ThinkingToggle.tsx`` — CC uses the brand/warning palette depending on
    state; we use success/dim.
    """
    glyph = "\u2726"  # ✦
    if enabled:
        t = Text()
        t.append(f"{glyph} ", style=COLORS.accent.thinking)
        t.append("thinking on", style=f"{COLORS.accent.success} bold")
        return t
    t = Text()
    t.append(f"{glyph} ", style=COLORS.text.tertiary)
    t.append("thinking off", style=COLORS.text.tertiary)
    return t


# --------------------------------------------------------------------------- #
#  render_ctrl_o_to_expand
# --------------------------------------------------------------------------- #


def render_ctrl_o_to_expand(shortcut: str = "Ctrl-O") -> Text:
    """Dim ``(Ctrl-O to expand)`` chip matching CC's ``CtrlOToExpand``."""
    t = Text()
    t.append("(", style=COLORS.text.tertiary)
    t.append(shortcut, style=COLORS.accent.cyan)
    t.append(" to expand)", style=COLORS.text.tertiary)
    return t


# --------------------------------------------------------------------------- #
#  run_wizard — multi-step question → answer collector
# --------------------------------------------------------------------------- #


async def run_wizard(steps: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Run a linear multi-step wizard and return the collected values.

    Each ``step`` is a dict with:

    * ``key``   — required; the output dict key for this step's value.
    * ``prompt`` — required; displayed to the user.
    * ``type``  — optional; one of ``"text"`` (default), ``"bool"``, ``"choice"``.
    * ``choices`` — required when ``type == "choice"``; list of option strings.
    * ``default`` — optional; returned when input is empty.
    * ``title`` — optional; a short heading printed once before ``prompt``.

    Navigation: the user can type ``:back`` to revisit the previous step
    (mirrors ``WizardProvider``'s ``goBack`` via its navigation history stack).
    ``:cancel`` raises ``asyncio.CancelledError`` — caller decides whether
    to clean up.

    The return value is ``{step["key"]: value, ...}`` in the order steps
    were answered. Matches the "collect data, complete, emit" shape of
    CC's ``WizardContextValue.wizardData``.
    """
    data: dict[str, Any] = {}
    history: list[int] = []
    i = 0
    total = len(steps)

    while i < total:
        step = steps[i]
        key = step["key"]
        stype = step.get("type", "text")
        default = step.get("default")

        if "title" in step:
            print(_brand_text(f"\n[{i + 1}/{total}] {step['title']}"))

        if stype == "bool":
            raw_default = bool(default) if default is not None else False
            val: Any = await confirm(step["prompt"], default=raw_default)
        elif stype == "choice":
            choices = list(step.get("choices", []))
            for idx, opt in enumerate(choices, start=1):
                print(f"  [{idx}] {opt}")
            default_idx = (
                choices.index(default) + 1 if default in choices else 1
            )
            raw = (
                await _prompt(f"{step['prompt']} [{default_idx}]: ")
            ).strip()
            if raw.lower() == ":back":
                val = None
            elif raw.lower() == ":cancel":
                raise asyncio.CancelledError("wizard cancelled")
            else:
                try:
                    idx = int(raw) if raw else default_idx
                    val = choices[idx - 1]
                except (ValueError, IndexError):
                    val = choices[default_idx - 1]
        else:  # text
            suffix = f" [{default}]" if default is not None else ""
            raw = (await _prompt(f"{step['prompt']}{suffix}: ")).strip()
            if raw.lower() == ":back":
                val = None
            elif raw.lower() == ":cancel":
                raise asyncio.CancelledError("wizard cancelled")
            else:
                val = raw if raw else default

        if val is None and i > 0:
            # :back — pop history and revisit the previous step
            i = history.pop() if history else max(0, i - 1)
            continue

        data[key] = val
        history.append(i)
        i += 1

    return data


# --------------------------------------------------------------------------- #
#  render_keybinding_warnings
# --------------------------------------------------------------------------- #


def render_keybinding_warnings(conflicts: list[str]) -> RenderableType:
    """Build a ``Panel`` listing keybinding conflicts; None-like on empty.

    Mirrors ``KeybindingWarnings.tsx``: bold title in the error palette,
    one line per conflict with a ``\u2514 `` tree joiner and a ``\u2192``
    prefix on the suggestion. For simplicity we accept already-formatted
    strings; callers building structured warnings should format them first.
    """
    if not conflicts:
        # CC returns null; we return an empty Text so callers can print()
        # without a None-check.
        return Text("")

    body = Text()
    body.append("Keybinding Configuration Issues\n", style=f"{COLORS.accent.danger} bold")
    for i, conflict in enumerate(conflicts):
        prefix = "\u2514 " if i == len(conflicts) - 1 else "\u251c "
        body.append(prefix, style=COLORS.text.tertiary)
        body.append("[Warning] ", style=COLORS.accent.warning)
        body.append(f"{conflict}\n", style=COLORS.text.secondary)

    return Panel(
        body,
        border_style=COLORS.accent.warning,
        padding=(0, 1),
        title="⚠  Keybindings",
    )


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #


__all__ = [
    "IdleReturnAction",
    "confirm",
    "press_enter_to_continue",
    "render_press_enter_to_continue",
    "exit_flow",
    "random_goodbye",
    "idle_return",
    "render_thinking_toggle",
    "render_ctrl_o_to_expand",
    "run_wizard",
    "render_keybinding_warnings",
]
