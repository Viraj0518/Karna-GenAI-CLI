"""Auto-compact conversations when they exceed a token budget.

Strategy
--------
1. Estimate the token usage of the full conversation via
   ``karna.tokens.count_tokens``.
2. If usage is under 80% of the configured budget, return the
   conversation unchanged — compaction is load-bearing only when the
   window is actually tight.
3. Otherwise split the transcript into three zones:
       [ head | middle | tail ]
   The head holds the anchor context (system prompt + first user
   turn), the tail holds the most recent back-and-forth so the model
   retains short-term coherence, and the middle is what we summarise.
4. Format the middle zone as plain text, run it through
   ``scrub_secrets`` (so any API key that leaked into a tool result
   doesn't get shipped off-server), and ask the LLM for a structured
   summary bounded by ``summary_budget`` tokens.
5. Replace the middle with a single system message:
       "[Compacted summary of N earlier turns]: ..."
6. Retries: three attempts with a short backoff. On exhaustion raise
   ``CompactionError`` — silently returning the unchanged conversation
   would let the agent loop spin forever against a context-overflow.

Secret scrubbing happens **before** the provider call, not after, so
leaked credentials never leave the host.

Portions adapted from cc-src ``autoCompact.ts`` and hermes-agent
``trajectory_compressor.py``. See NOTICES.md.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from karna.compaction.prompts import COMPACT_SYSTEM_PROMPT, SUMMARY_PROMPT
from karna.models import Conversation, Message
from karna.security import scrub_secrets
from karna.tokens import count_tokens

if TYPE_CHECKING:
    from karna.providers.base import BaseProvider

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3
_COMPACT_TRIGGER_FRACTION = 0.80


class CompactionError(RuntimeError):
    """Raised when compaction exhausts its retry budget.

    Surface this to the user — do not swallow it. Silently returning
    the unchanged conversation traps the agent loop in a
    context-overflow cycle where every subsequent provider call fails
    the same way.
    """


# ---------------------------------------------------------------------- #
#  Token accounting
# ---------------------------------------------------------------------- #


def _message_tokens(msg: Message, model: str) -> int:
    """Estimate tokens for a single message, including tool traffic."""
    total = 4  # per-message framing overhead
    total += count_tokens(msg.content or "", model)
    for tc in msg.tool_calls:
        total += count_tokens(tc.name, model)
        total += count_tokens(str(tc.arguments), model)
    for tr in msg.tool_results:
        total += count_tokens(tr.content or "", model)
    return total


def _conv_tokens(messages: list[Message], model: str) -> int:
    return sum(_message_tokens(m, model) for m in messages)


def should_compact(conv: Conversation, budget: int, model: str = "") -> bool:
    """True when the conversation is above the compaction trigger."""
    if budget <= 0:
        return False
    return _conv_tokens(conv.messages, model) > int(budget * _COMPACT_TRIGGER_FRACTION)


# ---------------------------------------------------------------------- #
#  Formatting
# ---------------------------------------------------------------------- #


def _format_middle(messages: list[Message]) -> str:
    """Render a list of middle-zone messages as a flat transcript.

    Long messages and tool results are truncated so the summariser
    prompt stays manageable — the summary's job is to compress, not to
    faithfully reproduce.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.role.upper()
        content = msg.content or ""
        if len(content) > 3000:
            content = content[:1500] + "\n...[truncated]...\n" + content[-500:]
        parts.append(f"[{role}]: {content}")
        for tr in msg.tool_results:
            text = tr.content or ""
            if len(text) > 2000:
                text = text[:1000] + "\n...[truncated]...\n" + text[-500:]
            status = "ERROR" if tr.is_error else "OK"
            parts.append(f"  [TOOL RESULT ({status})]: {text}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------- #
#  Main entry point
# ---------------------------------------------------------------------- #


async def auto_compact(
    conversation: Conversation,
    provider: "BaseProvider",
    model: str,
    *,
    budget_tokens: int,
    summary_budget: int = 800,
    head_turns_to_keep: int = 2,
    tail_turns_to_keep: int = 8,
) -> Conversation:
    """Compact ``conversation`` when it exceeds ``budget_tokens``.

    See module docstring for the algorithm. Returns the (possibly
    unchanged) conversation.

    Raises ``CompactionError`` if three consecutive summariser calls
    fail — callers should surface this to the user rather than retry.
    """
    if budget_tokens <= 0:
        return conversation

    used = _conv_tokens(conversation.messages, model)
    threshold = int(budget_tokens * _COMPACT_TRIGGER_FRACTION)
    if used <= threshold:
        return conversation

    messages = conversation.messages
    if len(messages) <= head_turns_to_keep + tail_turns_to_keep:
        # Not enough middle to compact.
        return conversation

    head = messages[:head_turns_to_keep]
    tail = messages[-tail_turns_to_keep:] if tail_turns_to_keep > 0 else []
    middle = messages[head_turns_to_keep : len(messages) - tail_turns_to_keep]
    if not middle:
        return conversation

    # Scrub BEFORE sending off-host — if a tool result echoed an API
    # key into the transcript, we don't want to hand it to the
    # summariser provider.
    formatted = scrub_secrets(_format_middle(middle))
    summary_prompt = SUMMARY_PROMPT.format(messages=formatted)

    summary_text: str | None = None
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = await provider.complete(
                [
                    Message(role="system", content=COMPACT_SYSTEM_PROMPT),
                    Message(role="user", content=summary_prompt),
                ],
                tools=None,
                max_tokens=summary_budget,
                temperature=0.3,
            )
            text = (response.content or "").strip()
            if not text:
                raise ValueError("Provider returned empty summary")
            summary_text = text
            break
        except Exception as exc:  # noqa: BLE001 — retry any failure
            last_exc = exc
            logger.warning(
                "Compaction attempt %d/%d failed: %s",
                attempt,
                _MAX_ATTEMPTS,
                exc,
            )
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(0.5 * attempt)

    if summary_text is None:
        raise CompactionError(f"Compaction failed after {_MAX_ATTEMPTS} attempts: {last_exc}") from last_exc

    summary_msg = Message(
        role="system",
        content=f"[Compacted summary of {len(middle)} earlier turns]: {summary_text}",
    )
    conversation.messages = list(head) + [summary_msg] + list(tail)
    return conversation


# ---------------------------------------------------------------------- #
#  Backwards-compat class (pre-existing callers)
# ---------------------------------------------------------------------- #


class Compactor:
    """Threshold-based auto-compactor — legacy class-style API.

    The functional ``auto_compact`` is preferred for new code; this
    wrapper exists so earlier integration points (the agent loop,
    sessions persistence) keep working without churn.
    """

    def __init__(
        self,
        provider: "BaseProvider",
        threshold: float = 0.93,
    ) -> None:
        self.provider = provider
        self.threshold = threshold
        self.consecutive_failures = 0
        self.max_failures = _MAX_ATTEMPTS

    @property
    def circuit_breaker_tripped(self) -> bool:
        return self.consecutive_failures >= self.max_failures

    async def should_compact(
        self,
        messages: list[Message],
        context_window: int,
    ) -> bool:
        if self.circuit_breaker_tripped or not messages:
            return False
        used = _conv_tokens(messages, "")
        return used > int(context_window * self.threshold)

    async def compact(
        self,
        conversation: Conversation,
        context_window: int,
    ) -> Conversation:
        budget = int(context_window * self.threshold)
        try:
            result = await auto_compact(
                conversation,
                self.provider,
                model="",
                budget_tokens=budget,
            )
            self.consecutive_failures = 0
            return result
        except CompactionError:
            self.consecutive_failures = self.max_failures
            raise


__all__ = ["auto_compact", "should_compact", "CompactionError", "Compactor"]
