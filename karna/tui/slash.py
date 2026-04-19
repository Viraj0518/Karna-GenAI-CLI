"""Slash-command handler for the Karna REPL.

Slash commands start with ``/`` and control the session (model switching,
conversation management, etc.) without being sent to the LLM.

This module exposes the public surface consumed by ``repl.py``:

    SessionCost                  - per-session token/cost accumulator
    COMMANDS                     - canonical command metadata
    handle_slash_command(...)    - parse + dispatch a raw ``/...`` string

The user-facing ``/help`` output is a grouped, icon-prefixed, padded
table rather than a plain list. Commands are tagged with a category
(``session``, ``context``, ``utility``, ``advanced``) and rendered
group-by-group so the picker feels discoverable. If/when we wire a
proper interactive picker, the same metadata powers it.

The ``advanced`` category holds the higher-level driving modes:

    /loop <goal>   - repeat-until-done autonomous agent (karna.agents.autonomous)
    /plan <goal>   - think-first / read-only plan mode    (karna.agents.plan)
    /do            - execute the last plan produced by /plan

``/plan`` parks its plan text in a module-level store keyed by
``id(conversation)`` so the matching ``/do`` can recall it inside
the same REPL session without needing to extend the sessions DB
schema (which is owned by another agent).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from karna.config import KarnaConfig, save_config
from karna.models import Conversation
from karna.tui.design_tokens import SEMANTIC

if TYPE_CHECKING:
    from karna.sessions.cost import CostTracker
    from karna.sessions.db import SessionDB
    from karna.skills.loader import SkillManager

# --------------------------------------------------------------------------- #
#  Icons (optional; authored by a sibling agent — degrade gracefully)
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - trivial import guard
    from karna.tui import icons as _icons  # type: ignore
except Exception:  # pragma: no cover
    _icons = None  # type: ignore[assignment]


def _icon(name: str, fallback: str) -> str:
    """Look up *name* on the optional icons module, else return *fallback*."""
    if _icons is None:
        return fallback
    for attr in (name, name.upper(), name.lower()):
        glyph = getattr(_icons, attr, None)
        if isinstance(glyph, str) and glyph:
            return glyph
    return fallback


# --------------------------------------------------------------------------- #
#  Session-level cost tracking (populated by output.py)
# --------------------------------------------------------------------------- #


@dataclass
class SessionCost:
    """Mutable accumulator for per-session token/cost tracking."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_usd: float = 0.0

    def add(self, prompt: int = 0, completion: int = 0, usd: float = 0.0) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_usd += usd


# --------------------------------------------------------------------------- #
#  Command definitions
# --------------------------------------------------------------------------- #


@dataclass
class SlashCommand:
    """Metadata for a single slash command."""

    name: str
    usage: str
    help_text: str
    handler: Callable[..., None] = field(repr=False, default=lambda *a, **k: None)
    # Enhancements (optional; safe defaults preserve existing construction):
    category: str = "utility"
    icon: str = "\u2022"  # bullet


# Category ordering + display labels for /help.
_CATEGORY_ORDER: tuple[str, ...] = ("session", "context", "advanced", "utility")
_CATEGORY_LABELS: dict[str, str] = {
    "session": "Session",
    "context": "Context",
    "advanced": "Advanced",
    "utility": "Utility",
}


def _build_commands() -> dict[str, SlashCommand]:
    """Return the canonical command table (handlers are bound later)."""
    # Icons resolved via the optional icons module with sensible fallbacks.
    ic_help = _icon("HELP", "?")
    ic_model = _icon("MODEL", "\u25ce")  # circled ring
    ic_clear = _icon("CLEAR", "\u2715")  # x
    ic_history = _icon("HISTORY", "\u231a")  # clock
    ic_cost = _icon("COST", "$")
    ic_exit = _icon("EXIT", "\u21b5")  # return
    ic_compact = _icon("COMPACT", "\u29c7")  # circled dot
    ic_tools = _icon("TOOLS", "\u2699")  # gear
    ic_system = _icon("SYSTEM", "\u24e2")  # circled S
    ic_sessions = _icon("SESSIONS", "\u2630")  # trigram
    ic_resume = _icon("RESUME", "\u21bb")  # rotate
    ic_paste = _icon("PASTE", "\u2398")  # next page
    ic_copy = _icon("COPY", "\u2398")
    # Advanced-mode icons — reuse the "running" / "thinking" glyphs if the
    # sibling icons module exposes them, else fall back to unicode glyphs.
    ic_running = _icon("RUNNING", "\u25b6")  # play
    ic_thinking = _icon("THINKING", "\u25cb")  # white circle

    cmds: list[SlashCommand] = [
        # ── Session ────────────────────────────────────────────────────
        SlashCommand("history", "/history", "Show conversation so far", category="session", icon=ic_history),
        SlashCommand("clear", "/clear", "Reset conversation history", category="session", icon=ic_clear),
        SlashCommand(
            "sessions", "/sessions", "Show last 5 sessions from history", category="session", icon=ic_sessions
        ),
        SlashCommand("resume", "/resume <id>", "Resume a previous session", category="session", icon=ic_resume),
        # ── Context ────────────────────────────────────────────────────
        SlashCommand(
            "model", "/model <provider:model>", "Switch model mid-conversation", category="context", icon=ic_model
        ),
        SlashCommand("system", "/system <prompt>", "Set the system prompt", category="context", icon=ic_system),
        SlashCommand("cost", "/cost", "Show total token usage and cost", category="context", icon=ic_cost),
        SlashCommand(
            "compact", "/compact", "Summarize older messages to free context space", category="context", icon=ic_compact
        ),
        SlashCommand("tools", "/tools", "List available tools", category="context", icon=ic_tools),
        SlashCommand(
            "skills",
            "/skills [enable|disable <name>]",
            "List, enable, or disable skills",
            category="context",
            icon=ic_tools,
        ),
        # ── Advanced ───────────────────────────────────────────────────
        SlashCommand(
            "loop",
            "/loop <goal>",
            "Repeat-until-done autonomous agent cycle",
            category="advanced",
            icon=ic_running,
        ),
        SlashCommand(
            "plan",
            "/plan <goal>",
            "Think first, don't execute (read-only)",
            category="advanced",
            icon=ic_thinking,
        ),
        SlashCommand(
            "do",
            "/do",
            "Execute the last plan produced by /plan",
            category="advanced",
            icon=ic_running,
        ),
        # ── Utility ────────────────────────────────────────────────────
        SlashCommand("copy", "/copy", "Copy last assistant response", category="utility", icon=ic_copy),
        SlashCommand("paste", "/paste", "Read clipboard and send as message", category="utility", icon=ic_paste),
        SlashCommand("help", "/help", "List available commands", category="utility", icon=ic_help),
        SlashCommand("exit", "/exit", "Exit the REPL", category="utility", icon=ic_exit),
        SlashCommand("quit", "/quit", "Exit the REPL", category="utility", icon=ic_exit),
    ]
    return {c.name: c for c in cmds}


COMMANDS = _build_commands()


# --------------------------------------------------------------------------- #
#  /help rendering helpers
# --------------------------------------------------------------------------- #


def _group_for_help(cmds: Iterable[SlashCommand]) -> dict[str, list[SlashCommand]]:
    """Partition *cmds* by category, preserving insertion order inside each."""
    buckets: dict[str, list[SlashCommand]] = {c: [] for c in _CATEGORY_ORDER}
    for c in cmds:
        if c.name == "quit":
            continue  # avoid duplicating /exit in help output
        buckets.setdefault(c.category, []).append(c)
    return buckets


def _render_category_table(label: str, cmds: list[SlashCommand]) -> Table:
    """Build a Rich table for one command category."""
    brand = SEMANTIC.get("accent.brand", "#3C73BD")
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    primary = SEMANTIC.get("text.primary", "#E6E8EC")
    secondary = SEMANTIC.get("text.secondary", "#A0A4AD")

    table = Table(
        title=Text(f"  {label}", style=f"bold {cyan}"),
        title_justify="left",
        show_header=False,
        show_edge=False,
        show_lines=False,
        box=None,
        padding=(0, 2),
        pad_edge=False,
    )
    table.add_column("Icon", style=brand, no_wrap=True, width=2)
    table.add_column("Usage", style=primary, no_wrap=True)
    table.add_column("Description", style=secondary)

    for cmd in cmds:
        table.add_row(cmd.icon, cmd.usage, cmd.help_text)
    return table


# --------------------------------------------------------------------------- #
#  Handler implementations
# --------------------------------------------------------------------------- #


def _cmd_help(console: Console, **_kw) -> None:  # type: ignore[no-untyped-def]
    """Grouped, icon-prefixed help panel."""
    tertiary = SEMANTIC.get("text.tertiary", "#5F6472")
    border = SEMANTIC.get("border.subtle", "#2A2F38")
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")

    buckets = _group_for_help(COMMANDS.values())
    renderables: list = []
    for i, cat in enumerate(_CATEGORY_ORDER):
        cmds = buckets.get(cat) or []
        if not cmds:
            continue
        if i > 0:
            renderables.append(Text(""))  # blank line between groups
        renderables.append(_render_category_table(_CATEGORY_LABELS[cat], cmds))

    # Footer hint — fuzzy-match teaser + exit reminder.
    renderables.append(Text(""))
    footer = Text("  ")
    footer.append("Type ", style=tertiary)
    footer.append("/", style=cyan)
    footer.append("<prefix> ", style=tertiary)
    footer.append("to fuzzy-match  -  ", style=tertiary)
    footer.append("ctrl+c", style=cyan)
    footer.append(" cancels, ", style=tertiary)
    footer.append("ctrl+d", style=cyan)
    footer.append(" exits.", style=tertiary)
    renderables.append(footer)

    console.print(
        Panel(
            Group(*renderables),
            title=Text("slash commands", style=f"bold {cyan}"),
            title_align="left",
            border_style=border,
            padding=(1, 2),
            expand=False,
        )
    )


def _cmd_model(console: Console, config: KarnaConfig, args: str, conversation: Conversation, **_kw) -> None:
    if not args:
        console.print(f"[bright_black]Current model:[/] [white]{config.active_provider}:{config.active_model}[/]")
        return
    if ":" not in args:
        console.print("[red]Usage: /model provider:model_name[/red]")
        return
    provider, model = args.split(":", 1)
    config.active_provider = provider.strip()
    config.active_model = model.strip()
    conversation.provider = config.active_provider
    conversation.model = config.active_model
    save_config(config)
    console.print(f"[green]Switched to [bold]{config.active_provider}:{config.active_model}[/bold][/green]")


def _cmd_clear(console: Console, conversation: Conversation, **_kw) -> None:
    conversation.messages.clear()
    console.print("[green]Conversation cleared.[/green]")


def _cmd_history(console: Console, conversation: Conversation, **_kw) -> None:
    if not conversation.messages:
        console.print("[bright_black]No messages yet.[/bright_black]")
        return
    for msg in conversation.messages:
        role_style = {
            "user": "white",
            "assistant": SEMANTIC.get("accent.cyan", "#87CEEB"),
            "system": "yellow",
            "tool": "dim green",
        }.get(msg.role, "white")
        label = msg.role.capitalize()
        content_preview = msg.content[:200]
        if len(msg.content) > 200:
            content_preview += "..."
        console.print(f"[bold {role_style}]{label}:[/bold {role_style}] {content_preview}")


def _cmd_cost(console: Console, session_cost: SessionCost, cost_tracker: "CostTracker | None" = None, **_kw) -> None:
    border = SEMANTIC.get("border.accent", "#3C73BD")
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    if cost_tracker is not None:
        summary = cost_tracker.get_session_summary()
        console.print(
            Panel(
                f"[bright_black]Input tokens:[/]  {summary['input_tokens']:,}\n"
                f"[bright_black]Output tokens:[/] {summary['output_tokens']:,}\n"
                f"[bright_black]Session cost:[/]  ${summary['cost_usd']:.4f}",
                title=f"[bold {cyan}]Session Cost[/]",
                border_style=border,
                expand=False,
            )
        )
    else:
        console.print(
            Panel(
                f"[bright_black]Prompt tokens:[/]  {session_cost.prompt_tokens:,}\n"
                f"[bright_black]Output tokens:[/] {session_cost.completion_tokens:,}\n"
                f"[bright_black]Total cost:[/]    ${session_cost.total_usd:.4f}",
                title=f"[bold {cyan}]Session Cost[/]",
                border_style=border,
                expand=False,
            )
        )


def _cmd_exit(**_kw) -> None:
    raise SystemExit(0)


def _cmd_compact(
    console: Console,
    conversation: Conversation,
    config: KarnaConfig,
    **_kw,
) -> None:
    """Manually trigger conversation compaction."""
    import asyncio

    from karna.compaction.compactor import Compactor, _conv_tokens
    from karna.providers import get_provider, resolve_model

    if len(conversation.messages) <= 6:
        console.print("[bright_black]Not enough messages to compact.[/bright_black]")
        return

    # Estimate tokens before compaction
    tokens_before = _conv_tokens(conversation.messages, "")
    msg_count_before = len(conversation.messages)

    try:
        # Resolve the current provider for summarization
        model_spec = (
            f"{config.active_provider}:{config.active_model}"
            if ":" not in (config.active_model or "")
            else config.active_model
        )
        provider_name, model_name = resolve_model(model_spec)
        provider = get_provider(provider_name)
        provider.model = model_name

        compactor = Compactor(provider, threshold=0.0)  # threshold=0 forces compaction

        # Default context window (generous)
        context_window = 200_000

        # Run the compaction
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                compactor.compact(conversation, context_window),
                loop,
            )
            future.result(timeout=60)
        else:
            asyncio.run(compactor.compact(conversation, context_window))

    except Exception as exc:
        console.print(f"[red]Compaction failed: {type(exc).__name__}: {exc}[/red]")
        return

    tokens_after = _conv_tokens(conversation.messages, "")
    msg_count_after = len(conversation.messages)
    saved = tokens_before - tokens_after

    border = SEMANTIC.get("border.accent", "#3C73BD")
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")

    console.print(
        Panel(
            f"[bright_black]Messages:[/] {msg_count_before} -> {msg_count_after}\n"
            f"[bright_black]Est. tokens:[/] ~{tokens_before:,} -> ~{tokens_after:,} "
            f"([green]-{saved:,}[/green])",
            title=f"[bold {cyan}]Compaction Complete[/]",
            border_style=border,
            expand=False,
        )
    )


def _cmd_tools(console: Console, tool_names: list[str], **_kw) -> None:
    if not tool_names:
        console.print("[bright_black]No tools loaded.[/bright_black]")
        return
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    border = SEMANTIC.get("border.accent", "#3C73BD")
    table = Table(show_header=True, header_style=f"bold {cyan}", border_style=border, expand=False)
    table.add_column("#", style="bright_black", justify="right")
    table.add_column("Tool", style="white")
    for i, name in enumerate(sorted(tool_names), 1):
        table.add_row(str(i), name)
    console.print(table)


def _cmd_skills(
    console: Console,
    args: str,
    skill_manager: "SkillManager | None" = None,
    **_kw,
) -> None:
    """``/skills`` -- list, enable, or disable skills."""
    if skill_manager is None:
        console.print("[bright_black]No skills loaded.[/bright_black]")
        return

    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    border = SEMANTIC.get("border.accent", "#3C73BD")

    parts = args.strip().split(None, 1) if args.strip() else []
    sub = parts[0].lower() if parts else ""
    sub_arg = parts[1].strip() if len(parts) > 1 else ""

    if sub == "enable":
        if not sub_arg:
            console.print("[red]Usage: /skills enable <name>[/red]")
            return
        if skill_manager.enable_skill(sub_arg):
            console.print(f"[green]Enabled skill: [bold]{sub_arg}[/bold][/green]")
        else:
            console.print(f"[red]Skill not found: {sub_arg}[/red]")
        return

    if sub == "disable":
        if not sub_arg:
            console.print("[red]Usage: /skills disable <name>[/red]")
            return
        if skill_manager.disable_skill(sub_arg):
            console.print(f"[yellow]Disabled skill: [bold]{sub_arg}[/bold][/yellow]")
        else:
            console.print(f"[red]Skill not found: {sub_arg}[/red]")
        return

    # Default: list all skills
    if not skill_manager.skills:
        console.print("[bright_black]No skills loaded.[/bright_black]")
        return

    table = Table(
        show_header=True,
        header_style=f"bold {cyan}",
        border_style=border,
        expand=False,
    )
    table.add_column("Name", style="white")
    table.add_column("Description", style="bright_black")
    table.add_column("Triggers", style="cyan")
    table.add_column("Enabled", style="green", justify="center")
    for skill in skill_manager.skills:
        triggers_str = ", ".join(skill.triggers) if skill.triggers else "-"
        enabled_str = "yes" if skill.enabled else "no"
        enabled_style = "green" if skill.enabled else "red"
        table.add_row(
            skill.name,
            skill.description,
            triggers_str,
            f"[{enabled_style}]{enabled_str}[/{enabled_style}]",
        )
    console.print(table)


def _cmd_system(console: Console, config: KarnaConfig, args: str, **_kw) -> None:
    if not args:
        console.print(f"[bright_black]System prompt:[/] [white]{config.system_prompt}[/white]")
        return
    config.system_prompt = args
    save_config(config)
    console.print("[green]System prompt updated.[/green]")


def _cmd_sessions(console: Console, session_db: "SessionDB | None" = None, **_kw) -> None:
    if session_db is None:
        console.print("[bright_black]Session database not available.[/bright_black]")
        return
    sessions = session_db.list_sessions(limit=5)
    if not sessions:
        console.print("[bright_black]No sessions found.[/bright_black]")
        return
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    border = SEMANTIC.get("border.accent", "#3C73BD")
    table = Table(show_header=True, header_style=f"bold {cyan}", border_style=border, expand=False)
    table.add_column("ID", style="cyan")
    table.add_column("Started", style="green")
    table.add_column("Model", style="white")
    table.add_column("Cost", justify="right", style="yellow")
    for s in sessions:
        started = s["started_at"][:19].replace("T", " ")
        cost = f"${s['total_cost_usd']:.4f}"
        table.add_row(s["id"], started, s.get("model", ""), cost)
    console.print(table)


def _cmd_paste(console: Console, conversation: Conversation, **_kw) -> str | None:
    """Read clipboard and return content to be injected as user message."""
    import asyncio

    from karna.tools.clipboard import ClipboardTool

    tool = ClipboardTool()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(tool.execute(action="read"), loop)
            result = future.result(timeout=10)
        else:
            result = asyncio.run(tool.execute(action="read"))
    except Exception as exc:
        console.print(f"[red]Failed to read clipboard: {exc}[/red]")
        return None

    if result.startswith("[error]") or result == "(clipboard is empty)":
        console.print(f"[bright_black]{result}[/bright_black]")
        return None

    preview = result[:100]
    if len(result) > 100:
        preview += "..."
    console.print(f"[bright_black]Pasted from clipboard ({len(result)} chars): {preview}[/bright_black]")
    return result


def _cmd_copy(console: Console, conversation: Conversation, **_kw) -> None:
    """Copy the last assistant response to clipboard."""
    import asyncio

    from karna.tools.clipboard import ClipboardTool

    last_assistant = None
    for msg in reversed(conversation.messages):
        if msg.role == "assistant" and msg.content:
            last_assistant = msg.content
            break

    if last_assistant is None:
        console.print("[bright_black]No assistant response to copy.[/bright_black]")
        return

    tool = ClipboardTool()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            future = asyncio.run_coroutine_threadsafe(tool.execute(action="write", content=last_assistant), loop)
            result = future.result(timeout=10)
        else:
            result = asyncio.run(tool.execute(action="write", content=last_assistant))
    except Exception as exc:
        console.print(f"[red]Failed to copy to clipboard: {exc}[/red]")
        return

    console.print(f"[green]{result}[/green]")


# --------------------------------------------------------------------------- #
#  Advanced commands: /loop, /plan, /do
# --------------------------------------------------------------------------- #

# Per-conversation plan storage. Keyed by id(conversation) so multiple
# Conversation objects (e.g. parallel REPLs, tests) don't collide.
# The sessions DB is owned by another agent so we deliberately keep
# plan persistence in-process — it survives within a single REPL run,
# which is exactly what ``/plan`` → ``/do`` needs.
_LAST_PLAN: dict[int, str] = {}


def _store_last_plan(conversation: Conversation, plan: str) -> None:
    """Remember *plan* so a later ``/do`` can retrieve it."""
    _LAST_PLAN[id(conversation)] = plan


def get_last_plan(conversation: Conversation) -> str | None:
    """Return the most recent plan for *conversation*, or ``None``.

    Exposed for the REPL (and tests) so the ``/do`` handler in
    ``repl.py`` can recall and execute the plan.
    """
    return _LAST_PLAN.get(id(conversation))


def clear_last_plan(conversation: Conversation) -> None:
    """Drop any stored plan for *conversation*.

    Called by the REPL once ``/do`` successfully executes the plan,
    so a stale plan isn't silently reused on a second ``/do``.
    """
    _LAST_PLAN.pop(id(conversation), None)


def _cmd_loop(console: Console, args: str, **_kw) -> str | None:
    """``/loop <goal>`` — kick the repeat-until-done autonomous agent.

    The actual agent invocation lives in ``repl.py`` (it needs the
    provider and tools). This handler only validates the args and
    returns a sentinel string that the REPL recognises; an empty
    goal is rejected here so we never dispatch a no-op cycle.
    """
    goal = args.strip()
    if not goal:
        console.print(
            "[red]Usage:[/red] [bold]/loop <goal>[/bold]  "
            "[bright_black](e.g. /loop refactor auth module and get tests green)[/bright_black]"
        )
        return None
    # Sentinel returned to repl.py — see handle_slash_command docstring.
    return f"__LOOP__{goal}"


def _cmd_plan(console: Console, args: str, **_kw) -> str | None:
    """``/plan <goal>`` — enter plan mode (read-only, no execution).

    Like ``/loop`` the real work happens in ``repl.py``; we just
    validate + hand off via a sentinel string.
    """
    goal = args.strip()
    if not goal:
        console.print(
            "[red]Usage:[/red] [bold]/plan <goal>[/bold]  "
            "[bright_black](investigates with read/grep/glob and outputs a numbered plan)[/bright_black]"
        )
        return None
    return f"__PLAN__{goal}"


def _cmd_do(console: Console, conversation: Conversation, **_kw) -> str | None:
    """``/do`` — execute the most recently produced plan.

    Recalls the plan stored by ``/plan`` for this conversation and
    hands it to the REPL as a regular user message (via a sentinel
    prefix that the REPL unwraps).
    """
    plan = get_last_plan(conversation)
    if not plan:
        console.print("[bright_black]No plan to execute. Run [bold]/plan <goal>[/bold] first.[/bright_black]")
        return None
    cyan = SEMANTIC.get("accent.cyan", "#87CEEB")
    console.print(f"[{cyan}]Executing last plan...[/]")
    return f"__DO__{plan}"


def _cmd_resume(console: Console, args: str, session_db: "SessionDB | None" = None, **_kw) -> None:
    if session_db is None:
        console.print("[bright_black]Session database not available.[/bright_black]")
        return
    sid = args.strip() if args else None
    if not sid:
        sid = session_db.get_latest_session_id()
    if not sid:
        console.print("[bright_black]No sessions to resume.[/bright_black]")
        return
    conv = session_db.resume_session(sid)
    if conv is None:
        console.print(f"[red]Session not found: {sid}[/red]")
        return
    console.print(f"[yellow]Use [bold]nellie resume {sid}[/bold] to resume a session with full context.[/yellow]")


# --------------------------------------------------------------------------- #
#  Fuzzy prefix matching (used by dispatcher before reporting "unknown")
# --------------------------------------------------------------------------- #


def _fuzzy_match(partial: str) -> str | None:
    """Return the unique command whose name starts with *partial*, else None."""
    if not partial:
        return None
    hits = [name for name in COMMANDS if name.startswith(partial)]
    # Collapse exit/quit to a single hit (they're aliases).
    if set(hits) == {"exit", "quit"}:
        return "exit"
    if len(hits) == 1:
        return hits[0]
    return None


# --------------------------------------------------------------------------- #
#  Dispatcher
# --------------------------------------------------------------------------- #

_HANDLERS: dict[str, Callable[..., None]] = {
    "help": _cmd_help,
    "model": _cmd_model,
    "clear": _cmd_clear,
    "history": _cmd_history,
    "cost": _cmd_cost,
    "exit": _cmd_exit,
    "quit": _cmd_exit,
    "compact": _cmd_compact,
    "tools": _cmd_tools,
    "skills": _cmd_skills,
    "system": _cmd_system,
    "sessions": _cmd_sessions,
    "resume": _cmd_resume,
    "paste": _cmd_paste,
    "copy": _cmd_copy,
    "loop": _cmd_loop,
    "plan": _cmd_plan,
    "do": _cmd_do,
}


def handle_slash_command(
    raw_input: str,
    console: Console,
    config: KarnaConfig,
    conversation: Conversation,
    session_cost: SessionCost | None = None,
    tool_names: list[str] | None = None,
    session_db: "SessionDB | None" = None,
    cost_tracker: "CostTracker | None" = None,
    skill_manager: "SkillManager | None" = None,
) -> str | None:
    """Parse and dispatch a slash command.

    *raw_input* is the full user string including the leading ``/``.

    Returns a string if the command produces text to inject as a user
    message (e.g. ``/paste``), otherwise ``None``.

    Sentinel return values (consumed by ``repl.py``, not the user):

    - ``"__LOOP__<goal>"``  — run :func:`karna.agents.autonomous.run_autonomous_loop`
    - ``"__PLAN__<goal>"``  — run :func:`karna.agents.plan.run_plan_mode`
    - ``"__DO__<plan>"``    — execute *plan* as a regular agent-loop turn

    The sentinels are prefixed with double underscores so they cannot
    collide with any plausible clipboard paste content.
    """
    stripped = raw_input.strip().lstrip("/")
    parts = stripped.split(None, 1)
    cmd_name = parts[0].lower() if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    handler = _HANDLERS.get(cmd_name)
    if handler is None:
        # Try a unique prefix match ("/m" -> "/model") before giving up.
        resolved = _fuzzy_match(cmd_name)
        if resolved is not None:
            handler = _HANDLERS.get(resolved)
        if handler is None:
            console.print(f"[red]Unknown command: /{cmd_name}[/red]  (type [bold]/help[/bold] to list commands)")
            return None

    result = handler(
        console=console,
        config=config,
        conversation=conversation,
        session_cost=session_cost or SessionCost(),
        tool_names=tool_names or [],
        args=args,
        session_db=session_db,
        cost_tracker=cost_tracker,
        skill_manager=skill_manager,
    )
    return result


__all__ = [
    "SessionCost",
    "SlashCommand",
    "COMMANDS",
    "handle_slash_command",
    "get_last_plan",
    "clear_last_plan",
]
