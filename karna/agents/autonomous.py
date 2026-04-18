"""Autonomous repeat-until-done agent loop.

This module implements the ``/loop`` slash command's semantics:
run the regular tool-use agent in an outer wrapper that keeps
re-prompting the model until it declares the goal achieved, or a
max-cycle cap is hit, or the user interrupts.

Each outer *cycle* does three things:

    1. Runs the normal :func:`karna.agents.loop.agent_loop_sync`
       with a prompt that combines the original goal and a running
       summary of what the agent has done so far.
    2. Captures the assistant's final text as that cycle's summary.
    3. Asks the model — with *no* tools and a targeted prompt —
       whether the goal is achieved. The model must reply with
       either ``DONE`` or ``NOT_YET <reason>``.

When ``DONE`` appears (case-insensitive), the outer loop breaks
and the final assistant message is returned. Otherwise the reason
is fed back into the next cycle as additional context.

This is intentionally a thin wrapper over the existing agent loop:
the inner loop already handles tool execution, permission checks,
safety guards, retries, and context truncation.  We only add the
outer termination-check + re-prompt scaffolding here.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from karna.agents.loop import agent_loop_sync
from karna.models import Conversation, Message
from karna.providers.base import BaseProvider
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)

# Sentinel the model is asked to emit when the goal is complete.
_DONE_MARKER = "DONE"
_NOT_YET_MARKER = "NOT_YET"

# The termination-check prompt. Kept tight so cheap models can comply.
_CHECK_PROMPT = (
    "Review the work you've done above against the original goal:\n"
    "\n"
    "  Goal: {goal}\n"
    "\n"
    "Has the goal been fully achieved? Reply with EXACTLY one of:\n"
    f"  - {_DONE_MARKER}          (if the goal is fully met)\n"
    f"  - {_NOT_YET_MARKER} <reason>  (if more work is needed; give a one-line reason)\n"
    "\n"
    "Do not call any tools. Do not elaborate. One line only."
)


def _build_cycle_prompt(goal: str, prior_summary: str | None, reason: str | None) -> str:
    """Assemble the user prompt for an outer cycle.

    On the first cycle *prior_summary* and *reason* are both ``None``
    and we just send the raw goal. On subsequent cycles we prepend a
    compact "so far you've done" recap plus the last checker reason
    so the model knows why it's being re-prompted.
    """
    if prior_summary is None and reason is None:
        return goal

    parts: list[str] = [f"Goal: {goal}", ""]
    if prior_summary:
        # Keep the summary bounded — the inner loop already persists
        # the full history, this is just a nudge.
        trimmed = prior_summary.strip()
        if len(trimmed) > 2000:
            trimmed = trimmed[:2000] + "..."
        parts.append("So far you've done:")
        parts.append(trimmed)
        parts.append("")
    if reason:
        parts.append(f"Reason the goal is not yet met: {reason}")
        parts.append("")
    parts.append("Continue working toward the goal.")
    return "\n".join(parts)


async def _ask_if_done(
    provider: BaseProvider,
    conversation: Conversation,
    goal: str,
    *,
    system_prompt: str | None,
) -> tuple[bool, str]:
    """Ask the model whether *goal* is complete.

    Returns ``(is_done, reason)``.  ``reason`` is the model's
    free-text justification when ``NOT_YET`` is emitted (empty
    string on ``DONE``).

    The check runs with tools=[] so the model can't side-effect
    during the termination probe.
    """
    probe_messages = list(conversation.messages)
    probe_messages.append(Message(role="user", content=_CHECK_PROMPT.format(goal=goal)))

    reply = await provider.complete(
        probe_messages,
        tools=[],
        system_prompt=system_prompt,
        max_tokens=64,
        temperature=0.0,
    )
    text = (reply.content or "").strip()
    upper = text.upper()

    if upper.startswith(_DONE_MARKER):
        return True, ""
    if upper.startswith(_NOT_YET_MARKER):
        reason = text[len(_NOT_YET_MARKER) :].lstrip(" :-").strip()
        return False, reason or "no reason given"
    # Unrecognised reply — treat as not-yet with the raw text as the reason.
    return False, text or "checker reply was empty"


async def run_autonomous_loop(
    goal: str,
    provider: BaseProvider,
    tools: list[BaseTool],
    model: str,
    *,
    max_cycles: int = 10,
    system_prompt: str | None = None,
    max_iterations_per_cycle: int = 25,
    on_cycle_complete: Callable[[int, str], None] | Callable[[int, str], Awaitable[None]] | None = None,
) -> str:
    """Run agent cycles until the goal is met or *max_cycles* is reached.

    Each cycle:

      1. Builds a prompt combining the original goal and a running
         summary of prior work.
      2. Runs the normal :func:`agent_loop_sync` on that prompt.
      3. Asks the model whether the goal is now achieved (``DONE``
         or ``NOT_YET <reason>``).
      4. If ``DONE`` → break and return the final assistant text.
         Otherwise feed the reason into the next cycle.

    Parameters
    ----------
    goal:
        The user's top-level goal (the text after ``/loop``).
    provider:
        Initialised provider whose ``model`` attribute has already
        been set to *model*.
    tools:
        Full tool set — the inner loop runs with every tool the
        user has available.
    model:
        Model identifier (stored for caller telemetry; the provider
        instance is expected to be configured already).
    max_cycles:
        Hard cap on outer cycles. Defaults to 10.
    system_prompt:
        Optional system prompt forwarded to the inner agent loop and
        the done-checker.
    max_iterations_per_cycle:
        Forwarded to the inner loop's own iteration cap.
    on_cycle_complete:
        Optional callback invoked after every cycle with
        ``(cycle_index, summary_text)``. Sync or async. Used by the
        TUI to stream per-cycle progress.

    Returns
    -------
    str
        The final assistant text from whichever cycle terminated
        the loop (either a successful ``DONE`` cycle or the last
        cycle when ``max_cycles`` was hit).
    """
    # NOTE: we keep *model* as a parameter even though we don't use
    # it directly — callers pass it for logging / telemetry and the
    # public signature in the spec says so.
    _ = model

    conversation = Conversation()
    last_summary: str = ""
    last_reason: str | None = None

    for cycle in range(max_cycles):
        prompt = _build_cycle_prompt(
            goal,
            prior_summary=last_summary if cycle > 0 else None,
            reason=last_reason,
        )
        conversation.messages.append(Message(role="user", content=prompt))

        logger.debug("autonomous cycle %d/%d starting", cycle + 1, max_cycles)
        final: Message = await agent_loop_sync(
            provider,
            conversation,
            tools,
            system_prompt=system_prompt,
            max_iterations=max_iterations_per_cycle,
        )
        last_summary = final.content or ""

        # Notify the observer (best-effort; exceptions from the
        # callback shouldn't kill the loop).
        if on_cycle_complete is not None:
            try:
                result = on_cycle_complete(cycle + 1, last_summary)
                if hasattr(result, "__await__"):
                    await result  # type: ignore[misc]
            except Exception:  # pragma: no cover - observer errors are non-fatal
                logger.exception("on_cycle_complete callback raised")

        # Termination check — ask the model whether the goal is done.
        try:
            done, reason = await _ask_if_done(
                provider,
                conversation,
                goal,
                system_prompt=system_prompt,
            )
        except Exception as exc:
            logger.warning("done-check failed on cycle %d: %s", cycle + 1, exc)
            # If the checker crashes we treat it as not-yet so the
            # outer cap (max_cycles) still protects us from runaways.
            done, reason = False, f"done-check error: {exc}"

        if done:
            logger.info("autonomous loop finished on cycle %d/%d", cycle + 1, max_cycles)
            return last_summary
        last_reason = reason

    logger.info("autonomous loop hit max_cycles=%d without DONE", max_cycles)
    return last_summary or f"[max cycles reached: {max_cycles}]"


__all__ = ["run_autonomous_loop"]
