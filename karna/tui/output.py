"""Streaming output renderer for the Nellie TUI.

Renders assistant text deltas, reasoning/thinking, tool calls with live
status, tool results, errors, and per-turn cost with a cohesive visual
rhythm inspired by Claude Code Ink, Warp, and bubbletea.

Key design moves:

* **Live windows are scoped.** Only the currently-active block (spinner,
  streaming text, pending tool status) lives inside a ``rich.live.Live``.
  Completed content flushes to scrollback so it never flickers.
* **Tool calls are stateful widgets.** Pending → running → ok/err, with a
  single-line status, collapsible JSON args, and a success/failure glyph.
* **Thinking is separated.** Italic + purple + collapsed to one line by
  default — stops it blurring into normal assistant prose.
* **Errors gain context.** Title, primary message, secondary tool/provider
  line, and a pattern-matched recovery hint where we can.
* **Turn rhythm.** A dimmed divider and blank line between turns.

Public API (preserved):

* ``__init__(console)``
* ``show_spinner()``
* ``handle(event: StreamEvent)``
* ``finish()``

``StreamEvent`` / ``EventKind`` are also exported — other modules import
them from here.
"""

from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text

from karna.tui.themes import (
    ASSISTANT_TEXT,
    BRAND_BLUE,
    COST_INFO,
    ERROR,
    TOOL_RESULT,
)

# --------------------------------------------------------------------------- #
#  Optional sibling modules (another agent is creating these). Import
#  defensively so the renderer keeps working if they're not present yet,
#  falling back to sensible string defaults.
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - defensive import
    from karna.tui import design_tokens as _tokens  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    _tokens = None  # type: ignore[assignment]

try:  # pragma: no cover - defensive import
    from karna.tui import icons as _icons  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    _icons = None  # type: ignore[assignment]


def _token(name: str, default: str) -> str:
    """Resolve a design token by semantic role, with a fallback."""
    if _tokens is None:
        return default
    # design_tokens may expose either module-level attrs or a mapping
    val = getattr(_tokens, name, None)
    if val is None and hasattr(_tokens, "TOKENS"):
        val = getattr(_tokens, "TOKENS", {}).get(name)  # type: ignore[union-attr]
    return str(val) if val else default


def _icon(name: str, default: str) -> str:
    """Resolve an icon glyph by semantic role, with a fallback."""
    if _icons is None:
        return default
    val = getattr(_icons, name, None)
    if val is None and hasattr(_icons, "ICONS"):
        val = getattr(_icons, "ICONS", {}).get(name)  # type: ignore[union-attr]
    return str(val) if val else default


# Semantic style roles -- resolved once at import time.
_STYLE_USER = _token("user_ink", "bold white")
_STYLE_ASSISTANT_LABEL = _token("assistant_label", "dim cyan")
_STYLE_ASSISTANT_INK = _token("assistant_ink", "#87CEEB")
_STYLE_THINKING = _token("accent_thinking", "italic magenta")
_STYLE_THINKING_DIM = _token("accent_thinking_dim", "italic dim magenta")
_STYLE_SUCCESS = _token("accent_success", "green")
_STYLE_DANGER = _token("accent_danger", "red")
_STYLE_META = _token("meta", "bright_black")
_STYLE_BRAND_DIM = _token("accent_brand_dim", f"dim {BRAND_BLUE}")
_STYLE_DIVIDER = _token("divider", "bright_black")

# Semantic icon roles -- resolved once at import time.
_ICON_USER = _icon("user", ">")
_ICON_ASSISTANT = _icon("assistant", "*")
_ICON_THINKING = _icon("thinking", "✦")
_ICON_TOOL = _icon("tool", "⚒")
_ICON_OK = _icon("success", "✓")
_ICON_ERR = _icon("failure", "✗")
_ICON_CURSOR = _icon("cursor", "▌")


# --------------------------------------------------------------------------- #
#  Event protocol — provider layer yields these to the REPL
# --------------------------------------------------------------------------- #


class EventKind(Enum):
    TEXT_DELTA = auto()
    THINKING_DELTA = auto()       # optional: reasoning / thinking content
    TOOL_CALL_START = auto()
    TOOL_CALL_ARGS_DELTA = auto()
    TOOL_CALL_END = auto()
    TOOL_RESULT = auto()
    ERROR = auto()
    USAGE = auto()                # token counts + cost
    DONE = auto()


@dataclass
class StreamEvent:
    """A single event emitted during a streaming response."""

    kind: EventKind
    data: Any = None


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

# Sentence-boundary flush: commit buffered text at these characters to get
# smooth chunk-by-chunk rendering without per-character flicker.
_SENTENCE_RE = re.compile(r"[.!?\n]\s")

_ARG_MAX_LINES = 20
_ARG_HEAD = 5
_ARG_TAIL = 5
_RESULT_INLINE_LIMIT = 120
_RESULT_PANEL_LIMIT = 4000


def _truncate_args_json(raw: str) -> str:
    """Pretty-print JSON and collapse long bodies to head + gap + tail."""
    try:
        parsed = _json.loads(raw) if raw else {}
        pretty = _json.dumps(parsed, indent=2, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pretty = raw or "(no arguments)"

    lines = pretty.splitlines()
    if len(lines) <= _ARG_MAX_LINES:
        return pretty

    hidden = len(lines) - _ARG_HEAD - _ARG_TAIL
    return "\n".join(
        lines[:_ARG_HEAD]
        + [f"  … {hidden} more lines …"]
        + lines[-_ARG_TAIL:]
    )


def _looks_like_json(s: str) -> bool:
    s = s.strip()
    return (s.startswith("{") and s.endswith("}")) or (
        s.startswith("[") and s.endswith("]")
    )


def _error_hint(msg: str) -> str | None:
    """Pattern-match common failure modes and return a one-line hint."""
    m = msg.lower()
    if "401" in m or "unauthorized" in m or "invalid api key" in m:
        return "hint: check your API key with `nellie auth list`"
    if "403" in m or "forbidden" in m:
        return "hint: key lacks permission — check provider dashboard"
    if "404" in m and "model" in m:
        return "hint: model not found — run `/model` to pick another"
    if "429" in m or "rate limit" in m:
        return "hint: rate-limited — wait a moment or switch providers"
    if "connection refused" in m or "econnrefused" in m:
        return "hint: endpoint unreachable — check the RPC/base URL"
    if "timeout" in m or "timed out" in m:
        return "hint: request timed out — network or model may be slow"
    if "ssl" in m or "certificate" in m:
        return "hint: TLS failure — check system certs or proxy"
    return None


# --------------------------------------------------------------------------- #
#  Tool call state widget
# --------------------------------------------------------------------------- #


@dataclass
class _ToolState:
    name: str
    id: str
    args_buffer: str = ""
    status: str = "pending"  # pending | running | ok | err
    status_text: str = "preparing..."

    def header(self, spinner_frame: str | None = None) -> Text:
        """Render the single-line status header for this tool call."""
        line = Text()
        line.append(f"{_ICON_TOOL} ", style=_STYLE_META)
        line.append(self.name, style="bold")
        line.append("  ")
        if self.status in ("pending", "running"):
            # Spinner slot
            if spinner_frame:
                line.append(spinner_frame, style=_STYLE_BRAND_DIM)
            line.append("  ")
            line.append(self.status_text, style=_STYLE_META)
        elif self.status == "ok":
            line.append(_ICON_OK, style=_STYLE_SUCCESS)
            line.append("  ")
            line.append(self.status_text, style=_STYLE_SUCCESS)
        else:  # err
            line.append(_ICON_ERR, style=_STYLE_DANGER)
            line.append("  ")
            line.append(self.status_text, style=_STYLE_DANGER)
        return line


# --------------------------------------------------------------------------- #
#  Renderer
# --------------------------------------------------------------------------- #


class OutputRenderer:
    """Stateful renderer that processes ``StreamEvent`` objects.

    Usage::

        renderer = OutputRenderer(console)
        renderer.show_spinner()
        for event in events:
            renderer.handle(event)
        renderer.finish()
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        # Buffers
        self._text_buffer: list[str] = []
        self._thinking_buffer: list[str] = []
        # Live state
        self._live: Live | None = None
        self._live_kind: str | None = None  # "spinner" | "stream" | "tool"
        # Tool state
        self._tool: _ToolState | None = None
        # Turn rhythm
        self._turn_started: bool = False

    # ── public API ─────────────────────────────────────────────────────

    def show_spinner(self) -> None:
        """Display a transient waiting spinner until the first event."""
        if self._live is not None:
            return
        self._ensure_turn_break()
        spinner = Spinner("dots", text=Text("thinking...", style=_STYLE_BRAND_DIM))
        self._live = Live(
            spinner,
            console=self.console,
            transient=True,
            refresh_per_second=12,
        )
        self._live.start()
        self._live_kind = "spinner"

    def handle(self, event: StreamEvent) -> None:
        """Dispatch a single stream event to the appropriate renderer."""
        dispatch = {
            EventKind.TEXT_DELTA: self._on_text_delta,
            EventKind.THINKING_DELTA: self._on_thinking_delta,
            EventKind.TOOL_CALL_START: self._on_tool_call_start,
            EventKind.TOOL_CALL_ARGS_DELTA: self._on_tool_call_args_delta,
            EventKind.TOOL_CALL_END: self._on_tool_call_end,
            EventKind.TOOL_RESULT: self._on_tool_result,
            EventKind.ERROR: self._on_error,
            EventKind.USAGE: self._on_usage,
            EventKind.DONE: self._on_done,
        }
        handler = dispatch.get(event.kind)
        if handler:
            handler(event.data)

    def finish(self) -> None:
        """Flush any remaining buffered content and stop the live display."""
        self._stop_live()
        self._flush_thinking()
        self._flush_text()
        self._tool = None

    # ── live display management ────────────────────────────────────────

    def _stop_live(self) -> None:
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:  # noqa: BLE001
                pass
            self._live = None
            self._live_kind = None

    def _ensure_turn_break(self) -> None:
        """Emit a dimmed divider + blank line before the first block of a turn."""
        if self._turn_started:
            return
        self._turn_started = True
        self.console.print()
        self.console.print(Rule(style=_STYLE_DIVIDER, characters="─"))
        self.console.print()

    # ── streaming text ─────────────────────────────────────────────────

    def _on_text_delta(self, delta: str) -> None:
        if not delta:
            return
        # Kill the transient spinner on first content
        if self._live_kind == "spinner":
            self._stop_live()

        self._text_buffer.append(delta)
        # Flush to scrollback at sentence boundaries so we avoid flicker on
        # Windows (Live redraws are expensive here) but still feel live.
        joined = "".join(self._text_buffer)
        if _SENTENCE_RE.search(joined[-3:]) or joined.endswith("\n"):
            self._flush_text()

    def _flush_text(self) -> None:
        if not self._text_buffer:
            return
        full = "".join(self._text_buffer).rstrip()
        self._text_buffer.clear()
        if not full:
            return
        self._stop_live()

        # Label line + content. Markdown renders code blocks / lists / etc.
        label = Text()
        label.append(f"{_ICON_ASSISTANT} ", style=_STYLE_ASSISTANT_LABEL)
        label.append("nellie", style=_STYLE_ASSISTANT_LABEL)
        self.console.print(label)
        self.console.print(Markdown(full), style=ASSISTANT_TEXT)

    # ── thinking / reasoning ───────────────────────────────────────────

    def _on_thinking_delta(self, delta: str) -> None:
        if not delta:
            return
        if self._live_kind == "spinner":
            self._stop_live()
        self._thinking_buffer.append(delta)

    def _flush_thinking(self) -> None:
        if not self._thinking_buffer:
            return
        full = "".join(self._thinking_buffer).strip()
        self._thinking_buffer.clear()
        if not full:
            return
        self._stop_live()

        lines = full.splitlines()
        first = lines[0] if lines else ""
        char_count = len(full)

        body = Text()
        body.append(f"{_ICON_THINKING} ", style=_STYLE_THINKING)
        body.append("thinking ", style=_STYLE_THINKING)
        body.append(first, style=_STYLE_THINKING_DIM)
        if len(lines) > 1:
            body.append(
                f"  … ({char_count} chars)",
                style=_STYLE_META,
            )
        self.console.print(body)

    # ── tool calls ─────────────────────────────────────────────────────

    def _on_tool_call_start(self, data: dict[str, Any] | None) -> None:
        # Close out any in-flight assistant text / thinking first.
        self._stop_live()
        self._flush_thinking()
        self._flush_text()

        data = data or {}
        name = str(data.get("name", "unknown"))
        tool_id = str(data.get("id", ""))
        initial_args = data.get("arguments", "") or ""
        if isinstance(initial_args, dict):
            try:
                initial_args = _json.dumps(initial_args)
            except Exception:  # noqa: BLE001
                initial_args = ""

        self._tool = _ToolState(
            name=name,
            id=tool_id,
            args_buffer=str(initial_args),
            status="running",
            status_text=f"calling {name}...",
        )

        # Live-update the tool header with a spinner until TOOL_CALL_END.
        spinner = Spinner(
            "dots",
            text=self._tool.header(),
            style=_STYLE_BRAND_DIM,
        )
        self._live = Live(
            spinner,
            console=self.console,
            transient=True,
            refresh_per_second=12,
        )
        self._live.start()
        self._live_kind = "tool"

    def _on_tool_call_args_delta(self, delta: str) -> None:
        if self._tool is None or not delta:
            return
        self._tool.args_buffer += str(delta)

    def _on_tool_call_end(self, _data: Any = None) -> None:
        if self._tool is None:
            return

        # Stop the live spinner — we're about to commit to scrollback.
        self._stop_live()

        tool = self._tool
        # Render the committed header (no spinner) plus a syntax block for args.
        header = Text()
        header.append(f"{_ICON_TOOL} ", style=_STYLE_META)
        header.append(tool.name, style="bold")
        header.append("  ")
        header.append("…", style=_STYLE_BRAND_DIM)
        header.append("  ")
        header.append(f"called {tool.name}", style=_STYLE_META)

        parts: list[RenderableType] = [header]
        pretty = _truncate_args_json(tool.args_buffer)
        if pretty and pretty != "(no arguments)":
            parts.append(
                Syntax(
                    pretty,
                    "json",
                    theme="ansi_dark",
                    line_numbers=False,
                    word_wrap=True,
                    background_color="default",
                )
            )

        self.console.print(Group(*parts))
        # Keep _tool around — TOOL_RESULT will update its status.

    def _on_tool_result(self, data: dict[str, Any] | None) -> None:
        data = data or {}
        content = str(data.get("content", ""))
        is_error = bool(data.get("is_error", False))
        tool_name = self._tool.name if self._tool else str(data.get("name", "tool"))

        # Update + render the terminal status line.
        status_icon = _ICON_ERR if is_error else _ICON_OK
        status_style = _STYLE_DANGER if is_error else _STYLE_SUCCESS

        # Short summary of result for the status line.
        summary = content.strip().splitlines()[0] if content.strip() else (
            "error" if is_error else "done"
        )
        if len(summary) > 80:
            summary = summary[:77] + "..."

        status_line = Text()
        status_line.append(f"{_ICON_TOOL} ", style=_STYLE_META)
        status_line.append(tool_name, style="bold")
        status_line.append("  ")
        status_line.append(status_icon, style=status_style)
        status_line.append("  ")
        status_line.append(summary, style=status_style if is_error else _STYLE_META)
        self.console.print(status_line)

        # Long results get a full panel; short ones are already covered.
        stripped = content.strip()
        if stripped and len(stripped) > _RESULT_INLINE_LIMIT:
            display = stripped
            if len(display) > _RESULT_PANEL_LIMIT:
                display = (
                    display[:_RESULT_PANEL_LIMIT]
                    + f"\n… ({len(stripped) - _RESULT_PANEL_LIMIT} chars truncated)"
                )

            body: RenderableType
            if _looks_like_json(display):
                body = Syntax(
                    display,
                    "json",
                    theme="ansi_dark",
                    line_numbers=False,
                    word_wrap=True,
                    background_color="default",
                )
            else:
                body = Text(display, style=TOOL_RESULT if not is_error else ERROR)

            title = (
                f"[{_STYLE_DANGER}]tool error · {tool_name}[/]"
                if is_error
                else f"[{_STYLE_META}]output · {tool_name}[/]"
            )
            self.console.print(
                Panel(
                    body,
                    title=title,
                    title_align="left",
                    border_style=_STYLE_DANGER if is_error else _STYLE_META,
                    padding=(0, 1),
                    expand=True,
                )
            )

        self._tool = None

    # ── errors ─────────────────────────────────────────────────────────

    def _on_error(self, data: Any = None) -> None:
        self._stop_live()
        self._flush_thinking()
        self._flush_text()

        msg = str(data) if data is not None else "unknown error"
        hint = _error_hint(msg)

        # Primary message (coloured but not bold-red screaming).
        primary = Text(msg, style=_STYLE_DANGER)
        # Secondary context line.
        context_bits: list[str] = []
        if self._tool is not None:
            context_bits.append(f"tool: {self._tool.name}")
        context = Text(
            "  ".join(context_bits) if context_bits else "provider: agent loop",
            style=_STYLE_META,
        )

        parts: list[RenderableType] = [primary, context]
        if hint:
            parts.append(Text(hint, style=_STYLE_META))

        self.console.print(
            Panel(
                Group(*parts),
                title=f"[{_STYLE_DANGER}]{_ICON_ERR} something went wrong[/]",
                title_align="left",
                border_style=_STYLE_DANGER,
                padding=(0, 1),
                expand=True,
            )
        )

    # ── usage / done ───────────────────────────────────────────────────

    def _on_usage(self, data: dict[str, Any] | None) -> None:
        data = data or {}
        prompt_tok = data.get("prompt_tokens", 0)
        completion_tok = data.get("completion_tokens", 0)
        total_usd = data.get("total_usd", 0.0)

        parts: list[str] = []
        if prompt_tok or completion_tok:
            parts.append(f"{prompt_tok:,} in / {completion_tok:,} out")
        if total_usd and total_usd > 0:
            parts.append(f"${total_usd:.4f}")

        if parts:
            self.console.print(
                Text("  " + " · ".join(parts), style=COST_INFO)
            )

    def _on_done(self, _data: Any = None) -> None:
        self._stop_live()
        self._flush_thinking()
        self._flush_text()
        # Reset turn rhythm for the next invocation.
        self._turn_started = False
