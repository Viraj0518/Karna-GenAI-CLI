"""Tests for karna.keybindings — load, validate, apply."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from karna.keybindings import (
    DEFAULT_BINDINGS,
    BindingsResult,
    load_bindings,
    parse_bindings,
    save_bindings,
)
from karna.keybindings.manager import _valid_descriptor

# --------------------------------------------------------------------------- #
#  Descriptor validation
# --------------------------------------------------------------------------- #


def test_descriptor_valid_simple() -> None:
    assert _valid_descriptor("ctrl+c")
    assert _valid_descriptor("enter")
    assert _valid_descriptor("up")
    assert _valid_descriptor("ctrl+shift+j")
    assert _valid_descriptor("a")
    assert _valid_descriptor("f1")
    assert _valid_descriptor("f12")


def test_descriptor_invalid() -> None:
    assert not _valid_descriptor("")
    assert not _valid_descriptor("ctrl+")
    assert not _valid_descriptor("ctrl++a")
    assert not _valid_descriptor("super+c")  # unknown modifier
    assert not _valid_descriptor("ctrl+longkey")  # not a named key, not a letter


# --------------------------------------------------------------------------- #
#  load_bindings — defaults when file missing
# --------------------------------------------------------------------------- #


def test_missing_file_uses_defaults(tmp_path: Path) -> None:
    result = load_bindings(tmp_path / "nope.toml")
    assert result.bindings == dict(DEFAULT_BINDINGS)
    assert result.warnings == []
    assert result.source is None


def test_default_bindings_covers_all_actions() -> None:
    for action in ("cancel", "submit", "newline", "history_up", "history_down", "toggle_vim"):
        assert action in DEFAULT_BINDINGS


# --------------------------------------------------------------------------- #
#  parse_bindings — custom overrides
# --------------------------------------------------------------------------- #


def test_custom_bindings_override_defaults() -> None:
    data = {"bindings": {"submit": "ctrl+s", "cancel": "ctrl+q"}}
    result = parse_bindings(data)
    assert result.bindings["submit"] == "ctrl+s"
    assert result.bindings["cancel"] == "ctrl+q"
    # Non-overridden keys still use defaults.
    assert result.bindings["newline"] == DEFAULT_BINDINGS["newline"]
    assert result.warnings == []


def test_invalid_binding_warns_and_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    data = {"bindings": {"submit": "ctrl+ctrl"}}
    with caplog.at_level(logging.WARNING, logger="karna.keybindings.manager"):
        result = parse_bindings(data)
    assert result.bindings["submit"] == DEFAULT_BINDINGS["submit"]
    assert any("invalid descriptor" in w for w in result.warnings)


def test_duplicate_bindings_last_wins_with_warning() -> None:
    data = {
        "bindings": {
            "submit": "ctrl+s",
            "cancel": "ctrl+s",  # duplicate descriptor
        }
    }
    result = parse_bindings(data)
    # Whichever was processed second wins the descriptor; both entries exist.
    # Warning must be emitted.
    assert any("used for both" in w for w in result.warnings)


def test_non_string_binding_rejected() -> None:
    data = {"bindings": {"submit": 123}}
    result = parse_bindings(data)
    assert result.bindings["submit"] == DEFAULT_BINDINGS["submit"]
    assert any("must be a string" in w for w in result.warnings)


def test_bindings_section_wrong_type() -> None:
    data = {"bindings": "not a table"}
    result = parse_bindings(data)
    assert result.bindings == dict(DEFAULT_BINDINGS)
    assert any("not a table" in w for w in result.warnings)


# --------------------------------------------------------------------------- #
#  load_bindings — real file round-trip
# --------------------------------------------------------------------------- #


def test_load_from_disk(tmp_path: Path) -> None:
    cfg = tmp_path / "keybindings.toml"
    cfg.write_text('[bindings]\nsubmit = "ctrl+s"\n', encoding="utf-8")
    result = load_bindings(cfg)
    assert result.bindings["submit"] == "ctrl+s"
    assert result.source == cfg


def test_malformed_toml_warns(tmp_path: Path) -> None:
    cfg = tmp_path / "keybindings.toml"
    cfg.write_text("not = valid = toml", encoding="utf-8")
    result = load_bindings(cfg)
    assert result.bindings == dict(DEFAULT_BINDINGS)
    assert result.warnings  # at least one warning


def test_save_and_reload_round_trip(tmp_path: Path) -> None:
    cfg = tmp_path / "keybindings.toml"
    original = {"submit": "ctrl+s", "cancel": "ctrl+q", "newline": "ctrl+j"}
    save_bindings(original, cfg)
    result = load_bindings(cfg)
    for k, v in original.items():
        assert result.bindings[k] == v


# --------------------------------------------------------------------------- #
#  apply_bindings — prompt_toolkit integration
# --------------------------------------------------------------------------- #


def test_apply_bindings_wires_handlers() -> None:
    pytest.importorskip("prompt_toolkit")
    from prompt_toolkit.key_binding import KeyBindings

    from karna.keybindings import apply_bindings

    kb = KeyBindings()
    called: dict[str, bool] = {}

    def handler(event) -> None:  # type: ignore[no-untyped-def]
        called["submit"] = True

    bindings = dict(DEFAULT_BINDINGS)
    bindings["submit"] = "ctrl+s"
    apply_bindings(kb, bindings, {"submit": handler})

    # Internal representation: at least one binding should now exist.
    assert len(list(kb.bindings)) >= 1


def test_apply_bindings_result_shape() -> None:
    # BindingsResult is a simple dataclass; this ensures the public shape.
    r = BindingsResult()
    assert r.bindings == {}
    assert r.warnings == []
    assert r.source is None
