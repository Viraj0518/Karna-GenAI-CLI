"""Main REPL loop for the Karna TUI.

Launched by ``nellie`` (no args) — provides streaming conversation with
tool use, slash commands, multiline input, and Rich-rendered output.
"""

from __future__ import annotations

import os

from rich.console import Console

from karna.config import KarnaConfig
from karna.models import Conversation, Message
from karna.sessions.db import SessionDB
from karna.sessions.cost import CostTracker
from karna.tools import TOOLS
from karna.tui.banner import print_banner
from karna.tui.input import get_multiline_input
from karna.tui.output import EventKind, OutputRenderer, StreamEvent
from karna.tui.slash import SessionCost, handle_slash_command
from karna.tui.themes import KARNA_THEME


def _load_tool_names() -> list[str]:
    """Return the names of all registered tools."""
    return sorted(TOOLS.keys())


async def _agent_loop(
    config: KarnaConfig,
    conversation: Conversation,
    tools: list[str],
) -> list[StreamEvent]:
    """Run a single turn of the agent loop.

    In later phases this will call the actual provider streaming API and
    handle tool-use cycles.  For now it returns a placeholder response
    so the REPL can be exercised end-to-end.
    """
    # Phase 2B stub — will be wired to real providers in Phase 3
    events: list[StreamEvent] = [
        StreamEvent(kind=EventKind.TEXT_DELTA, data="I'm **Karna**, your AI assistant. "),
        StreamEvent(
            kind=EventKind.TEXT_DELTA,
            data=f"Provider `{config.active_provider}` streaming is not yet wired — coming in Phase 3.",
        ),
        StreamEvent(
            kind=EventKind.USAGE,
            data={"prompt_tokens": 0, "completion_tokens": 0, "total_usd": 0.0},
        ),
        StreamEvent(kind=EventKind.DONE),
    ]
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
                capture_output=True, text=True, timeout=2,
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

    print_banner(console, config, tool_names)

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
            handle_slash_command(
                user_input,
                console,
                config,
                conversation,
                session_cost=session_cost,
                tool_names=tool_names,
                session_db=session_db,
                cost_tracker=cost_tracker,
            )
            # Refresh prompt in case the model was switched
            model_short = config.active_model.split("/")[-1] if "/" in config.active_model else config.active_model
            if len(model_short) > 24:
                model_short = model_short[:21] + "..."
            prompt_str = f"{model_short}> "
            continue

        # ── Regular message ─────────────────────────────────────────────
        user_msg = Message(role="user", content=user_input)
        conversation.messages.append(user_msg)
        session_db.add_message(session_id, user_msg)

        renderer = OutputRenderer(console)
        renderer.show_spinner()

        try:
            events = await _agent_loop(config, conversation, tool_names)
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
        full_reply = "".join(
            e.data for e in events if e.kind == EventKind.TEXT_DELTA and isinstance(e.data, str)
        )
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
                console.print(RichText(
                    f"  [{turn_tokens:,} tokens, ${turn_cost:.4f}]",
                    style="bright_black",
                ))
