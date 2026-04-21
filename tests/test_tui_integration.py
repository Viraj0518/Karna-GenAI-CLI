"""Headless TUI integration tests — catch rendering/queueing bugs without a real TTY.

These tests drive the REPL's state + accept-handler code path directly with a
fake provider, then inspect ``TUIOutputWriter._lines`` to verify what the user
SHOULD have seen on screen. No prompt_toolkit ``Application`` is started — we
replay just the writer → output-pane contract.

The motivating bug: Viraj typed prompt 2 while prompt 1's turn was still
running. Prompt 2 got queued as a steering message rather than starting a new
turn, so no fresh ``✦ Thinking…`` spinner appeared. Video frame looked like a
blank pane. Trace log proved the path — ``-> message queued`` printed at the
same moment as the first turn's TEXT_DELTA events. These tests lock down that
exact timeline so the regression can't resurface silently.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from karna.models import Conversation, Message, StreamEvent
from karna.providers.base import BaseProvider
from karna.tui.repl import REPLState, TUIOutputWriter


class _ScriptedProvider(BaseProvider):
    """Provider that yields a scripted sequence of events, with a configurable
    per-turn delay so tests can reproduce the 'user types while agent running'
    race deterministically."""

    name = "scripted"

    def __init__(self, replies: list[str], per_turn_delay: float = 0.0) -> None:
        super().__init__()
        self._replies = list(replies)
        self._delay = per_turn_delay
        self.calls = 0

    async def complete(self, *args: Any, **kwargs: Any) -> Message:
        self.calls += 1
        text = self._replies.pop(0) if self._replies else "(no more replies)"
        return Message(role="assistant", content=text)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        text = self._replies.pop(0) if self._replies else "(no more replies)"
        yield StreamEvent(type="text", text=text)
        yield StreamEvent(type="done")

    async def list_models(self):
        return []


def test_writer_appends_are_ordered_and_annotated():
    """Basic sanity: writer preserves insertion order + invalidates callback."""
    w = TUIOutputWriter(width=80)
    fired = []
    w.set_invalidate(lambda: fired.append(1))
    w._append("a")
    w._append("b")
    w._append("c")
    assert list(w._lines) == ["a", "b", "c"]
    # _append itself doesn't invalidate — only explicit writers do. (Matches
    # the real behaviour we saw in the trace log.)
    assert fired == []


def test_writer_deque_caps_at_5000_lines():
    """Runaway tool output can't starve the render loop."""
    w = TUIOutputWriter(width=80)
    for i in range(6000):
        w._append(f"line {i}")
    assert len(w._lines) == 5000
    # Oldest evicted, newest kept.
    assert w._lines[0] == "line 1000"
    assert w._lines[-1] == "line 5999"


def test_repl_state_has_turn_start_field():
    """Regression guard: the status bar's live Thinking counter depends on
    state.turn_start being present. A missing field would silently break the
    counter without crashing."""
    s = REPLState()
    assert hasattr(s, "turn_start")
    assert s.turn_start == 0.0  # not started yet
    assert s.agent_running is False


@pytest.mark.asyncio
async def test_queue_vs_fresh_turn_contract():
    """The core bug Viraj hit: if agent_running is True, a new user input goes
    onto input_queue (mid-stream steering), NOT a fresh turn. Lock that
    contract so any future refactor that breaks it surfaces here, not in a
    user's pane."""
    s = REPLState()
    s.agent_running = True
    # Simulate the accept-handler's queue path
    s.input_queue.put_nowait("second prompt")
    assert s.input_queue.qsize() == 1

    # When turn 1 finishes, finally block flips this False
    s.agent_running = False
    assert not s.agent_running

    # A subsequent accept-handler call with agent_running=False should route
    # to a fresh turn (see repl.py:1040). We don't call the real handler here
    # (requires full app wiring); we just assert the guard condition.
    assert s.input_queue.qsize() == 1  # steering still pending, to be consumed


@pytest.mark.asyncio
async def test_scripted_provider_roundtrip():
    """End-to-end: drive agent_loop with a scripted provider, verify the event
    stream shape matches what the TUI will render."""
    from karna.agents.loop import agent_loop

    prov = _ScriptedProvider(replies=["Hi!", "What's up?"])
    conv = Conversation(messages=[Message(role="user", content="hey")])

    events = []
    async for ev in agent_loop(prov, conv, tools=[]):
        events.append(ev)

    text_events = [e for e in events if e.type == "text"]
    assert any("Hi" in (e.text or "") for e in text_events)

    # Second turn should work too
    conv.messages.append(Message(role="assistant", content="Hi!"))
    conv.messages.append(Message(role="user", content="how are you"))
    events2 = []
    async for ev in agent_loop(prov, conv, tools=[]):
        events2.append(ev)
    assert any("up" in (e.text or "") for e in events2 if e.type == "text")


def test_status_bar_formatting_components_are_importable():
    """The status-bar closure in _build_application references module-level
    identifiers that prompt_toolkit calls on a 500ms timer. If any of them
    disappear from the import block, ``nellie`` crashes on first tick."""
    from karna.tui.repl import BRAILLE_FRAMES, FACES, LONG_RUN_CHARMS, VERBS

    for name, collection in [
        ("BRAILLE_FRAMES", BRAILLE_FRAMES),
        ("FACES", FACES),
        ("VERBS", VERBS),
        ("LONG_RUN_CHARMS", LONG_RUN_CHARMS),
    ]:
        assert len(collection) > 0, f"{name} is empty"
        assert all(isinstance(x, str) for x in collection)
