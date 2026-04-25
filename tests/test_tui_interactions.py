"""Tests for the new TUI interaction surface on dev.

Covers the findings from ``research/karna/TUI_AUDIT_20260420.md``:
- REPLState exposes the new flags (output_window, output_scroll_locked,
  interrupt_requested) that the keybindings and autoscroll depend on.
- TUIOutputWriter evicts oldest lines once the ring-buffer cap is hit.
"""

from __future__ import annotations

from karna.tui.repl import (
    _MAX_OUTPUT_LINES,
    REPLState,
    TUIOutputWriter,
)


class TestReplStateFlags:
    """These flags are the contract between keybindings, the render
    hook, and the agent loop. Removing one silently breaks a fix."""

    def test_new_flags_default_cleanly(self):
        state = REPLState()
        assert state.output_window is None
        assert state.output_scroll_locked is False
        assert state.interrupt_requested is False

    def test_interrupt_flag_can_be_toggled(self):
        state = REPLState()
        state.interrupt_requested = True
        assert state.interrupt_requested is True
        state.interrupt_requested = False
        assert state.interrupt_requested is False


class TestOutputBufferBounds:
    """P2 fix: the output buffer must not grow without bound."""

    def test_ring_buffer_evicts_oldest(self):
        writer = TUIOutputWriter(width=80)
        # Fill well past the cap so eviction kicks in.
        for i in range(_MAX_OUTPUT_LINES + 2000):
            writer.write_ansi(f"line {i}")
        assert len(writer._lines) <= _MAX_OUTPUT_LINES
        # The most recent chunks must have survived — user cares about
        # what's current, not what's ancient.
        assert any(f"line {_MAX_OUTPUT_LINES + 1999}" in ln for ln in writer._lines)
        # And the very-oldest must have been dropped.
        assert not any("line 0" == ln for ln in writer._lines)

    def test_under_cap_no_eviction(self):
        writer = TUIOutputWriter(width=80)
        for i in range(50):
            writer.write_ansi(f"line {i}")
        assert len(writer._lines) == 50
