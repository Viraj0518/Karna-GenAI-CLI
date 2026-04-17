"""Central context manager — assembles the full context window.

Responsible for:
1. Injecting project context (KARNA.md / CLAUDE.md / etc.)
2. Injecting git repo state
3. Injecting environment metadata
4. Fitting the conversation into the token budget via truncation

Called before every provider call in the agent loop.

Adapted from cc-src ``context.ts``.  See NOTICES.md for attribution.
"""

from __future__ import annotations

import logging
from pathlib import Path

from karna.config import KarnaConfig
from karna.context.environment import EnvironmentContext
from karna.context.git import GitContext
from karna.context.project import ProjectContext
from karna.models import Conversation, Message
from karna.tokens import count_tokens

logger = logging.getLogger(__name__)


class ContextManager:
    """Build the message list that fits within the context window."""

    def __init__(
        self,
        config: KarnaConfig,
        max_context_tokens: int = 128_000,
        cwd: Path | None = None,
    ) -> None:
        self.config = config
        self.max_tokens = max_context_tokens
        self.cwd = cwd or Path.cwd()
        self.project_ctx = ProjectContext()
        self.git_ctx = GitContext()
        self.env_ctx = EnvironmentContext()

        # Lazily populated caches (populated on first build_messages call).
        self._project_context: str | None = None
        self._project_context_loaded = False

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    async def build_messages(
        self,
        conversation: Conversation,
        system_prompt: str,
    ) -> list[Message]:
        """Build the message list that fits within the context window.

        Strategy:
        1. System prompt (always kept)
        2. Project context injection (always if present)
        3. Git + environment context (always)
        4. Recent messages with full tool results (keep last N)
        5. Older messages summarized or dropped (FIFO)
        6. Never drop the user's last message
        """
        # -- 1. System prompt ----------------------------------------- #
        system_parts: list[str] = [system_prompt]

        # -- 2. Project context (cached after first load) -------------- #
        if not self._project_context_loaded:
            self._project_context = self.project_ctx.detect(self.cwd)
            self._project_context_loaded = True

        if self._project_context:
            system_parts.append(
                f"\n<project-context>\n{self._project_context}\n</project-context>"
            )

        # -- 3. Git + environment ------------------------------------- #
        git_ctx_str = await self.git_ctx.get_context(self.cwd)
        if git_ctx_str:
            system_parts.append(
                f"\n<git-context>\n{git_ctx_str}\n</git-context>"
            )

        env_ctx_str = self.env_ctx.get_context(self.cwd)
        system_parts.append(
            f"\n<environment>\n{env_ctx_str}\n</environment>"
        )

        # Assemble the full system message.
        system_message = Message(
            role="system",
            content="\n".join(system_parts),
        )

        # -- 4-6. Fit conversation into budget ------------------------ #
        system_tokens = self.estimate_tokens(system_message.content)
        remaining_budget = self.max_tokens - system_tokens

        conv_messages = list(conversation.messages)
        fitted = self.truncate_to_fit(conv_messages, remaining_budget)

        return [system_message] + fitted

    # ------------------------------------------------------------------ #
    #  Token estimation
    # ------------------------------------------------------------------ #

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Token count via tiktoken when available, else len//4 fallback."""
        return count_tokens(text)

    # ------------------------------------------------------------------ #
    #  Truncation
    # ------------------------------------------------------------------ #

    def truncate_to_fit(
        self,
        messages: list[Message],
        budget: int,
    ) -> list[Message]:
        """Trim oldest non-system messages to fit *budget* (in tokens).

        Rules:
        - System messages are never dropped (handled by caller).
        - The last user message is always preserved.
        - Oldest messages are dropped first (FIFO).
        """
        if not messages:
            return []

        # Quick check: does everything fit?
        total = sum(self.estimate_tokens(self._msg_text(m)) for m in messages)
        if total <= budget:
            return list(messages)

        # Identify the last user message index.
        last_user_idx: int | None = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "user":
                last_user_idx = i
                break

        # Drop from the front until we fit.
        result: list[Message] = []
        used = 0

        # Work backwards so we keep the most recent messages.
        for i in range(len(messages) - 1, -1, -1):
            msg = messages[i]
            cost = self.estimate_tokens(self._msg_text(msg))
            if used + cost <= budget:
                result.append(msg)
                used += cost
            elif i == last_user_idx:
                # Force-keep the last user message even if over budget.
                result.append(msg)
                used += cost

        result.reverse()
        return result

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _msg_text(msg: Message) -> str:
        """Extract the text payload of a message for token estimation."""
        parts = [msg.content]
        for tc in msg.tool_calls:
            parts.append(tc.name)
            parts.append(str(tc.arguments))
        for tr in msg.tool_results:
            parts.append(tr.content)
        return " ".join(parts)
