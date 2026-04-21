"""UserProfile -- Honcho-lite, accrues facts about the user across sessions.

Storage is a single ``user_profile.md`` entry in the typed memdir.
At session end (or on demand) we feed the last ~20 messages to the
model, ask for structured facts about the user, dedupe against the
current profile body, and append new unique lines.

Design choices:
- No embeddings, no graphs. Just a flat bullet list of one-line facts.
- Dedupe is exact + normalised: two facts that differ only in case
  or trailing punctuation collapse.
- Model is asked for plain text, one fact per line. Robust to a
  chatty response: we strip ``-`` / ``*`` bullets and blank lines.
- Scrubbing is handled by :class:`Memdir` on write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from karna.memory.memdir import Memdir
from karna.models import Message

if TYPE_CHECKING:
    from karna.providers.base import BaseProvider

_PROFILE_FILENAME = "user_profile.md"
_PROFILE_NAME = "User profile"
_PROFILE_DESCRIPTION = "Who the user is, their preferences, context, constraints"


@dataclass
class Fact:
    """A single one-line fact about the user."""

    text: str

    def normalised(self) -> str:
        return _normalise(self.text)


_NORMALISE_RE = re.compile(r"[^a-z0-9]+")


def _normalise(text: str) -> str:
    return _NORMALISE_RE.sub(" ", text.lower()).strip()


_EXTRACTION_PROMPT = """\
You are maintaining a user profile. Extract NEW factual information about
the user from the recent conversation below. Output each fact as a single
plain-text line, no numbering, no bullets, no quotes. Only include facts
that are about the USER (their role, projects, preferences, working
style, tools, constraints, location, name) -- NOT facts about the world.

If nothing new is learned, output exactly: NONE

Recent conversation:
---
{transcript}
---

New facts about the user (one per line):"""

class UserProfile:
    """Accrues durable facts about the user into ``user_profile.md``."""

    def __init__(
        self,
        memdir: Memdir,
        provider: "BaseProvider | None" = None,
        model: str = "",
    ) -> None:
        self.memdir = memdir
        self.provider = provider
        self.model = model

    # ------------------------------------------------------------------ #
    #  Read
    # ------------------------------------------------------------------ #

    def _find_profile_path(self):
        """Locate an existing profile file, if any.

        Checks the canonical filename first, then falls back to the
        first user-type memory with name='User profile'.
        """
        canonical = self.memdir.root / _PROFILE_FILENAME
        if canonical.exists():
            return canonical
        for mem in self.memdir.list(type="user"):
            if mem.name == _PROFILE_NAME:
                return self.memdir.root / mem.filename
        return None

    def read(self) -> str:
        """Return the current profile body (empty string when missing)."""
        path = self._find_profile_path()
        if path is None:
            return ""
        try:
            return self.memdir.get(path.name).body
        except FileNotFoundError:
            return ""

    # ------------------------------------------------------------------ #
    #  Extract
    # ------------------------------------------------------------------ #

    async def extract_new_facts(self, recent_messages: list[Message], *, limit: int = 20) -> list[Fact]:
        """Ask the model for user facts from the last *limit* messages."""
        if self.provider is None:
            raise ValueError("UserProfile.extract_new_facts requires a provider")

        tail = recent_messages[-limit:]
        transcript = _render_transcript(tail)
        prompt = _EXTRACTION_PROMPT.format(transcript=transcript)

        reply = await self.provider.complete(
            [Message(role="user", content=prompt)],
            tools=None,
            system_prompt=None,
            max_tokens=400,
            temperature=0.0,
        )
        return _parse_facts(reply.content)

    # ------------------------------------------------------------------ #
    #  Merge
    # ------------------------------------------------------------------ #

    def merge_facts(self, facts: list[Fact]) -> None:
        """Append new unique facts, normalising for dedupe."""
        if not facts:
            return

        existing_body = self.read()
        existing_lines = [ln.strip() for ln in existing_body.split("\n") if ln.strip()]
        seen = {_normalise(ln.lstrip("- *").strip()) for ln in existing_lines}

        additions: list[str] = []
        for fact in facts:
            norm = fact.normalised()
            if not norm or norm in seen:
                continue
            seen.add(norm)
            additions.append(f"- {fact.text.strip()}")

        if not additions:
            return

        # Normalise existing body to bullet form for consistency.
        bulletised: list[str] = []
        for ln in existing_lines:
            if ln.startswith(("-", "*")):
                bulletised.append(ln)
            else:
                bulletised.append(f"- {ln}")

        new_body = "\n".join(bulletised + additions).strip() + "\n"

        path = self._find_profile_path()
        if path is not None:
            self.memdir.update(path.name, new_body)
        else:
            # Seed the canonical user_profile.md directly so later
            # reads are deterministic regardless of the slug policy.
            self.memdir.root.mkdir(parents=True, exist_ok=True)
            filename = self.memdir.add(
                type="user",
                name=_PROFILE_NAME,
                description=_PROFILE_DESCRIPTION,
                body=new_body,
            )
            # Rename to canonical if the slug differs
            canonical = self.memdir.root / _PROFILE_FILENAME
            created = self.memdir.root / filename
            if created != canonical and not canonical.exists():
                created.rename(canonical)


# --------------------------------------------------------------------------- #
#  Parsing helpers
# --------------------------------------------------------------------------- #


def _render_transcript(messages: list[Message]) -> str:
    lines: list[str] = []
    for msg in messages:
        role = msg.role.upper()
        content = (msg.content or "").strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _parse_facts(raw: str) -> list[Fact]:
    out: list[Fact] = []
    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.upper() == "NONE":
            return []
        # Strip common bullet / numbering prefixes
        cleaned = re.sub(r"^[\-\*\d\.\)\s]+", "", stripped).strip().strip('"').strip("'")
        if not cleaned:
            continue
        out.append(Fact(text=cleaned))
    return out
