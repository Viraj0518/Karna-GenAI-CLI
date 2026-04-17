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
        system_budget_ratio: float = 0.5,
    ) -> None:
        self.config = config
        self.max_tokens = max_context_tokens
        self.cwd = cwd or Path.cwd()
        self.project_ctx = ProjectContext()
        self.git_ctx = GitContext()
        self.env_ctx = EnvironmentContext()
        # Fraction of the total context window that the system message
        # (prompt + injected sections) is allowed to consume.  The rest is
        # reserved for conversation history + the assistant's reply.
        self.system_budget_ratio = system_budget_ratio

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

        The system message is capped at :attr:`system_budget_ratio` of the
        total budget (default 50%).  If the injected context sections would
        push the system message past that cap, lower-priority sections are
        dropped and the drop is logged at INFO level so the user knows
        which context was sacrificed.
        """
        # Build the injected sections in priority order (lowest priority
        # number = most important, kept first).  This mirrors the prompt
        # engine so we have one canonical ordering.
        sections: list[tuple[str, str, int]] = []

        # -- 2. Project context (cached after first load) -------------- #
        if not self._project_context_loaded:
            self._project_context = self.project_ctx.detect(self.cwd)
            self._project_context_loaded = True

        if self._project_context:
            sections.append((
                "project-context",
                f"<project-context>\n{self._project_context}\n</project-context>",
                2,
            ))

        # -- 3. Git + environment ------------------------------------- #
        git_ctx_str = await self.git_ctx.get_context(self.cwd)
        if git_ctx_str:
            sections.append((
                "git-context",
                f"<git-context>\n{git_ctx_str}\n</git-context>",
                3,
            ))

        env_ctx_str = self.env_ctx.get_context(self.cwd)
        sections.append((
            "environment",
            f"<environment>\n{env_ctx_str}\n</environment>",
            1,  # environment is cheap + always useful, so keep ahead of others
        ))

        # -- 1. System prompt + fit injected sections ----------------- #
        system_budget = int(self.max_tokens * self.system_budget_ratio)
        system_message_content = self._fit_system_sections(
            system_prompt,
            sections,
            system_budget,
        )

        system_message = Message(role="system", content=system_message_content)

        # -- 4-6. Fit conversation into budget ------------------------ #
        system_tokens = self.estimate_tokens(system_message.content)
        remaining_budget = self.max_tokens - system_tokens

        conv_messages = list(conversation.messages)
        fitted = self.truncate_to_fit(conv_messages, remaining_budget)

        return [system_message] + fitted

    # ------------------------------------------------------------------ #
    #  System-message fitting
    # ------------------------------------------------------------------ #

    def _fit_system_sections(
        self,
        system_prompt: str,
        sections: list[tuple[str, str, int]],
        budget: int,
    ) -> str:
        """Assemble the system message, respecting *budget* (tokens).

        The raw ``system_prompt`` is always kept (it carries identity, tool
        docs, behavioural rules — dropping those breaks the agent).  Injected
        sections are added in priority order; any section that would push
        the total past the budget is dropped and logged at INFO level.
        """
        used = self.estimate_tokens(system_prompt)
        if used >= budget:
            # System prompt alone already exceeds the cap.  We can't fix
            # this here, but log it so upstream sees the overflow.
            logger.info(
                "Context: system prompt alone (%d tok) exceeds system budget "
                "(%d tok); dropping all injected context sections.",
                used,
                budget,
            )
            return system_prompt

        parts: list[str] = [system_prompt]
        # Keep highest-priority (lowest number) sections first.
        sorted_sections = sorted(sections, key=lambda s: s[2])

        for name, content, _priority in sorted_sections:
            cost = self.estimate_tokens(content)
            if used + cost > budget:
                logger.info(
                    "Context: dropping %s section (%d tok) — would exceed "
                    "system budget (%d/%d tok used).",
                    name,
                    cost,
                    used,
                    budget,
                )
                continue
            parts.append("\n" + content)
            used += cost

        return "\n".join(parts)

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
