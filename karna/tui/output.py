"""Streaming output renderer for the Karna REPL.

Renders assistant text deltas, tool calls, tool results, errors,
and per-turn cost information using Rich panels and live updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
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
#  Event protocol — provider layer yields these to the REPL
# --------------------------------------------------------------------------- #


class EventKind(Enum):
    TEXT_DELTA = auto()
    TOOL_CALL_START = auto()
    TOOL_CALL_ARGS_DELTA = auto()
    TOOL_CALL_END = auto()
    TOOL_RESULT = auto()
    ERROR = auto()
    USAGE = auto()  # token counts + cost
    DONE = auto()


@dataclass
class StreamEvent:
    """A single event emitted during a streaming response."""

    kind: EventKind
    data: Any = None


# --------------------------------------------------------------------------- #
#  Renderer
# --------------------------------------------------------------------------- #


class OutputRenderer:
    """Stateful renderer that processes ``StreamEvent`` objects.

    Usage::

        renderer = OutputRenderer(console)
        async for event in agent_loop(...):
            renderer.handle(event)
        renderer.finish()
    """

    def __init__(self, console: Console) -> None:
        self.console = console
        self._text_buffer: list[str] = []
        self._tool_args_buffer: str = ""
        self._current_tool_name: str = ""
        self._current_tool_id: str = ""
        self._live: Live | None = None
        self._spinner_shown: bool = False

    # ── public API ──────────────────────────────────────────────────────

    def show_spinner(self) -> None:
        """Display a waiting spinner until the first text delta arrives."""
        if self._spinner_shown:
            return
        self._spinner_shown = True
        self._live = Live(
            Spinner("dots", text="thinking...", style=f"bold {BRAND_BLUE}"),
            console=self.console,
            transient=True,
        )
        self._live.start()

    def handle(self, event: StreamEvent) -> None:
        """Dispatch a single stream event to the appropriate renderer."""
        dispatch = {
            EventKind.TEXT_DELTA: self._on_text_delta,
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
        """Flush any remaining buffered text and stop the live display."""
        self._stop_live()
        self._flush_text()

    # ── private handlers ────────────────────────────────────────────────

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _flush_text(self) -> None:
        if self._text_buffer:
            full = "".join(self._text_buffer)
            self._text_buffer.clear()
            # Render assistant markdown
            self.console.print()
            self.console.print(Markdown(full), style=ASSISTANT_TEXT)

    def _on_text_delta(self, delta: str) -> None:
        self._stop_live()
        self._text_buffer.append(delta)

    def _on_tool_call_start(self, data: dict[str, str] | None) -> None:
        self._stop_live()
        self._flush_text()
        data = data or {}
        self._current_tool_name = data.get("name", "unknown")
        self._current_tool_id = data.get("id", "")
        self._tool_args_buffer = ""

    def _on_tool_call_args_delta(self, delta: str) -> None:
        self._tool_args_buffer += delta or ""

    def _on_tool_call_end(self, _data: Any = None) -> None:
        # Pretty-print the tool invocation
        import json as _json

        try:
            formatted_args = _json.dumps(_json.loads(self._tool_args_buffer), indent=2)
        except Exception:
            formatted_args = self._tool_args_buffer or "(no arguments)"

        syntax = Syntax(formatted_args, "json", theme="monokai", line_numbers=False)
        self.console.print(
            Panel(
                syntax,
                title=f"[bold yellow]tool call:[/bold yellow] {self._current_tool_name}",
                border_style="yellow",
                expand=False,
            )
        )
        self._tool_args_buffer = ""

    def _on_tool_result(self, data: dict[str, Any] | None) -> None:
        data = data or {}
        content = str(data.get("content", ""))
        is_error = data.get("is_error", False)

        if is_error:
            self.console.print(
                Panel(
                    Text(content, style=ERROR),
                    title="[bold red]tool error[/bold red]",
                    border_style="red",
                    expand=False,
                )
            )
        else:
            # Truncate very long results for display
            preview = content[:2000]
            if len(content) > 2000:
                preview += f"\n... ({len(content) - 2000} chars truncated)"
            self.console.print(
                Panel(
                    Text(preview, style=TOOL_RESULT),
                    title="[dim green]tool result[/dim green]",
                    border_style="dim green",
                    expand=False,
                )
            )

    def _on_error(self, data: Any = None) -> None:
        self._stop_live()
        self._flush_text()
        self.console.print(
            Panel(
                Text(str(data), style=ERROR),
                title="[bold red]Error[/bold red]",
                border_style="red",
                expand=False,
            )
        )

    def _on_usage(self, data: dict[str, Any] | None) -> None:
        """Render token-usage/cost information after the response."""
        data = data or {}
        prompt_tok = data.get("prompt_tokens", 0)
        completion_tok = data.get("completion_tokens", 0)
        total_usd = data.get("total_usd", 0.0)

        parts = []
        if prompt_tok or completion_tok:
            parts.append(f"tokens: {prompt_tok:,} in / {completion_tok:,} out")
        if total_usd > 0:
            parts.append(f"cost: ${total_usd:.4f}")

        if parts:
            self.console.print(Text("  " + "  |  ".join(parts), style=COST_INFO))

    def _on_done(self, _data: Any = None) -> None:
        self._flush_text()
