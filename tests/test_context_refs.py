"""Tests for karna.context.references."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from karna.context.references import (
    ContextReference,
    inject_resolved_refs,
    parse_references,
    resolve_references,
)

# ---------------------------------------------------------------------- #
#  Parsing
# ---------------------------------------------------------------------- #


def test_parse_plain_file_reference() -> None:
    refs = parse_references("check @karna/agents/loop.py for the logic")
    assert len(refs) == 1
    assert refs[0].kind == "file"
    assert refs[0].target == "karna/agents/loop.py"
    assert refs[0].raw == "@karna/agents/loop.py"


def test_parse_file_range() -> None:
    refs = parse_references("look at @karna/agents/loop.py:50-100 only")
    assert len(refs) == 1
    assert refs[0].kind == "file_range"
    assert refs[0].target == "karna/agents/loop.py"
    assert refs[0].start_line == 50
    assert refs[0].end_line == 100


def test_parse_url_ref() -> None:
    refs = parse_references("see @url:https://example.com/page for details")
    assert len(refs) == 1
    assert refs[0].kind == "url"
    assert refs[0].target == "https://example.com/page"


def test_parse_glob_ref() -> None:
    refs = parse_references("search @glob:karna/**/*.py for imports")
    assert len(refs) == 1
    assert refs[0].kind == "glob"
    assert refs[0].target == "karna/**/*.py"


def test_parse_git_ref() -> None:
    refs = parse_references("compare with @git:HEAD~1 plz")
    assert len(refs) == 1
    assert refs[0].kind == "git"
    assert refs[0].target == "HEAD~1"


def test_parse_multiple_refs_preserve_order() -> None:
    refs = parse_references("@a.py and then @b.py:1-10 and @url:https://x")
    assert [r.kind for r in refs] == ["file", "file_range", "url"]
    assert [r.target for r in refs] == ["a.py", "b.py", "https://x"]


def test_parse_no_refs() -> None:
    assert parse_references("hello world, no refs here") == []


# ---------------------------------------------------------------------- #
#  Resolution — file refs
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_real_file(tmp_path: Path) -> None:
    target = tmp_path / "hello.py"
    target.write_text("print('hi')\nprint('bye')\n")
    refs = parse_references("load @hello.py please")
    resolved = await resolve_references(refs, cwd=tmp_path, budget_tokens=10_000)
    assert resolved[0].error is None
    assert "print('hi')" in resolved[0].resolved_content
    assert resolved[0].token_estimate > 0


@pytest.mark.asyncio
async def test_resolve_file_range(tmp_path: Path) -> None:
    target = tmp_path / "lines.txt"
    target.write_text("\n".join(f"line {i}" for i in range(1, 21)) + "\n")
    refs = parse_references("see @lines.txt:5-7")
    resolved = await resolve_references(refs, cwd=tmp_path, budget_tokens=10_000)
    assert resolved[0].resolved_content == "line 5\nline 6\nline 7"


@pytest.mark.asyncio
async def test_resolve_missing_file_has_error(tmp_path: Path) -> None:
    refs = parse_references("load @does_not_exist.py")
    resolved = await resolve_references(refs, cwd=tmp_path, budget_tokens=10_000)
    assert resolved[0].error is not None
    assert "not found" in resolved[0].error


@pytest.mark.asyncio
async def test_budget_truncation_kicks_in(tmp_path: Path) -> None:
    """When total content exceeds the budget, per-ref truncation should fire."""
    big = tmp_path / "big.txt"
    big.write_text("abcdefghij" * 10_000)  # 100k chars
    refs = parse_references("@big.txt")
    resolved = await resolve_references(refs, cwd=tmp_path, budget_tokens=200)
    # After truncation, token estimate should be close to budget, not massive
    assert resolved[0].token_estimate < 2_000
    assert "[truncated]" in resolved[0].resolved_content


# ---------------------------------------------------------------------- #
#  Resolution — URL ref (mocked, never hits network)
# ---------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_url_ref_is_mocked_does_not_hit_network(tmp_path: Path) -> None:
    refs = parse_references("fetch @url:https://example.com/data.json")
    mock_resp = MagicMock()
    mock_resp.text = "<html>fake page</html>"
    mock_resp.raise_for_status = MagicMock(return_value=None)

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client):
        resolved = await resolve_references(refs, cwd=tmp_path, budget_tokens=10_000)

    assert resolved[0].error is None
    assert "fake page" in resolved[0].resolved_content
    mock_client.get.assert_awaited_once_with("https://example.com/data.json")


# ---------------------------------------------------------------------- #
#  Injection
# ---------------------------------------------------------------------- #


def test_inject_wraps_in_xml_tags() -> None:
    ref = ContextReference(
        kind="file",
        raw="@foo.py",
        target="foo.py",
        resolved_content="print('x')",
        token_estimate=5,
    )
    out = inject_resolved_refs("check @foo.py now", [ref])
    assert "<context" in out
    assert 'kind="file"' in out
    assert 'ref="@foo.py"' in out
    assert "print('x')" in out
    # The ref body appears inside the <context ref="@foo.py"> attribute,
    # but it should NOT appear as the bare "check @foo.py now" text any more.
    assert "check @foo.py now" not in out
    assert "check <context" in out


def test_inject_file_range_includes_lines_attr() -> None:
    ref = ContextReference(
        kind="file_range",
        raw="@foo.py:1-10",
        target="foo.py",
        start_line=1,
        end_line=10,
        resolved_content="line1\nline2",
        token_estimate=3,
    )
    out = inject_resolved_refs("see @foo.py:1-10", [ref])
    assert 'lines="1-10"' in out


def test_inject_unresolved_ref_still_emits_block() -> None:
    ref = ContextReference(
        kind="file",
        raw="@missing.py",
        target="missing.py",
        resolved_content="[unresolved @missing.py: not found]",
        token_estimate=5,
        error="not found",
    )
    out = inject_resolved_refs("need @missing.py", [ref])
    assert "[unresolved @missing.py" in out
