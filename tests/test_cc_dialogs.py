"""Tests for the upstream-ported small-dialogs module.

Async IO-backed dialogs (confirm, press-enter, exit-flow, idle-return,
run-wizard) are driven by monkey-patching the module-level ``_prompt``
helper so we can assert the exact control flow without a real TTY.

Pure renderers (thinking toggle, ctrl+o hint, keybinding warnings) are
checked structurally via their Rich output.
"""

from __future__ import annotations

import asyncio
from typing import Iterator

import pytest
from rich.console import Console

from karna.tui.cc_components import dialogs as cc_dialogs
from karna.tui.cc_components.dialogs import (
    IdleReturnAction,
    _format_idle_duration,
    confirm,
    exit_flow,
    idle_return,
    press_enter_to_continue,
    random_goodbye,
    render_ctrl_o_to_expand,
    render_keybinding_warnings,
    render_thinking_toggle,
    run_wizard,
)


def _render(obj) -> str:
    buf = Console(record=True, width=120, color_system=None, force_terminal=False)
    buf.print(obj)
    return buf.export_text(clear=True)


def _stub_prompt(responses: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Queue ``responses`` as the answers to sequential ``_prompt`` calls."""
    it: Iterator[str] = iter(responses)

    async def fake_prompt(_message: str) -> str:
        try:
            return next(it)
        except StopIteration:
            return ""

    monkeypatch.setattr(cc_dialogs, "_prompt", fake_prompt)


# --------------------------------------------------------------------------- #
#  1. confirm — Y/n parsing and default handling
# --------------------------------------------------------------------------- #


def test_confirm_parses_yes_no_and_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_prompt(["y"], monkeypatch)
    assert asyncio.run(confirm("proceed?")) is True

    _stub_prompt(["no"], monkeypatch)
    assert asyncio.run(confirm("proceed?", default=True)) is False

    # Empty input returns the default.
    _stub_prompt([""], monkeypatch)
    assert asyncio.run(confirm("proceed?", default=True)) is True
    _stub_prompt([""], monkeypatch)
    assert asyncio.run(confirm("proceed?", default=False)) is False

    # Invalid then valid — loops until parseable.
    _stub_prompt(["maybe", "YES"], monkeypatch)
    assert asyncio.run(confirm("proceed?")) is True


# --------------------------------------------------------------------------- #
#  2. press_enter_to_continue + exit_flow + idle_return
# --------------------------------------------------------------------------- #


def test_press_enter_and_flow_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    # press_enter_to_continue: resolves regardless of input content.
    _stub_prompt([""], monkeypatch)
    assert asyncio.run(press_enter_to_continue()) is None

    # exit_flow(unsaved=False) — default yes
    _stub_prompt([""], monkeypatch)
    assert asyncio.run(exit_flow(unsaved=False)) is True
    # exit_flow(unsaved=True) — default no, "y" forces exit
    _stub_prompt(["y"], monkeypatch)
    assert asyncio.run(exit_flow(unsaved=True)) is True
    _stub_prompt([""], monkeypatch)
    assert asyncio.run(exit_flow(unsaved=True)) is False

    # idle_return — boolean wrapper around the 4-way dialog.
    _stub_prompt([""], monkeypatch)  # default yes
    assert asyncio.run(idle_return(idle_minutes=12)) is True
    _stub_prompt(["n"], monkeypatch)
    assert asyncio.run(idle_return(idle_minutes=12)) is False

    # Goodbye picker returns one of the known messages.
    assert random_goodbye() in ("Goodbye!", "See ya!", "Bye!", "Catch you later!")


# --------------------------------------------------------------------------- #
#  3. idle-duration formatter (ported from upstream)
# --------------------------------------------------------------------------- #


def test_format_idle_duration_matches_cc_semantics() -> None:
    assert _format_idle_duration(0) == "< 1m"
    assert _format_idle_duration(5) == "5m"
    assert _format_idle_duration(59) == "59m"
    assert _format_idle_duration(60) == "1h"
    assert _format_idle_duration(125) == "2h 5m"
    assert _format_idle_duration(180) == "3h"


# --------------------------------------------------------------------------- #
#  4. run_wizard — linear traversal + :back + :cancel
# --------------------------------------------------------------------------- #


def test_run_wizard_collects_values_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_prompt(["alpha", "y", "2"], monkeypatch)
    steps = [
        {"key": "name", "prompt": "Name?"},
        {"key": "confirm", "prompt": "OK?", "type": "bool", "default": False},
        {
            "key": "color",
            "prompt": "Pick",
            "type": "choice",
            "choices": ["red", "green", "blue"],
        },
    ]
    result = asyncio.run(run_wizard(steps))
    assert result == {"name": "alpha", "confirm": True, "color": "green"}


def test_run_wizard_cancel_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_prompt([":cancel"], monkeypatch)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(run_wizard([{"key": "name", "prompt": "Name?"}]))


# --------------------------------------------------------------------------- #
#  5. Pure render helpers — thinking toggle, ctrl+o, keybinding warnings
# --------------------------------------------------------------------------- #


def test_render_helpers_produce_expected_text() -> None:
    on = render_thinking_toggle(True)
    off = render_thinking_toggle(False)
    assert "thinking on" in _render(on)
    assert "thinking off" in _render(off)
    # Both should include the sparkle glyph from upstream's vocabulary.
    assert "\u2726" in _render(on) and "\u2726" in _render(off)

    chip = render_ctrl_o_to_expand()
    chip_text = _render(chip)
    assert "Ctrl-O" in chip_text and "expand" in chip_text
    # Custom shortcut is rendered verbatim.
    chip2 = render_ctrl_o_to_expand("Ctrl-T")
    assert "Ctrl-T" in _render(chip2)

    # Empty conflicts → empty renderable (not an error).
    empty = render_keybinding_warnings([])
    assert _render(empty).strip() == ""

    warn = render_keybinding_warnings(["Ctrl-C clashes with exit"])
    warn_text = _render(warn)
    assert "Keybinding Configuration Issues" in warn_text
    assert "Ctrl-C clashes with exit" in warn_text


# --------------------------------------------------------------------------- #
#  6. IdleReturnAction enum — parity with upstream
# --------------------------------------------------------------------------- #


def test_idle_return_action_enum_parity() -> None:
    # upstream exposes: continue / clear / dismiss / never
    assert {a.value for a in IdleReturnAction} == {
        "continue",
        "clear",
        "dismiss",
        "never",
    }
