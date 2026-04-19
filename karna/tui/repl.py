"""Main REPL loop for the Karna TUI.

Launched by ``nellie`` (no args) — provides streaming conversation with
tool use, slash commands, multiline input, and Rich-rendered output.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from karna.compaction.compactor import Compactor

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
from karna.tui.input import get_multiline_input
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


async def _agent_loop(
    config: KarnaConfig,
    conversation: Conversation,
    tool_names: list[str],
    skill_manager: SkillManager | None = None,
    compactor: "Compactor | None" = None,
) -> list[StreamEvent]:
    """Run a single turn of the agent loop — LIVE provider + tool execution."""
    from karna.compaction.compactor import Compactor

    events: list[StreamEvent] = []

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
            # Map agent StreamEvent → TUI StreamEvent
            if event.type == "text":
                events.append(StreamEvent(kind=EventKind.TEXT_DELTA, data=event.text or ""))
            elif event.type == "tool_call_start":
                tc = event.tool_call
                events.append(
                    StreamEvent(
                        kind=EventKind.TOOL_CALL_START,
                        data={"name": tc.name if tc else "?", "arguments": tc.arguments if tc else "{}"},
                    )
                )
            elif event.type == "tool_call_end":
                tc = event.tool_call
                # Emit TOOL_CALL_END so the renderer closes the live
                # spinner before we send the result.
                events.append(StreamEvent(kind=EventKind.TOOL_CALL_END))
                events.append(
                    StreamEvent(
                        kind=EventKind.TOOL_RESULT,
                        data={"content": event.text or (tc.arguments if tc else ""), "is_error": False},
                    )
                )
            elif event.type == "tool_call_delta":
                pass  # accumulating, not rendered until tool_call_end
            elif event.type == "error":
                events.append(StreamEvent(kind=EventKind.ERROR, data=event.text or "Unknown error"))
            elif event.type == "done":
                if event.usage:
                    events.append(
                        StreamEvent(
                            kind=EventKind.USAGE,
                            data={
                                "prompt_tokens": event.usage.input_tokens,
                                "completion_tokens": event.usage.output_tokens,
                                "total_usd": event.usage.cost_usd or 0.0,
                            },
                        )
                    )
                events.append(StreamEvent(kind=EventKind.DONE))

    except Exception as e:
        events.append(StreamEvent(kind=EventKind.ERROR, data=f"Agent error: {type(e).__name__}: {e}"))
        events.append(StreamEvent(kind=EventKind.DONE))

    return events


async def run_repl(
    config: KarnaConfig,
    resume_conversation: Conversation | None = None,
    resume_session_id: str | None = None,
) -> None:
    """Main REPL — streaming conversation with tool use.

    This is the primary interactive loop invoked by ``nellie`` with no
    arguments.  It:

    1. Prints the startup banner.
    2. Reads user input (multiline, with history).
    3. Dispatches slash commands.
    4. Streams assistant responses through the OutputRenderer.
    5. Loops until Ctrl-D or /exit.
    """
    console = Console(theme=KARNA_THEME)
    tool_names = _load_tool_names()

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

    # Resolve provider once for the compactor — it will be re-resolved
    # per-turn inside _agent_loop, but the Compactor only needs a
    # provider for its summarisation calls.
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

    while True:
        try:
            user_input = await get_multiline_input(console, prompt_str)
        except EOFError:
            console.print("\n[bright_black]Goodbye.[/bright_black]")
            break
        except KeyboardInterrupt:
            console.print()
            continue

        if not user_input:
            continue

        # ── Slash commands ──────────────────────────────────────────────
        if user_input.startswith("/"):
            result = handle_slash_command(
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
            # Refresh prompt in case the model was switched
            model_short = config.active_model.split("/")[-1] if "/" in config.active_model else config.active_model
            if len(model_short) > 24:
                model_short = model_short[:21] + "..."
            prompt_str = f"{model_short}> "

            # Advanced-command sentinels — these return a payload that
            # the REPL (not the slash handler) executes.
            if isinstance(result, str) and result.startswith(_LOOP_SENTINEL):
                goal = result[len(_LOOP_SENTINEL) :]
                try:
                    final_text = await _run_loop_mode(console, config, goal)
                except KeyboardInterrupt:
                    console.print("\n[yellow]Loop interrupted by user.[/yellow]")
                    continue
                except Exception as exc:
                    console.print(f"[red]Loop failed: {type(exc).__name__}: {exc}[/red]")
                    continue
                if final_text:
                    console.print(final_text)
                continue

            if isinstance(result, str) and result.startswith(_PLAN_SENTINEL):
                goal = result[len(_PLAN_SENTINEL) :]
                try:
                    plan_text = await _run_plan_mode(console, config, goal)
                except Exception as exc:
                    console.print(f"[red]Plan mode failed: {type(exc).__name__}: {exc}[/red]")
                    continue
                if plan_text:
                    console.print(plan_text)
                    _store_last_plan(conversation, plan_text)
                continue

            if isinstance(result, str) and result.startswith(_DO_SENTINEL):
                # /do → execute the stored plan by injecting it as the
                # next user message and falling through to the normal
                # agent-loop path below.
                plan_text = result[len(_DO_SENTINEL) :]
                clear_last_plan(conversation)
                user_input = plan_text
                # Fall through to the "Regular message" block below.
            else:
                # Any other (non-sentinel) slash result — including the
                # string returned by /paste — should be treated as user
                # input. /paste historically returned raw text.
                if isinstance(result, str) and result:
                    user_input = result
                else:
                    continue

        # ── Skill trigger matching ──────────────────────────────────────────
        # Check if user input matches any skill triggers. If so, prepend
        # the skill instructions to the user message so the LLM follows
        # the skill's workflow for this turn.
        matched_skills = skill_manager.match_trigger(user_input)
        if matched_skills:
            skill_preamble_parts: list[str] = []
            for skill in matched_skills:
                if skill.instructions:
                    skill_preamble_parts.append(f"[Skill: {skill.name}]\n{skill.instructions}")
            if skill_preamble_parts:
                skill_preamble = "\n\n".join(skill_preamble_parts)
                user_input = f"{skill_preamble}\n\n---\n\n{user_input}"

        # ── Regular message ─────────────────────────────────────────────
        user_msg = Message(role="user", content=user_input)
        conversation.messages.append(user_msg)
        session_db.add_message(session_id, user_msg)

        renderer = OutputRenderer(console)
        renderer.show_spinner()

        try:
            events = await _agent_loop(
                config,
                conversation,
                tool_names,
                skill_manager=skill_manager,
                compactor=repl_compactor,
            )
            for event in events:
                renderer.handle(event)

                # Accumulate session cost
                if event.kind == EventKind.USAGE and isinstance(event.data, dict):
                    session_cost.add(
                        prompt=event.data.get("prompt_tokens", 0),
                        completion=event.data.get("completion_tokens", 0),
                        usd=event.data.get("total_usd", 0.0),
                    )
        except Exception as exc:
            renderer.handle(StreamEvent(kind=EventKind.ERROR, data=str(exc)))
        finally:
            renderer.finish()

        # Append assistant reply to conversation and persist
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
