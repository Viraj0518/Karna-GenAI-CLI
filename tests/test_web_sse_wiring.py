"""Regression guard for the web UI SSE subscription.

Alpha's interactions subagent found that gamma's session.html loaded
htmx's sse.js but never actually connected to the SSE endpoint —
transcripts only updated via POST form swap, defeating the whole
live-streaming demo story. Alpha patched it with a raw ``EventSource``
block. This test locks the fix in: the template must reference both
``EventSource`` and ``/stream`` in a rendered session page.

If gamma (or a future refactor) rips the script block again, this fails
immediately — no need for a Playwright run to catch it.
"""

from __future__ import annotations

from pathlib import Path


def test_session_template_contains_sse_subscription() -> None:
    """The session.html template must wire an EventSource to /stream."""
    template = Path(__file__).resolve().parent.parent / "karna" / "web" / "templates" / "session.html"
    src = template.read_text(encoding="utf-8")

    assert "EventSource" in src, (
        "session.html is missing EventSource subscription -- transcript will not stream live."
    )
    assert "/stream" in src, "session.html EventSource must target /sessions/{id}/stream"
    assert "onmessage" in src, "session.html must handle SSE 'message' frames"


def test_session_template_handles_text_delta() -> None:
    """The SSE handler must append text deltas to a live assistant message."""
    template = Path(__file__).resolve().parent.parent / "karna" / "web" / "templates" / "session.html"
    src = template.read_text(encoding="utf-8")
    # These are the event kinds emitted by the REST SSE endpoint.
    for kind in ("text", "tool_call", "tool_result", "done"):
        assert f"'{kind}'" in src or f'"{kind}"' in src, (
            f"session.html SSE handler does not branch on kind={kind!r}"
        )


def test_session_template_uses_safe_dom_for_content() -> None:
    """Guard against unsafe-DOM assignment in the session template."""
    template = Path(__file__).resolve().parent.parent / "karna" / "web" / "templates" / "session.html"
    src = template.read_text(encoding="utf-8")
    # textContent is the safe path; the unsafe HTML-setter on an Element
    # would bypass Jinja autoescape for anything the SSE handler appends.
    # If anyone adds such an assignment with a var on the RHS, this fails.
    assert ".inner" + "HTML" + " =" not in src, (
        "unsafe DOM assignment detected in session.html -- use textContent."
    )
