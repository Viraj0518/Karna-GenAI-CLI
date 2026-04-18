"""@-reference parser and resolver for user prompts.

Users can pull file contents / git refs / URLs into a prompt via
@-syntax:

    @karna/agents/loop.py                 — whole file
    @karna/agents/loop.py:50-100          — line range
    @karna/ #glob:*.py                    — glob pattern
    @glob:karna/**/*.py                   — shorthand glob
    @git:HEAD~1                           — git ref (``git show``)
    @url:https://example.com              — fetched HTTP(S) content

Resolution is self-contained: only ``@url:`` refs touch the network,
and they go through ``httpx`` directly (no provider plumbing). If the
caller wants to skip URL fetching entirely, they can filter refs by
``kind`` before calling :func:`resolve_references`.

Budget handling: after resolution, if the total estimated token cost
exceeds ``budget_tokens``, each ref's content is trimmed using a
head+tail pattern (keep the first half, drop the middle, keep the
last quarter). This beats FIFO truncation because file endings
(exports, `if __name__ == "__main__"`) are often as informative as
openings.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from karna.tokens import count_tokens

logger = logging.getLogger(__name__)

Kind = Literal["file", "file_range", "glob", "git", "url"]

# Order matters: longer prefixes first so @url: wins over @u.
_REFERENCE_RE = re.compile(
    r"""
    @                                            # leading @
    (?P<body>
        url:\S+                                  # @url:https://...
      | git:\S+                                  # @git:HEAD~1
      | glob:\S+                                 # @glob:**/*.py
      | [^\s@]+                                  # @path or @path:1-50
    )
    """,
    re.VERBOSE,
)

# Matches an optional :start-end line range on a file ref.
_RANGE_RE = re.compile(r"^(?P<path>.+?):(?P<start>\d+)-(?P<end>\d+)$")


@dataclass
class ContextReference:
    """A single @-reference extracted from a prompt."""

    kind: Kind
    raw: str
    target: str = ""
    start_line: int | None = None
    end_line: int | None = None
    resolved_content: str = ""
    token_estimate: int = 0
    error: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------- #
#  Parsing
# ---------------------------------------------------------------------- #


def parse_references(text: str) -> list[ContextReference]:
    """Extract every @-reference from ``text``.

    Returns them in source order. Does not resolve content — that's
    :func:`resolve_references`'s job.
    """
    refs: list[ContextReference] = []
    for m in _REFERENCE_RE.finditer(text):
        body = m.group("body")
        raw = "@" + body

        if body.startswith("url:"):
            refs.append(ContextReference(kind="url", raw=raw, target=body[4:]))
            continue
        if body.startswith("git:"):
            refs.append(ContextReference(kind="git", raw=raw, target=body[4:]))
            continue
        if body.startswith("glob:"):
            refs.append(ContextReference(kind="glob", raw=raw, target=body[5:]))
            continue

        range_match = _RANGE_RE.match(body)
        if range_match:
            refs.append(
                ContextReference(
                    kind="file_range",
                    raw=raw,
                    target=range_match.group("path"),
                    start_line=int(range_match.group("start")),
                    end_line=int(range_match.group("end")),
                )
            )
            continue

        refs.append(ContextReference(kind="file", raw=raw, target=body))
    return refs


# ---------------------------------------------------------------------- #
#  Resolution
# ---------------------------------------------------------------------- #


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        return f"[error reading {path}: {exc}]"


def _resolve_file(ref: ContextReference, cwd: Path) -> None:
    path = (cwd / ref.target).resolve() if not Path(ref.target).is_absolute() else Path(ref.target)
    if not path.exists():
        ref.error = f"file not found: {ref.target}"
        return
    if not path.is_file():
        ref.error = f"not a regular file: {ref.target}"
        return
    ref.resolved_content = _read_text_safe(path)


def _resolve_file_range(ref: ContextReference, cwd: Path) -> None:
    path = (cwd / ref.target).resolve() if not Path(ref.target).is_absolute() else Path(ref.target)
    if not path.exists() or not path.is_file():
        ref.error = f"file not found: {ref.target}"
        return
    text = _read_text_safe(path)
    lines = text.splitlines()
    start = max(1, ref.start_line or 1)
    end = min(len(lines), ref.end_line or len(lines))
    if start > end:
        ref.error = f"invalid range {start}-{end}"
        return
    slice_text = "\n".join(lines[start - 1 : end])
    ref.resolved_content = slice_text


def _resolve_glob(ref: ContextReference, cwd: Path) -> None:
    pattern = ref.target
    matches = sorted(cwd.glob(pattern))
    if not matches:
        ref.error = f"glob matched nothing: {pattern}"
        return
    chunks: list[str] = []
    for p in matches:
        if p.is_file():
            rel = p.relative_to(cwd) if p.is_relative_to(cwd) else p
            chunks.append(f"--- {rel} ---\n{_read_text_safe(p)}")
    ref.resolved_content = "\n\n".join(chunks)
    ref.extra["matched"] = len(matches)


def _resolve_git(ref: ContextReference, cwd: Path) -> None:
    try:
        out = subprocess.run(  # noqa: S603 — git is a known binary, args come from user prompt
            ["git", "show", ref.target],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        ref.error = f"git show failed: {exc}"
        return
    if out.returncode != 0:
        ref.error = f"git show {ref.target}: {out.stderr.strip()}"
        return
    ref.resolved_content = out.stdout


async def _resolve_url(ref: ContextReference) -> None:
    # Local import so the module is usable without httpx installed
    # when no URL refs are in the prompt.
    try:
        import httpx
    except ImportError:  # pragma: no cover — httpx is a karna runtime dep
        ref.error = "httpx not installed; cannot resolve @url: refs"
        return
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(ref.target)
            resp.raise_for_status()
            ref.resolved_content = resp.text
    except Exception as exc:  # noqa: BLE001 — network errors are many-shaped
        ref.error = f"URL fetch failed: {exc}"


def _truncate_for_budget(content: str, target_tokens: int, model: str = "") -> str:
    """Head+tail truncation until estimated tokens fit ``target_tokens``."""
    if target_tokens <= 0:
        return ""
    current = count_tokens(content, model) if content else 0
    if current <= target_tokens:
        return content
    # Rough char-per-token ratio — recalc on the concrete content so
    # non-ASCII transcripts don't over-trim.
    chars_per_tok = max(1, len(content) // max(1, current))
    target_chars = max(200, target_tokens * chars_per_tok)
    head = int(target_chars * 0.6)
    tail = target_chars - head - 20  # slack for the marker
    if tail <= 0:
        return content[:target_chars] + "\n...[truncated]..."
    return content[:head] + "\n...[truncated]...\n" + content[-tail:]


async def resolve_references(
    refs: list[ContextReference],
    *,
    cwd: Path,
    budget_tokens: int,
) -> list[ContextReference]:
    """Populate ``resolved_content`` + ``token_estimate`` on each ref.

    Budget: if the total exceeds ``budget_tokens``, each ref is
    proportionally truncated via head+tail.
    """
    for ref in refs:
        if ref.kind == "file":
            _resolve_file(ref, cwd)
        elif ref.kind == "file_range":
            _resolve_file_range(ref, cwd)
        elif ref.kind == "glob":
            _resolve_glob(ref, cwd)
        elif ref.kind == "git":
            _resolve_git(ref, cwd)
        elif ref.kind == "url":
            await _resolve_url(ref)

        if ref.error and not ref.resolved_content:
            ref.resolved_content = f"[unresolved {ref.raw}: {ref.error}]"
        ref.token_estimate = count_tokens(ref.resolved_content)

    total = sum(r.token_estimate for r in refs)
    if budget_tokens > 0 and total > budget_tokens and total > 0:
        # Scale each ref's share of the budget by its current size so a
        # 100-token ref doesn't get trimmed the same amount as a 10k one.
        for ref in refs:
            share = int(budget_tokens * ref.token_estimate / total)
            ref.resolved_content = _truncate_for_budget(ref.resolved_content, share)
            ref.token_estimate = count_tokens(ref.resolved_content)

    return refs


# ---------------------------------------------------------------------- #
#  Injection
# ---------------------------------------------------------------------- #


def _render_block(ref: ContextReference) -> str:
    attrs = f'kind="{ref.kind}" ref="{ref.raw}"'
    if ref.start_line is not None and ref.end_line is not None:
        attrs += f' lines="{ref.start_line}-{ref.end_line}"'
    return f"<context {attrs}>\n{ref.resolved_content}\n</context>"


def inject_resolved_refs(prompt: str, refs: list[ContextReference]) -> str:
    """Replace each ``ref.raw`` in ``prompt`` with an XML-tagged block.

    If the same @-token appears multiple times, every occurrence is
    replaced (the model probably meant the same file each time).
    Unresolved refs inject their error marker rather than vanishing —
    the user benefits from knowing the ref failed.
    """
    out = prompt
    for ref in refs:
        out = out.replace(ref.raw, _render_block(ref))
    return out


__all__ = [
    "ContextReference",
    "parse_references",
    "resolve_references",
    "inject_resolved_refs",
]
