"""Hermes-style REPL port for Nellie.

This mirrors Hermes's ``HermesCLI.run()`` pattern verbatim:

- ``prompt_toolkit.Application`` with ``full_screen=False`` so the TUI is
  anchored to the bottom of the terminal and the real terminal scrollback
  keeps working. This is the entire point of the port — Windows Terminal's
  scrollbar scrolls through past conversation naturally because we do NOT
  take over the alternate screen buffer.
- ``patch_stdout()`` wraps ``app.run()`` so every Rich ``console.print``
  goes above the pinned input row without fighting the cursor.
- ``ChatConsole`` = Rich adapter that captures ANSI output and re-emits it
  through prompt_toolkit's native renderer (ported from Hermes verbatim).
- Key bindings: Enter submits, Alt+Enter / Ctrl+Enter (c-j) inserts
  newline, Ctrl+C interrupts / exits, Ctrl+D exits, Esc soft-interrupts,
  PgUp/PgDn still work because the terminal owns scrolling.
- Skin: only Nellie's brand blue, ASCII banner, and ``◆ nellie`` glyph
  differ from Hermes.  Everything structural is identical.

Reuses Nellie's agent loop, providers, sessions, slash commands, skills,
compactor, and config unchanged. No invention — direct port.
"""

from __future__ import annotations

import asyncio
import os
import random
import re as _re_mod
import shutil
import subprocess as _subprocess_mod
import tempfile as _tempfile_mod
import threading
import time
from collections import deque
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from karna.compaction.compactor import Compactor

# prompt_toolkit imports — same set as Hermes uses
from prompt_toolkit import print_formatted_text as _pt_print
from prompt_toolkit.application import Application
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI as _PT_ANSI
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.widgets import TextArea
from rich.console import Console

try:  # pragma: no cover - prompt_toolkit version-dependent
    from prompt_toolkit.cursor_shapes import CursorShape

    _STEADY_CURSOR: Any = CursorShape.BLOCK
except (ImportError, AttributeError):  # pragma: no cover
    _STEADY_CURSOR = None


from karna.agents.autonomous import run_autonomous_loop
from karna.agents.loop import agent_loop
from karna.agents.plan import run_plan_mode
from karna.config import KarnaConfig
from karna.models import Conversation, Message
from karna.prompts import build_system_prompt
from karna.providers import get_provider, resolve_model
from karna.sessions.cost import CostTracker
from karna.sessions.db import SessionDB
from karna.skills.loader import SkillManager
from karna.tools import TOOLS, get_all_tools
from karna.tui.completer import NellieCompleter
from karna.tui.design_tokens import SEMANTIC
from karna.tui.output import (
    BRAILLE_FRAMES,
    FACES,  # noqa: F401 — mirrored from legacy repl for test parity
    LONG_RUN_CHARMS,
    VERBS,  # noqa: F401 — mirrored from legacy repl for test parity
    EventKind,
    OutputRenderer,
    StreamEvent,
)
from karna.tui.slash import (
    SessionCost,
    _store_last_plan,  # type: ignore[attr-defined]
    clear_last_plan,
    handle_slash_command,
)
from karna.tui.themes import BRAND_BLUE, KARNA_THEME

# Sibling subagent ships ``karna.tui.hermes_display`` with Hermes's spinner,
# faces, and tool-preview helpers.  Shim it when absent so the REPL still
# imports cleanly on fresh checkouts / tests.
try:  # pragma: no cover — optional sibling module
    from karna.tui import hermes_display as _hermes_display  # type: ignore
except Exception:  # noqa: BLE001
    _hermes_display = None  # type: ignore[assignment]


# Sentinel prefixes — kept in sync with ``karna.tui.slash.handle_slash_command``.
_LOOP_SENTINEL = "__LOOP__"
_PLAN_SENTINEL = "__PLAN__"
_DO_SENTINEL = "__DO__"
_CRON_RUN_SENTINEL = "__CRON_RUN__"


# --------------------------------------------------------------------------- #
#  TUI debug trace — opt-in via KARNA_DEBUG_TUI=1 (same contract as legacy).
# --------------------------------------------------------------------------- #

_TUI_DEBUG = os.environ.get("KARNA_DEBUG_TUI", "").lower() in ("1", "true", "yes", "on")
_TUI_LOG_PATH = Path.home() / ".karna" / "logs" / "tui.log"


def _tui_log(event: str, **fields: Any) -> None:
    if not _TUI_DEBUG:
        return
    try:
        _TUI_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        bits = [f"{time.time():.3f}", event]
        bits.extend(f"{k}={v!r}" for k, v in fields.items())
        with _TUI_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write("  ".join(bits) + "\n")
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
#  Shell interpolation: {!command} -> stdout (same contract as legacy)
# --------------------------------------------------------------------------- #

_SHELL_INTERP_RE = _re_mod.compile(r"\{!([^}]+)\}")


def _interpolate_shell(text: str) -> str:
    def _run(m: _re_mod.Match[str]) -> str:
        cmd = m.group(1)
        try:
            result = _subprocess_mod.run(
                cmd,
                shell=True,  # noqa: S602
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip()
        except Exception:
            return f"(error running: {cmd})"

    return _SHELL_INTERP_RE.sub(_run, text)


# --------------------------------------------------------------------------- #
#  _cprint  —  Hermes's ANSI print helper, ported verbatim.
# --------------------------------------------------------------------------- #


def _cprint(text: str) -> None:
    """Print ANSI-coloured text through prompt_toolkit's native renderer.

    Raw ANSI escapes written via ``print()`` are swallowed by
    ``patch_stdout``'s ``StdoutProxy``.  Routing through
    ``print_formatted_text(ANSI(...))`` lets prompt_toolkit parse the
    escapes and render real colours.
    """
    _pt_print(_PT_ANSI(text))


# --------------------------------------------------------------------------- #
#  ChatConsole  —  Hermes's Rich adapter for patch_stdout.
#  Ported verbatim from cli.py:1484.  Only import paths changed.
# --------------------------------------------------------------------------- #


class ChatConsole:
    """Rich Console adapter for prompt_toolkit's patch_stdout context.

    Captures Rich's rendered ANSI output and routes it through
    :func:`_cprint` so colours and markup render correctly inside the
    interactive chat loop.  Drop-in replacement for Rich ``Console`` —
    just pass this to any function that expects a ``console.print()``
    interface.
    """

    def __init__(self) -> None:
        self._buffer = StringIO()
        self._inner = Console(
            file=self._buffer,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
            theme=KARNA_THEME,
        )

    # Rich's Console API that the renderer + slash handlers call.
    def print(self, *args: Any, **kwargs: Any) -> None:
        self._buffer.seek(0)
        self._buffer.truncate()
        # Read terminal width at render time so panels adapt to current size.
        self._inner.width = shutil.get_terminal_size((80, 24)).columns
        self._inner.print(*args, **kwargs)
        output = self._buffer.getvalue()
        for line in output.rstrip("\n").split("\n"):
            _cprint(line)

    @contextmanager
    def status(self, *_args: Any, **_kwargs: Any):
        """No-op Rich-compatible status context (same as Hermes)."""
        yield self

    # Rich's real Console exposes ``.size.width`` and ``.size.height``.
    @property
    def size(self):  # pragma: no cover - trivial
        return self._inner.size


# --------------------------------------------------------------------------- #
#  Shared REPL state  (same shape as legacy ``REPLState`` so the
#  dispatch helpers below keep working unchanged).
# --------------------------------------------------------------------------- #


class REPLState:
    def __init__(self) -> None:
        self.input_queue: asyncio.Queue[str] = asyncio.Queue()
        self.agent_running: bool = False
        self.agent_task: asyncio.Task | None = None
        self.status_text: str = ""
        self.session_cost: SessionCost = SessionCost()
        self.session_start: float = time.time()
        self.turn_start: float = 0.0
        self.context_tokens_used: int = 0
        self.context_window: int = 128_000
        self.long_run_start: float = 0.0
        self.long_run_charm_shown: bool = False
        self.interrupt_requested: bool = False
        self.should_exit: bool = False
        self.last_ctrl_c_time: float = 0.0


# --------------------------------------------------------------------------- #
#  Dispatch helpers (ported unchanged from legacy repl.py)
# --------------------------------------------------------------------------- #


async def _run_loop_mode(console: Any, config: KarnaConfig, goal: str) -> str:
    provider_name, model_name = resolve_model(
        f"{config.active_provider}:{config.active_model}"
        if ":" not in (config.active_model or "")
        else config.active_model
    )
    provider = get_provider(provider_name)
    provider.model = model_name
    tools = get_all_tools()
    system_prompt = build_system_prompt(config, tools)

    def _on_cycle(idx: int, summary: str) -> None:
        preview = summary.strip().splitlines()[0] if summary.strip() else "(no output)"
        if len(preview) > 120:
            preview = preview[:117] + "..."
        console.print(f"[bright_black]  cycle {idx}:[/bright_black] {preview}")

    return await run_autonomous_loop(
        goal,
        provider=provider,
        tools=tools,
        model=model_name,
        system_prompt=system_prompt,
        on_cycle_complete=_on_cycle,
    )


async def _run_plan_mode(console: Any, config: KarnaConfig, goal: str) -> str:
    provider_name, model_name = resolve_model(
        f"{config.active_provider}:{config.active_model}"
        if ":" not in (config.active_model or "")
        else config.active_model
    )
    provider = get_provider(provider_name)
    provider.model = model_name
    tools = get_all_tools()

    return await run_plan_mode(
        goal,
        provider=provider,
        tools=tools,
        model=model_name,
        base_system_prompt=config.system_prompt,
    )


def _load_tool_names() -> list[str]:
    return sorted(TOOLS.keys())


# --------------------------------------------------------------------------- #
#  Agent stream adapter (same wire format the legacy renderer expects)
# --------------------------------------------------------------------------- #


async def _agent_loop(
    config: KarnaConfig,
    conversation: Conversation,
    tool_names: list[str],
    skill_manager: SkillManager | None = None,
    compactor: "Compactor | None" = None,
) -> AsyncIterator[StreamEvent]:
    from karna.compaction.compactor import Compactor

    try:
        provider_name, model_name = resolve_model(
            f"{config.active_provider}:{config.active_model}"
            if ":" not in (config.active_model or "")
            else config.active_model
        )
        provider = get_provider(provider_name)
        provider.model = model_name

        tools = get_all_tools()

        rag_context: str | None = None
        try:
            import json as _json
            from pathlib import Path as _Path

            meta = _Path.home() / ".karna" / "rag" / "meta.json"
            has_index = False
            if meta.exists():
                try:
                    has_index = bool(_json.loads(meta.read_text(encoding="utf-8")).get("files"))
                except (OSError, _json.JSONDecodeError):
                    has_index = False
            if has_index:
                from karna.rag.context import build_rag_context

                user_msgs = [m for m in conversation.messages if m.role == "user"]
                if user_msgs:
                    rag_context = await build_rag_context(user_msgs[-1].content, top_k=5)
        except Exception:
            pass

        system_prompt = build_system_prompt(
            config, tools, skill_manager=skill_manager, rag_context=rag_context
        )

        turn_compactor = compactor or Compactor(provider, threshold=0.80)
        context_window = 128_000

        async for event in agent_loop(
            provider=provider,
            conversation=conversation,
            tools=tools,
            system_prompt=system_prompt,
            max_iterations=25,
            context_window=context_window,
            compactor=turn_compactor,
        ):
            if event.type == "thinking":
                yield StreamEvent(kind=EventKind.THINKING_DELTA, data=event.text or "")
            elif event.type == "text":
                yield StreamEvent(kind=EventKind.TEXT_DELTA, data=event.text or "")
            elif event.type == "tool_call_start":
                tc = event.tool_call
                yield StreamEvent(
                    kind=EventKind.TOOL_CALL_START,
                    data={
                        "name": tc.name if tc else "?",
                        "arguments": tc.arguments if tc else "{}",
                    },
                )
            elif event.type == "tool_call_end":
                tc = event.tool_call
                yield StreamEvent(kind=EventKind.TOOL_CALL_END)
                yield StreamEvent(
                    kind=EventKind.TOOL_RESULT,
                    data={
                        "content": event.text or (tc.arguments if tc else ""),
                        "is_error": False,
                    },
                )
            elif event.type == "tool_call_delta":
                pass
            elif event.type == "error":
                yield StreamEvent(kind=EventKind.ERROR, data=event.text or "Unknown error")
            elif event.type == "done":
                if event.usage:
                    yield StreamEvent(
                        kind=EventKind.USAGE,
                        data={
                            "prompt_tokens": event.usage.input_tokens,
                            "completion_tokens": event.usage.output_tokens,
                            "total_usd": event.usage.cost_usd or 0.0,
                        },
                    )
                yield StreamEvent(kind=EventKind.DONE)

    except Exception as e:
        yield StreamEvent(kind=EventKind.ERROR, data=f"Agent error: {type(e).__name__}: {e}")
        yield StreamEvent(kind=EventKind.DONE)


# --------------------------------------------------------------------------- #
#  Single-turn runner
# --------------------------------------------------------------------------- #


async def _run_agent_turn(
    state: REPLState,
    config: KarnaConfig,
    conversation: Conversation,
    console: Any,
    session_db: SessionDB,
    session_id: str,
    skill_manager: SkillManager | None,
    repl_compactor: "Compactor",
    tool_names: list[str],
    app: Application,
) -> None:
    _tui_log("agent_turn.enter")
    renderer = OutputRenderer(console)
    # Thinking header is printed synchronously in the accept handler, so mark
    # the turn as already started — same pattern as the legacy repl.
    renderer._turn_started = True
    renderer._spinner_shown = True

    events: list[StreamEvent] = []
    try:
        async for event in _agent_loop(
            config,
            conversation,
            tool_names,
            skill_manager=skill_manager,
            compactor=repl_compactor,
        ):
            if state.interrupt_requested:
                interrupt_event = StreamEvent(
                    kind=EventKind.ERROR,
                    data="[interrupted by user]",
                )
                renderer.handle(interrupt_event)
                events.append(interrupt_event)
                state.interrupt_requested = False
                break

            _tui_log("event", kind=str(event.kind), has_data=event.data is not None)
            renderer.handle(event)
            events.append(event)

            if event.kind == EventKind.USAGE and isinstance(event.data, dict):
                state.session_cost.add(
                    prompt=event.data.get("prompt_tokens", 0),
                    completion=event.data.get("completion_tokens", 0),
                    usd=event.data.get("total_usd", 0.0),
                )

            if event.kind == EventKind.THINKING_DELTA:
                state.status_text = "reasoning..."
            elif event.kind == EventKind.TEXT_DELTA:
                state.status_text = "writing..."
            elif event.kind == EventKind.TOOL_CALL_START and isinstance(event.data, dict):
                state.status_text = f"calling {event.data.get('name', 'tool')}..."
                state.long_run_start = time.time()
                state.long_run_charm_shown = False
            elif event.kind == EventKind.TOOL_RESULT:
                state.status_text = "processing..."
                state.long_run_start = 0.0
                state.long_run_charm_shown = False
            elif event.kind == EventKind.USAGE and isinstance(event.data, dict):
                prompt_tok = event.data.get("prompt_tokens", 0)
                comp_tok = event.data.get("completion_tokens", 0)
                state.context_tokens_used = prompt_tok + comp_tok
            elif event.kind == EventKind.DONE:
                state.status_text = ""
                state.long_run_start = 0.0

            app.invalidate()

            # Drain queued steering messages.
            while not state.input_queue.empty():
                try:
                    steered = state.input_queue.get_nowait()
                    conversation.messages.append(Message(role="user", content=steered))
                    session_db.add_message(session_id, Message(role="user", content=steered))
                    preview = steered[:60] + ("..." if len(steered) > 60 else "")
                    console.print(f"\n[bright_black]  -> injected: {preview}[/bright_black]")
                except asyncio.QueueEmpty:
                    break

    except Exception as exc:
        _tui_log("agent_turn.exception", type=type(exc).__name__, msg=str(exc)[:200])
        renderer.handle(StreamEvent(kind=EventKind.ERROR, data=str(exc)))
    finally:
        _tui_log("agent_turn.finish", event_count=len(events))
        renderer.finish()
        state.agent_running = False
        state.agent_task = None
        state.status_text = ""
        app.invalidate()

    full_reply = "".join(
        e.data for e in events if e.kind == EventKind.TEXT_DELTA and isinstance(e.data, str)
    )

    saw_error = any(e.kind == EventKind.ERROR for e in events)
    saw_tool = any(e.kind == EventKind.TOOL_CALL_START for e in events)
    if not full_reply and not saw_error:
        reason = (
            "Agent completed without producing any text reply"
            + (" (only tool calls ran)." if saw_tool else ".")
            + " Try rephrasing the request, or check /history for the tool results."
        )
        console.print(f"[yellow]{reason}[/yellow]")

    if full_reply:
        assistant_msg = Message(role="assistant", content=full_reply)
        conversation.messages.append(assistant_msg)

        turn_tokens = 0
        turn_cost = 0.0
        for e in events:
            if e.kind == EventKind.USAGE and isinstance(e.data, dict):
                turn_tokens = e.data.get("prompt_tokens", 0) + e.data.get("completion_tokens", 0)
                turn_cost = e.data.get("total_usd", 0.0)
        session_db.add_message(session_id, assistant_msg, tokens=turn_tokens, cost_usd=turn_cost)

        if turn_tokens or turn_cost:
            from rich.text import Text as RichText

            console.print(
                RichText(
                    f"  [{turn_tokens:,} tokens, ${turn_cost:.4f}]",
                    style="bright_black",
                )
            )


# --------------------------------------------------------------------------- #
#  Slash command dispatch (ported unchanged from legacy)
# --------------------------------------------------------------------------- #


async def _process_slash_command(
    user_input: str,
    console: Any,
    config: KarnaConfig,
    conversation: Conversation,
    session_cost: SessionCost,
    tool_names: list[str],
    session_db: SessionDB,
    cost_tracker: CostTracker,
    skill_manager: SkillManager,
) -> str | None:
    result = await handle_slash_command(
        user_input,
        console,
        config,
        conversation,
        session_cost=session_cost,
        tool_names=tool_names,
        session_db=session_db,
        cost_tracker=cost_tracker,
        skill_manager=skill_manager,
    )

    if isinstance(result, str) and result.startswith(_LOOP_SENTINEL):
        goal = result[len(_LOOP_SENTINEL) :]
        try:
            final_text = await _run_loop_mode(console, config, goal)
        except KeyboardInterrupt:
            console.print("\n[yellow]Loop interrupted by user.[/yellow]")
            return None
        except Exception as exc:
            console.print(f"[red]Loop failed: {type(exc).__name__}: {exc}[/red]")
            return None
        if final_text:
            console.print(final_text)
        return None

    if isinstance(result, str) and result.startswith(_PLAN_SENTINEL):
        goal = result[len(_PLAN_SENTINEL) :]
        try:
            plan_text = await _run_plan_mode(console, config, goal)
        except Exception as exc:
            console.print(f"[red]Plan mode failed: {type(exc).__name__}: {exc}[/red]")
            return None
        if plan_text:
            console.print(plan_text)
            _store_last_plan(conversation, plan_text)
        return None

    if isinstance(result, str) and result.startswith(_DO_SENTINEL):
        plan_text = result[len(_DO_SENTINEL) :]
        clear_last_plan(conversation)
        return plan_text

    if isinstance(result, str) and result.startswith(_CRON_RUN_SENTINEL):
        prompt = result[len(_CRON_RUN_SENTINEL) :]
        return prompt

    if isinstance(result, str) and result:
        return result

    return None


# --------------------------------------------------------------------------- #
#  Context-bar helpers (same as legacy — status bar reuses them).
# --------------------------------------------------------------------------- #


def _ctx_bar(pct: float, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _ctx_color(pct: float) -> str:
    if pct >= 95:
        return "31"
    if pct > 80:
        return "33"
    if pct >= 50:
        return "36"
    return "32"


# --------------------------------------------------------------------------- #
#  Main entry point — mirrors Hermes's HermesCLI.run()
# --------------------------------------------------------------------------- #


async def run_hermes_repl(
    config: KarnaConfig,
    resume_conversation: Conversation | None = None,
    resume_session_id: str | None = None,
) -> None:
    """Port of Hermes's ``HermesCLI.run()`` to Nellie.

    Structural choices mirrored from Hermes:

    - ``Application(full_screen=False, ...)`` anchored at the bottom of
      the terminal so the shell's native scrollback keeps working.  This
      is the reason for the port.
    - ``patch_stdout()`` wraps ``app.run_async()`` so Rich prints land
      above the input row without cursor fighting.
    - ``ChatConsole`` captures Rich output and re-emits through
      ``_cprint`` (prompt_toolkit ANSI renderer).
    - Key bindings: Enter submits, Alt+Enter / Ctrl+Enter inserts
      newline, Ctrl+C cancels / exits, Ctrl+D exits, Esc soft-interrupts.
    """
    # --- Console + state ---------------------------------------------------
    console = ChatConsole()
    tool_names = _load_tool_names()
    state = REPLState()

    # --- Session persistence (unchanged from legacy repl) -----------------
    session_db = SessionDB()
    if resume_conversation is not None and resume_session_id is not None:
        conversation = resume_conversation
        session_id = resume_session_id
    else:
        conversation = Conversation(
            model=config.active_model, provider=config.active_provider
        )
        cwd = os.getcwd()
        git_branch: str | None = None
        try:
            result = _subprocess_mod.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                git_branch = result.stdout.strip()
        except Exception:
            pass
        session_id = session_db.create_session(
            model=config.active_model,
            provider=config.active_provider,
            cwd=cwd,
            git_branch=git_branch,
        )

    cost_tracker = CostTracker(
        db=session_db,
        session_id=session_id,
        model=config.active_model,
        provider=config.active_provider,
    )

    # --- Skills ------------------------------------------------------------
    skill_manager = SkillManager()
    skill_manager.load_all()
    local_skills_dir = Path.cwd() / ".karna" / "skills"
    if local_skills_dir.is_dir():
        local_mgr = SkillManager(skills_dir=local_skills_dir)
        local_mgr.load_all()
        for skill in local_mgr.skills:
            if not skill_manager.get_skill_by_name(skill.name):
                skill_manager.skills.append(skill)

    # --- Banner (call Nellie's existing print_banner per spec) ------------
    from karna.tui.banner import print_banner

    print_banner(console, config, tool_names)

    # --- Persistent compactor ---------------------------------------------
    from karna.compaction.compactor import Compactor

    _init_prov_name, _init_model = resolve_model(
        f"{config.active_provider}:{config.active_model}"
        if ":" not in (config.active_model or "")
        else config.active_model
    )
    _init_provider = get_provider(_init_prov_name)
    _init_provider.model = _init_model
    repl_compactor = Compactor(_init_provider, threshold=0.80)

    # --- Application scaffolding (Hermes pattern) -------------------------
    app_ref: list[Application | None] = [None]

    # History file for persistent input recall across sessions (Hermes parity)
    history_file = Path.home() / ".karna" / ".nellie_history"
    try:
        history_file.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    # Completer + autosuggest (Hermes parity)
    completer = NellieCompleter()

    # Assistant glyph + prompt colours come from Nellie's design tokens per spec.
    _BRAND = SEMANTIC.get("accent.brand", BRAND_BLUE)
    # "◆ nellie" is the accepted Nellie assistant glyph — used in banner hints
    # and turn dividers.  Stored once here so the rest of the file references a
    # single source of truth.
    _ASSISTANT_GLYPH = "\u25c6 nellie"

    async def _on_submit(buf: Buffer) -> None:
        """Enter key handler — same routing logic as the Hermes KB handler."""
        text = buf.text.strip()
        buf.reset()
        if not text:
            return

        # Prompt-injection sniff (warn but don't block)
        if not text.startswith("/"):
            try:
                from karna.security.prompt_injection import detect_prompt_injection

                hits = detect_prompt_injection(text)
                if hits:
                    console.print(
                        "[yellow]\u26a0 prompt-injection patterns detected: "
                        f"{', '.join(hits)}. Proceeding anyway — vet the source.[/yellow]"
                    )
            except Exception:
                pass

        # Bare exit keywords
        if text.lower() in ("exit", "quit", "q"):
            text = "/exit"

        # Busy-state routing — steering messages go to queue, slash-commands
        # are handled synchronously when possible.
        if state.agent_running:
            if text.startswith("/"):
                if text.strip() in ("/exit", "/quit"):
                    if state.agent_task is not None and not state.agent_task.done():
                        state.agent_task.cancel()
                        try:
                            await state.agent_task
                        except (asyncio.CancelledError, Exception):
                            pass
                    if app_ref[0] is not None:
                        app_ref[0].exit()
                    return
                console.print(
                    "[yellow]Agent is working. Slash commands are available when idle.[/yellow]"
                )
                return

            state.input_queue.put_nowait(text)
            from rich.panel import Panel as _Panel
            from rich.text import Text as _Txt

            queued = _Txt()
            queued.append("queued — turn is still running\n", style="bold yellow")
            queued.append(f"\u2192 {text[:80]}", style="yellow")
            queued.append(
                "\n\nWait for the current reply to finish, then the next turn will start.",
                style="dim",
            )
            console.print(_Panel(queued, border_style="yellow", padding=(0, 1), expand=False))
            return

        user_input = text
        matched_skills = skill_manager.match_trigger(user_input)
        if matched_skills:
            preamble_parts: list[str] = []
            for skill in matched_skills:
                if skill.instructions:
                    preamble_parts.append(f"[Skill: {skill.name}]\n{skill.instructions}")
            if preamble_parts:
                preamble = "\n\n".join(preamble_parts)
                user_input = f"{preamble}\n\n---\n\n{user_input}"
        elif user_input.startswith("/"):
            injected = await _process_slash_command(
                user_input,
                console,
                config,
                conversation,
                state.session_cost,
                tool_names,
                session_db,
                cost_tracker,
                skill_manager,
            )
            if injected is not None:
                user_input = injected
            else:
                return

        matched_skills = (
            skill_manager.match_trigger(user_input)
            if not user_input.startswith("/")
            else []
        )
        if matched_skills:
            preamble_parts = []
            for skill in matched_skills:
                if skill.instructions:
                    preamble_parts.append(f"[Skill: {skill.name}]\n{skill.instructions}")
            if preamble_parts:
                preamble = "\n\n".join(preamble_parts)
                user_input = f"{preamble}\n\n---\n\n{user_input}"

        if _SHELL_INTERP_RE.search(user_input):
            user_input = _interpolate_shell(user_input)

        # Echo user input + turn-break divider synchronously (Hermes pattern)
        from rich.rule import Rule as _Rule
        from rich.text import Text as RichText

        user_echo = RichText()
        user_echo.append("> ", style=f"bold {_BRAND}")
        user_echo.append(user_input, style="bold #E6E8EC")
        console.print(user_echo)

        user_msg = Message(role="user", content=user_input)
        conversation.messages.append(user_msg)
        session_db.add_message(session_id, user_msg)

        state.agent_running = True
        state.turn_start = time.time()
        state.status_text = "thinking..."

        console.print()
        console.print(_Rule(style="bright_black", characters="\u2500"))
        console.print()

        thinking = RichText()
        thinking.append("\u2726 ", style="bold cyan")
        thinking.append("Thinking\u2026", style="bold cyan")
        thinking.append("  (esc to interrupt)", style="bright_black")
        console.print(thinking)

        state.agent_task = asyncio.create_task(
            _run_agent_turn(
                state,
                config,
                conversation,
                console,
                session_db,
                session_id,
                skill_manager,
                repl_compactor,
                tool_names,
                app_ref[0],  # type: ignore[arg-type]
            )
        )
        if app_ref[0] is not None:
            app_ref[0].invalidate()

    # --- Build the input area (TextArea with history + completer) ---------
    input_area = TextArea(
        height=Dimension(min=1, max=8, preferred=1),
        prompt=[("class:prompt", "\u276f ")],
        style="class:input-area",
        multiline=True,
        wrap_lines=True,
        history=FileHistory(str(history_file)),
        completer=completer,
        complete_while_typing=True,
        auto_suggest=AutoSuggestFromHistory(),
        accept_handler=lambda buf: asyncio.ensure_future(_on_submit(buf)),
    )

    # --- Key bindings (Hermes pattern) ------------------------------------
    kb = KeyBindings()

    @kb.add("enter")
    def _enter(event):  # type: ignore[no-untyped-def]
        """Enter = submit.  Routes through the accept handler."""
        buf = event.current_buffer
        # TextArea's accept handler fires when we validate-and-handle.
        if buf.validate(set_cursor=True):
            buf.validate_and_handle()

    @kb.add("escape", "enter")
    def _alt_enter(event):  # type: ignore[no-untyped-def]
        """Alt+Enter inserts a newline (Hermes parity)."""
        event.current_buffer.insert_text("\n")

    @kb.add("c-j")
    def _ctrl_enter(event):  # type: ignore[no-untyped-def]
        """Ctrl+Enter (c-j) inserts a newline (Hermes parity)."""
        event.current_buffer.insert_text("\n")

    @kb.add("escape")
    def _soft_interrupt(event):  # type: ignore[no-untyped-def]
        """Plain Esc = cooperative interrupt (same semantics as legacy)."""
        if state.agent_running:
            state.interrupt_requested = True
            state.status_text = "interrupting..."
            console.print(
                "\n[bright_black]Esc — stopping at next checkpoint. Ctrl-C to force-cancel.[/bright_black]"
            )

    @kb.add("c-c")
    def _ctrl_c(event):  # type: ignore[no-untyped-def]
        """Ctrl+C: interrupt turn, or double-press to exit (Hermes parity)."""
        now = time.time()
        if state.agent_running and state.agent_task is not None:
            if now - state.last_ctrl_c_time < 2.0:
                console.print("\n[red]\u26a1 Force exiting...[/red]")
                state.should_exit = True
                state.agent_task.cancel()
                event.app.exit()
                return
            state.last_ctrl_c_time = now
            console.print(
                "\n[yellow]\u26a1 Interrupting agent... (press Ctrl+C again to force exit)[/yellow]"
            )
            state.interrupt_requested = True
            state.agent_task.cancel()
            state.agent_running = False
            state.agent_task = None
            state.status_text = ""
        else:
            if event.current_buffer.text:
                event.current_buffer.reset()
            else:
                state.should_exit = True
                event.app.exit()

    @kb.add("c-d")
    def _ctrl_d(event):  # type: ignore[no-untyped-def]
        if state.agent_task is not None and not state.agent_task.done():
            state.agent_task.cancel()
        console.print(f"\n[bright_black]Goodbye from {_ASSISTANT_GLYPH}.[/bright_black]")
        state.should_exit = True
        event.app.exit()

    @kb.add("c-g")
    def _open_editor(event):  # type: ignore[no-untyped-def]
        """Ctrl+G opens $EDITOR on the current draft (Hermes parity)."""
        editor = os.environ.get("EDITOR", "vim")
        buf = event.app.current_buffer
        with _tempfile_mod.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write(buf.text)
            f.flush()
            path = f.name
        try:
            event.app.suspend_to_background()
            _subprocess_mod.call([editor, path])  # noqa: S603
            with open(path) as f:
                buf.text = f.read()
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # --- Status bar (same visual contract as legacy repl) -----------------
    def _status_bar_fragments():
        m = config.active_model or ""
        if m.startswith(f"{config.active_provider}/"):
            model = m
        else:
            model = f"{config.active_provider}/{m}"

        now = time.time()
        parts = [f" {model}"]

        if state.agent_running:
            braille = BRAILLE_FRAMES[int(now * 10) % len(BRAILLE_FRAMES)]
            turn_t = now - state.turn_start if state.turn_start > 0 else 0.0
            bits = [f"\x1b[38;5;111m{braille} Thinking\x1b[38;5;245m"]
            bits.append(f"{int(turn_t)}s")
            tok_in = state.context_tokens_used
            if tok_in > 0:
                if tok_in >= 1000:
                    bits.append(f"\u2191 {tok_in / 1000:.1f}k tok")
                else:
                    bits.append(f"\u2191 {tok_in} tok")
            bits.append("esc")
            parts.append(" \u00b7 ".join(bits))

            if state.long_run_start > 0:
                tool_elapsed = now - state.long_run_start
                if tool_elapsed > 10 and not state.long_run_charm_shown:
                    charm = random.choice(LONG_RUN_CHARMS)  # noqa: S311
                    parts.append(charm)
                    state.long_run_charm_shown = True
        elif state.status_text:
            parts.append(state.status_text)

        if state.context_tokens_used > 0:
            pct = min(100.0, state.context_tokens_used / state.context_window * 100)
            bar = _ctx_bar(pct)
            color = _ctx_color(pct)
            parts.append(f"\x1b[{color}m{bar} {pct:.0f}%\x1b[38;5;245m")

        queued = state.input_queue.qsize()
        if queued > 0:
            parts.append(f"\x1b[38;5;214m\u2709 {queued} queued\x1b[38;5;245m")

        if state.session_cost.total_usd > 0:
            parts.append(f"${state.session_cost.total_usd:.4f}")

        elapsed = now - state.session_start
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        parts.append(f"{mins}:{secs:02d}")

        text = f"\x1b[38;5;245m{'  |  '.join(parts)}\x1b[0m"
        return _PT_ANSI(text)

    status_bar = Window(
        content=FormattedTextControl(_status_bar_fragments, focusable=False),
        height=1,
    )

    # --- Layout (Hermes pattern: anchored bottom, NOT full-screen) --------
    # An initial spacer pushes the input to the bottom while content renders
    # above via patch_stdout.  This is the Hermes shape (see cli.py:8913).
    layout = Layout(
        HSplit(
            [
                Window(height=0),  # spacer at top — keeps input pinned
                status_bar,
                input_area,
            ]
        )
    )

    style = PTStyle.from_dict(
        {
            "input-area": SEMANTIC.get("text.primary", "#E6E8EC"),
            "prompt": f"bold {_BRAND}",
            "completion-menu": "bg:#0E0F12 #E6E8EC",
            "completion-menu.completion": "bg:#0E0F12 #E6E8EC",
            "completion-menu.completion.current": f"bg:#1A1D23 {_BRAND} bold",
        }
    )

    app = Application(
        layout=layout,
        key_bindings=kb,
        style=style,
        full_screen=False,  # <<< the port's core: native terminal scrollback works
        mouse_support=False,
        **({"cursor": _STEADY_CURSOR} if _STEADY_CURSOR is not None else {}),
    )
    app_ref[0] = app

    # --- Periodic status bar refresh --------------------------------------
    async def _refresh_status_bar() -> None:
        while True:
            await asyncio.sleep(0.5)
            try:
                if app.is_running:
                    app.invalidate()
            except Exception:  # noqa: BLE001
                break

    app.create_background_task(_refresh_status_bar())

    # --- Run with patch_stdout (THE Hermes pattern) -----------------------
    try:
        with patch_stdout():
            await app.run_async()
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        if state.agent_task is not None and not state.agent_task.done():
            state.agent_task.cancel()
            try:
                await state.agent_task
            except (asyncio.CancelledError, Exception):
                pass


__all__ = ["run_hermes_repl", "ChatConsole", "REPLState"]
