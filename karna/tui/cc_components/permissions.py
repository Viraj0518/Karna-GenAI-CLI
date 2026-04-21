"""upstream-ported permission + trust dialogs — skinned for Nellie.

Mirrors the visuals and decision-shapes of upstream reference's permission UX:

* ``components/permissions/PermissionDialog.tsx`` — the bordered outer chrome
  (rounded border, title, inner padding).
* ``components/permissions/PermissionPrompt.tsx`` and
  ``components/permissions/BashPermissionRequest/bashToolUseOptions.tsx`` —
  the four-way choice surface (yes / yes-always / no / no-always) presented
  to the user when a tool requires approval. upstream's exact option labels are
  preserved.
* ``components/MCPServerApprovalDialog.tsx`` — the three-way MCP-server
  trust prompt (``yes_all`` / ``yes`` / ``no``).
* ``components/ApproveApiKey.tsx`` — the first-use API-key trust prompt
  (masked ``sk-ant-...XXXX`` preview + default-no).
* ``components/BypassPermissionsModeDialog.tsx`` — upstream's red-banner "are you
  sure you want to bypass permissions" confirmation.
* ``components/TrustDialog/TrustDialog.tsx`` — the summary table of
  surfaced rules shown when a workspace is first opened.

Integration contract with Nellie's ``karna.permissions`` resolver
----------------------------------------------------------------
This module is **library-only**. It does not touch
``karna.permissions.manager.PermissionManager.check`` or its rule state.
The resolver keeps owning the ALLOW / ASK / DENY decision; when it decides
to prompt, it calls :func:`prompt_tool_permission` (or one of its
siblings) and applies the returned enum to its session-allow / persistent-
allow sets the same way it does today.

The return value is a ``Literal[...]`` that matches upstream's four-way
``BashToolUseOption`` shape (``yes`` / ``yes_always`` / ``no`` /
``no_always``) — this is richer than Nellie's current boolean
``request_approval`` result. Follow-up: the resolver's
``request_approval`` signature could widen to this four-state return so
``no_always`` adds to ``session_denies`` without re-prompting. That would
mirror upstream exactly but requires touching the resolver, which is explicitly
out of scope here.

Rendering
---------
All dialogs render via Rich and are compatible with
``prompt_toolkit.patch_stdout`` (the same pattern used by
``karna.tui.hermes_repl``). The prompts themselves drop below the pinned
input, collect a single keystroke, and return — no Ink state, no React
reconciler, no layout ownership.

Upstream license: MIT (see ``NOTICES.md``).
"""

from __future__ import annotations

import asyncio
from typing import Literal, Sequence

from rich.box import ROUNDED
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from karna.tui.design_tokens import COLORS

# --------------------------------------------------------------------------- #
#  Brand palette (shared with the sibling status module).
# --------------------------------------------------------------------------- #

BRAND = COLORS.accent.brand
MUTED = COLORS.text.secondary
SUBTLE = COLORS.text.tertiary
WARNING = COLORS.accent.warning
DANGER = COLORS.accent.danger
SUCCESS = COLORS.accent.success

# Border colors by decision tier — mirrors upstream's
# `color="warning"` / `color="error"` / `color="permission"` props on
# `Dialog` / `PermissionDialog`.
BORDER_ASK = WARNING  # yellow — default prompt tier
BORDER_DENY_DEFAULT = DANGER  # red — deny-by-default / destructive
BORDER_ALLOW = SUCCESS  # green — info / current allowlist

# --------------------------------------------------------------------------- #
#  Return-value aliases (match upstream's BashToolUseOption shape).
# --------------------------------------------------------------------------- #

ToolPermissionChoice = Literal["allow_once", "allow_always", "deny_once", "deny_always"]

# The canonical keystrokes — case-sensitive, same shape upstream uses:
#   lowercase = "this call only", uppercase = "always / never ask again".
# Mirrors the existing ``[y]es / [Y]es always / [n]o / [N]o always`` prompt
# in ``karna.permissions.manager.PermissionManager.request_approval`` so the
# muscle-memory is shared.
_KEY_MAP: dict[str, ToolPermissionChoice] = {
    # lowercase — "this call only"
    "y": "allow_once",
    "yes": "allow_once",
    "a": "allow_once",  # 'a'llow — matches the task brief's "[a/A/d/D]"
    "n": "deny_once",
    "no": "deny_once",
    "d": "deny_once",  # 'd'eny — alternative spelling of 'n'
    "": "deny_once",  # ENTER with no input = "no, this call only"
    # uppercase — "always / never ask again"
    "Y": "allow_always",
    "A": "allow_always",
    "always": "allow_always",
    "N": "deny_always",
    "D": "deny_always",
    "never": "deny_always",
}


# --------------------------------------------------------------------------- #
#  Rendering helpers
# --------------------------------------------------------------------------- #


def _permission_panel(
    title: str,
    body: RenderableType,
    *,
    border: str,
) -> Panel:
    """Mirror of upstream's ``PermissionDialog`` outer chrome.

    upstream uses ``borderStyle="round"`` with only the top border rendered
    (``borderLeft=false`` / ``borderRight=false`` / ``borderBottom=false``).
    Rich's panel always draws four sides, so we use a full rounded border
    tinted by tier — it reads the same in a mono terminal.
    """
    return Panel(
        body,
        title=Text(title, style=f"bold {border}"),
        title_align="left",
        border_style=border,
        box=ROUNDED,
        padding=(0, 1),
    )


def _format_tool_args(args: dict, max_len: int = 160) -> str:
    """One-line summary of a tool-call's arguments, for the dialog header."""
    if not args:
        return ""
    # Bash gets special treatment — show the command verbatim.
    if "command" in args and isinstance(args["command"], str):
        cmd = args["command"]
        if len(cmd) > max_len:
            return cmd[: max_len - 3] + "..."
        return cmd
    parts = []
    for k, v in args.items():
        sv = str(v)
        if len(sv) > 48:
            sv = sv[:45] + "..."
        parts.append(f"{k}={sv}")
    joined = ", ".join(parts)
    if len(joined) > max_len:
        joined = joined[: max_len - 3] + "..."
    return joined


def _tool_request_body(
    tool_name: str,
    tool_args: dict,
    *,
    already_allowed_rules: Sequence[str],
    intro: str = "Claude wants to run:",
) -> RenderableType:
    """The body of a per-tool permission request — tool-name row, args
    preview, and (optionally) the list of currently-allowed rules.

    Layout mirrors upstream's ``BashPermissionRequest`` / ``FallbackPermission
    Request`` inner table: one-line header, then the input preview inside a
    subtle-bordered box, then any context footers.
    """
    preview = _format_tool_args(tool_args)
    tbl = Table.grid(padding=(0, 1), expand=True)
    tbl.add_column(no_wrap=True, style=f"bold {BRAND}")
    tbl.add_column(overflow="fold")
    tbl.add_row("Tool", Text(tool_name, style="bold"))
    if preview:
        tbl.add_row("Input", Text(preview, style=MUTED))

    parts: list[RenderableType] = [Text(intro, style=MUTED), tbl]

    if already_allowed_rules:
        rules_tbl = render_permission_allowlist(list(already_allowed_rules))
        parts.append(Text(""))
        parts.append(Text("Already allowed in this session:", style=SUBTLE))
        parts.append(rules_tbl)

    return Group(*parts)


def render_permission_allowlist(rules: Sequence[str]) -> RenderableType:
    """Render the current session allowlist as a Rich table.

    Mirrors upstream's ``PermissionRuleExplanation`` + ``TrustDialog`` rule-row
    layout: a left-aligned bullet column and a rule-text column. Pure —
    no IO. Callers pass the rule strings from
    ``PermissionManager.session_allows`` / the persistent-allow set.
    """
    tbl = Table.grid(padding=(0, 1))
    tbl.add_column(style=SUCCESS, no_wrap=True)
    tbl.add_column(style=MUTED, overflow="fold")
    if not rules:
        tbl.add_row("·", Text("(none yet)", style=SUBTLE))
        return tbl
    for rule in rules:
        tbl.add_row("\u2713", rule)  # ✓
    return tbl


# --------------------------------------------------------------------------- #
#  Input helper — keystroke-level, patch_stdout-friendly.
# --------------------------------------------------------------------------- #


async def _read_keystroke(prompt: str) -> str:
    """Read a single line of input without blocking the event loop.

    Uses ``prompt_toolkit.PromptSession`` when available so the prompt
    renders below ``patch_stdout()`` without fighting the cursor. Falls
    back to ``asyncio.to_thread(input, ...)`` in environments where
    prompt_toolkit isn't installed (e.g. CI, piped stdin).
    """
    try:
        from prompt_toolkit import PromptSession

        session: PromptSession = PromptSession()
        return await session.prompt_async(prompt)
    except Exception:
        # Graceful fallback — tests monkeypatch ``input`` directly so this
        # path is the one they exercise.
        try:
            return await asyncio.to_thread(input, prompt)
        except (EOFError, KeyboardInterrupt):
            return ""


def _classify_tool_response(raw: str) -> ToolPermissionChoice:
    """Map a raw keystroke / word into one of the four canonical choices."""
    stripped = raw.strip()
    # Case-sensitive lookups first (``Y`` vs ``y``) then fall back to
    # case-insensitive word matches ("always", "never", "no", ...).
    if stripped in _KEY_MAP:
        return _KEY_MAP[stripped]
    lower = stripped.lower()
    if lower in _KEY_MAP:
        return _KEY_MAP[lower]
    return "deny_once"


def _classify_yes_no(raw: str, *, default: bool = False) -> bool:
    """Collapse a yes/no response into a bool. ``default`` applies to empty input."""
    stripped = raw.strip().lower()
    if stripped == "":
        return default
    return stripped in ("y", "yes", "a", "always", "accept")


# --------------------------------------------------------------------------- #
#  Public dialog API
# --------------------------------------------------------------------------- #


async def prompt_tool_permission(
    tool_name: str,
    tool_args: dict,
    *,
    already_allowed_rules: list[str],
    console: Console | None = None,
) -> ToolPermissionChoice:
    """Ask the user whether a tool invocation may proceed.

    Mirrors upstream's ``BashPermissionRequest`` / ``FallbackPermissionRequest``
    four-way choice surface: ``yes`` / ``yes-always`` (adds a rule) /
    ``no`` / ``no-always`` (session-deny). Returns the canonical
    ``ToolPermissionChoice`` enum; the caller is responsible for pushing
    the decision into ``PermissionManager.session_allows`` /
    ``session_denies`` / the persistent-allow store.

    The prompt string matches the inline shape upstream shows in terminals
    without TTY (the ``[a/A/d/D]`` fallback): lowercase = this call only,
    uppercase = remember.
    """
    cons = console or Console()
    panel = _permission_panel(
        f"Permission required: {tool_name}",
        _tool_request_body(
            tool_name,
            tool_args,
            already_allowed_rules=already_allowed_rules,
        ),
        border=BORDER_ASK,
    )
    cons.print(panel)
    raw = await _read_keystroke("Allow? [a]llow once / [A]llow always / [d]eny once / [D]eny always: ")
    return _classify_tool_response(raw)


async def prompt_mcp_server_approval(
    server_name: str,
    tools: list[str],
    *,
    console: Console | None = None,
) -> bool:
    """Approve (or reject) an MCP server's tool set on first sight.

    Mirrors ``MCPServerApprovalDialog.tsx``'s three-way choice, collapsed
    to a boolean for Nellie's resolver (the ``yes_all`` case is not yet
    modelled in ``karna.mcp_servers``; flag for follow-up). Default is
    no.

    The body lists every tool the server plans to expose — this is the
    same surface upstream uses to avoid surprise tool-installation.
    """
    cons = console or Console()
    tbl = Table.grid(padding=(0, 1))
    tbl.add_column(style=f"bold {BRAND}", no_wrap=True)
    tbl.add_column(overflow="fold")
    tbl.add_row("Server", Text(server_name, style="bold"))
    if tools:
        tbl.add_row("Tools", Text(", ".join(tools), style=MUTED))
    else:
        tbl.add_row("Tools", Text("(none advertised)", style=SUBTLE))

    body = Group(
        Text(
            "This MCP server will gain the ability to run the tools listed below.",
            style=MUTED,
        ),
        tbl,
    )
    panel = _permission_panel(
        f"New MCP server: {server_name}",
        body,
        border=BORDER_ASK,
    )
    cons.print(panel)
    raw = await _read_keystroke("Approve this MCP server? [y/N]: ")
    return _classify_yes_no(raw, default=False)


async def prompt_api_key_trust(
    provider: str,
    key_preview: str,
    *,
    console: Console | None = None,
) -> bool:
    """Confirm first-use of an API key pulled from the environment.

    Mirrors ``ApproveApiKey.tsx``: masks everything except the last four
    characters (the caller should pre-mask — this function prints the
    preview as-is) and defaults to **no** (upstream labels the "No" option
    *recommended*).
    """
    cons = console or Console()
    tbl = Table.grid(padding=(0, 1))
    tbl.add_column(style=f"bold {BRAND}", no_wrap=True)
    tbl.add_column(overflow="fold")
    tbl.add_row("Provider", Text(provider, style="bold"))
    tbl.add_row("Key", Text(key_preview, style=MUTED))

    body = Group(
        Text(
            "Detected an API key in your environment. Use it for this session?",
            style=MUTED,
        ),
        tbl,
    )
    panel = _permission_panel(
        "API key trust",
        body,
        border=BORDER_ASK,
    )
    cons.print(panel)
    raw = await _read_keystroke("Use this key? [y/N]: ")
    return _classify_yes_no(raw, default=False)


async def prompt_bypass_permissions(console: Console | None = None) -> bool:
    """Confirm entering bypass-permissions mode.

    Mirrors ``BypassPermissionsModeDialog.tsx``: red banner, explicit
    "yes, I accept" / "no, exit" choices, defaults to decline. The
    warning copy is a verbatim port of upstream's phrasing.
    """
    cons = console or Console()
    body = Group(
        Text(
            "In Bypass Permissions mode, Nellie will not ask for your "
            "approval before running potentially dangerous commands.",
            style=DANGER,
        ),
        Text(""),
        Text(
            "This mode should only be used in a sandboxed container/VM "
            "that has restricted internet access and can easily be "
            "restored if damaged.",
            style=MUTED,
        ),
        Text(""),
        Text(
            "By proceeding, you accept all responsibility for actions taken while running in Bypass Permissions mode.",
            style=MUTED,
        ),
    )
    panel = _permission_panel(
        "WARNING: Bypass Permissions mode",
        body,
        border=BORDER_DENY_DEFAULT,
    )
    cons.print(panel)
    raw = await _read_keystroke("Yes, I accept? [y/N]: ")
    return _classify_yes_no(raw, default=False)


# --------------------------------------------------------------------------- #
#  Test hooks — exposed for ``tests/test_cc_permissions.py``.
# --------------------------------------------------------------------------- #

__all__ = [
    "ToolPermissionChoice",
    "prompt_tool_permission",
    "prompt_mcp_server_approval",
    "prompt_api_key_trust",
    "prompt_bypass_permissions",
    "render_permission_allowlist",
    # Internal helpers re-exported for targeted tests.
    "_classify_tool_response",
    "_classify_yes_no",
    "_format_tool_args",
]
