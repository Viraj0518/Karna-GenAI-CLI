"""Auto-memory extraction — pattern-based detection of memory-worthy content.

Scans user messages for patterns that indicate information worth persisting:
- Feedback (corrections, confirmations)
- User profile (role, expertise, preferences)
- Project facts (conventions, tooling decisions)
- References (URLs, external systems)

Uses regex + keyword matching (no LLM calls) to keep per-turn cost at zero.
Deduplicates against existing memories and rate-limits saves.

Adapted from cc-src memoryScan.ts patterns.  See NOTICES.md for attribution.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from karna.config import MemoryConfig
    from karna.memory.manager import MemoryManager
    from karna.memory.types import MemoryEntry

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Pattern definitions
# --------------------------------------------------------------------------- #

# -- Negative feedback (corrections) ---------------------------------------- #
_NEGATIVE_FEEDBACK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:no|nope),?\s+(?:don'?t|do not|stop|never)\b", re.I),
    re.compile(r"\b(?:don'?t|do not|stop|never)\s+\w+", re.I),
    re.compile(r"\bthat'?s?\s+(?:wrong|incorrect|not right|not what)\b", re.I),
    re.compile(r"\bnot like that\b", re.I),
    re.compile(r"\bplease\s+(?:don'?t|do not|stop|avoid|never)\b", re.I),
    re.compile(r"\b(?:wrong|incorrect)\s+(?:approach|way|method)\b", re.I),
    re.compile(r"\binstead\s+(?:of|use|do)\b", re.I),
    re.compile(r"\balways\s+(?:use|do|prefer|make sure)\b", re.I),
    re.compile(r"\bnever\s+(?:use|do|make|create|add)\b", re.I),
]

# -- Positive feedback (confirmations) -------------------------------------- #
_POSITIVE_FEEDBACK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(?:yes|yeah|yep),?\s+(?:exactly|that'?s?\s+(?:right|correct|perfect|it))\b", re.I),
    re.compile(r"\b(?:exactly|perfect|great|excellent)\b.*\b(?:like that|that way|keep|continue)\b", re.I),
    re.compile(r"\bthat'?s?\s+(?:exactly|perfect|great|correct)\b", re.I),
    re.compile(r"\bkeep\s+(?:doing|using|it)\b", re.I),
    re.compile(r"\bgood,?\s+(?:approach|way|method|job)\b", re.I),
]

# -- User profile ----------------------------------------------------------- #
_USER_PROFILE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bi(?:'?m| am)\s+(?:a |an |the )?\w+", re.I),
    re.compile(r"\bi\s+work\s+(?:on|at|with|in|for)\b", re.I),
    re.compile(r"\bmy\s+(?:role|job|title|position)\s+is\b", re.I),
    re.compile(r"\bi(?:'?m| am)\s+(?:new to|learning|experienced (?:with|in))\b", re.I),
    re.compile(r"\bi\s+(?:prefer|like|want|need)\s+\w+", re.I),
    re.compile(r"\bmy\s+(?:name|email)\s+is\b", re.I),
    re.compile(r"\bi\s+(?:usually|typically|always|often)\s+\w+", re.I),
]

# -- Project facts ---------------------------------------------------------- #
_PROJECT_FACT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bwe\s+(?:use|run|deploy|host|build)\b", re.I),
    re.compile(r"\bour\s+(?:convention|standard|rule|practice|policy|stack|team)\b", re.I),
    re.compile(r"\bthe\s+(?:data|config|code|app|service)\s+is\s+(?:in|at|on|stored)\b", re.I),
    re.compile(r"\bdeploy\s+to\b", re.I),
    re.compile(r"\bwe\s+(?:don'?t|never|always|should)\b", re.I),
    re.compile(r"\bthe\s+(?:database|db|api|endpoint|server)\s+(?:is|runs|lives)\b", re.I),
    re.compile(r"\bwe'?re?\s+(?:using|migrating|switching)\b", re.I),
    re.compile(r"\bour\s+(?:repo|repository|codebase|project)\b", re.I),
]

# -- References (URLs, external pointers) ----------------------------------- #
_REFERENCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"https?://[^\s<>\"']+", re.I),
    re.compile(r"\b(?:bugs?|issues?|tickets?)\s+(?:are|is)\s+(?:tracked|filed|logged)\s+(?:in|at|on)\b", re.I),
    re.compile(r"\bdocs?\s+(?:are|is)\s+at\b", re.I),
    re.compile(r"\bcheck\s+(?:the|our)\s+\w+\s+(?:dashboard|board|page|wiki)\b", re.I),
    re.compile(r"\b(?:dashboard|wiki|docs?|documentation)\s+(?:is|at|url)\b", re.I),
    re.compile(r"\bslack\s+(?:channel|#)\b", re.I),
]


# --------------------------------------------------------------------------- #
#  Extraction result
# --------------------------------------------------------------------------- #


@dataclass
class ExtractionCandidate:
    """A potential memory to save."""

    name: str
    description: str
    type: str  # user | feedback | project | reference
    content: str


# --------------------------------------------------------------------------- #
#  Rate limiter
# --------------------------------------------------------------------------- #


@dataclass
class _RateLimiter:
    """Simple turn-based rate limiter for memory saves."""

    min_turns_between_saves: int = 5
    _turns_since_last_save: int = 0

    def tick(self) -> None:
        """Record that a turn has passed."""
        self._turns_since_last_save += 1

    def can_save(self) -> bool:
        """Return True if enough turns have passed since the last save."""
        return self._turns_since_last_save >= self.min_turns_between_saves

    def reset(self) -> None:
        """Reset counter after a successful save."""
        self._turns_since_last_save = 0


# --------------------------------------------------------------------------- #
#  MemoryExtractor
# --------------------------------------------------------------------------- #


@dataclass
class MemoryExtractor:
    """Scan user messages for memory-worthy patterns and persist them.

    Designed to be called from the auto_save_memory_hook after each
    assistant response.  Uses pattern matching only -- no LLM calls.

    Parameters
    ----------
    memory_manager : MemoryManager
        The manager instance for searching existing memories and saving new ones.
    memory_config : MemoryConfig, optional
        Memory configuration from ``config.toml``.  When provided,
        ``rate_limit_turns``, ``dedup_threshold``, ``auto_extract``, and
        ``types`` are read from config instead of using defaults.
    """

    memory_manager: "MemoryManager"
    memory_config: "MemoryConfig | None" = None
    _rate_limiter: _RateLimiter = field(default_factory=lambda: _RateLimiter())
    _last_save_time: float = 0.0

    def __post_init__(self) -> None:
        if self.memory_config is not None:
            self._rate_limiter = _RateLimiter(
                min_turns_between_saves=self.memory_config.rate_limit_turns,
            )
            self._auto_extract = self.memory_config.auto_extract
            self._dedup_threshold = self.memory_config.dedup_threshold
            self._allowed_types: list[str] = list(self.memory_config.types)
        else:
            if not hasattr(self, "_rate_limiter") or self._rate_limiter is None:
                self._rate_limiter = _RateLimiter()
            self._auto_extract = True
            self._dedup_threshold = 0.6
            self._allowed_types = ["user", "feedback", "project", "reference"]

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def extract_and_save(
        self,
        user_message: str,
        assistant_response: str = "",
    ) -> list["MemoryEntry"]:
        """Scan user_message for memory-worthy patterns, dedup, and save.

        Parameters
        ----------
        user_message : str
            The user's message to scan for patterns.
        assistant_response : str
            The assistant's response (used for context, not scanned for patterns).

        Returns
        -------
        list[MemoryEntry]
            List of newly saved memory entries (may be empty).
        """
        if not self._auto_extract:
            return []

        self._rate_limiter.tick()

        if not self._rate_limiter.can_save():
            return []

        candidates = self._detect_candidates(user_message)
        if not candidates:
            return []

        # Dedup: check each candidate against existing memories
        new_candidates = self._dedup(candidates)
        if not new_candidates:
            return []

        # Save at most 1 candidate per turn (pick the first / highest-priority)
        candidate = new_candidates[0]
        saved = self._save_candidate(candidate)
        if saved:
            self._rate_limiter.reset()
            self._last_save_time = time.time()
            return [saved]

        return []

    def detect_candidates(self, user_message: str) -> list[ExtractionCandidate]:
        """Public wrapper for pattern detection (useful for testing)."""
        return self._detect_candidates(user_message)

    # ------------------------------------------------------------------ #
    #  Pattern detection
    # ------------------------------------------------------------------ #

    def _detect_candidates(self, text: str) -> list[ExtractionCandidate]:
        """Scan text against all pattern categories and return candidates.

        Priority order: feedback > user > project > reference.
        """
        candidates: list[ExtractionCandidate] = []

        # Negative feedback
        for pattern in _NEGATIVE_FEEDBACK_PATTERNS:
            match = pattern.search(text)
            if match:
                snippet = self._extract_snippet(text, match)
                candidates.append(
                    ExtractionCandidate(
                        name=self._make_name("correction", snippet),
                        description=f"User correction: {snippet[:80]}",
                        type="feedback",
                        content=f"User said: {snippet}\n\nContext: The user corrected the assistant's approach.",
                    )
                )
                break  # One feedback per message

        # Positive feedback
        if not candidates:
            for pattern in _POSITIVE_FEEDBACK_PATTERNS:
                match = pattern.search(text)
                if match:
                    snippet = self._extract_snippet(text, match)
                    candidates.append(
                        ExtractionCandidate(
                            name=self._make_name("positive feedback", snippet),
                            description=f"Positive feedback: {snippet[:80]}",
                            type="feedback",
                            content=(
                                f"User confirmed: {snippet}\n\nContext: The user validated the assistant's approach."
                            ),
                        )
                    )
                    break

        # User profile
        for pattern in _USER_PROFILE_PATTERNS:
            match = pattern.search(text)
            if match:
                snippet = self._extract_snippet(text, match)
                candidates.append(
                    ExtractionCandidate(
                        name=self._make_name("user profile", snippet),
                        description=f"User self-identification: {snippet[:80]}",
                        type="user",
                        content=f"The user said: {snippet}",
                    )
                )
                break

        # Project facts
        for pattern in _PROJECT_FACT_PATTERNS:
            match = pattern.search(text)
            if match:
                snippet = self._extract_snippet(text, match)
                candidates.append(
                    ExtractionCandidate(
                        name=self._make_name("project fact", snippet),
                        description=f"Project decision: {snippet[:80]}",
                        type="project",
                        content=f"Project fact: {snippet}",
                    )
                )
                break

        # References (URLs, external pointers)
        for pattern in _REFERENCE_PATTERNS:
            match = pattern.search(text)
            if match:
                snippet = self._extract_snippet(text, match)
                candidates.append(
                    ExtractionCandidate(
                        name=self._make_name("reference", snippet),
                        description=f"External reference: {snippet[:80]}",
                        type="reference",
                        content=f"Reference: {snippet}",
                    )
                )
                break

        # Filter out candidates whose type is not in the allowed types list.
        return [c for c in candidates if c.type in self._allowed_types]

    # ------------------------------------------------------------------ #
    #  Deduplication
    # ------------------------------------------------------------------ #

    def _dedup(self, candidates: list[ExtractionCandidate]) -> list[ExtractionCandidate]:
        """Remove candidates that are too similar to existing memories."""
        new_candidates: list[ExtractionCandidate] = []

        for candidate in candidates:
            # Search existing memories for overlap
            existing = self.memory_manager.search(candidate.description[:50])
            is_dup = False

            for entry in existing[:5]:  # Check top 5 matches
                # Simple similarity: if the existing memory content overlaps
                # significantly with the candidate content, skip it
                if self._is_similar(candidate.content, entry.content, self._dedup_threshold):
                    is_dup = True
                    logger.debug(
                        "Dedup: skipping '%s' — similar to existing '%s'",
                        candidate.name,
                        entry.name,
                    )
                    break

            if not is_dup:
                new_candidates.append(candidate)

        return new_candidates

    @staticmethod
    def _is_similar(text_a: str, text_b: str, threshold: float = 0.6) -> bool:
        """Check if two texts are similar using word overlap ratio."""
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())

        if not words_a or not words_b:
            return False

        intersection = words_a & words_b
        smaller = min(len(words_a), len(words_b))

        if smaller == 0:
            return False

        return len(intersection) / smaller >= threshold

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _extract_snippet(text: str, match: re.Match[str]) -> str:
        """Extract a meaningful snippet around the regex match.

        Takes the sentence containing the match, capped at 200 chars.
        """
        start = match.start()
        end = match.end()

        # Expand to sentence boundaries
        sentence_start = text.rfind(".", 0, start)
        sentence_start = 0 if sentence_start == -1 else sentence_start + 1

        sentence_end = text.find(".", end)
        sentence_end = len(text) if sentence_end == -1 else sentence_end + 1

        snippet = text[sentence_start:sentence_end].strip()
        if len(snippet) > 200:
            snippet = snippet[:200] + "..."
        return snippet

    @staticmethod
    def _make_name(category: str, snippet: str) -> str:
        """Generate a concise name for the memory entry."""
        # Take first 40 chars of snippet, clean up
        short = snippet[:40].strip()
        # Remove special chars
        short = re.sub(r"[^\w\s-]", "", short).strip()
        if not short:
            short = category
        return f"{category}: {short}"

    def _save_candidate(self, candidate: ExtractionCandidate) -> "MemoryEntry | None":
        """Persist a candidate to disk via MemoryManager."""
        try:
            path = self.memory_manager.save_memory(
                name=candidate.name,
                type=candidate.type,
                description=candidate.description,
                content=candidate.content,
            )
            # Load it back as a MemoryEntry for the return value
            entries = self.memory_manager.load_all()
            for entry in entries:
                if entry.file_path == path:
                    return entry

            logger.warning("Saved memory to %s but could not reload it", path)
            return None
        except Exception as exc:
            logger.warning("Failed to save memory candidate '%s': %s", candidate.name, exc)
            return None
