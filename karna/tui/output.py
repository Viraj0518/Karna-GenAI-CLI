"""Streaming output renderer for the Nellie TUI.

Renders assistant text deltas, reasoning/thinking, tool calls with live
status, tool results, errors, and per-turn cost with a cohesive visual
rhythm inspired by Claude Code Ink, Warp, and bubbletea.

Key design moves:

* **No Rich Live.** All output goes through ``console.print()`` which
  routes through the ``TUIOutputWriter`` into prompt_toolkit's output
  pane.  No ``Live`` or ``patch_stdout`` --- the Application owns the
  terminal so there is zero cursor fighting.
* **Tool calls are stateful widgets.** Pending -> running -> ok/err, with a
  single-line status, collapsible JSON args, and a success/failure glyph.
* **Thinking is separated.** Italic + purple + collapsed to one line by
  default --- stops it blurring into normal assistant prose.
* **Errors gain context.** Title, primary message, secondary tool/provider
  line, and a pattern-matched recovery hint where we can.
* **Turn rhythm.** A dimmed divider and blank line between turns.

Public API (preserved):

* ``__init__(console)``
* ``show_spinner()``
* ``handle(event: StreamEvent)``
* ``finish()``

``StreamEvent`` / ``EventKind`` are also exported --- other modules import
them from here.
"""

from __future__ import annotations

import json as _json
import re
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from karna.tui.themes import (
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
_STYLE_TOOL_BULLET = _token("tool_bullet", BRAND_BLUE)
_STYLE_RESULT_BRANCH = _token("result_branch", "bright_black")

# --------------------------------------------------------------------------- #
#  Icon set detection: Nerd Font → Emoji → ASCII
#
#  Controlled by config.toml [theme] icon_set = "nerd" | "emoji" | "ascii"
#  or auto-detected: if NELLIE_ICONS env var is set, use that; otherwise
#  default to "nerd" (most dev terminals have Nerd Fonts).
# --------------------------------------------------------------------------- #

_ICON_SETS = {
    "nerd": {
        "user": "\uf061",  # nf-fa-arrow_right
        "assistant": "\uf005",  # nf-fa-star
        "thinking": "\u2726",  # ✦ black four-pointed star — same as Claude Code
        "tool": "\uf085",  # nf-fa-cogs
        "success": "\uf00c",  # nf-fa-check
        "failure": "\uf00d",  # nf-fa-times
        "cursor": "\u258c",  # left half block
    },
    "emoji": {
        "user": ">",
        "assistant": "◆",
        "thinking": "✦",
        "tool": "⚒",
        "success": "✓",
        "failure": "✗",
        "cursor": "▌",
    },
    "ascii": {
        "user": ">",
        "assistant": "*",
        "thinking": "*",
        "tool": "#",
        "success": "+",
        "failure": "x",
        "cursor": "|",
    },
}


def _detect_icon_set() -> str:
    """Detect which icon set to use."""
    import os

    # Explicit override via env var
    env = os.environ.get("NELLIE_ICONS", "").lower()
    if env in _ICON_SETS:
        return env
    # Try config
    try:
        from karna.config import load_config

        cfg = load_config()
        theme = getattr(cfg, "theme", None) or {}
        if isinstance(theme, dict):
            icon_set = theme.get("icon_set", "")
            if icon_set in _ICON_SETS:
                return icon_set
    except Exception:  # noqa: BLE001
        pass
    # Default to nerd
    return "nerd"


_ACTIVE_ICON_SET = _detect_icon_set()
_ICONS_MAP = _ICON_SETS[_ACTIVE_ICON_SET]

_ICON_USER = _icon("user", _ICONS_MAP["user"])
_ICON_ASSISTANT = _icon("assistant", _ICONS_MAP["assistant"])
_ICON_THINKING = _icon("thinking", _ICONS_MAP["thinking"])
_ICON_TOOL = _icon("tool", _ICONS_MAP["tool"])
_ICON_OK = _icon("success", _ICONS_MAP["success"])
_ICON_ERR = _icon("failure", _ICONS_MAP["failure"])
_ICON_CURSOR = _icon("cursor", _ICONS_MAP["cursor"])


# --------------------------------------------------------------------------- #
#  Hermes-style kawaii faces, thinking verbs, tool mappings, long-run charms
# --------------------------------------------------------------------------- #

BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

FACES = [
    "(｡•́︿•̀｡)",
    "(◔_◔)",
    "(¬‿¬)",
    "(⌐■_■)",
    "(´･_･`)",
    "◉_◉",
    "(°ロ°)",
    "( ˘⌣˘)♡",
    "(⊙_⊙)",
    "( ͡° ͜ʖ ͡°)",
]

VERBS = [
    "pondering",
    "contemplating",
    "musing",
    "cogitating",
    "ruminating",
    "deliberating",
    "mulling",
    "reflecting",
    "processing",
    "reasoning",
    "analyzing",
    "computing",
    "synthesizing",
    "formulating",
    "brainstorming",
]

TOOL_VERBS = {
    "bash": "terminal",
    "read": "reading",
    "write": "writing",
    "edit": "patching",
    "grep": "searching",
    "glob": "listing",
    "git": "git",
    "web_search": "searching",
    "web_fetch": "fetching",
    "monitor": "monitoring",
    "task": "delegating",
    "mcp": "calling",
    "image": "analyzing",
    "clipboard": "clipboard",
    "notebook": "notebook",
}

_TOOL_EMOJI_SETS = {
    "nerd": {
        "bash": "\uf120",  # nf-fa-terminal
        "read": "\uf06e",  # nf-fa-eye
        "write": "\uf044",  # nf-fa-pencil_square_o
        "edit": "\uf440",  # nf-oct-diff
        "grep": "\uf002",  # nf-fa-search
        "glob": "\uf07b",  # nf-fa-folder
        "git": "\ue725",  # nf-dev-git_branch
        "web_search": "\uf0ac",  # nf-fa-globe
        "web_fetch": "\uf0ed",  # nf-fa-cloud_download
        "monitor": "\uf0e7",  # nf-fa-bolt
        "task": "\uf0c1",  # nf-fa-link
        "mcp": "\uf0e7",  # nf-fa-bolt
        "image": "\uf03e",  # nf-fa-image
        "clipboard": "\uf0ea",  # nf-fa-clipboard
        "notebook": "\ue736",  # nf-dev-notebook
    },
    "emoji": {
        "bash": "\U0001f4bb",
        "read": "\U0001f4d6",
        "write": "\u270d\ufe0f",
        "edit": "\U0001f527",
        "grep": "\U0001f50e",
        "glob": "\U0001f4c1",
        "git": "\U0001f500",
        "web_search": "\U0001f50d",
        "web_fetch": "\U0001f4c4",
        "monitor": "\U0001f4e1",
        "task": "\U0001f500",
        "mcp": "\u26a1",
        "image": "\U0001f5bc\ufe0f",
        "clipboard": "\U0001f4cb",
        "notebook": "\U0001f4d3",
    },
    "ascii": {
        "bash": "$",
        "read": "R",
        "write": "W",
        "edit": "E",
        "grep": "?",
        "glob": "F",
        "git": "G",
        "web_search": "S",
        "web_fetch": "D",
        "monitor": "M",
        "task": "T",
        "mcp": "X",
        "image": "I",
        "clipboard": "C",
        "notebook": "N",
    },
}
TOOL_EMOJI = _TOOL_EMOJI_SETS.get(_ACTIVE_ICON_SET, _TOOL_EMOJI_SETS["nerd"])

LONG_RUN_CHARMS = [
    "still cooking...",
    "polishing edges...",
    "asking the void nicely...",
    "almost there...",
    "worth the wait...",
    "patience is a virtue...",
]


def _tool_base_name(name: str) -> str:
    """Normalize tool name to a base key for verb/emoji lookup."""
    # e.g. "Read" -> "read", "web_search" stays
    return name.lower().split("_")[0] if "_" not in name.lower() else name.lower()


def _get_tool_emoji(name: str) -> str:
    base = _tool_base_name(name)
    return TOOL_EMOJI.get(base, TOOL_EMOJI.get(name.lower(), "\u2692"))


def _get_tool_verb(name: str) -> str:
    base = _tool_base_name(name)
    return TOOL_VERBS.get(base, TOOL_VERBS.get(name.lower(), name.lower()))


def _get_tool_display_name(name: str) -> str:
    """Claude-Code-style CamelCase tool name for the header bullet line.

    ``read`` → ``Read``, ``web_search`` → ``WebSearch``, ``bash`` → ``Bash``.
    """
    if not name:
        return "Tool"
    parts = re.split(r"[_\s]+", str(name))
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _summarise_tool_result(name: str, content: str, is_error: bool) -> str:
    """Claude-Code-style one-line summary for a tool result.

    ``read``/``write`` → `` Read 42 lines``; generic → first line of
    output, elided to 80 chars. Used in the ``⎿`` branch line.
    """
    if is_error or not content:
        return ""
    stripped = content.strip()
    base = _tool_base_name(name)
    lines = stripped.splitlines()
    if base in ("read", "write", "edit"):
        return f"{len(lines)} lines"
    first = lines[0] if lines else ""
    return first if len(first) <= 80 else first[:77] + "…"


def _extract_tool_context(name: str, args_buffer: str) -> str:
    """Extract a short context string from tool arguments."""
    try:
        args = _json.loads(args_buffer) if args_buffer else {}
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(args, dict):
        return ""
    # file path
    fp = args.get("file_path", "") or args.get("path", "")
    if fp:
        return str(fp)
    # command
    cmd = args.get("command", "")
    if cmd:
        short = str(cmd).split("\n")[0]
        return short[:60] + ("..." if len(short) > 60 else "")
    # pattern
    pat = args.get("pattern", "")
    if pat:
        return str(pat)
    return ""


# --------------------------------------------------------------------------- #
#  Event protocol — provider layer yields these to the REPL
# --------------------------------------------------------------------------- #


class EventKind(Enum):
    TEXT_DELTA = auto()
    THINKING_DELTA = auto()  # optional: reasoning / thinking content
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
    return "\n".join(lines[:_ARG_HEAD] + [f"  … {hidden} more lines …"] + lines[-_ARG_TAIL:])


def _looks_like_json(s: str) -> bool:
    s = s.strip()
    return (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]"))


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
    start_time: float = field(default_factory=time.time)
    charm_shown: bool = False
    args_printed: bool = False  # True once header emitted with full context

    def header(self, spinner_frame: str | None = None) -> Text:
        """Render the single-line status header for this tool call.

        Claude-Code shape: ``● ToolName(context)`` — bullet + bold tool name
        + parenthesized short arg. Status icon (✓/✗ + elapsed) appended on
        completion; spinner frame appended while running.
        """
        context = _extract_tool_context(self.name, self.args_buffer)
        display_name = _get_tool_display_name(self.name)
        line = Text()
        line.append("\u25cf ", style=_STYLE_TOOL_BULLET)
        line.append(display_name, style="bold")
        if context:
            line.append("(", style=_STYLE_META)
            line.append(context, style=_STYLE_META)
            line.append(")", style=_STYLE_META)
        if self.status in ("pending", "running"):
            if spinner_frame:
                line.append(f"  {spinner_frame}", style=_STYLE_BRAND_DIM)
        elif self.status == "ok":
            elapsed = time.time() - self.start_time
            line.append(f"  {_ICON_OK} {elapsed:.1f}s", style=_STYLE_SUCCESS)
        else:  # err
            elapsed = time.time() - self.start_time
            line.append(f"  {_ICON_ERR} {elapsed:.1f}s", style=_STYLE_DANGER)
        return line


# --------------------------------------------------------------------------- #
#  Inline diff helper
# --------------------------------------------------------------------------- #


def _render_simple_diff(old_str: str, new_str: str) -> Text:
    """Render old_string -> new_string as a compact colored diff."""
    result = Text()
    for line in old_str.splitlines():
        result.append(f"    - {line}\n", style="red")
    for line in new_str.splitlines():
        result.append(f"    + {line}\n", style="green")
    return result


# --------------------------------------------------------------------------- #
#  Renderer
# --------------------------------------------------------------------------- #


class OutputRenderer:
    """Stateful renderer that processes ``StreamEvent`` objects.

    All output goes through ``console.print()`` --- no ``rich.live.Live``
    is used.  When the console is a ``RedirectedConsole`` (split-pane
    TUI), prints route into the output pane.  When it is a normal
    ``Console`` (e.g. in tests), prints go to the console's file.

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
        # Code-fence tracking: True while inside a fenced code block
        self._in_code_fence: bool = False
        # Print "* nellie" label only once per turn
        self._text_label_printed: bool = False
        # Spinner state (replaces Live)
        self._spinner_shown: bool = False
        # Tool state
        self._tool: _ToolState | None = None
        # Turn rhythm
        self._turn_started: bool = False
        # Thinking stream state --- tracks whether the thinking header has been
        # printed so subsequent deltas are streamed inline.
        self._thinking_started: bool = False

    # ── public API ─────────────────────────────────────────────────────

    def show_spinner(self) -> None:
        """Display a waiting indicator until the first event.

        Claude-Code style: ``✢ Thinking… (esc to interrupt)``. The animated
        counter (elapsed + token usage) lives in the status bar; this inline
        indicator is a single quiet line that disappears on first content.
        """
        if self._spinner_shown:
            return
        self._ensure_turn_break()
        indicator = Text()
        indicator.append(f"{_ICON_THINKING} ", style=_STYLE_BRAND_DIM)
        indicator.append("Thinking…", style=_STYLE_BRAND_DIM)
        indicator.append("  (esc to interrupt)", style="dim")
        self.console.print(indicator)
        self._spinner_shown = True

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
        """Flush any remaining buffered content."""
        self._flush_thinking()
        self._flush_text()
        self._tool = None
        self._in_code_fence = False
        self._text_label_printed = False
        self._thinking_started = False
        self._spinner_shown = False

    # ── display management ─────────────────────────────────────────────

    def _dismiss_spinner(self) -> None:
        """Mark the spinner as dismissed (real content is now showing)."""
        self._spinner_shown = False

    def _ensure_turn_break(self) -> None:
        """Emit a dimmed divider + blank line before the first block of a turn."""
        if self._turn_started:
            return
        self._turn_started = True
        self.console.print()
        self.console.print(Rule(style=_STYLE_DIVIDER, characters="\u2500"))
        self.console.print()

    # ── streaming text ─────────────────────────────────────────────────

    def _on_text_delta(self, delta: str) -> None:
        if not delta:
            return
        # Dismiss the static spinner indicator on first content
        if self._spinner_shown:
            self._dismiss_spinner()
        # End thinking block when assistant text starts arriving
        if self._thinking_started:
            self._end_thinking_block()

        self._text_buffer.append(delta)
        # Buffer output and only flush on complete blocks to avoid flicker:
        # - Double newline (paragraph break / blank line between blocks)
        # - End of a fenced code block (closing ```)
        # - Sentence boundary followed by whitespace
        # Single newlines are NOT flushed — they cause mid-paragraph
        # re-renders that flicker, especially on Windows where Live
        # redraws are expensive.
        joined = "".join(self._text_buffer)

        # Track code-fence state: toggle on each line that starts with ```
        # (with optional language tag). Only flush when transitioning from
        # inside a fence to outside (i.e., the closing fence).
        fence_closed = False
        if joined.rstrip().endswith("```"):
            # Check if this ``` is on a line by itself (or with only a
            # language tag after the opening ```).
            last_line = joined.rstrip().rsplit("\n", 1)[-1].strip()
            if last_line.startswith("```"):
                was_in_fence = self._in_code_fence
                self._in_code_fence = not self._in_code_fence
                # Only treat as a flush point when closing a fence
                fence_closed = was_in_fence and not self._in_code_fence

        # Flush only on paragraph breaks (``\n\n``) or code-fence closes.
        # We used to also flush on sentence boundaries for streaming
        # responsiveness, but that chunks markdown mid-block — bullets
        # and numbered lists get fragmented and Rich re-renders them as
        # unrelated lists, dropping items visually. Paragraph-level
        # flushing costs a little interactive feel but preserves list
        # structure, code fences, and headings end-to-end.
        if "\n\n" in joined[-3:] or fence_closed:
            self._flush_text()

    def _flush_text(self) -> None:
        if not self._text_buffer:
            return
        full = "".join(self._text_buffer).rstrip()
        self._text_buffer.clear()
        if not full:
            return

        # Print the assistant label ONCE per turn, not per flush
        if not self._text_label_printed:
            self._text_label_printed = True
            label = Text()
            label.append(f"{_ICON_ASSISTANT} ", style=_STYLE_ASSISTANT_LABEL)
            label.append("nellie", style=_STYLE_ASSISTANT_LABEL)
            self.console.print(label)
        # NO ``style=`` override on the Markdown — that flattens Rich's
        # built-in code-theme + link styling. Set code_theme explicitly so
        # fenced blocks render with Python/JS/etc. syntax colors like
        # Claude Code does.
        self.console.print(Markdown(full, code_theme="ansi_dark"))

    # ── thinking / reasoning ───────────────────────────────────────────

    def _on_thinking_delta(self, delta: str) -> None:
        """Stream each thinking delta immediately as dimmed italic text.

        On the first delta, prints a header line (icon + "reasoning" +
        an Esc-to-interrupt hint).  Subsequent deltas render inline so
        the user watches the reasoning stream in real time, Claude
        Code-style.
        """
        if not delta:
            return
        if self._spinner_shown:
            self._dismiss_spinner()

        # Print the thinking header on first delta
        if not self._thinking_started:
            self._thinking_started = True
            self._ensure_turn_break()
            header = Text()
            header.append(f"{_ICON_THINKING} ", style=_STYLE_THINKING)
            header.append("reasoning ", style=_STYLE_THINKING)
            header.append("(esc to interrupt)", style="dim")
            self.console.print(header)

        # Stream the delta immediately with thinking style
        self.console.print(
            Text(delta, style=_STYLE_THINKING_DIM),
            end="",
        )
        # Keep a buffer copy for total char count on collapse
        self._thinking_buffer.append(delta)

    def _end_thinking_block(self) -> None:
        """Close the live-streamed thinking block with a newline + summary.

        Called when the first non-thinking event (TEXT_DELTA,
        TOOL_CALL_START) arrives after a thinking sequence, or on
        finish().  Short thinking stays expanded; long thinking gets a
        collapsed char-count note.
        """
        if not self._thinking_started:
            return
        self._thinking_started = False

        full = "".join(self._thinking_buffer).strip()
        char_count = len(full)
        self._thinking_buffer.clear()

        # End the inline thinking stream with a newline
        self.console.print()
        # If there was substantial reasoning, show a summary note
        if char_count > 200:
            note = Text()
            note.append(f"  {_ICON_THINKING} ", style=_STYLE_META)
            note.append(f"{char_count:,} chars of reasoning", style=_STYLE_META)
            self.console.print(note)
        self.console.print()

    def _flush_thinking(self) -> None:
        """Flush remaining thinking — just delegates to _end_thinking_block."""
        self._end_thinking_block()

    # ── tool calls ─────────────────────────────────────────────────────

    def _on_tool_call_start(self, data: dict[str, Any] | None) -> None:
        # Close out any in-flight assistant text / thinking first.
        if self._spinner_shown:
            self._dismiss_spinner()
        if self._thinking_started:
            self._end_thinking_block()
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
            start_time=time.time(),
        )

        # Print the Claude-Code-style bullet header ONLY if we already have
        # enough to render ``● Tool(context)`` cleanly. If args are still
        # streaming, defer the print to _on_tool_call_end so we don't emit
        # a bare ``● Tool`` line followed by a corrected one.
        if _extract_tool_context(name, self._tool.args_buffer):
            self.console.print(self._tool.header())
            self._tool.args_printed = True

    def _on_tool_call_args_delta(self, delta: str) -> None:
        if self._tool is None or not delta:
            return
        self._tool.args_buffer += str(delta)

    def _on_tool_call_end(self, _data: Any = None) -> None:
        if self._tool is None:
            return

        # Deferred case: TOOL_CALL_START had no context, so we held the print.
        # Now that args are fully buffered, emit the header exactly once.
        if not self._tool.args_printed:
            self.console.print(self._tool.header())
            self._tool.args_printed = True

    def _on_tool_result(self, data: dict[str, Any] | None) -> None:
        # --- robust extraction: data may be str, dict, or None ---
        if isinstance(data, str):
            content = data
            is_error = False
        elif isinstance(data, dict):
            content = data.get("content", "")
            if isinstance(content, dict):
                content = str(content)
            else:
                content = str(content) if content else ""
            is_error = bool(data.get("is_error", False))
        else:
            content = str(data) if data else ""
            is_error = False
        if self._tool:
            tool_name = self._tool.name
        elif isinstance(data, dict):
            tool_name = str(data.get("name", "tool"))
        else:
            tool_name = "tool"

        # Compute elapsed time
        elapsed = time.time() - self._tool.start_time if self._tool else 0.0
        elapsed_str = f"{elapsed:.1f}s"

        # For write/edit tools, show inline diff if possible
        if tool_name in ("write", "edit") and not is_error:
            file_hint = ""
            old_str = ""
            new_str = ""
            if self._tool and self._tool.args_buffer:
                try:
                    args = _json.loads(self._tool.args_buffer)
                    file_hint = args.get("file_path", "")
                    old_str = args.get("old_string", "")
                    new_str = args.get("new_string", "")
                except Exception:  # noqa: BLE001
                    pass

            # Claude-Code-style result branch: `  ⎿  wrote <path>  ✓ 0.3s`
            verb = _get_tool_verb(tool_name)
            status_line = Text()
            status_line.append("  \u23bf  ", style=_STYLE_RESULT_BRANCH)
            status_line.append(verb, style=_STYLE_META)
            if file_hint:
                status_line.append(f" {file_hint}", style=_STYLE_META)
            status_line.append(f"  {_ICON_OK} {elapsed_str}", style=_STYLE_SUCCESS)
            self.console.print(status_line)

            # Show inline diff for edit tool
            if old_str and new_str and tool_name == "edit":
                diff_text = _render_simple_diff(old_str, new_str)
                self.console.print(diff_text)

            self._tool = None
            return

        # Update tool status
        if self._tool:
            self._tool.status = "err" if is_error else "ok"

        # Claude-Code-style result branch: `  ⎿  summary  ✓ 0.3s`
        verb = _get_tool_verb(tool_name)
        summary = _summarise_tool_result(tool_name, content, is_error)

        status_icon = _ICON_ERR if is_error else _ICON_OK
        status_style = _STYLE_DANGER if is_error else _STYLE_SUCCESS

        status_line = Text()
        status_line.append("  \u23bf  ", style=_STYLE_RESULT_BRANCH)
        if summary:
            status_line.append(summary, style=_STYLE_META)
        else:
            status_line.append(verb, style=_STYLE_META)
        status_line.append(f"  {status_icon} {elapsed_str}", style=status_style)
        self.console.print(status_line)

        # Short summary for errors
        stripped = content.strip()
        if is_error and stripped:
            first_line = stripped.splitlines()[0]
            summary = first_line if len(first_line) <= 80 else first_line[:77] + "..."
            err_line = Text()
            err_line.append("  ", style=_STYLE_META)
            err_line.append(summary, style=_STYLE_DANGER)
            self.console.print(err_line)

        # Long results get a full panel; short ones are already covered.
        if stripped and len(stripped) > _RESULT_INLINE_LIMIT and not is_error:
            display = stripped
            if len(display) > _RESULT_PANEL_LIMIT:
                extra = len(stripped) - _RESULT_PANEL_LIMIT
                display = display[:_RESULT_PANEL_LIMIT] + f"\n\u2026 ({extra} chars truncated)"

            # Detect language for syntax highlighting
            lang = "text"
            if _looks_like_json(display):
                lang = "json"
            elif self._tool and self._tool.args_buffer:
                try:
                    args = _json.loads(self._tool.args_buffer)
                    fp = args.get("file_path", "") or args.get("path", "")
                    ext = fp.rsplit(".", 1)[-1].lower() if "." in fp else ""
                    lang_map = {
                        "py": "python",
                        "js": "javascript",
                        "ts": "typescript",
                        "rs": "rust",
                        "go": "go",
                        "java": "java",
                        "rb": "ruby",
                        "sh": "bash",
                        "bash": "bash",
                        "zsh": "bash",
                        "yaml": "yaml",
                        "yml": "yaml",
                        "toml": "toml",
                        "json": "json",
                        "md": "markdown",
                        "sql": "sql",
                        "html": "html",
                        "css": "css",
                        "xml": "xml",
                    }
                    lang = lang_map.get(ext, "text")
                except Exception:  # noqa: BLE001
                    pass

            body: RenderableType
            if lang != "text":
                body = Syntax(
                    display,
                    lang,
                    theme="ansi_dark",
                    line_numbers=False,
                    word_wrap=True,
                    background_color="default",
                )
            else:
                body = Text(display, style=TOOL_RESULT if not is_error else ERROR)

            title = f"[{_STYLE_META}]output \u00b7 {tool_name}[/]"
            self.console.print(
                Panel(
                    body,
                    title=title,
                    title_align="left",
                    border_style=_STYLE_META,
                    padding=(0, 1),
                    expand=True,
                )
            )

        self._tool = None

    # ── errors ─────────────────────────────────────────────────────────

    def _on_error(self, data: Any = None) -> None:
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
        # Flush any buffered text/thinking so the cost line never prints
        # before the reply itself. Short replies without a sentence
        # boundary used to render in the wrong order.
        if self._spinner_shown:
            self._dismiss_spinner()
        self._flush_thinking()
        self._flush_text()

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
            self.console.print(Text("  " + " · ".join(parts), style=COST_INFO))

    def _on_done(self, _data: Any = None) -> None:
        self._flush_thinking()
        self._flush_text()
        # Reset turn rhythm for the next invocation.
        self._turn_started = False
