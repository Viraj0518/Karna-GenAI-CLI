"""Tests for auto-memory extraction (E7).

Covers:
- Feedback pattern detection (positive and negative)
- User profile detection
- Project fact detection
- Reference detection
- Deduplication (don't save duplicate memories)
- Rate limiting (max 1 save per 5 turns)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karna.memory.extractor import (
    MemoryExtractor,
    _RateLimiter,
)
from karna.memory.manager import MemoryManager

# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture()
def memory_dir(tmp_path: Path) -> Path:
    """Provide a fresh temporary memory directory."""
    d = tmp_path / "memory"
    d.mkdir()
    return d


@pytest.fixture()
def mm(memory_dir: Path) -> MemoryManager:
    """Provide a MemoryManager pointing at the temp directory."""
    return MemoryManager(memory_dir=memory_dir)


@pytest.fixture()
def extractor(mm: MemoryManager) -> MemoryExtractor:
    """Provide a MemoryExtractor with rate limiting disabled for tests."""
    ext = MemoryExtractor(memory_manager=mm)
    # Set the rate limiter to allow immediate saves
    ext._rate_limiter = _RateLimiter(min_turns_between_saves=0)
    ext._rate_limiter._turns_since_last_save = 0
    return ext


# --------------------------------------------------------------------------- #
#  Negative feedback detection
# --------------------------------------------------------------------------- #


class TestNegativeFeedback:
    """User corrections should be detected as feedback memories."""

    def test_dont_do_that(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("no, don't use tabs for indentation")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"

    def test_stop_doing_x(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("please stop adding docstrings to every function")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"

    def test_thats_wrong(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("that's wrong, the API endpoint is /v2/users")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"

    def test_not_like_that(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("not like that, use the factory pattern instead")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"

    def test_never_use(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("never use print statements for logging")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"

    def test_always_use(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("always use ruff for linting, not flake8")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"

    def test_instead_of(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("instead of unittest, use pytest for all tests")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"


# --------------------------------------------------------------------------- #
#  Positive feedback detection
# --------------------------------------------------------------------------- #


class TestPositiveFeedback:
    """User confirmations should be detected as positive feedback memories."""

    def test_yes_exactly(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("yes, exactly like that")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"

    def test_thats_perfect(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("that's perfect, keep doing it this way")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"

    def test_good_approach(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("good approach with the decorator pattern")
        assert len(candidates) >= 1
        assert candidates[0].type == "feedback"


# --------------------------------------------------------------------------- #
#  User profile detection
# --------------------------------------------------------------------------- #


class TestUserProfile:
    """Self-identification patterns should be detected as user memories."""

    def test_im_a_developer(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("I'm a backend developer working on microservices")
        assert any(c.type == "user" for c in candidates)

    def test_i_work_on(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("I work on the payments team at Acme Corp")
        assert any(c.type == "user" for c in candidates)

    def test_my_role_is(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("my role is tech lead for the platform team")
        assert any(c.type == "user" for c in candidates)

    def test_im_new_to(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("I'm new to Rust, coming from Python")
        assert any(c.type == "user" for c in candidates)

    def test_i_prefer(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("I prefer functional programming patterns")
        assert any(c.type == "user" for c in candidates)


# --------------------------------------------------------------------------- #
#  Project fact detection
# --------------------------------------------------------------------------- #


class TestProjectFacts:
    """Project decisions should be detected as project memories."""

    def test_we_use(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("we use PostgreSQL for the main database")
        assert any(c.type == "project" for c in candidates)

    def test_our_convention(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("our convention is to prefix all API routes with /api/v2")
        assert any(c.type == "project" for c in candidates)

    def test_deploy_to(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("deploy to AWS ECS in us-east-1")
        assert any(c.type == "project" for c in candidates)

    def test_data_is_in(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("the data is stored in S3 under the analytics bucket")
        assert any(c.type == "project" for c in candidates)

    def test_we_dont(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("we don't use ORMs, all SQL is raw queries")
        assert any(c.type == "project" for c in candidates)

    def test_we_are_using(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("we're using Terraform for infrastructure")
        assert any(c.type == "project" for c in candidates)


# --------------------------------------------------------------------------- #
#  Reference detection
# --------------------------------------------------------------------------- #


class TestReferences:
    """External pointers should be detected as reference memories."""

    def test_url_detection(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("check https://grafana.internal.io/dashboard/main")
        assert any(c.type == "reference" for c in candidates)

    def test_bugs_tracked_in(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("bugs are tracked in Linear under the Platform project")
        assert any(c.type == "reference" for c in candidates)

    def test_docs_are_at(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("docs are at https://docs.example.com/api")
        assert any(c.type == "reference" for c in candidates)

    def test_slack_channel(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("ask in the slack channel #platform-eng")
        assert any(c.type == "reference" for c in candidates)


# --------------------------------------------------------------------------- #
#  No false positives
# --------------------------------------------------------------------------- #


class TestNoFalsePositives:
    """Normal conversation should NOT trigger memory extraction."""

    def test_simple_question(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("how do I write a for loop in Python?")
        # Should not detect meaningful patterns (may detect "I" but we check it's not overwhelming)
        feedback = [c for c in candidates if c.type == "feedback"]
        assert len(feedback) == 0

    def test_code_request(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("write a function to parse JSON from a file")
        feedback = [c for c in candidates if c.type == "feedback"]
        assert len(feedback) == 0

    def test_empty_message(self, extractor: MemoryExtractor) -> None:
        candidates = extractor.detect_candidates("")
        assert len(candidates) == 0


# --------------------------------------------------------------------------- #
#  Deduplication
# --------------------------------------------------------------------------- #


class TestDedup:
    """Don't save memories that are too similar to existing ones."""

    def test_no_duplicate_save(self, extractor: MemoryExtractor, mm: MemoryManager) -> None:
        # First save
        saved1 = extractor.extract_and_save("always use ruff for linting, not flake8")
        assert len(saved1) == 1

        # Reset rate limiter for second attempt
        extractor._rate_limiter._turns_since_last_save = 999

        # Second save of very similar content
        saved2 = extractor.extract_and_save("always use ruff for linting instead of flake8")
        assert len(saved2) == 0  # Should be deduped

    def test_different_content_not_deduped(self, extractor: MemoryExtractor) -> None:
        saved1 = extractor.extract_and_save("we use PostgreSQL for the main database")
        assert len(saved1) == 1

        extractor._rate_limiter._turns_since_last_save = 999

        saved2 = extractor.extract_and_save("deploy to AWS ECS in us-east-1")
        assert len(saved2) == 1  # Different content, should save


# --------------------------------------------------------------------------- #
#  Rate limiting
# --------------------------------------------------------------------------- #


class TestRateLimiting:
    """Rate limiter should prevent saving too frequently."""

    def test_rate_limiter_blocks_too_frequent(self) -> None:
        rl = _RateLimiter(min_turns_between_saves=5)
        assert not rl.can_save()  # 0 turns, can't save
        for _ in range(4):
            rl.tick()
        assert not rl.can_save()  # 4 turns, still can't
        rl.tick()
        assert rl.can_save()  # 5 turns, now can save
        rl.reset()
        assert not rl.can_save()  # Reset, can't save again

    def test_extract_and_save_respects_rate_limit(self, mm: MemoryManager) -> None:
        ext = MemoryExtractor(memory_manager=mm)
        # Rate limiter starts at 0 turns, min is 5
        saved = ext.extract_and_save("always use ruff for linting")
        assert len(saved) == 0  # Rate limited

        # Tick 5 times
        for _ in range(5):
            ext._rate_limiter.tick()

        saved = ext.extract_and_save("always use ruff for linting")
        assert len(saved) == 1  # Now it saves


# --------------------------------------------------------------------------- #
#  Similarity check
# --------------------------------------------------------------------------- #


class TestSimilarity:
    """Test the word-overlap similarity function."""

    def test_identical_texts(self) -> None:
        assert MemoryExtractor._is_similar("hello world", "hello world")

    def test_very_different(self) -> None:
        assert not MemoryExtractor._is_similar("apple banana cherry", "dog elephant fox")

    def test_partial_overlap(self) -> None:
        assert MemoryExtractor._is_similar(
            "always use ruff for linting",
            "always use ruff for linting not flake8",
        )

    def test_empty_strings(self) -> None:
        assert not MemoryExtractor._is_similar("", "hello")
        assert not MemoryExtractor._is_similar("hello", "")
        assert not MemoryExtractor._is_similar("", "")


# --------------------------------------------------------------------------- #
#  Integration: full save to disk
# --------------------------------------------------------------------------- #


class TestIntegration:
    """End-to-end: extract -> dedup -> save -> verify on disk."""

    def test_save_creates_file(self, extractor: MemoryExtractor, memory_dir: Path) -> None:
        saved = extractor.extract_and_save("we use PostgreSQL for the main database")
        assert len(saved) == 1
        entry = saved[0]
        assert entry.file_path.exists()
        assert entry.type == "project"

        # Verify the file content
        text = entry.file_path.read_text()
        assert "PostgreSQL" in text

    def test_save_updates_index(self, extractor: MemoryExtractor, memory_dir: Path) -> None:
        extractor.extract_and_save("I'm a senior backend engineer")
        index_path = memory_dir / "MEMORY.md"
        assert index_path.exists()
        index_content = index_path.read_text()
        assert "senior backend engineer" in index_content.lower() or "user profile" in index_content.lower()
