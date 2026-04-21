"""Tests for the CC-ported permission + trust dialog module.

The module is library-only — every prompt function awaits a helper that
reads a keystroke. We monkeypatch ``_read_keystroke`` to simulate user
input, then assert the decoded return value.
"""

from __future__ import annotations

import asyncio

import pytest
from rich.console import Console
from rich.table import Table

from karna.tui.cc_components import permissions as perms


def _render_plain(renderable) -> str:
    """Render a Rich renderable to plain text for string assertions."""
    buf = Console(record=True, width=120, color_system=None, force_terminal=False)
    buf.print(renderable)
    return buf.export_text(clear=True)


def _patch_input(monkeypatch: pytest.MonkeyPatch, response: str) -> None:
    """Replace the async keystroke-reader with one that returns ``response``.

    This is the prompt_toolkit/stdin boundary — everything above it is
    pure logic, so monkeypatching here covers both the prompt_toolkit
    path and the ``input()`` fallback with a single seam.
    """

    async def fake(_prompt: str) -> str:
        return response

    monkeypatch.setattr(perms, "_read_keystroke", fake)


# --------------------------------------------------------------------------- #
#  1. Response classifier — every CC keystroke maps to the right enum.
# --------------------------------------------------------------------------- #


def test_classify_tool_response_covers_all_four_tiers() -> None:
    # lowercase = "this call only"
    assert perms._classify_tool_response("y") == "allow_once"
    assert perms._classify_tool_response("yes") == "allow_once"
    assert perms._classify_tool_response("a") == "allow_once"
    assert perms._classify_tool_response("n") == "deny_once"
    assert perms._classify_tool_response("no") == "deny_once"
    assert perms._classify_tool_response("") == "deny_once"  # default on ENTER
    # Capitals = "remember / always"
    assert perms._classify_tool_response("Y") == "allow_always"
    assert perms._classify_tool_response("A") == "allow_always"
    assert perms._classify_tool_response("always") == "allow_always"
    assert perms._classify_tool_response("N") == "deny_always"
    assert perms._classify_tool_response("D") == "deny_always"
    assert perms._classify_tool_response("never") == "deny_always"
    # Garbage collapses to safe default (deny_once).
    assert perms._classify_tool_response("zzz") == "deny_once"
    # Whitespace trimming.
    assert perms._classify_tool_response("  y  ") == "allow_once"


# --------------------------------------------------------------------------- #
#  2. prompt_tool_permission — four-way shape + renders a yellow panel.
# --------------------------------------------------------------------------- #


def test_prompt_tool_permission_returns_each_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = [
        ("y", "allow_once"),
        ("Y", "allow_always"),
        ("n", "deny_once"),
        ("N", "deny_always"),
    ]
    for raw, expected in cases:
        _patch_input(monkeypatch, raw)
        got = asyncio.run(
            perms.prompt_tool_permission(
                tool_name="bash",
                tool_args={"command": "rm -rf /tmp/demo"},
                already_allowed_rules=["read", "grep"],
                console=Console(record=True, width=100, color_system=None),
            )
        )
        assert got == expected, f"input {raw!r} should decode to {expected!r}, got {got!r}"


# --------------------------------------------------------------------------- #
#  3. prompt_mcp_server_approval — yes/no collapse + lists the tools.
# --------------------------------------------------------------------------- #


def test_prompt_mcp_server_approval_yes_and_default_no(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Explicit yes → True.
    _patch_input(monkeypatch, "y")
    assert (
        asyncio.run(
            perms.prompt_mcp_server_approval(
                "pencil",
                ["batch_get", "batch_design"],
                console=Console(record=True, width=100, color_system=None),
            )
        )
        is True
    )

    # Empty response → defaults to No (matches CC's `onCancel → no`).
    _patch_input(monkeypatch, "")
    assert (
        asyncio.run(
            perms.prompt_mcp_server_approval(
                "pencil",
                ["batch_get"],
                console=Console(record=True, width=100, color_system=None),
            )
        )
        is False
    )

    # Unknown input also falls to No.
    _patch_input(monkeypatch, "maybe")
    assert (
        asyncio.run(
            perms.prompt_mcp_server_approval(
                "pencil",
                [],
                console=Console(record=True, width=100, color_system=None),
            )
        )
        is False
    )


# --------------------------------------------------------------------------- #
#  4. prompt_api_key_trust — defaults to no (CC marks No as *recommended*).
# --------------------------------------------------------------------------- #


def test_prompt_api_key_trust_defaults_to_no(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_input(monkeypatch, "")
    assert (
        asyncio.run(
            perms.prompt_api_key_trust(
                "anthropic",
                "sk-ant-...abcd",
                console=Console(record=True, width=100, color_system=None),
            )
        )
        is False
    )
    _patch_input(monkeypatch, "yes")
    assert (
        asyncio.run(
            perms.prompt_api_key_trust(
                "anthropic",
                "sk-ant-...abcd",
                console=Console(record=True, width=100, color_system=None),
            )
        )
        is True
    )


# --------------------------------------------------------------------------- #
#  5. prompt_bypass_permissions — default-deny, accept only on explicit yes.
# --------------------------------------------------------------------------- #


def test_prompt_bypass_permissions_default_no_and_accept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_input(monkeypatch, "")
    assert (
        asyncio.run(
            perms.prompt_bypass_permissions(
                console=Console(record=True, width=100, color_system=None),
            )
        )
        is False
    )

    _patch_input(monkeypatch, "accept")
    assert (
        asyncio.run(
            perms.prompt_bypass_permissions(
                console=Console(record=True, width=100, color_system=None),
            )
        )
        is True
    )


# --------------------------------------------------------------------------- #
#  6. render_permission_allowlist — pure renderer, shows rules or placeholder.
# --------------------------------------------------------------------------- #


def test_render_permission_allowlist_populated_and_empty() -> None:
    populated = perms.render_permission_allowlist(["read", "grep", "bash:ls"])
    assert isinstance(populated, Table)
    out = _render_plain(populated)
    assert "read" in out
    assert "grep" in out
    assert "bash:ls" in out

    empty = perms.render_permission_allowlist([])
    empty_text = _render_plain(empty)
    assert "(none yet)" in empty_text
