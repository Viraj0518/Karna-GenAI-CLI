"""Tests for configurable memory system.

Covers:
- Default config values for memory section
- Custom memory types (add "runbook", "sop" to types list)
- Custom directory path
- auto_extract=False disables extraction
- Custom rate limit
- Custom dedup threshold
- Invalid memory type rejected on save
- Config serialization to/from TOML
- /memory types slash command output
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from karna.config import (
    _BUILTIN_MEMORY_TYPES,
    KarnaConfig,
    MemoryConfig,
    load_config,
    save_config,
)
from karna.memory.extractor import MemoryExtractor, _RateLimiter
from karna.memory.manager import MemoryManager
from karna.models import Conversation

# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def memory_dir(tmp_path: Path) -> Path:
    d = tmp_path / "memory"
    d.mkdir()
    return d


@pytest.fixture()
def mm(memory_dir: Path) -> MemoryManager:
    return MemoryManager(memory_dir=memory_dir)


# --------------------------------------------------------------------------- #
#  Default config values
# --------------------------------------------------------------------------- #


class TestDefaultValues:
    """Default memory config should match the spec."""

    def test_default_memory_config(self) -> None:
        cfg = KarnaConfig()
        mem = cfg.memory
        assert mem.directory == "~/.karna/memory"
        assert mem.types == ["user", "feedback", "project", "reference"]
        assert mem.auto_extract is True
        assert mem.rate_limit_turns == 5
        assert mem.dedup_threshold == 0.60
        assert mem.index_file == "MEMORY.md"

    def test_builtin_types_constant(self) -> None:
        assert set(_BUILTIN_MEMORY_TYPES) == {"user", "feedback", "project", "reference"}


# --------------------------------------------------------------------------- #
#  Custom memory types
# --------------------------------------------------------------------------- #


class TestCustomTypes:
    """Custom types like 'runbook' and 'sop' should be accepted."""

    def test_custom_types_in_config(self) -> None:
        cfg = KarnaConfig(
            memory=MemoryConfig(
                types=["user", "feedback", "project", "reference", "runbook", "sop"],
            )
        )
        assert "runbook" in cfg.memory.types
        assert "sop" in cfg.memory.types

    def test_manager_accepts_custom_types(self, memory_dir: Path) -> None:
        mem_cfg = MemoryConfig(
            directory=str(memory_dir),
            types=["user", "feedback", "project", "reference", "runbook"],
        )
        mgr = MemoryManager(memory_config=mem_cfg)
        path = mgr.save_memory(
            name="Deploy procedure",
            type="runbook",
            description="How to deploy the app",
            content="Step 1: ...",
        )
        assert path.exists()

    def test_manager_rejects_unknown_type(self, memory_dir: Path) -> None:
        mem_cfg = MemoryConfig(
            directory=str(memory_dir),
            types=["user", "feedback"],
        )
        mgr = MemoryManager(memory_config=mem_cfg)
        with pytest.raises(ValueError, match="Invalid memory type"):
            mgr.save_memory(
                name="Some project fact",
                type="project",
                description="Should fail",
                content="Not allowed",
            )

    def test_extractor_filters_disallowed_types(self, memory_dir: Path) -> None:
        """Extractor should not produce candidates for disabled types."""
        mem_cfg = MemoryConfig(
            directory=str(memory_dir),
            types=["user", "feedback"],  # no 'project' or 'reference'
        )
        mgr = MemoryManager(memory_config=mem_cfg)
        ext = MemoryExtractor(memory_manager=mgr, memory_config=mem_cfg)
        ext._rate_limiter = _RateLimiter(min_turns_between_saves=0)
        ext._rate_limiter._turns_since_last_save = 999

        # This message would normally detect a project fact
        candidates = ext.detect_candidates("we use PostgreSQL for the main database")
        project_candidates = [c for c in candidates if c.type == "project"]
        assert len(project_candidates) == 0


# --------------------------------------------------------------------------- #
#  Custom directory path
# --------------------------------------------------------------------------- #


class TestCustomDirectory:
    """Memory files should be stored in the configured directory."""

    def test_custom_directory(self, tmp_path: Path) -> None:
        custom_dir = tmp_path / "custom_memory"
        mem_cfg = MemoryConfig(directory=str(custom_dir))
        mgr = MemoryManager(memory_config=mem_cfg)
        path = mgr.save_memory(
            name="Test",
            type="user",
            description="test entry",
            content="hello",
        )
        assert path.parent == custom_dir
        assert custom_dir.exists()

    def test_tilde_expansion(self) -> None:
        mem_cfg = MemoryConfig(directory="~/my_memories")
        mgr = MemoryManager(memory_config=mem_cfg)
        assert str(mgr.memory_dir) == str(Path.home() / "my_memories")


# --------------------------------------------------------------------------- #
#  auto_extract=False disables extraction
# --------------------------------------------------------------------------- #


class TestAutoExtractDisabled:
    """When auto_extract is False, the extractor should skip entirely."""

    def test_auto_extract_false(self, memory_dir: Path) -> None:
        mem_cfg = MemoryConfig(directory=str(memory_dir), auto_extract=False)
        mgr = MemoryManager(memory_config=mem_cfg)
        ext = MemoryExtractor(memory_manager=mgr, memory_config=mem_cfg)
        ext._rate_limiter = _RateLimiter(min_turns_between_saves=0)
        ext._rate_limiter._turns_since_last_save = 999

        saved = ext.extract_and_save("always use ruff for linting, not flake8")
        assert len(saved) == 0

    def test_auto_extract_true(self, memory_dir: Path) -> None:
        mem_cfg = MemoryConfig(directory=str(memory_dir), auto_extract=True)
        mgr = MemoryManager(memory_config=mem_cfg)
        ext = MemoryExtractor(memory_manager=mgr, memory_config=mem_cfg)
        ext._rate_limiter = _RateLimiter(min_turns_between_saves=0)
        ext._rate_limiter._turns_since_last_save = 999

        saved = ext.extract_and_save("always use ruff for linting, not flake8")
        assert len(saved) >= 1


# --------------------------------------------------------------------------- #
#  Custom rate limit
# --------------------------------------------------------------------------- #


class TestCustomRateLimit:
    """Rate limit should respect the configured value."""

    def test_custom_rate_limit(self, memory_dir: Path) -> None:
        mem_cfg = MemoryConfig(directory=str(memory_dir), rate_limit_turns=3)
        mgr = MemoryManager(memory_config=mem_cfg)
        ext = MemoryExtractor(memory_manager=mgr, memory_config=mem_cfg)

        # Each call to extract_and_save ticks once.
        # With rate_limit_turns=3, can_save() is True when turns >= 3.
        # Turn 1: can_save()=False
        saved = ext.extract_and_save("always use ruff for linting")
        assert len(saved) == 0

        # Turn 2: can_save()=False
        saved = ext.extract_and_save("never use print for logging")
        assert len(saved) == 0

        # Turn 3: can_save()=True -> saves
        saved = ext.extract_and_save("always use ruff for linting, not flake8")
        assert len(saved) == 1

    def test_zero_rate_limit_saves_every_turn(self, memory_dir: Path) -> None:
        mem_cfg = MemoryConfig(directory=str(memory_dir), rate_limit_turns=0)
        mgr = MemoryManager(memory_config=mem_cfg)
        ext = MemoryExtractor(memory_manager=mgr, memory_config=mem_cfg)

        saved = ext.extract_and_save("we use PostgreSQL for the main database")
        assert len(saved) == 1


# --------------------------------------------------------------------------- #
#  Custom dedup threshold
# --------------------------------------------------------------------------- #


class TestCustomDedupThreshold:
    """Dedup threshold should control similarity sensitivity."""

    def test_high_threshold_allows_more(self, memory_dir: Path) -> None:
        # With a high threshold (0.95), nearly everything passes dedup
        mem_cfg = MemoryConfig(directory=str(memory_dir), dedup_threshold=0.95)
        mgr = MemoryManager(memory_config=mem_cfg)
        ext = MemoryExtractor(memory_manager=mgr, memory_config=mem_cfg)
        ext._rate_limiter = _RateLimiter(min_turns_between_saves=0)
        ext._rate_limiter._turns_since_last_save = 999

        saved1 = ext.extract_and_save("always use ruff for linting, not flake8")
        assert len(saved1) == 1

        ext._rate_limiter._turns_since_last_save = 999
        # Very similar content -- with default 0.6 threshold would be deduped,
        # but 0.95 should let it through
        saved2 = ext.extract_and_save("always use ruff for linting instead of flake8")
        assert len(saved2) == 1

    def test_low_threshold_blocks_more(self, memory_dir: Path) -> None:
        # With very low threshold (0.1), even loosely related is a dup
        mem_cfg = MemoryConfig(directory=str(memory_dir), dedup_threshold=0.1)
        mgr = MemoryManager(memory_config=mem_cfg)
        ext = MemoryExtractor(memory_manager=mgr, memory_config=mem_cfg)
        ext._rate_limiter = _RateLimiter(min_turns_between_saves=0)
        ext._rate_limiter._turns_since_last_save = 999

        saved1 = ext.extract_and_save("always use ruff for linting, not flake8")
        assert len(saved1) == 1

        ext._rate_limiter._turns_since_last_save = 999
        saved2 = ext.extract_and_save("always use ruff for linting instead of flake8")
        assert len(saved2) == 0  # Blocked by low threshold


# --------------------------------------------------------------------------- #
#  Invalid memory type rejected on save
# --------------------------------------------------------------------------- #


class TestInvalidTypeRejected:
    """Manager should reject types not in the configured list."""

    def test_default_manager_rejects_bogus(self, mm: MemoryManager) -> None:
        with pytest.raises(ValueError, match="Invalid memory type"):
            mm.save_memory(
                name="test",
                type="bogus",
                description="should fail",
                content="nope",
            )

    def test_config_manager_rejects_unlisted(self, memory_dir: Path) -> None:
        mem_cfg = MemoryConfig(
            directory=str(memory_dir),
            types=["user", "feedback"],
        )
        mgr = MemoryManager(memory_config=mem_cfg)
        with pytest.raises(ValueError, match="Invalid memory type"):
            mgr.save_memory(
                name="ref",
                type="reference",
                description="not in types list",
                content="blocked",
            )


# --------------------------------------------------------------------------- #
#  Config serialization to/from TOML
# --------------------------------------------------------------------------- #


class TestSerialization:
    """Memory config should round-trip through TOML save/load."""

    def test_roundtrip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_karna = tmp_path / ".karna"
        monkeypatch.setattr("karna.config.KARNA_DIR", fake_karna)
        monkeypatch.setattr("karna.config.CONFIG_PATH", fake_karna / "config.toml")

        cfg = KarnaConfig(
            memory=MemoryConfig(
                directory="/custom/memory",
                types=["user", "feedback", "project", "reference", "runbook", "sop"],
                auto_extract=False,
                rate_limit_turns=10,
                dedup_threshold=0.80,
                index_file="INDEX.md",
            )
        )
        save_config(cfg)
        loaded = load_config()

        assert loaded.memory.directory == "/custom/memory"
        assert loaded.memory.types == ["user", "feedback", "project", "reference", "runbook", "sop"]
        assert loaded.memory.auto_extract is False
        assert loaded.memory.rate_limit_turns == 10
        assert loaded.memory.dedup_threshold == 0.80
        assert loaded.memory.index_file == "INDEX.md"

    def test_toml_has_memory_section(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_karna = tmp_path / ".karna"
        monkeypatch.setattr("karna.config.KARNA_DIR", fake_karna)
        monkeypatch.setattr("karna.config.CONFIG_PATH", fake_karna / "config.toml")

        cfg = KarnaConfig()
        save_config(cfg)

        raw = (fake_karna / "config.toml").read_text()
        assert "[memory]" in raw
        assert 'directory = "~/.karna/memory"' in raw

    def test_defaults_preserved_when_section_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If config.toml has no [memory] section, defaults should load."""
        fake_karna = tmp_path / ".karna"
        fake_karna.mkdir(parents=True)
        monkeypatch.setattr("karna.config.KARNA_DIR", fake_karna)
        monkeypatch.setattr("karna.config.CONFIG_PATH", fake_karna / "config.toml")

        # Write a minimal config without [memory]
        (fake_karna / "config.toml").write_text('active_model = "openrouter/auto"\nactive_provider = "openrouter"\n')
        # Also need a credentials dir for permission checks
        (fake_karna / "credentials").mkdir(exist_ok=True)

        loaded = load_config()
        assert loaded.memory.directory == "~/.karna/memory"
        assert loaded.memory.types == ["user", "feedback", "project", "reference"]
        assert loaded.memory.auto_extract is True


# --------------------------------------------------------------------------- #
#  /memory types slash command
# --------------------------------------------------------------------------- #


class TestSlashMemoryTypes:
    """/memory types should list configured memory types."""

    def test_memory_types_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_karna = tmp_path / ".karna"
        monkeypatch.setattr("karna.config.KARNA_DIR", fake_karna)
        monkeypatch.setattr("karna.config.CONFIG_PATH", fake_karna / "config.toml")

        # Write config with custom types
        cfg = KarnaConfig(
            memory=MemoryConfig(
                types=["user", "feedback", "project", "reference", "runbook"],
            )
        )
        save_config(cfg)

        output = StringIO()
        console = Console(file=output, force_terminal=True)
        conversation = Conversation(
            provider="test",
            model="test",
            messages=[],
        )

        from karna.tui.slash import handle_slash_command

        handle_slash_command(
            "/memory types",
            console=console,
            config=cfg,
            conversation=conversation,
        )

        rendered = output.getvalue()
        assert "user" in rendered
        assert "feedback" in rendered
        assert "runbook" in rendered
        assert "built-in" in rendered
        assert "custom" in rendered

    def test_memory_types_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_karna = tmp_path / ".karna"
        monkeypatch.setattr("karna.config.KARNA_DIR", fake_karna)
        monkeypatch.setattr("karna.config.CONFIG_PATH", fake_karna / "config.toml")

        cfg = KarnaConfig()
        save_config(cfg)

        output = StringIO()
        console = Console(file=output, force_terminal=True)
        conversation = Conversation(
            provider="test",
            model="test",
            messages=[],
        )

        from karna.tui.slash import handle_slash_command

        handle_slash_command(
            "/memory types",
            console=console,
            config=cfg,
            conversation=conversation,
        )

        rendered = output.getvalue()
        assert "user" in rendered
        assert "built-in" in rendered
        # No custom types with defaults
        assert "custom" not in rendered


# --------------------------------------------------------------------------- #
#  MemoryManager with config uses custom index file
# --------------------------------------------------------------------------- #


class TestCustomIndexFile:
    """Manager should use the configured index file name."""

    def test_custom_index_file(self, tmp_path: Path) -> None:
        memory_dir = tmp_path / "mem"
        mem_cfg = MemoryConfig(
            directory=str(memory_dir),
            index_file="INDEX.md",
        )
        mgr = MemoryManager(memory_config=mem_cfg)
        mgr.save_memory(
            name="Test",
            type="user",
            description="test",
            content="hello",
        )
        assert (memory_dir / "INDEX.md").exists()
        assert not (memory_dir / "MEMORY.md").exists()
