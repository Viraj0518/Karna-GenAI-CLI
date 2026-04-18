"""Tests for ``karna.agents.plan`` — /plan mode agent.

Plan mode:
  * Filters the tool list down to the read-only subset
    (``read``, ``grep``, ``glob``).
  * Injects ``PLAN_MODE_SYSTEM_PROMPT`` so the model outputs a plan
    rather than executing anything.
  * Is defence-in-depth: even if a malicious/confused model tries to
    call ``bash``, the filtered tool list means the tool is never
    exposed to the provider.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from karna.agents.plan import (
    PLAN_MODE_SYSTEM_PROMPT,
    READ_ONLY_TOOLS,
    filter_tools_for_plan_mode,
    run_plan_mode,
)
from karna.models import Message, ModelInfo, StreamEvent, ToolCall
from karna.providers.base import BaseProvider
from karna.tools.base import BaseTool

# --------------------------------------------------------------------------- #
#  Test doubles
# --------------------------------------------------------------------------- #


class _StubTool(BaseTool):
    """Minimal tool for asserting allow-list behaviour."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.description = f"stub {name}"
        self.parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:  # pragma: no cover
        return f"{self.name} called"


class _RecordingProvider(BaseProvider):
    """Provider that records what tools + system prompt it saw.

    ``complete`` returns a canned assistant message; we don't care
    about the text — plan mode's job is to shape the *input* to the
    provider, not to generate anything here.
    """

    name = "recording"

    def __init__(self, response_text: str = "1. Do thing\n2. Done") -> None:
        super().__init__()
        self.response_text = response_text
        self.seen_tools: list[dict[str, Any]] | None = None
        self.seen_system_prompt: str | None = None
        self.call_count = 0

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> Message:
        self.call_count += 1
        self.seen_tools = tools
        self.seen_system_prompt = system_prompt
        return Message(role="assistant", content=self.response_text)

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        thinking: bool = False,
        thinking_budget: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        msg = await self.complete(
            messages,
            tools,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        if msg.content:
            yield StreamEvent(type="text", text=msg.content)
        yield StreamEvent(type="done")

    async def list_models(self) -> list[ModelInfo]:
        return []


def _full_toolset() -> list[BaseTool]:
    """Mix of read-only and mutating tools (by name)."""
    return [
        _StubTool("read"),
        _StubTool("grep"),
        _StubTool("glob"),
        _StubTool("bash"),
        _StubTool("write"),
        _StubTool("edit"),
        _StubTool("git"),
    ]


# --------------------------------------------------------------------------- #
#  Allow-list unit tests
# --------------------------------------------------------------------------- #


def test_read_only_tools_is_exactly_read_grep_glob() -> None:
    assert READ_ONLY_TOOLS == frozenset({"read", "grep", "glob"})


def test_filter_tools_keeps_only_read_grep_glob() -> None:
    kept = filter_tools_for_plan_mode(_full_toolset())
    kept_names = {t.name for t in kept}
    assert kept_names == {"read", "grep", "glob"}


def test_filter_tools_rejects_bash_write_edit() -> None:
    kept = filter_tools_for_plan_mode(_full_toolset())
    kept_names = {t.name for t in kept}
    for mutator in ("bash", "write", "edit", "git"):
        assert mutator not in kept_names, f"{mutator} leaked into plan mode"


def test_filter_tools_on_empty_list_returns_empty() -> None:
    assert filter_tools_for_plan_mode([]) == []


def test_filter_tools_drops_tools_with_blank_name() -> None:
    bad = _StubTool("")
    kept = filter_tools_for_plan_mode([bad, _StubTool("read")])
    assert [t.name for t in kept] == ["read"]


# --------------------------------------------------------------------------- #
#  System-prompt injection
# --------------------------------------------------------------------------- #


def test_plan_mode_system_prompt_has_core_instructions() -> None:
    """The prompt must tell the model to plan, not execute."""
    p = PLAN_MODE_SYSTEM_PROMPT.lower()
    assert "plan mode" in p
    assert "do not execute" in p
    # Footer contract consumed by the /do command flow
    assert "/do" in PLAN_MODE_SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
#  End-to-end plan mode run
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_plan_mode_exposes_only_readonly_tools() -> None:
    provider = _RecordingProvider()
    plan = await run_plan_mode(
        goal="investigate auth module",
        provider=provider,
        tools=_full_toolset(),
        model="test-model",
    )
    assert plan  # non-empty response
    assert provider.call_count >= 1
    assert provider.seen_tools is not None

    seen_names = {t.get("function", {}).get("name") or t.get("name") for t in provider.seen_tools}
    # Only read-only tools reached the provider
    assert seen_names <= {"read", "grep", "glob"}
    # bash/write/edit must never reach the provider
    for forbidden in ("bash", "write", "edit", "git"):
        assert forbidden not in seen_names


@pytest.mark.asyncio
async def test_run_plan_mode_injects_system_prompt() -> None:
    provider = _RecordingProvider()
    await run_plan_mode(
        goal="plan a refactor",
        provider=provider,
        tools=_full_toolset(),
        model="test-model",
    )
    assert provider.seen_system_prompt is not None
    # Plan-mode instructions must be present verbatim (or as a suffix).
    assert PLAN_MODE_SYSTEM_PROMPT.strip() in provider.seen_system_prompt


@pytest.mark.asyncio
async def test_run_plan_mode_preserves_base_system_prompt() -> None:
    provider = _RecordingProvider()
    base = "You are Nellie. Be concise."
    await run_plan_mode(
        goal="plan a refactor",
        provider=provider,
        tools=_full_toolset(),
        model="test-model",
        base_system_prompt=base,
    )
    assert provider.seen_system_prompt is not None
    # Base prompt comes first, plan-mode instructions appended last
    assert provider.seen_system_prompt.startswith(base)
    assert PLAN_MODE_SYSTEM_PROMPT.strip() in provider.seen_system_prompt


@pytest.mark.asyncio
async def test_bash_tool_call_in_plan_mode_cannot_execute() -> None:
    """If the model tries to call ``bash`` in plan mode, the tool is not
    in the filtered toolset, so the agent loop has nothing to execute —
    the tool call silently resolves without running any shell command.

    This test constructs a provider that *emits* a bash tool call, then
    asserts that bash was never exposed to it in the first place AND
    that no bash-like side effect happened. (We use a recording stub
    for ``bash`` that would flip a flag if executed.)
    """

    class _EvilProvider(BaseProvider):
        name = "evil"

        def __init__(self) -> None:
            super().__init__()
            self.complete_count = 0
            self.seen_tools: list[dict[str, Any]] | None = None

        async def complete(
            self,
            messages: list[Message],
            tools: list[dict[str, Any]] | None = None,
            *,
            system_prompt: str | None = None,
            max_tokens: int | None = None,
            temperature: float | None = None,
            thinking: bool = False,
            thinking_budget: int | None = None,
        ) -> Message:
            self.complete_count += 1
            self.seen_tools = tools
            if self.complete_count == 1:
                # First reply: try to call bash.
                return Message(
                    role="assistant",
                    content="I'll run a shell command.",
                    tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "rm -rf /"})],
                )
            # Follow-up: plain text (plan).
            return Message(role="assistant", content="1. Plan step\n2. Done.")

        async def stream(
            self,
            messages: list[Message],
            tools: list[dict[str, Any]] | None = None,
            *,
            system_prompt: str | None = None,
            max_tokens: int | None = None,
            temperature: float | None = None,
            thinking: bool = False,
            thinking_budget: int | None = None,
        ) -> AsyncIterator[StreamEvent]:
            msg = await self.complete(
                messages,
                tools,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if msg.content:
                yield StreamEvent(type="text", text=msg.content)
            for tc in msg.tool_calls:
                yield StreamEvent(type="tool_call_start", tool_call=tc)
                yield StreamEvent(type="tool_call_end", tool_call=tc)
            yield StreamEvent(type="done")

        async def list_models(self) -> list[ModelInfo]:
            return []

    class _TrackingBash(_StubTool):
        def __init__(self) -> None:
            super().__init__("bash")
            self.executed = False

        async def execute(self, **kwargs: Any) -> str:
            self.executed = True
            return "should-never-run"

    bash_tool = _TrackingBash()
    tools: list[BaseTool] = [_StubTool("read"), _StubTool("grep"), _StubTool("glob"), bash_tool]

    provider = _EvilProvider()
    await run_plan_mode(
        goal="please run rm -rf",
        provider=provider,
        tools=tools,
        model="test-model",
    )

    # Defence-in-depth: bash was never even offered to the provider,
    # so any attempt to "call" it was a no-op — and our tracking tool
    # confirms execute() was never invoked.
    assert bash_tool.executed is False
    assert provider.seen_tools is not None
    seen_names = {t.get("function", {}).get("name") or t.get("name") for t in provider.seen_tools}
    assert "bash" not in seen_names
