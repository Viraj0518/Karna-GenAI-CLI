"""Main REPL loop for the Karna TUI.

Launched by ``nellie`` (no args) — provides streaming conversation with
tool use, slash commands, multiline input, and Rich-rendered output.

Supports always-active input: the user can type at any time, even while
the agent is streaming.  If the agent is idle, Enter starts a new turn.
If the agent is working, the message is queued and injected mid-stream
as a steering message — Claude Code-style UX.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from karna.compaction.compactor import Compactor

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.patch_stdout import patch_stdout
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
from karna.tui.banner import print_banner
from karna.tui.input import _bottom_toolbar_factory, _format_prompt, _pt_style
from karna.tui.output import EventKind, OutputRenderer, StreamEvent
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


# --------------------------------------------------------------------------- #
#  Always-active input state
# --------------------------------------------------------------------------- #


class REPLState:
    """Shared mutable state between the input loop and agent tasks."""

    def __init__(self) -> None:
        self.input_queue: asyncio.Queue[str] = asyncio.Queue()
        self.agent_running: bool = False
        self.agent_task: asyncio.Task | None = None


# --------------------------------------------------------------------------- #
#  Dispatch helpers (unchanged)
# --------------------------------------------------------------------------- #


async def _run_loop_mode(
    console: Console,
    config: KarnaConfig,
    goal: str,
) -> str:
    """Dispatch ``/loop`` — run the autonomous repeat-until-done agent.

    Uses a fresh provider+tools bundle so the outer cycles don't share
    cumulative usage state with the main REPL provider instance.
    """
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
    """Dispatch ``/plan`` — run plan mode and return the plan text."""
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
    """Yield TUI StreamEvents live from the agent loop.

    This is an async generator -- each event is yielded immediately as
    the underlying provider produces it, enabling true real-time
    streaming of thinking and text deltas to the renderer.
    """
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

        # Build system prompt (with skills if available)
        system_prompt = build_system_prompt(config, tools, skill_manager=skill_manager)

        # Re-use the caller-supplied compactor so the circuit breaker
        # persists across REPL turns.  Fall back to creating one if
        # the caller didn't supply it (e.g. non-REPL callers).
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
            # Map agent StreamEvent → TUI StreamEvent and yield immediately
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
#  Agent turn — runs as a concurrent task
# --------------------------------------------------------------------------- #


async def _run_agent_turn(
    state: REPLState,
    config: KarnaConfig,
    conversation: Conversation,
    console: Console,
    session_db: SessionDB,
    session_id: str,
    session_cost: SessionCost,
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
            renderer.handle(event)
            events.append(event)

            # Accumulate session cost
            if event.kind == EventKind.USAGE and isinstance(event.data, dict):
                session_cost.add(
                    prompt=event.data.get("prompt_tokens", 0),
                    completion=event.data.get("completion_tokens", 0),
                    usd=event.data.get("total_usd", 0.0),
                )

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

    # Persist assistant reply to conversation and session DB
    full_reply = "".join(e.data for e in events if e.kind == EventKind.TEXT_DELTA and isinstance(e.data, str))
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
    """Handle a slash command and return user_input to send (or None to skip).

    Returns:
        - A string to treat as user input (e.g. /do injects the plan)
        - None if the command was fully handled (no message to send)
    """
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

    # /paste returns raw text — treat as user input
    if isinstance(result, str) and result:
        return result

    return None


# --------------------------------------------------------------------------- #
#  Main REPL — always-active input with concurrent agent execution
# --------------------------------------------------------------------------- #


async def run_repl(
    config: KarnaConfig,
    resume_conversation: Conversation | None = None,
    resume_session_id: str | None = None,
) -> None:
    """Main REPL — streaming conversation with always-active input.

    The user can always type, even while the agent is working:
    - If the agent is idle, Enter starts a new turn.
    - If the agent is working, the message is queued and injected
      mid-stream as a steering message.

    Uses ``prompt_toolkit``'s ``prompt_async`` with ``patch_stdout``
    so the input prompt stays at the bottom while agent output scrolls
    above it.
    """
    console = Console(theme=KARNA_THEME)
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
    session_cost = SessionCost()

    # Load skills from ~/.karna/skills/ and .karna/skills/
    skill_manager = SkillManager()
    skill_manager.load_all()
    # Also load project-local skills if present
    local_skills_dir = Path.cwd() / ".karna" / "skills"
    if local_skills_dir.is_dir():
        local_mgr = SkillManager(skills_dir=local_skills_dir)
        local_mgr.load_all()
        for skill in local_mgr.skills:
            if not skill_manager.get_skill_by_name(skill.name):
                skill_manager.skills.append(skill)

    print_banner(console, config, tool_names)

    # Create a single Compactor that persists across REPL turns so the
    # circuit breaker's consecutive_failures counter is not reset each turn.
    from karna.compaction.compactor import Compactor

    _init_prov_name, _init_model = resolve_model(
        f"{config.active_provider}:{config.active_model}"
        if ":" not in (config.active_model or "")
        else config.active_model
    )
    _init_provider = get_provider(_init_prov_name)
    _init_provider.model = _init_model
    repl_compactor = Compactor(_init_provider, threshold=0.80)

    # Build the display prompt from the model name
    model_short = config.active_model.split("/")[-1] if "/" in config.active_model else config.active_model
    if len(model_short) > 24:
        model_short = model_short[:21] + "..."
    prompt_str = f"{model_short}> "

    # Build the prompt_toolkit session for async prompting
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings

    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _insert_newline(event):  # type: ignore[no-untyped-def]
        event.current_buffer.insert_text("\n")

    pt_session: PromptSession[str] = PromptSession(
        message=_format_prompt(prompt_str),
        history=InMemoryHistory(),
        key_bindings=bindings,
        multiline=False,
        enable_history_search=True,
        style=_pt_style(),
        placeholder=HTML("<placeholder>Ask anything...</placeholder>"),
        bottom_toolbar=_bottom_toolbar_factory(),
        include_default_pygments_style=False,
    )

    # ── Main input loop — always active ───────────────────────────────
    with patch_stdout():
        while True:
            try:
                text = await pt_session.prompt_async(
                    _format_prompt(prompt_str),
                )
            except EOFError:
                console.print("\n[bright_black]Goodbye.[/bright_black]")
                # Cancel any running agent task
                if state.agent_task is not None and not state.agent_task.done():
                    state.agent_task.cancel()
                    try:
                        await state.agent_task
                    except (asyncio.CancelledError, Exception):
                        pass
                break
            except KeyboardInterrupt:
                console.print()
                continue

            text = text.strip()
            if not text:
                continue

            # ── If agent is running, queue as steering message ─────────
            if state.agent_running:
                # Slash commands still work immediately even during agent execution
                if text.startswith("/"):
                    if text.strip() in ("/exit", "/quit"):
                        if state.agent_task is not None and not state.agent_task.done():
                            state.agent_task.cancel()
                            try:
                                await state.agent_task
                            except (asyncio.CancelledError, Exception):
                                pass
                        console.print("\n[bright_black]Goodbye.[/bright_black]")
                        return
                    # Other slash commands during agent run — warn
                    console.print("[yellow]Agent is working. Slash commands are available when idle.[/yellow]")
                    continue

                state.input_queue.put_nowait(text)
                console.print("[bright_black]  -> message queued (will be injected mid-stream)[/bright_black]")
                continue

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
                # Skills matched — skip slash command handling, fall through
                # to the regular message path below.

            # ── Slash commands ─────────────────────────────────────────
            elif user_input.startswith("/"):
                injected = await _process_slash_command(
                    user_input,
                    console,
                    config,
                    conversation,
                    session_cost,
                    tool_names,
                    session_db,
                    cost_tracker,
                    skill_manager,
                )
                # Refresh prompt in case the model was switched
                model_short = config.active_model.split("/")[-1] if "/" in config.active_model else config.active_model
                if len(model_short) > 24:
                    model_short = model_short[:21] + "..."
                prompt_str = f"{model_short}> "

                if injected is not None:
                    # /do or /paste returned text — treat as user input
                    user_input = injected
                else:
                    continue

            # ── Skill trigger matching for non-slash ───────────────────
            matched_skills = skill_manager.match_trigger(user_input) if not user_input.startswith("/") else []
            if matched_skills:
                skill_preamble_parts = []
                for skill in matched_skills:
                    if skill.instructions:
                        skill_preamble_parts.append(f"[Skill: {skill.name}]\n{skill.instructions}")
                if skill_preamble_parts:
                    skill_preamble = "\n\n".join(skill_preamble_parts)
                    user_input = f"{skill_preamble}\n\n---\n\n{user_input}"

            # ── Start new agent turn ───────────────────────────────────
            user_msg = Message(role="user", content=user_input)
            conversation.messages.append(user_msg)
            session_db.add_message(session_id, user_msg)

            state.agent_running = True
            state.agent_task = asyncio.create_task(
                _run_agent_turn(
                    state,
                    config,
                    conversation,
                    console,
                    session_db,
                    session_id,
                    session_cost,
                    skill_manager,
                    repl_compactor,
                    tool_names,
                )
            )
