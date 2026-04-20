"""Main REPL loop for the Karna TUI.

Launched by ``nellie`` (no args) --- provides streaming conversation with
tool use, slash commands, multiline input, and Rich-rendered output.

Uses a ``prompt_toolkit.Application`` with a split-pane layout so that
the output area and input area never fight for cursor position:

    +------------------------------------------+
    |  Output Window (scrollable)               |  <- ANSI formatted text
    |  - Banner, thinking, tool calls, text     |     auto-scrolls to bottom
    |------------------------------------------|
    |  Status bar (1 line)                      |  <- model, cost, status
    |------------------------------------------|
    |  > user input here                        |  <- BufferControl + Buffer
    |                                           |     always active
    +------------------------------------------+

This eliminates the spinner/prompt cursor fight that occurred with the
previous ``patch_stdout`` + ``PromptSession.prompt_async`` approach.
"""

from __future__ import annotations

import asyncio
import os
import random
import re as _re_mod
import subprocess as _subprocess_mod
import tempfile as _tempfile_mod
import time
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator

if TYPE_CHECKING:
    from karna.compaction.compactor import Compactor

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension as D
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.output import ColorDepth
from rich.console import Console

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
from karna.tui.output import (
    FACES,
    LONG_RUN_CHARMS,
    VERBS,
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
from karna.tui.themes import KARNA_THEME

# Sentinel prefixes returned by slash handlers that need REPL-level execution.
# Kept in sync with ``karna.tui.slash.handle_slash_command``.
_LOOP_SENTINEL = "__LOOP__"
_PLAN_SENTINEL = "__PLAN__"
_DO_SENTINEL = "__DO__"
_CRON_RUN_SENTINEL = "__CRON_RUN__"


# --------------------------------------------------------------------------- #
#  Shell interpolation: {!command} -> stdout
# --------------------------------------------------------------------------- #

_SHELL_INTERP_RE = _re_mod.compile(r"\{!([^}]+)\}")


def _interpolate_shell(text: str) -> str:
    """Replace ``{!command}`` patterns with the command's stdout.

    Example::

        >>> _interpolate_shell("explain {!echo hello}")
        'explain hello'

    Commands that fail or time out are replaced with an error marker.
    """

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
#  Context usage bar helpers
# --------------------------------------------------------------------------- #


def _ctx_bar(pct: float, width: int = 10) -> str:
    """Render a block-character progress bar."""
    filled = round(pct / 100 * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _ctx_color(pct: float) -> str:
    """ANSI color code for context usage percentage."""
    if pct >= 95:
        return "31"  # red
    if pct > 80:
        return "33"  # yellow
    if pct >= 50:
        return "36"  # cyan
    return "32"  # green


# --------------------------------------------------------------------------- #
#  TUI Output Writer -- captures Rich output for the split-pane layout
# --------------------------------------------------------------------------- #


_MAX_OUTPUT_LINES = 5000


class TUIOutputWriter:
    """Captures Rich console output as ANSI strings for prompt_toolkit.

    Instead of Rich printing directly to stdout (which fights with
    prompt_toolkit), this writer accumulates rendered ANSI text in a
    buffer.  The prompt_toolkit ``Application`` reads from this buffer
    via an ``OutputControl`` and redraws only when invalidated.

    The buffer is capped at ``_MAX_OUTPUT_LINES`` rendered chunks —
    pathological cases (a tool dumping a 100 MB CSV) evict oldest
    output rather than growing unbounded and eventually starving the
    render loop.
    """

    def __init__(self, width: int = 120) -> None:
        self._lines: list[str] = []
        self._width = width
        self._invalidate_cb: Any = None

    def _append(self, text: str) -> None:
        """Append one rendered chunk and enforce the ring-buffer cap."""
        self._lines.append(text)
        overflow = len(self._lines) - _MAX_OUTPUT_LINES
        if overflow > 0:
            # Drop the oldest half of the overflow in one slice — cheaper
            # than trimming one line per append once we're at the cap.
            trim = max(overflow, _MAX_OUTPUT_LINES // 10)
            del self._lines[:trim]

    def set_invalidate(self, cb: Any) -> None:
        """Register the Application.invalidate callback."""
        self._invalidate_cb = cb

    @property
    def console(self) -> Console:
        """Return a Rich Console that writes to this writer's buffer.

        Each call returns a fresh Console so callers can use it without
        worrying about interleaved output from concurrent tasks.
        """
        buf = StringIO()
        return Console(
            file=buf,
            force_terminal=True,
            width=self._width,
            theme=KARNA_THEME,
            color_system="truecolor",
        )

    def write_rich(self, renderable: Any) -> None:
        """Render a Rich object and append to output buffer."""
        buf = StringIO()
        console = Console(
            file=buf,
            force_terminal=True,
            width=self._width,
            theme=KARNA_THEME,
            color_system="truecolor",
        )
        console.print(renderable)
        text = buf.getvalue()
        if text:
            self._append(text.rstrip("\n"))
            self._invalidate()

    def write_ansi(self, text: str) -> None:
        """Append raw ANSI text to output buffer."""
        if text:
            self._append(text.rstrip("\n"))
            self._invalidate()

    def write_console_output(self, console_buf: StringIO) -> None:
        """Flush a StringIO that a Rich Console wrote to."""
        text = console_buf.getvalue()
        if text:
            self._append(text.rstrip("\n"))
            self._invalidate()

    def get_text(self) -> str:
        """Return all accumulated output as a single ANSI string."""
        return "\n".join(self._lines)

    def _invalidate(self) -> None:
        """Trigger a prompt_toolkit redraw."""
        if self._invalidate_cb is not None:
            try:
                self._invalidate_cb()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
#  Redirected Console -- a Rich Console that writes through TUIOutputWriter
# --------------------------------------------------------------------------- #


class RedirectedConsole(Console):
    """A Rich Console whose output is captured by a TUIOutputWriter.

    This is the main Console instance used throughout the REPL.  All
    ``print()`` calls go through the writer so they appear in the
    output pane rather than fighting with prompt_toolkit.
    """

    def __init__(self, writer: TUIOutputWriter, **kwargs: Any) -> None:
        self._writer = writer
        self._capture_buf = StringIO()
        super().__init__(
            file=self._capture_buf,
            force_terminal=True,
            width=writer._width,
            theme=KARNA_THEME,
            color_system="truecolor",
            **kwargs,
        )

    def print(self, *args: Any, **kwargs: Any) -> None:
        """Override to capture output and route to the writer."""
        # Reset the capture buffer, render, then flush to writer
        self._capture_buf.seek(0)
        self._capture_buf.truncate(0)
        super().print(*args, **kwargs)
        text = self._capture_buf.getvalue()
        if text:
            self._writer._append(text.rstrip("\n"))
            self._writer._invalidate()


# --------------------------------------------------------------------------- #
#  Always-active input state
# --------------------------------------------------------------------------- #


class REPLState:
    """Shared mutable state between the input loop and agent tasks."""

    def __init__(self) -> None:
        self.input_queue: asyncio.Queue[str] = asyncio.Queue()
        self.agent_running: bool = False
        self.agent_task: asyncio.Task | None = None
        self.status_text: str = ""
        self.session_cost: SessionCost = SessionCost()
        self.session_start: float = time.time()
        # Context usage tracking
        self.context_tokens_used: int = 0
        self.context_window: int = 128_000
        # Long-run charm tracking
        self.long_run_start: float = 0.0
        self.long_run_charm_shown: bool = False
        # Output scroll control — populated by _build_application so key handlers
        # (PgUp/PgDn/Home/End/Ctrl-Up/Ctrl-Down/mouse-wheel) can move the view.
        # Keep typed as Any to avoid a prompt_toolkit import cycle in the header.
        self.output_window: Any = None
        # True when the user has manually scrolled back — suppresses autoscroll
        # until they return to the bottom (Home-to-bottom or End).
        self.output_scroll_locked: bool = False
        # Interrupt flag — set by Esc handler, polled by the agent loop so a
        # long thinking/tool-use cycle can be cancelled without Ctrl-C which
        # also kills the input buffer.
        self.interrupt_requested: bool = False


# --------------------------------------------------------------------------- #
#  Dispatch helpers (unchanged)
# --------------------------------------------------------------------------- #


async def _run_loop_mode(
    console: Console,
    config: KarnaConfig,
    goal: str,
) -> str:
    """Dispatch ``/loop`` --- run the autonomous repeat-until-done agent."""
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


async def _run_plan_mode(
    console: Console,
    config: KarnaConfig,
    goal: str,
) -> str:
    """Dispatch ``/plan`` --- run plan mode and return the plan text."""
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
    """Return the names of all registered tools."""
    return sorted(TOOLS.keys())


# --------------------------------------------------------------------------- #
#  Agent loop async generator (unchanged from original)
# --------------------------------------------------------------------------- #


async def _agent_loop(
    config: KarnaConfig,
    conversation: Conversation,
    tool_names: list[str],
    skill_manager: SkillManager | None = None,
    compactor: "Compactor | None" = None,
) -> AsyncIterator[StreamEvent]:
    """Yield TUI StreamEvents live from the agent loop."""
    from karna.compaction.compactor import Compactor

    try:
        # Resolve provider
        provider_name, model_name = resolve_model(
            f"{config.active_provider}:{config.active_model}"
            if ":" not in (config.active_model or "")
            else config.active_model
        )
        provider = get_provider(provider_name)
        provider.model = model_name

        # Build tools
        tools = get_all_tools()

        # Query the RAG knowledge base for relevant context.
        rag_context: str | None = None
        try:
            from karna.rag.context import build_rag_context

            # Use the latest user message as the query.
            user_msgs = [m for m in conversation.messages if m.role == "user"]
            if user_msgs:
                rag_context = await build_rag_context(user_msgs[-1].content, top_k=5)
        except Exception:
            pass  # RAG is best-effort — never block the agent loop.

        # Build system prompt (with skills and RAG context if available)
        system_prompt = build_system_prompt(config, tools, skill_manager=skill_manager, rag_context=rag_context)

        # Re-use the caller-supplied compactor so the circuit breaker
        # persists across REPL turns.
        turn_compactor = compactor or Compactor(provider, threshold=0.80)

        # Default context window (128k tokens)
        context_window = 128_000

        # Run the real agent loop with auto-compaction
        async for event in agent_loop(
            provider=provider,
            conversation=conversation,
            tools=tools,
            system_prompt=system_prompt,
            max_iterations=25,
            context_window=context_window,
            compactor=turn_compactor,
        ):
            # Map agent StreamEvent -> TUI StreamEvent and yield immediately
            if event.type == "thinking":
                yield StreamEvent(kind=EventKind.THINKING_DELTA, data=event.text or "")
            elif event.type == "text":
                yield StreamEvent(kind=EventKind.TEXT_DELTA, data=event.text or "")
            elif event.type == "tool_call_start":
                tc = event.tool_call
                yield StreamEvent(
                    kind=EventKind.TOOL_CALL_START,
                    data={"name": tc.name if tc else "?", "arguments": tc.arguments if tc else "{}"},
                )
            elif event.type == "tool_call_end":
                tc = event.tool_call
                # Emit TOOL_CALL_END so the renderer closes the live
                # spinner before we send the result.
                yield StreamEvent(kind=EventKind.TOOL_CALL_END)
                yield StreamEvent(
                    kind=EventKind.TOOL_RESULT,
                    data={"content": event.text or (tc.arguments if tc else ""), "is_error": False},
                )
            elif event.type == "tool_call_delta":
                pass  # accumulating, not rendered until tool_call_end
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
#  Agent turn --- runs as a concurrent task
# --------------------------------------------------------------------------- #


async def _run_agent_turn(
    state: REPLState,
    config: KarnaConfig,
    conversation: Conversation,
    console: Console,
    session_db: SessionDB,
    session_id: str,
    skill_manager: SkillManager | None,
    repl_compactor: "Compactor",
    tool_names: list[str],
) -> None:
    """Execute one agent turn, checking for queued steering messages.

    Runs as an ``asyncio.Task`` so the input loop stays responsive.
    """
    renderer = OutputRenderer(console)
    renderer.show_spinner()

    events: list[StreamEvent] = []
    try:
        async for event in _agent_loop(
            config,
            conversation,
            tool_names,
            skill_manager=skill_manager,
            compactor=repl_compactor,
        ):
            # Cooperative interrupt — Esc sets this flag; we stop
            # consuming events and surface a soft interruption.
            if state.interrupt_requested:
                renderer.handle(
                    StreamEvent(
                        kind=EventKind.ERROR,
                        data="[interrupted by user]",
                    )
                )
                state.interrupt_requested = False
                break
            renderer.handle(event)
            events.append(event)

            # Accumulate session cost
            if event.kind == EventKind.USAGE and isinstance(event.data, dict):
                state.session_cost.add(
                    prompt=event.data.get("prompt_tokens", 0),
                    completion=event.data.get("completion_tokens", 0),
                    usd=event.data.get("total_usd", 0.0),
                )

            # Update status bar
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
                # Track context usage from usage events
                prompt_tok = event.data.get("prompt_tokens", 0)
                comp_tok = event.data.get("completion_tokens", 0)
                state.context_tokens_used = prompt_tok + comp_tok
            elif event.kind == EventKind.DONE:
                state.status_text = ""
                state.long_run_start = 0.0

            # Check for queued steering messages (non-blocking)
            while not state.input_queue.empty():
                try:
                    steered = state.input_queue.get_nowait()
                    conversation.messages.append(Message(role="user", content=steered))
                    session_db.add_message(session_id, Message(role="user", content=steered))
                    preview = steered[:60]
                    if len(steered) > 60:
                        preview += "..."
                    console.print(f"\n[bright_black]  -> injected: {preview}[/bright_black]")
                except asyncio.QueueEmpty:
                    break

    except Exception as exc:
        renderer.handle(StreamEvent(kind=EventKind.ERROR, data=str(exc)))
    finally:
        renderer.finish()
        state.agent_running = False
        state.agent_task = None
        state.status_text = ""

    # Persist assistant reply to conversation and session DB
    full_reply = "".join(e.data for e in events if e.kind == EventKind.TEXT_DELTA and isinstance(e.data, str))

    # Surface empty-reply + tool-halt conditions so the user isn't left
    # looking at silence. If the agent did tool calls but produced no
    # text, or hit max_iterations without a DONE text, say so.
    saw_error = any(e.kind == EventKind.ERROR for e in events)
    saw_tool = any(e.kind == EventKind.TOOL_CALL_START for e in events)
    if not full_reply and not saw_error:
        reason = (
            "Agent completed without producing any text reply"
            + (" (only tool calls ran)." if saw_tool else ".")
            + " Try rephrasing the request, or check /history for the"
            " tool results."
        )
        console.print(f"[yellow]{reason}[/yellow]")

    if full_reply:
        assistant_msg = Message(role="assistant", content=full_reply)
        conversation.messages.append(assistant_msg)

        # Compute token count from usage event
        turn_tokens = 0
        turn_cost = 0.0
        for e in events:
            if e.kind == EventKind.USAGE and isinstance(e.data, dict):
                turn_tokens = e.data.get("prompt_tokens", 0) + e.data.get("completion_tokens", 0)
                turn_cost = e.data.get("total_usd", 0.0)
        session_db.add_message(session_id, assistant_msg, tokens=turn_tokens, cost_usd=turn_cost)

        # Display per-turn cost in dim text
        if turn_tokens or turn_cost:
            from rich.text import Text as RichText

            console.print(
                RichText(
                    f"  [{turn_tokens:,} tokens, ${turn_cost:.4f}]",
                    style="bright_black",
                )
            )


# --------------------------------------------------------------------------- #
#  Slash command processing (extracted for reuse)
# --------------------------------------------------------------------------- #


async def _process_slash_command(
    user_input: str,
    console: Console,
    config: KarnaConfig,
    conversation: Conversation,
    session_cost: SessionCost,
    tool_names: list[str],
    session_db: SessionDB,
    cost_tracker: CostTracker,
    skill_manager: SkillManager,
) -> str | None:
    """Handle a slash command and return user_input to send (or None to skip)."""
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

    # Advanced-command sentinels
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

    # /paste returns raw text --- treat as user input
    if isinstance(result, str) and result:
        return result

    return None


# --------------------------------------------------------------------------- #
#  Build the prompt_toolkit Application with split-pane layout
# --------------------------------------------------------------------------- #


def _build_application(
    writer: TUIOutputWriter,
    input_buffer: Buffer,
    kb: KeyBindings,
    state: REPLState,
    config: KarnaConfig,
) -> Application:
    """Construct the full-screen split-pane Application.

    Layout:
        - Top: scrollable output window (all agent output)
        - Middle: 1-line status bar (model, cost, agent status)
        - Bottom: fixed input area (always accepts input)
    """
    from karna.tui.design_tokens import SEMANTIC

    # Output window -- scrollable, shows all agent output, with scrollbar.
    #
    # focusable=True is REQUIRED even though the input stays focused by default
    # (see `focused_element=input_window` below). Without it, prompt_toolkit
    # never tracks `vertical_scroll` on this window, which makes both the
    # ScrollbarMargin and the mouse-wheel/PgUp/PgDn handlers no-ops. Regression
    # fixed: commit ccb866a added the scrollbar without this flag; the arrows
    # rendered but nothing scrolled. See TUI_AUDIT_20260420.md §A.
    #
    # display_arrows=False keeps the scrollbar as a clean thumb only.
    output_window = Window(
        content=FormattedTextControl(
            lambda: ANSI(writer.get_text()),
            focusable=True,
        ),
        wrap_lines=True,
        right_margins=[ScrollbarMargin(display_arrows=False)],
        allow_scroll_beyond_bottom=False,
    )
    # Publish the reference so key handlers + the agent loop (autoscroll) can
    # read/write its scroll state. Typed as Any on state to avoid header churn.
    state.output_window = output_window

    # Status bar -- 1-line, shows model + animated face/verb + context bar + cost + timer
    def _status_bar_text():
        # Avoid doubled prefix like "openrouter/openrouter/auto"
        m = config.active_model or ""
        if m.startswith(f"{config.active_provider}/"):
            model = m
        else:
            model = f"{config.active_provider}/{m}"

        now = time.time()
        parts = [f" {model}"]

        # Animated kawaii face + verb when agent is running
        if state.agent_running:
            face = FACES[int(now * 0.4) % len(FACES)]
            verb = VERBS[int(now * 0.1) % len(VERBS)]
            parts.append(f"{face} {verb}...")

            # Long-run charm (>10s on a tool call)
            if state.long_run_start > 0:
                tool_elapsed = now - state.long_run_start
                if tool_elapsed > 10 and not state.long_run_charm_shown:
                    charm = random.choice(LONG_RUN_CHARMS)  # noqa: S311
                    parts.append(charm)
                    state.long_run_charm_shown = True
        elif state.status_text:
            parts.append(state.status_text)

        # Context usage bar
        if state.context_tokens_used > 0:
            pct = min(100.0, state.context_tokens_used / state.context_window * 100)
            bar = _ctx_bar(pct)
            color = _ctx_color(pct)
            parts.append(f"\x1b[{color}m{bar} {pct:.0f}%\x1b[38;5;245m")

        # Queued mid-stream messages — persistent indicator so the user
        # knows the agent hasn't forgotten a steering message between
        # event boundaries.
        queued = state.input_queue.qsize()
        if queued > 0:
            parts.append(f"\x1b[38;5;214m✉ {queued} queued\x1b[38;5;245m")

        # Session cost
        if state.session_cost.total_usd > 0:
            parts.append(f"${state.session_cost.total_usd:.4f}")

        # Session duration timer
        elapsed = now - state.session_start
        mins = int(elapsed // 60)
        secs = int(elapsed % 60)
        parts.append(f"{mins}:{secs:02d}")

        return ANSI(f"\x1b[38;5;245m{'  |  '.join(parts)}\x1b[0m")

    status_bar = Window(
        content=FormattedTextControl(_status_bar_text, focusable=False),
        height=1,
        style=f"bg:{SEMANTIC.get('bg.subtle', '#0E0F12')}",
    )

    # Empty input line renders with just the prompt glyph + cursor.
    # (Earlier iterations rotated a "Try \"refactor the auth module\"" placeholder;
    #  removed per direction — no background text, just the line.)

    # Input window -- always active, accepts user input.
    # Only processor: the prompt glyph ❯ before the cursor. No placeholder text.
    input_window = Window(
        content=BufferControl(
            buffer=input_buffer,
            input_processors=[
                BeforeInput(ANSI("\x1b[1;38;2;60;115;189m\u276f\x1b[0m ")),
            ],
        ),
        height=D(min=1, max=5),
        dont_extend_height=True,
    )

    layout = Layout(
        HSplit(
            [
                output_window,  # scrollable output
                status_bar,  # model + cost + status
                input_window,  # always-active input
            ]
        ),
        focused_element=input_window,
    )

    return Application(
        layout=layout,
        key_bindings=kb,
        full_screen=True,
        color_depth=ColorDepth.TRUE_COLOR,
        mouse_support=True,
    )


# --------------------------------------------------------------------------- #
#  Main REPL -- split-pane TUI with prompt_toolkit Application
# --------------------------------------------------------------------------- #


async def run_repl(
    config: KarnaConfig,
    resume_conversation: Conversation | None = None,
    resume_session_id: str | None = None,
) -> None:
    """Main REPL -- streaming conversation with split-pane TUI.

    Uses a ``prompt_toolkit.Application`` with a full-screen layout:
    - Scrolling output area (top) -- all agent output renders here
    - Fixed status bar (middle) -- model, cost, agent status
    - Fixed input area (bottom) -- always visible, always accepts input

    The Application owns the terminal, so there is no fighting between
    Rich spinners and prompt_toolkit's prompt redraw.
    """
    # Determine terminal width for Rich rendering
    try:
        term_width = os.get_terminal_size().columns
    except OSError:
        term_width = 120

    writer = TUIOutputWriter(width=term_width)
    console = RedirectedConsole(writer)
    tool_names = _load_tool_names()
    state = REPLState()

    # Session persistence
    session_db = SessionDB()
    if resume_conversation is not None and resume_session_id is not None:
        conversation = resume_conversation
        session_id = resume_session_id
    else:
        conversation = Conversation(model=config.active_model, provider=config.active_provider)
        cwd = os.getcwd()
        git_branch: str | None = None
        try:
            import subprocess

            result = subprocess.run(
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

    # Load skills from ~/.karna/skills/ and .karna/skills/
    skill_manager = SkillManager()
    skill_manager.load_all()
    local_skills_dir = Path.cwd() / ".karna" / "skills"
    if local_skills_dir.is_dir():
        local_mgr = SkillManager(skills_dir=local_skills_dir)
        local_mgr.load_all()
        for skill in local_mgr.skills:
            if not skill_manager.get_skill_by_name(skill.name):
                skill_manager.skills.append(skill)

    # Render banner into the output buffer
    from karna.tui.banner import print_banner

    print_banner(console, config, tool_names)

    # Create a single Compactor that persists across REPL turns
    from karna.compaction.compactor import Compactor

    _init_prov_name, _init_model = resolve_model(
        f"{config.active_provider}:{config.active_model}"
        if ":" not in (config.active_model or "")
        else config.active_model
    )
    _init_provider = get_provider(_init_prov_name)
    _init_provider.model = _init_model
    repl_compactor = Compactor(_init_provider, threshold=0.80)

    # ── Build the prompt_toolkit Application ──────────────────────────

    # Shared reference so the submit handler can access REPL state
    app_ref: list[Application | None] = [None]

    async def _on_submit(buf: Buffer) -> None:
        """Called when Enter is pressed in the input buffer."""
        text = buf.text.strip()
        buf.reset()
        if not text:
            return

        # ── Bare exit/quit detection ─────────────────────────────
        if text.strip().lower() in ("exit", "quit", "q"):
            text = "/exit"

        # ── If agent is running, queue as steering message ────────
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
                console.print("[yellow]Agent is working. Slash commands are available when idle.[/yellow]")
                return

            state.input_queue.put_nowait(text)
            console.print("[bright_black]  -> message queued (will be injected mid-stream)[/bright_black]")
            return

        # ── Skill trigger matching (checked BEFORE slash commands) ─
        user_input = text
        matched_skills = skill_manager.match_trigger(user_input)
        if matched_skills:
            skill_preamble_parts: list[str] = []
            for skill in matched_skills:
                if skill.instructions:
                    skill_preamble_parts.append(f"[Skill: {skill.name}]\n{skill.instructions}")
            if skill_preamble_parts:
                skill_preamble = "\n\n".join(skill_preamble_parts)
                user_input = f"{skill_preamble}\n\n---\n\n{user_input}"
            # Skills matched --- skip slash command handling, fall through

        # ── Slash commands ──────────────────────────────────────────
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

        # ── Skill trigger matching for non-slash ────────────────────
        matched_skills = skill_manager.match_trigger(user_input) if not user_input.startswith("/") else []
        if matched_skills:
            skill_preamble_parts = []
            for skill in matched_skills:
                if skill.instructions:
                    skill_preamble_parts.append(f"[Skill: {skill.name}]\n{skill.instructions}")
            if skill_preamble_parts:
                skill_preamble = "\n\n".join(skill_preamble_parts)
                user_input = f"{skill_preamble}\n\n---\n\n{user_input}"

        # ── Shell interpolation: {!cmd} -> stdout ──────────────────
        if _SHELL_INTERP_RE.search(user_input):
            user_input = _interpolate_shell(user_input)

        # ── Start new agent turn ────────────────────────────────────
        from rich.text import Text as RichText

        user_echo = RichText()
        user_echo.append("> ", style="bold #E8C26B")
        user_echo.append(user_input, style="bold #E6E8EC")
        console.print(user_echo)

        user_msg = Message(role="user", content=user_input)
        conversation.messages.append(user_msg)
        session_db.add_message(session_id, user_msg)

        state.agent_running = True
        state.status_text = "thinking..."

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
            )
        )

    # Tab-completion for slash commands, file paths, and model names
    completer = NellieCompleter()

    # Input buffer with accept handler and tab completion
    input_buffer = Buffer(
        name="input",
        completer=completer,
        complete_while_typing=True,
        multiline=False,
        accept_handler=lambda buf: asyncio.ensure_future(_on_submit(buf)),
    )

    # Key bindings
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _insert_newline(event):  # type: ignore[no-untyped-def]
        event.current_buffer.insert_text("\n")

    @kb.add("c-c")
    def _interrupt(event):  # type: ignore[no-untyped-def]
        if state.agent_running and state.agent_task is not None:
            state.agent_task.cancel()
            console.print("\n[yellow]Interrupted.[/yellow]")
            state.agent_running = False
            state.agent_task = None
            state.status_text = ""
        else:
            event.current_buffer.reset()

    @kb.add("escape", eager=True)
    def _soft_interrupt(event):  # type: ignore[no-untyped-def]
        """Esc alone asks the agent to stop at its next checkpoint.

        Distinct from Ctrl-C: this is a cooperative interrupt. The
        agent loop sees ``state.interrupt_requested`` between events
        and winds down cleanly. Eager=True prevents prompt_toolkit
        from waiting for a follow-on key before dispatching.
        """
        if state.agent_running:
            state.interrupt_requested = True
            state.status_text = "interrupting..."
            console.print(
                "\n[bright_black]Esc — stopping at next checkpoint. "
                "Ctrl-C to force-cancel.[/bright_black]"
            )

    @kb.add("c-d")
    def _exit(event):  # type: ignore[no-untyped-def]
        if state.agent_task is not None and not state.agent_task.done():
            state.agent_task.cancel()
        console.print("\n[bright_black]Goodbye.[/bright_black]")
        event.app.exit()

    # ---- Scroll the output window without moving keyboard focus off input ----
    #
    # PgUp/PgDn  — page scroll (uses the visible window height)
    # Home/End   — jump to top / bottom (End also re-enables autoscroll)
    # c-up/c-down — single-line scroll (Ctrl-Up / Ctrl-Down)
    # Mouse wheel is handled by prompt_toolkit automatically because the
    # output window is now focusable, but we also honor scroll events via
    # the Window's built-in wheel handler when mouse_support=True.
    def _win_height(w) -> int:
        """Best-effort current render height; fall back to a sane default."""
        try:
            info = w.render_info
            if info is not None:
                return max(1, info.window_height)
        except Exception:
            pass
        return 20

    def _scroll(win, delta: int) -> None:
        if win is None:
            return
        cur = getattr(win, "vertical_scroll", 0) or 0
        win.vertical_scroll = max(0, cur + delta)
        # A negative delta = scrolling up, so user is looking at older output;
        # suppress autoscroll until they jump back to the bottom.
        if delta < 0:
            state.output_scroll_locked = True

    @kb.add("pageup")
    def _scroll_pageup(event):  # type: ignore[no-untyped-def]
        w = state.output_window
        _scroll(w, -max(1, _win_height(w) - 2))

    @kb.add("pagedown")
    def _scroll_pagedown(event):  # type: ignore[no-untyped-def]
        w = state.output_window
        _scroll(w, max(1, _win_height(w) - 2))

    @kb.add("c-up")
    def _scroll_line_up(event):  # type: ignore[no-untyped-def]
        _scroll(state.output_window, -1)

    @kb.add("c-down")
    def _scroll_line_down(event):  # type: ignore[no-untyped-def]
        _scroll(state.output_window, 1)

    @kb.add("home")
    def _scroll_home(event):  # type: ignore[no-untyped-def]
        w = state.output_window
        if w is not None:
            w.vertical_scroll = 0
            state.output_scroll_locked = True

    @kb.add("end")
    def _scroll_end(event):  # type: ignore[no-untyped-def]
        # Jumping to the end re-enables autoscroll so new output tracks again.
        w = state.output_window
        if w is not None:
            # A big number beyond the document; prompt_toolkit clamps to max.
            w.vertical_scroll = 10_000_000
            state.output_scroll_locked = False

    @kb.add("c-g")
    def _open_editor(event):  # type: ignore[no-untyped-def]
        """Open $EDITOR with the current input buffer for long-form editing."""
        editor = os.environ.get("EDITOR", "vim")
        with _tempfile_mod.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
            f.write(event.app.current_buffer.text)
            f.flush()
            path = f.name
        event.app.suspend_to_background()
        _subprocess_mod.call([editor, path])  # noqa: S603
        with open(path) as f:
            event.app.current_buffer.text = f.read()
        os.unlink(path)

    # Build the application
    app = _build_application(writer, input_buffer, kb, state, config)
    app_ref[0] = app

    # Wire up invalidation so output changes trigger redraws. Also
    # autoscroll the output window to the bottom on every new chunk
    # unless the user has explicitly scrolled up (output_scroll_locked).
    # Once locked, new output accumulates off-screen until the user
    # presses End (or scrolls back to the bottom manually) — matches
    # the convention in every TUI that handles long logs.
    def _invalidate_and_autoscroll() -> None:
        if not state.output_scroll_locked and state.output_window is not None:
            # Large sentinel; prompt_toolkit clamps to the document end.
            state.output_window.vertical_scroll = 10_000_000
        app.invalidate()

    writer.set_invalidate(_invalidate_and_autoscroll)

    # Periodic status bar refresh (face ticker, duration timer, context bar)
    async def _refresh_status_bar() -> None:
        while True:
            await asyncio.sleep(0.5)
            try:
                if app.is_running:
                    app.invalidate()
            except Exception:  # noqa: BLE001
                break

    app.create_background_task(_refresh_status_bar())

    # Run the application
    try:
        await app.run_async()
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        # Cancel any running agent task on exit
        if state.agent_task is not None and not state.agent_task.done():
            state.agent_task.cancel()
            try:
                await state.agent_task
            except (asyncio.CancelledError, Exception):
                pass
