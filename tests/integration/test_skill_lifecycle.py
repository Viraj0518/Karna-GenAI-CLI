"""E2E skill lifecycle: create .md -> load -> match trigger -> inject prompt.

Also verifies that a skill file with invalid YAML-style frontmatter is
rejected gracefully while other skills in the same directory continue
to load.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from karna.skills.loader import SkillManager


def _write_skill(dir_: Path, name: str, body: str, *, triggers: list[str]) -> Path:
    """Write a well-formed skill file and return its path."""
    trigger_str = ", ".join(f'"{t}"' for t in triggers)
    content = f"---\nname: {name}\ndescription: test skill {name}\ntriggers: [{trigger_str}]\n---\n\n{body}\n"
    path = dir_ / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_skill_created_loaded_and_queryable(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(
        skills_dir,
        "commit-helper",
        "Always end commit messages with a period.",
        triggers=["/commit", "make a commit"],
    )

    mgr = SkillManager(skills_dir=skills_dir)
    skills = mgr.load_all()

    assert len(skills) == 1
    assert skills[0].name == "commit-helper"
    # Query by name
    found = mgr.get_skill_by_name("commit-helper")
    assert found is not None
    assert found.description == "test skill commit-helper"
    assert "end commit messages with a period" in found.instructions


def test_trigger_match_and_prompt_injection(tmp_path: Path) -> None:
    """A matching user message should activate the skill and its
    instructions should appear in the system-prompt section."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    _write_skill(
        skills_dir,
        "pirate-voice",
        "Answer every question in pirate dialect, matey.",
        triggers=["/pirate", "speak like a pirate"],
    )

    mgr = SkillManager(skills_dir=skills_dir)
    mgr.load_all()

    # Slash-command trigger
    matched = mgr.match_trigger("/pirate tell me a joke")
    assert len(matched) == 1
    assert matched[0].name == "pirate-voice"

    # Keyword trigger
    matched2 = mgr.match_trigger("please speak like a pirate now")
    assert len(matched2) == 1
    assert matched2[0].name == "pirate-voice"

    # Non-matching input returns nothing
    assert mgr.match_trigger("hello world") == []

    # Prompt injection -- the skill body must appear in the rendered section
    section = mgr.get_skills_for_prompt(max_tokens=3000)
    assert "pirate-voice" in section
    assert "pirate dialect" in section


def test_invalid_frontmatter_rejected_other_skills_load(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A broken skill file must not prevent other skills from loading."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Valid skill
    _write_skill(
        skills_dir,
        "good-skill",
        "Body of the good skill.",
        triggers=["/good"],
    )

    # Invalid: truncated frontmatter (no closing ---)
    bad = skills_dir / "bad-skill.md"
    bad.write_text(
        "---\nname: bad-skill\ndescription: broken\n\nBody without a closing fence so frontmatter never terminates.\n",
        encoding="utf-8",
    )

    # Unreadable file: simulate a file that fails to parse by writing
    # invalid UTF-8 (parse_skill_file will raise on .read_text()).
    unreadable = skills_dir / "binary.md"
    unreadable.write_bytes(b"\xff\xfe\x00\x00---name: x---\n")

    mgr = SkillManager(skills_dir=skills_dir)
    with caplog.at_level(logging.WARNING, logger="karna.skills.loader"):
        loaded = mgr.load_all()

    loaded_names = [s.name for s in loaded]
    # The good skill survived
    assert "good-skill" in loaded_names

    # The UTF-8-broken file was rejected with a warning logged
    warning_messages = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("binary.md" in m or "Failed to parse" in m for m in warning_messages)

    # The good skill is still queryable
    assert mgr.get_skill_by_name("good-skill") is not None
