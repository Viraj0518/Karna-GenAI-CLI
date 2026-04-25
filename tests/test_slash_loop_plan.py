"""Tests for the /loop, /plan, and /do slash commands.

These commands are parser-only — actual execution lives in
``karna.tui.repl`` (which needs a live provider and tool bundle).
The unit contract we verify here:

1. ``/loop <goal>`` returns the ``__LOOP__<goal>`` sentinel.
2. ``/plan <goal>`` returns the ``__PLAN__<goal>`` sentinel.
3. ``/do`` with a stored plan returns ``__DO__<plan>``.
4. Missing-arg variants produce a usage error and ``None`` return.
5. ``/do`` without a stored plan reports "no plan" and returns ``None``.
6. The COMMANDS table exposes the three new commands under the
   ``advanced`` category with the spec'd icons.
"""

from __future__ import annotations

from io import StringIO

import pytest
from rich.console import Console

from karna.config import KarnaConfig
from karna.models import Conversation
from karna.tui.slash import (
    COMMANDS,
    _store_last_plan,  # type: ignore[attr-defined]
    clear_last_plan,
    get_last_plan,
    handle_slash_command,
)

# --------------------------------------------------------------------------- #
#  Command registration
# --------------------------------------------------------------------------- #


def test_loop_plan_do_registered_in_commands_table() -> None:
    """/loop, /plan, /do must appear in COMMANDS under the advanced group."""
    for name in ("loop", "plan", "do"):
        assert name in COMMANDS, f"{name} missing from COMMANDS"
        assert COMMANDS[name].category == "advanced"


def test_loop_has_goal_usage() -> None:
    assert "<goal>" in COMMANDS["loop"].usage


def test_plan_has_goal_usage() -> None:
    assert "<goal>" in COMMANDS["plan"].usage


def test_do_usage_is_argless() -> None:
    assert COMMANDS["do"].usage.strip() == "/do"


# --------------------------------------------------------------------------- #
#  /loop parsing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_loop_returns_sentinel_with_goal() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    result = await handle_slash_command(
        "/loop refactor auth and get tests green",
        console,
        KarnaConfig(),
        Conversation(),
    )
    assert isinstance(result, str)
    assert result.startswith("__LOOP__")
    assert result[len("__LOOP__") :] == "refactor auth and get tests green"


@pytest.mark.asyncio
async def test_loop_without_arg_errors_cleanly() -> None:
    # Disable colour so ANSI escapes don't fragment substrings.
    console = Console(file=StringIO(), force_terminal=False, no_color=True, width=80)
    buf = console.file  # type: ignore[assignment]
    result = await handle_slash_command("/loop", console, KarnaConfig(), Conversation())
    assert result is None
    out = buf.getvalue().lower()  # type: ignore[attr-defined]
    assert "usage" in out and "loop" in out


@pytest.mark.asyncio
async def test_loop_with_only_whitespace_arg_errors_cleanly() -> None:
    console = Console(file=StringIO(), force_terminal=False, no_color=True, width=80)
    buf = console.file  # type: ignore[assignment]
    result = await handle_slash_command("/loop    ", console, KarnaConfig(), Conversation())
    assert result is None
    assert "usage" in buf.getvalue().lower()  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
#  /plan parsing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_plan_returns_sentinel_with_goal() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    result = await handle_slash_command(
        "/plan add a new endpoint to the user API",
        console,
        KarnaConfig(),
        Conversation(),
    )
    assert isinstance(result, str)
    assert result.startswith("__PLAN__")
    assert result[len("__PLAN__") :] == "add a new endpoint to the user API"


@pytest.mark.asyncio
async def test_plan_without_arg_errors_cleanly() -> None:
    console = Console(file=StringIO(), force_terminal=False, no_color=True, width=80)
    buf = console.file  # type: ignore[assignment]
    result = await handle_slash_command("/plan", console, KarnaConfig(), Conversation())
    assert result is None
    out = buf.getvalue().lower()  # type: ignore[attr-defined]
    assert "usage" in out and "plan" in out


# --------------------------------------------------------------------------- #
#  /do parsing
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_do_without_stored_plan_returns_none() -> None:
    console = Console(file=StringIO(), force_terminal=False, no_color=True, width=80)
    buf = console.file  # type: ignore[assignment]
    conv = Conversation()
    # Make sure there's no lingering plan from another test.
    clear_last_plan(conv)
    result = await handle_slash_command("/do", console, KarnaConfig(), conv)
    assert result is None
    assert "no plan" in buf.getvalue().lower()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_do_returns_sentinel_when_plan_stored() -> None:
    buf = StringIO()
    console = Console(file=buf, force_terminal=True, width=80)
    conv = Conversation()
    _store_last_plan(conv, "1. do X\n2. do Y\n3. done")
    assert get_last_plan(conv) == "1. do X\n2. do Y\n3. done"

    result = await handle_slash_command("/do", console, KarnaConfig(), conv)
    assert isinstance(result, str)
    assert result.startswith("__DO__")
    assert result[len("__DO__") :] == "1. do X\n2. do Y\n3. done"


def test_clear_last_plan_drops_stored_plan() -> None:
    conv = Conversation()
    _store_last_plan(conv, "plan text")
    assert get_last_plan(conv) is not None
    clear_last_plan(conv)
    assert get_last_plan(conv) is None
