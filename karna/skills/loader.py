"""Skill loader — parse .md files with YAML frontmatter into ``Skill`` objects.

Skill files live in ``~/.karna/skills/`` and follow this format::

    ---
    name: my-skill
    description: What this skill does
    triggers: ["/my-skill", "do the thing"]
    ---

    Instructions injected into the system prompt when this skill is active.

The ``SkillManager`` loads all ``.md`` files from the skills directory,
matches user input against triggers, and builds the skills section of
the system prompt within a token budget.

Adapted from hermes-agent SKILL.md patterns and agentskills.io conventions.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Frontmatter regex — matches ``---\n<yaml>\n---``
# --------------------------------------------------------------------------- #

_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)


# --------------------------------------------------------------------------- #
#  Skill model
# --------------------------------------------------------------------------- #


class Skill(BaseModel):
    """A single loaded skill parsed from a ``.md`` file."""

    name: str = Field(..., description="Unique skill identifier (slug)")
    description: str = Field(default="", description="One-line summary")
    instructions: str = Field(default="", description="Full text injected into the system prompt")
    triggers: list[str] = Field(default_factory=list, description="Keywords or slash commands that activate this skill")
    file_path: Path = Field(..., description="Absolute path to the source .md file")
    enabled: bool = Field(default=True, description="Whether this skill is active")
    version: str = Field(default="", description="Optional semver version string")
    author: str = Field(default="", description="Optional author attribution")


# --------------------------------------------------------------------------- #
#  Frontmatter parser (minimal YAML subset — no PyYAML dependency)
# --------------------------------------------------------------------------- #


def _parse_yaml_value(raw: str) -> str | list[str] | bool:
    """Parse a single YAML value (string, list, or bool).

    Handles:
    - ``[item1, item2]`` → list[str]
    - ``true`` / ``false`` → bool
    - ``"quoted"`` or ``'quoted'`` → str (unquoted)
    - plain scalar → str
    """
    stripped = raw.strip()

    # List: [a, b, c]
    if stripped.startswith("[") and stripped.endswith("]"):
        inner = stripped[1:-1]
        items: list[str] = []
        for item in inner.split(","):
            item = item.strip().strip("\"'")
            if item:
                items.append(item)
        return items

    # Bool
    if stripped.lower() == "true":
        return True
    if stripped.lower() == "false":
        return False

    # Quoted string
    if (stripped.startswith('"') and stripped.endswith('"')) or (stripped.startswith("'") and stripped.endswith("'")):
        return stripped[1:-1]

    return stripped


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a markdown file into frontmatter dict and body text.

    Returns ``({}, text)`` if no frontmatter is found.
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    yaml_block = match.group(1)
    body = text[match.end() :]

    data: dict[str, Any] = {}
    current_key: str | None = None
    multiline_value: list[str] = []

    for line in yaml_block.splitlines():
        # Skip comments and blank lines
        if not line.strip() or line.strip().startswith("#"):
            continue

        # Multi-line continuation (for ``description: >`` style)
        if current_key and (line.startswith("  ") or line.startswith("\t")):
            multiline_value.append(line.strip())
            continue

        # Flush previous multi-line value
        if current_key and multiline_value:
            data[current_key] = " ".join(multiline_value)
            current_key = None
            multiline_value = []

        # Key: value
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if not value or value == ">":
                # Start multi-line
                current_key = key
                multiline_value = []
                if value and value != ">":
                    multiline_value.append(value)
            else:
                data[key] = _parse_yaml_value(value)

    # Flush any trailing multi-line
    if current_key and multiline_value:
        data[current_key] = " ".join(multiline_value)

    return data, body


def parse_skill_file(path: Path) -> Skill:
    """Parse a single ``.md`` skill file into a ``Skill`` object.

    Raises ``ValueError`` if the file lacks a ``name`` in frontmatter.
    """
    # Read the raw markdown file
    text = path.read_text(encoding="utf-8")

    # Split into frontmatter metadata dict and body text.
    # _parse_frontmatter uses regex to match the ---\n<yaml>\n--- block
    # and a minimal YAML parser for the key-value pairs inside.
    meta, body = _parse_frontmatter(text)

    # Name is required but falls back to the filename stem if missing
    name = meta.get("name")
    if not name:
        # Fall back to filename without extension (e.g., "my-skill.md" -> "my-skill")
        name = path.stem

    # Triggers may be a list or a single string in frontmatter;
    # normalise to always be a list.
    triggers = meta.get("triggers", [])
    if isinstance(triggers, str):
        triggers = [triggers]

    # Build the Skill model — body becomes the instructions that get
    # injected into the system prompt when this skill is activated.
    return Skill(
        name=name,
        description=meta.get("description", ""),
        instructions=body.strip(),
        triggers=triggers,
        file_path=path.resolve(),
        enabled=meta.get("enabled", True),
        version=meta.get("version", ""),
        author=meta.get("author", ""),
    )


# --------------------------------------------------------------------------- #
#  SkillManager
# --------------------------------------------------------------------------- #


class SkillManager:
    """Load, manage, and query skills from ``.md`` files.

    Parameters
    ----------
    skills_dir : Path, optional
        Directory containing skill ``.md`` files.
        Defaults to ``~/.karna/skills/``.
    """

    def __init__(self, skills_dir: Path | None = None) -> None:
        self.skills_dir = skills_dir or Path.home() / ".karna" / "skills"
        self.skills: list[Skill] = []

    # ------------------------------------------------------------------ #
    #  Loading
    # ------------------------------------------------------------------ #

    def load_all(self) -> list[Skill]:
        """Load all ``.md`` files from ``skills_dir``.

        Skips files that fail to parse and logs a warning.
        Returns the list of successfully loaded skills.
        """
        self.skills = []

        if not self.skills_dir.is_dir():
            logger.debug("Skills directory does not exist: %s", self.skills_dir)
            return self.skills

        for path in sorted(self.skills_dir.glob("*.md")):
            try:
                skill = parse_skill_file(path)
                self.skills.append(skill)
                logger.debug("Loaded skill: %s from %s", skill.name, path)
            except Exception:
                logger.warning("Failed to parse skill file: %s", path, exc_info=True)

        return self.skills

    # ------------------------------------------------------------------ #
    #  Queries
    # ------------------------------------------------------------------ #

    def get_active_skills(self) -> list[Skill]:
        """Return only enabled skills."""
        return [s for s in self.skills if s.enabled]

    def get_skill_by_name(self, name: str) -> Skill | None:
        """Find a skill by name (case-insensitive)."""
        name_lower = name.lower()
        for s in self.skills:
            if s.name.lower() == name_lower:
                return s
        return None

    def match_trigger(self, user_input: str) -> list[Skill]:
        """Find skills whose triggers match the user's input.

        Matching rules:
        - Slash commands: exact prefix match (``/commit`` matches input starting with ``/commit``)
        - Keywords: case-insensitive substring match in the input text
        - Only enabled skills are considered
        """
        matched: list[Skill] = []
        input_lower = user_input.lower().strip()

        for skill in self.get_active_skills():
            for trigger in skill.triggers:
                trigger_lower = trigger.lower().strip()
                if trigger_lower.startswith("/"):
                    # Slash command: must match at start of input
                    if input_lower.startswith(trigger_lower):
                        matched.append(skill)
                        break
                else:
                    # Keyword: substring match
                    if trigger_lower in input_lower:
                        matched.append(skill)
                        break

        return matched

    # ------------------------------------------------------------------ #
    #  Prompt injection
    # ------------------------------------------------------------------ #

    def get_skills_for_prompt(self, max_tokens: int = 3000) -> str:
        """Build the skills section for the system prompt.

        Concatenates instructions from all enabled skills, respecting
        the token budget (estimated at ~3 chars/token).

        Returns an empty string if no skills are active.
        """
        active = self.get_active_skills()
        if not active:
            return ""

        parts: list[str] = []
        budget_chars = max_tokens * 3  # conservative estimate
        used = 0

        header = "# Skills\n\nThe following skills extend your capabilities:\n"
        used += len(header)
        parts.append(header)

        for skill in active:
            section = f"\n## {skill.name}\n_{skill.description}_\n\n{skill.instructions}\n"
            section_len = len(section)
            if used + section_len > budget_chars:
                # Add a note that some skills were truncated
                parts.append(f"\n_(+{len(active) - len(parts) + 1} more skills omitted for token budget)_\n")
                break
            parts.append(section)
            used += section_len

        return "".join(parts)

    # ------------------------------------------------------------------ #
    #  Enable / disable
    # ------------------------------------------------------------------ #

    def enable_skill(self, name: str) -> bool:
        """Enable a skill by name. Returns True if found."""
        skill = self.get_skill_by_name(name)
        if skill is None:
            return False
        skill.enabled = True
        self._persist_enabled_state(skill)
        return True

    def disable_skill(self, name: str) -> bool:
        """Disable a skill by name. Returns True if found."""
        skill = self.get_skill_by_name(name)
        if skill is None:
            return False
        skill.enabled = False
        self._persist_enabled_state(skill)
        return True

    def _persist_enabled_state(self, skill: Skill) -> None:
        """Update the ``enabled`` field in the skill file's frontmatter.

        If the frontmatter already contains ``enabled:``, update it in-place.
        Otherwise, add it after the last frontmatter line.
        """
        try:
            text = skill.file_path.read_text(encoding="utf-8")
            match = _FRONTMATTER_RE.match(text)
            if not match:
                return

            yaml_block = match.group(1)
            body = text[match.end() :]

            # Check if enabled: already exists
            enabled_re = re.compile(r"^enabled:\s*\S+", re.MULTILINE)
            enabled_str = f"enabled: {'true' if skill.enabled else 'false'}"

            if enabled_re.search(yaml_block):
                yaml_block = enabled_re.sub(enabled_str, yaml_block)
            else:
                yaml_block = yaml_block.rstrip() + f"\n{enabled_str}"

            new_text = f"---\n{yaml_block}\n---\n{body}"
            skill.file_path.write_text(new_text, encoding="utf-8")
        except Exception:
            logger.warning("Failed to persist enabled state for skill: %s", skill.name, exc_info=True)

    # ------------------------------------------------------------------ #
    #  Installation / creation
    # ------------------------------------------------------------------ #

    def install_skill(self, source: str) -> Skill:
        """Install a skill from a URL or local file path.

        - URLs: downloaded via httpx and saved to ``skills_dir``
        - Local paths: copied to ``skills_dir``

        Returns the parsed ``Skill`` object.
        Raises ``ValueError`` if the source is not a valid URL or path.
        Raises ``FileNotFoundError`` if a local path does not exist.
        """
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        source_path = Path(source)

        if source.startswith(("http://", "https://")):
            # Download from URL
            response = httpx.get(source, follow_redirects=True, timeout=30)
            response.raise_for_status()

            # Derive filename from URL
            url_path = source.rstrip("/").split("/")[-1]
            if not url_path.endswith(".md"):
                url_path += ".md"
            dest = self.skills_dir / url_path
            dest.write_text(response.text, encoding="utf-8")

        elif source_path.is_file():
            # Copy local file
            dest = self.skills_dir / source_path.name
            shutil.copy2(source_path, dest)

        else:
            raise ValueError(f"Source is not a valid URL or existing file: {source}")

        skill = parse_skill_file(dest)
        # Add to loaded skills if not already present
        existing = self.get_skill_by_name(skill.name)
        if existing:
            self.skills.remove(existing)
        self.skills.append(skill)

        return skill

    def create_skill(
        self,
        name: str,
        description: str,
        instructions: str,
        triggers: list[str] | None = None,
    ) -> Skill:
        """Create a new skill file in ``skills_dir``.

        Parameters
        ----------
        name : str
            Skill identifier (used as filename slug).
        description : str
            One-line description.
        instructions : str
            Full instruction text (the body after frontmatter).
        triggers : list[str], optional
            Trigger keywords/slash commands.

        Returns
        -------
        Skill
            The newly created skill.
        """
        self.skills_dir.mkdir(parents=True, exist_ok=True)

        triggers = triggers or [f"/{name}"]
        triggers_str = ", ".join(f'"{t}"' for t in triggers)

        content = f"---\nname: {name}\ndescription: {description}\ntriggers: [{triggers_str}]\n---\n\n{instructions}\n"

        dest = self.skills_dir / f"{name}.md"
        dest.write_text(content, encoding="utf-8")

        skill = parse_skill_file(dest)
        self.skills.append(skill)
        return skill
