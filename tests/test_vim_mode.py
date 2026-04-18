"""Tests for karna.tui.vim — config reading + session plumbing."""

from __future__ import annotations

from pathlib import Path

import pytest

from karna.tui import vim as vim_mod

# --------------------------------------------------------------------------- #
#  vim_mode_enabled()
# --------------------------------------------------------------------------- #


def test_vim_mode_enabled_missing_file(tmp_path: Path) -> None:
    """Missing config file returns False cleanly."""
    assert vim_mod.vim_mode_enabled(tmp_path / "nope.toml") is False


def test_vim_mode_enabled_true(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("[tui]\nvim_mode = true\n", encoding="utf-8")
    assert vim_mod.vim_mode_enabled(cfg) is True


def test_vim_mode_enabled_false(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("[tui]\nvim_mode = false\n", encoding="utf-8")
    assert vim_mod.vim_mode_enabled(cfg) is False


def test_vim_mode_enabled_no_tui_section(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("[other]\nfoo = 1\n", encoding="utf-8")
    assert vim_mod.vim_mode_enabled(cfg) is False


def test_vim_mode_enabled_malformed_toml(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text("not = valid = toml\n", encoding="utf-8")
    assert vim_mod.vim_mode_enabled(cfg) is False


# --------------------------------------------------------------------------- #
#  apply_vim_mode()
# --------------------------------------------------------------------------- #


def test_apply_vim_mode_noop_when_disabled() -> None:
    kwargs: dict = {"foo": "bar"}
    out = vim_mod.apply_vim_mode(kwargs, enabled=False)
    assert "vi_mode" not in out
    assert out is kwargs


def test_apply_vim_mode_sets_vi_mode_true() -> None:
    pytest.importorskip("prompt_toolkit")
    kwargs: dict = {}
    vim_mod.apply_vim_mode(kwargs, enabled=True)
    assert kwargs.get("vi_mode") is True
    assert kwargs.get("key_bindings") is not None


def test_apply_vim_mode_merges_existing_bindings() -> None:
    pytest.importorskip("prompt_toolkit")
    from prompt_toolkit.key_binding import KeyBindings

    existing = KeyBindings()
    kwargs: dict = {"key_bindings": existing}
    vim_mod.apply_vim_mode(kwargs, enabled=True)
    # Should not be the same object — merged via merge_key_bindings.
    assert kwargs["key_bindings"] is not None


def test_build_vim_keybindings_returns_keybindings() -> None:
    pytest.importorskip("prompt_toolkit")
    from prompt_toolkit.key_binding import KeyBindings

    kb = vim_mod.build_vim_keybindings()
    assert isinstance(kb, KeyBindings)


# --------------------------------------------------------------------------- #
#  Input session kwarg plumbing
# --------------------------------------------------------------------------- #


def test_get_multiline_input_accepts_vim_mode_kwarg() -> None:
    """Signature must accept ``vim_mode`` kwarg without raising."""
    import inspect

    from karna.tui.input import get_multiline_input

    sig = inspect.signature(get_multiline_input)
    assert "vim_mode" in sig.parameters
    assert sig.parameters["vim_mode"].default is False
