"""Tests for the CC-ported picker dialogs.

The pickers are built on ``prompt_toolkit.Application`` with
``full_screen=False``. We drive them via a pipe input + ``DummyOutput``
so no live TTY is required. One test per picker exercises its full
keystroke lifecycle (navigate, select, cancel).
"""

from __future__ import annotations

import asyncio

import pytest

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from karna.tui.cc_components import pickers


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _run(coro_factory, keys: str):
    """Run an async picker coroutine with a scripted key sequence.

    ``coro_factory`` must be a zero-arg callable that returns the
    coroutine to await. We create a fresh pipe input per invocation
    because prompt_toolkit closes it on app shutdown.
    """

    async def runner():
        with create_pipe_input() as pipe:
            with create_app_session(input=pipe, output=DummyOutput()):
                pipe.send_text(keys)
                return await coro_factory()

    return asyncio.run(runner())


# Keystroke literals matching prompt_toolkit's input parser.
KEY_UP = "\x1b[A"
KEY_DOWN = "\x1b[B"
KEY_ENTER = "\r"
KEY_ESC = "\x1b\x1b"  # double-escape to dodge the timeout-based ambiguity


# --------------------------------------------------------------------------- #
#  1. Base Picker — navigation + selection
# --------------------------------------------------------------------------- #


def test_picker_enter_returns_first_option_id() -> None:
    p = pickers.Picker()
    opts = [
        ("one", "Option one", "first"),
        ("two", "Option two", "second"),
        ("three", "Option three", "third"),
    ]
    got = _run(lambda: p.prompt("Pick one", opts), KEY_ENTER)
    assert got == "one"


def test_picker_arrow_down_then_enter_selects_second_option() -> None:
    p = pickers.Picker()
    opts = [
        ("a", "Alpha", ""),
        ("b", "Beta", ""),
        ("c", "Gamma", ""),
    ]
    got = _run(lambda: p.prompt("Pick one", opts), KEY_DOWN + KEY_ENTER)
    assert got == "b"


def test_picker_escape_returns_none() -> None:
    p = pickers.Picker()
    opts = [("x", "X", ""), ("y", "Y", "")]
    got = _run(lambda: p.prompt("Pick", opts), KEY_ESC)
    assert got is None


# --------------------------------------------------------------------------- #
#  2. pick_model — provider grouping + column layout
# --------------------------------------------------------------------------- #


class _FakeModel:
    """Minimal stand-in for `karna.models.ModelInfo` (duck-typed)."""

    def __init__(self, mid: str, provider: str, ctx: int, cap: int, name: str = "") -> None:
        self.id = mid
        self.provider = provider
        self.context_window = ctx
        self.max_output_tokens = cap
        self.name = name or mid


def test_pick_model_initial_selects_current_model() -> None:
    models = [
        _FakeModel("claude-opus-4-20250514", "anthropic", 200_000, 8192),
        _FakeModel("gpt-4o", "openai", 128_000, 16_384),
        _FakeModel("gemini-2.5-pro-preview", "vertex", 1_000_000, 8192),
    ]
    # Enter immediately -> should return the `current` id because the
    # picker seeks to it on open.
    got = _run(lambda: pickers.pick_model("gpt-4o", models), KEY_ENTER)
    assert got == "gpt-4o"


def test_model_rows_group_by_provider_and_show_columns() -> None:
    models = [
        _FakeModel("claude-opus-4-20250514", "anthropic", 200_000, 8192),
        _FakeModel("gpt-4o", "openai", 128_000, 16_384),
    ]
    rows = pickers._model_rows(models)
    # One row per model.
    assert len(rows) == 2
    ids = {r[0] for r in rows}
    assert ids == {"claude-opus-4-20250514", "gpt-4o"}
    # Label shows the ctx / max columns ported from CC's ModelPicker.
    assert any("ctx" in r[1] and "max" in r[1] for r in rows)
    # Description carries the provider name — used for grouping in CC.
    providers = {r[2].split()[0] for r in rows}
    assert providers == {"anthropic", "openai"}


# --------------------------------------------------------------------------- #
#  3. pick_theme — builtin palette options
# --------------------------------------------------------------------------- #


def test_pick_theme_enter_returns_current() -> None:
    got = _run(lambda: pickers.pick_theme("dark"), KEY_ENTER)
    assert got == "dark"


# --------------------------------------------------------------------------- #
#  4. pick_output_style — reads BUILTIN_STYLES
# --------------------------------------------------------------------------- #


def test_pick_output_style_enter_returns_current() -> None:
    got = _run(lambda: pickers.pick_output_style("default"), KEY_ENTER)
    assert got == "default"


def test_pick_output_style_navigates_to_next() -> None:
    from karna.tui.output_style import BUILTIN_STYLES

    names = list(BUILTIN_STYLES.keys())
    assert len(names) >= 2  # sanity — there should be several built-ins
    # Start on names[0], press Down, press Enter -> names[1].
    got = _run(lambda: pickers.pick_output_style(names[0]), KEY_DOWN + KEY_ENTER)
    assert got == names[1]


# --------------------------------------------------------------------------- #
#  5. pick_language — raw list
# --------------------------------------------------------------------------- #


def test_pick_language_returns_selected_id() -> None:
    langs = ["", "python", "rust", "go"]
    got = _run(lambda: pickers.pick_language("python", langs), KEY_ENTER)
    assert got == "python"
