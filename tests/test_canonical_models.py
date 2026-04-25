"""Tests for the canonical model registry (B1).

Fixture-based — no network calls. The production data file
`karna/providers/canonical_models.json` is real, but these tests also use
a small synthetic fixture loaded via monkeypatching so the suite runs
deterministically regardless of what's in the live file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from karna import providers as P

FIXTURE = [
    {
        "id": "anthropic/claude-haiku-4-5",
        "provider": "anthropic",
        "context_window": 200000,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": True,
        "cost_per_mtok_input": 1.0,
        "cost_per_mtok_output": 5.0,
        "source": "direct",
    },
    {
        "id": "openai/gpt-4o",
        "provider": "openai",
        "context_window": 128000,
        "max_output": 16384,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": True,
        "supports_thinking": False,
        "cost_per_mtok_input": 2.5,
        "cost_per_mtok_output": 10.0,
        "source": "direct",
    },
    {
        "id": "ollama/qwen3",
        "provider": "ollama",
        "context_window": 262144,
        "max_output": 8192,
        "supports_tools": True,
        "supports_streaming": True,
        "supports_vision": False,
        "supports_thinking": True,
        "cost_per_mtok_input": 0.0,
        "cost_per_mtok_output": 0.0,
        "source": "direct",
    },
]


@pytest.fixture
def canonical_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Swap the canonical registry path for a 3-model fixture."""
    p = tmp_path / "fixture_models.json"
    p.write_text(json.dumps(FIXTURE))
    monkeypatch.setattr(P, "_CANONICAL_MODELS_PATH", p)
    monkeypatch.setattr(P, "_canonical_cache", None)
    monkeypatch.setattr(P, "_canonical_by_id", None)
    return p


class TestLoad:
    def test_returns_full_list(self, canonical_fixture: Path) -> None:
        models = P.canonical_models()
        assert len(models) == 3
        assert {m["id"] for m in models} == {
            "anthropic/claude-haiku-4-5",
            "openai/gpt-4o",
            "ollama/qwen3",
        }

    def test_caches_across_calls(self, canonical_fixture: Path) -> None:
        a = P.canonical_models()
        b = P.canonical_models()
        assert a == b
        # Mutating the returned list must not mutate the cache
        a.clear()
        assert len(P.canonical_models()) == 3

    def test_returns_empty_when_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(P, "_CANONICAL_MODELS_PATH", tmp_path / "nope.json")
        monkeypatch.setattr(P, "_canonical_cache", None)
        monkeypatch.setattr(P, "_canonical_by_id", None)
        assert P.canonical_models() == []


class TestCapabilities:
    def test_lookup_by_provider_colon_model(self, canonical_fixture: Path) -> None:
        caps = P.model_capabilities("anthropic:claude-haiku-4-5")
        assert caps is not None
        assert caps["context_window"] == 200000
        assert caps["supports_tools"] is True

    def test_lookup_by_org_slash_model(self, canonical_fixture: Path) -> None:
        caps = P.model_capabilities("openai/gpt-4o")
        assert caps is not None
        assert caps["context_window"] == 128000

    def test_lookup_by_bare_model_defaults_to_openrouter(
        self,
        canonical_fixture: Path,
    ) -> None:
        # Bare name goes through resolve_model which defaults to openrouter;
        # lookup falls through the candidate list and matches the literal model
        # key if the org slug happens to match.
        assert P.model_capabilities("gpt-4o") is None  # openrouter/gpt-4o not in fixture

    def test_returns_none_on_unknown_model(self, canonical_fixture: Path) -> None:
        assert P.model_capabilities("nonsense/nope-4.2") is None

    def test_returned_dict_is_a_copy(self, canonical_fixture: Path) -> None:
        caps = P.model_capabilities("anthropic:claude-haiku-4-5")
        caps["context_window"] = 1  # type: ignore[index]
        fresh = P.model_capabilities("anthropic:claude-haiku-4-5")
        assert fresh is not None
        assert fresh["context_window"] == 200000

    def test_thinking_flag_preserved(self, canonical_fixture: Path) -> None:
        assert P.model_capabilities("ollama/qwen3")["supports_thinking"] is True
        assert P.model_capabilities("openai/gpt-4o")["supports_thinking"] is False


class TestLiveRegistry:
    """Quick-sanity checks against the shipped canonical_models.json."""

    def test_file_exists_and_large(self) -> None:
        # Load fresh (no monkeypatch) so we hit the real file.
        P._canonical_cache = None
        P._canonical_by_id = None
        models = P.canonical_models()
        assert len(models) >= 1000, f"expected >=1000 models in shipped registry, got {len(models)}"

    def test_shipped_registry_has_anthropic_haiku(self) -> None:
        P._canonical_cache = None
        P._canonical_by_id = None
        caps = P.model_capabilities("anthropic/claude-haiku-4-5")
        assert caps is not None
        assert caps["supports_tools"] is True
        assert caps["context_window"] >= 100000

    def test_shipped_registry_has_openrouter_entries(self) -> None:
        P._canonical_cache = None
        P._canonical_by_id = None
        models = P.canonical_models()
        sources = {m["source"] for m in models}
        assert "openrouter" in sources
        assert "direct" in sources

    def test_all_entries_have_required_fields(self) -> None:
        P._canonical_cache = None
        P._canonical_by_id = None
        required = {
            "id",
            "provider",
            "context_window",
            "max_output",
            "supports_tools",
            "supports_streaming",
            "supports_vision",
            "supports_thinking",
            "cost_per_mtok_input",
            "cost_per_mtok_output",
        }
        for m in P.canonical_models():
            assert required <= set(m), f"missing fields in {m['id']}: {required - set(m)}"
