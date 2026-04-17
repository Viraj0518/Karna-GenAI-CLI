"""Auto-compact conversation when context hits threshold.

Strategy (ported from cc-src autoCompact.ts + hermes-agent trajectory_compressor.py):

1. Trigger when estimated tokens > threshold percentage of context window
2. Take oldest N messages (keeping system prompt + last 5 messages)
3. Send to the same provider with the summarization prompt
4. Replace oldest messages with a single summary message
5. Circuit breaker: if 3 consecutive compaction failures, stop trying

See NOTICES.md for attribution.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from karna.compaction.prompts import COMPACT_SYSTEM_PROMPT, SUMMARY_PROMPT
from karna.models import Conversation, Message
from karna.security import scrub_secrets

if TYPE_CHECKING:
    from karna.providers.base import BaseProvider

logger = logging.getLogger(__name__)

# Number of recent messages to always preserve (never summarized)
_PRESERVE_TAIL = 5

# Max consecutive failures before the circuit breaker trips
_MAX_CONSECUTIVE_FAILURES = 3


class CompactionError(RuntimeError):
    """Raised when conversation compaction fails.

    The agent loop should surface this as a user-visible error so the
    user knows context is overflowing and can start a new conversation
    or compact manually.  Swallowing these failures silently leaves the
    user trapped in an infinite context-overflow loop.
    """


def _estimate_tokens(messages: list[Message]) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    total = 0
    for m in messages:
        total += len(m.content)
        for tr in m.tool_results:
            total += len(tr.content)
        for tc in m.tool_calls:
            # Count serialized arguments roughly
            total += len(str(tc.arguments))
    return total // 4


def _format_messages_for_summary(messages: list[Message]) -> str:
    """Format a list of messages into a text block for the summarization prompt."""
    parts: list[str] = []
    for i, msg in enumerate(messages):
        role = msg.role.upper()
        content = msg.content
        # Truncate very long messages to keep the summary prompt manageable
        if len(content) > 3000:
            content = content[:1500] + "\n...[truncated]...\n" + content[-500:]
        parts.append(f"[{role}]: {content}")

        # Include tool results inline
        for tr in msg.tool_results:
            result_text = tr.content
            if len(result_text) > 2000:
                result_text = result_text[:1000] + "\n...[truncated]...\n" + result_text[-500:]
            status = "ERROR" if tr.is_error else "OK"
            parts.append(f"  [TOOL RESULT ({status})]: {result_text}")

    return "\n\n".join(parts)


class Compactor:
    """Auto-compact conversation when context hits threshold.

    Strategy (from cc-src):
    1. Trigger when estimated tokens > threshold % of context window
    2. Take oldest N messages (keeping system + last 5 messages)
    3. Send to the provider with summarization prompt
    4. Replace oldest messages with the summary
    5. Circuit breaker: if 3 consecutive compaction failures, stop trying
    """

    def __init__(
        self,
        provider: BaseProvider,
        threshold: float = 0.93,
    ) -> None:
        self.provider = provider
        self.threshold = threshold
        self.consecutive_failures = 0
        self.max_failures = _MAX_CONSECUTIVE_FAILURES

    @property
    def circuit_breaker_tripped(self) -> bool:
        """True if the circuit breaker has tripped (too many failures)."""
        return self.consecutive_failures >= self.max_failures

    async def should_compact(
        self,
        messages: list[Message],
        context_window: int,
    ) -> bool:
        """Check if compaction is needed.

        Returns True when estimated token usage exceeds the threshold
        percentage of the context window AND the circuit breaker has
        not tripped.
        """
        if self.circuit_breaker_tripped:
            return False

        if not messages:
            return False

        estimated = _estimate_tokens(messages)
        limit = int(context_window * self.threshold)
        return estimated > limit

    async def compact(
        self,
        conversation: Conversation,
        context_window: int,
    ) -> Conversation:
        """Compact the conversation by summarizing older messages.

        Preserves:
        - System prompt (always -- first message if role == system)
        - Last 5 messages (recent context)
        - All tool results from the current turn

        Summarizes everything in between.
        """
        messages = conversation.messages

        # Nothing to compact if too few messages
        if len(messages) <= _PRESERVE_TAIL + 1:
            return conversation

        # Identify system message (if present) and split
        system_msgs: list[Message] = []
        rest: list[Message] = []
        for msg in messages:
            if msg.role == "system" and not rest:
                system_msgs.append(msg)
            else:
                rest.append(msg)

        # If not enough to compact after preserving tail, skip
        if len(rest) <= _PRESERVE_TAIL:
            return conversation

        # Split into summarizable and preserved portions
        to_summarize = rest[:-_PRESERVE_TAIL]
        to_preserve = rest[-_PRESERVE_TAIL:]

        if not to_summarize:
            return conversation

        # Build the summarization prompt
        summary_prompt = self._build_summary_prompt(to_summarize)

        try:
            # Call the provider for summarization
            summary_response = await self.provider.complete(
                [
                    Message(role="system", content=COMPACT_SYSTEM_PROMPT),
                    Message(role="user", content=summary_prompt),
                ],
                tools=None,
                max_tokens=1024,
                temperature=0.3,
            )

            summary_text = summary_response.content.strip()
            if not summary_text:
                raise ValueError("Provider returned empty summary")

            # Reset failure counter on success
            self.consecutive_failures = 0

        except Exception as exc:
            self.consecutive_failures += 1
            logger.warning(
                "Compaction failed (attempt %d/%d): %s",
                self.consecutive_failures,
                self.max_failures,
                exc,
            )
            if self.circuit_breaker_tripped:
                logger.warning(
                    "Compaction circuit breaker tripped after %d consecutive "
                    "failures -- skipping future attempts this session",
                    self.max_failures,
                )
                # Refuse further auto-compaction: require a manual /compact.
                raise CompactionError(
                    f"Context compaction failed: {exc}. "
                    f"Auto-compaction disabled after "
                    f"{self.max_failures} consecutive failures. "
                    f"Please start a new conversation or compact manually "
                    f"with /compact."
                ) from exc
            # Raise so the agent loop can surface a user-visible error event
            # instead of silently returning the unchanged conversation (which
            # would leave the user trapped in a context-overflow loop).
            raise CompactionError(
                f"Context compaction failed: {exc}. "
                f"Please start a new conversation or compact manually "
                f"with /compact."
            ) from exc

        # Build compacted conversation
        summary_msg = Message(
            role="user",
            content=(
                "[Context compacted -- the following is an LLM-generated "
                "summary of earlier messages]\n\n" + summary_text
            ),
        )

        new_messages = system_msgs + [summary_msg] + to_preserve
        conversation.messages = new_messages
        return conversation

    def _build_summary_prompt(self, messages_to_summarize: list[Message]) -> str:
        """Build the summarization prompt.

        From cc-src: Ask for structured summary with:
        - Key decisions made
        - Code changes completed
        - Open questions/tasks remaining
        - Important context for continuing the conversation
        """
        # MEDIUM-2 fix: scrub API keys / tokens from the summary prompt
        # before sending to the LLM.  Compaction is a natural leak point
        # because earlier tool results may echo secrets into the context.
        formatted = scrub_secrets(_format_messages_for_summary(messages_to_summarize))
        return SUMMARY_PROMPT.format(messages=formatted)
