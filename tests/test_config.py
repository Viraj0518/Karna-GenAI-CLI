"""Tests for Karna configuration management."""

from pathlib import Path

import pytest

from karna.config import KarnaConfig, load_config, save_config


def test_default_config_loads() -> None:
    """Default config should have sensible defaults."""
    cfg = KarnaConfig()
    assert cfg.active_model == "openrouter/auto"
    assert cfg.active_provider == "openrouter"
    assert cfg.max_tokens > 0
    assert 0.0 <= cfg.temperature <= 2.0


def test_save_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Config should survive a save/load roundtrip."""
    fake_karna = tmp_path / ".karna"
    monkeypatch.setattr("karna.config.KARNA_DIR", fake_karna)
    monkeypatch.setattr("karna.config.CONFIG_PATH", fake_karna / "config.toml")

    cfg = KarnaConfig(
        active_model="test-model",
        active_provider="openai",
        system_prompt="Test prompt",
        max_tokens=2048,
        temperature=0.5,
    )
    save_config(cfg)
    loaded = load_config()

    assert loaded.active_model == "test-model"
    assert loaded.active_provider == "openai"
    assert loaded.system_prompt == "Test prompt"
    assert loaded.max_tokens == 2048
    assert loaded.temperature == 0.5


def test_load_creates_default_on_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """load_config() should create a default file if none exists."""
    fake_karna = tmp_path / ".karna"
    monkeypatch.setattr("karna.config.KARNA_DIR", fake_karna)
    monkeypatch.setattr("karna.config.CONFIG_PATH", fake_karna / "config.toml")

    cfg = load_config()
    assert cfg.active_model == "openrouter/auto"
    assert (fake_karna / "config.toml").exists()
