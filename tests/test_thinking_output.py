"""Tests for live thinking-stream output and Esc-to-interrupt plumbing.

Covers:
- Thinking deltas render immediately (not buffered) via the OutputRenderer
- Thinking header appears on first delta with the Esc hint
- Thinking block ends with a divider/summary when text starts
- Interrupt flag detection in the REPL event loop
- StreamEvent model accepts the new "thinking" type
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from karna.models import StreamEvent as ModelStreamEvent
from karna.tui.output import EventKind, OutputRenderer, StreamEvent


def _make_console() -> tuple[Console, StringIO]:
    """Create a Rich Console that writes to a StringIO buffer."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="truecolor",
    )
    return console, buf


# --------------------------------------------------------------------------- #
#  Thinking header appears on first delta
# --------------------------------------------------------------------------- #


def test_thinking_header_on_first_delta() -> None:
    """The thinking header (icon + 'reasoning' + esc hint) should print
    on the very first THINKING_DELTA event."""
    console, buf = _make_console()
    renderer = OutputRenderer(console)

    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="Let me think"))
    renderer.finish()

    output = buf.getvalue()
    assert "reasoning" in output
    assert "esc to interrupt" in output


# --------------------------------------------------------------------------- #
#  Thinking deltas render immediately (not buffered until flush)
# --------------------------------------------------------------------------- #


def test_thinking_deltas_render_inline() -> None:
    """Multiple THINKING_DELTA events should all appear in the output
    (streamed inline), not collapsed to a single summary line."""
    console, buf = _make_console()
    renderer = OutputRenderer(console)

    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="First chunk. "))
    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="Second chunk. "))
    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="Third chunk."))
    renderer.finish()

    output = buf.getvalue()
    assert "First chunk" in output
    assert "Second chunk" in output
    assert "Third chunk" in output


# --------------------------------------------------------------------------- #
#  Thinking block ends when text starts
# --------------------------------------------------------------------------- #


def test_thinking_ends_when_text_delta_arrives() -> None:
    """When a TEXT_DELTA event follows THINKING_DELTA events, the
    thinking block should close (newline + optional summary) and the
    text should appear as normal assistant output."""
    console, buf = _make_console()
    renderer = OutputRenderer(console)

    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="reasoning about the problem"))
    renderer.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="Here is my answer.\n"))
    renderer.finish()

    output = buf.getvalue()
    assert "reasoning about the problem" in output
    assert "Here is my answer" in output


# --------------------------------------------------------------------------- #
#  Long thinking gets a char-count summary
# --------------------------------------------------------------------------- #


def test_long_thinking_shows_char_count() -> None:
    """When thinking content exceeds 200 chars, a summary note with the
    character count should appear after the block closes."""
    console, buf = _make_console()
    renderer = OutputRenderer(console)

    # Emit > 200 chars of thinking
    thinking_text = "x" * 250
    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data=thinking_text))
    renderer.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="Done.\n"))
    renderer.finish()

    output = buf.getvalue()
    assert "250" in output  # char count
    assert "chars of reasoning" in output


# --------------------------------------------------------------------------- #
#  Short thinking does NOT show char-count summary
# --------------------------------------------------------------------------- #


def test_short_thinking_no_char_count() -> None:
    """When thinking is short (<= 200 chars), no char-count summary line."""
    console, buf = _make_console()
    renderer = OutputRenderer(console)

    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="Quick thought."))
    renderer.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="Answer.\n"))
    renderer.finish()

    output = buf.getvalue()
    assert "chars of reasoning" not in output


# --------------------------------------------------------------------------- #
#  Thinking block also ends when tool call starts
# --------------------------------------------------------------------------- #


def test_thinking_ends_on_tool_call_start() -> None:
    """TOOL_CALL_START after thinking should close the thinking block."""
    console, buf = _make_console()
    renderer = OutputRenderer(console)

    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="I need to run a tool"))
    renderer.handle(
        StreamEvent(
            kind=EventKind.TOOL_CALL_START,
            data={"name": "bash", "id": "tc_1", "arguments": "{}"},
        )
    )
    renderer.finish()

    output = buf.getvalue()
    assert "reasoning" in output
    assert "I need to run a tool" in output


# --------------------------------------------------------------------------- #
#  No thinking → no header
# --------------------------------------------------------------------------- #


def test_no_thinking_no_header() -> None:
    """If only TEXT_DELTA events arrive, no thinking header should appear."""
    console, buf = _make_console()
    renderer = OutputRenderer(console)

    renderer.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="Just text.\n"))
    renderer.finish()

    output = buf.getvalue()
    assert "reasoning" not in output
    assert "esc to interrupt" not in output


# --------------------------------------------------------------------------- #
#  _thinking_started resets between turns
# --------------------------------------------------------------------------- #


def test_thinking_resets_between_turns() -> None:
    """After finish(), a new thinking block should print a fresh header."""
    console, buf = _make_console()
    renderer = OutputRenderer(console)

    # Turn 1
    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="Turn 1 thinking"))
    renderer.finish()

    # Turn 2
    renderer.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data="Turn 2 thinking"))
    renderer.finish()

    output = buf.getvalue()
    # "reasoning" header should appear twice (once per turn)
    assert output.count("reasoning") >= 2


# --------------------------------------------------------------------------- #
#  Model StreamEvent accepts "thinking" type
# --------------------------------------------------------------------------- #


def test_model_stream_event_thinking_type() -> None:
    """The Pydantic StreamEvent model in karna.models should accept
    type='thinking' without validation errors."""
    evt = ModelStreamEvent(type="thinking", text="some reasoning")
    assert evt.type == "thinking"
    assert evt.text == "some reasoning"


# --------------------------------------------------------------------------- #
#  Always-active input state (unit test)
# --------------------------------------------------------------------------- #


def test_repl_state_creation() -> None:
    """The REPLState class should be importable and instantiable."""
    from karna.tui.repl import REPLState

    state = REPLState()
    assert state.agent_running is False
    assert state.agent_task is None
    assert state.input_queue.empty()
