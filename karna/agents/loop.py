"""Core tool-use agent loop — the heart of Karna.

Implements the iterative tool-call cycle:

    1. Send messages to the provider with tool definitions
    2. If the response contains tool_calls, execute each tool
    3. Append tool results to the conversation
    4. Loop back to step 1
    5. If the response has no tool_calls, yield final text and stop

Supports both streaming (``stream``) and non-streaming (``complete``)
providers.  Works with any provider that follows the
``BaseProvider`` interface and any tools that follow ``BaseTool``.

Ported from the cc-src coordinator pattern with attribution to the
Anthropic Claude Code codebase.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator

from karna.models import Conversation, Message, StreamEvent, ToolCall, ToolResult
from karna.providers.base import BaseProvider
from karna.tools import get_tool
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------- #
#  Tool execution helper
# ----------------------------------------------------------------------- #

async def _execute_tool(
    tool: BaseTool,
    arguments: dict[str, Any],
) -> ToolResult:
    """Run a single tool and return a ``ToolResult``.

    Catches all exceptions so the agent loop never crashes from tool
    errors — failures are returned as error strings the model can
    reason about.
    """
    # We'll set tool_call_id later when we know it
    try:
        result_text = await tool.execute(**arguments)
        return ToolResult(tool_call_id="", content=result_text, is_error=False)
    except Exception as exc:
        logger.exception("Tool %s raised: %s", tool.name, exc)
        return ToolResult(tool_call_id="", content=f"[error] {exc}", is_error=True)


# ----------------------------------------------------------------------- #
#  Tool definitions builder
# ----------------------------------------------------------------------- #

def _build_tool_defs(
    tools: list[BaseTool],
    *,
    format: str = "openai",
) -> list[dict[str, Any]]:
    """Convert tool instances to API-compatible definitions."""
    if format == "anthropic":
        return [t.to_anthropic_tool() for t in tools]
    return [t.to_openai_tool() for t in tools]


# ----------------------------------------------------------------------- #
#  Streaming agent loop
# ----------------------------------------------------------------------- #

async def agent_loop(
    provider: BaseProvider,
    conversation: Conversation,
    tools: list[BaseTool],
    *,
    system_prompt: str | None = None,
    max_iterations: int = 25,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> AsyncIterator[StreamEvent]:
    """Run the tool-use agent loop (streaming variant).

    Yields ``StreamEvent`` objects. The caller can process text deltas
    and tool-call events in real time.

    The loop terminates when the model produces a response with no
    tool calls, or when *max_iterations* is reached.
    """
    tool_defs = _build_tool_defs(tools)
    tool_map = {t.name: t for t in tools}

    for iteration in range(max_iterations):
        # ---- Collect events from one provider turn --------------------
        pending_tool_calls: list[ToolCall] = []
        assistant_text_parts: list[str] = []
        # Accumulate argument fragments keyed by tool call id
        arg_buffers: dict[str, str] = {}
        done = False

        async for event in provider.stream(
            conversation.messages,
            tools=tool_defs,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            yield event

            if event.type == "text" and event.text:
                assistant_text_parts.append(event.text)

            elif event.type == "tool_call_start" and event.tool_call:
                tc = event.tool_call
                arg_buffers[tc.id] = json.dumps(tc.arguments) if tc.arguments else ""

            elif event.type == "tool_call_delta" and event.tool_call:
                tc = event.tool_call
                if tc.id in arg_buffers:
                    # Delta text arrives in event.text for some providers
                    delta = event.text or ""
                    arg_buffers[tc.id] += delta

            elif event.type == "tool_call_end" and event.tool_call:
                tc = event.tool_call
                # Merge any accumulated argument fragments
                if tc.id in arg_buffers and not tc.arguments:
                    raw = arg_buffers[tc.id]
                    try:
                        tc = tc.model_copy(update={"arguments": json.loads(raw)})
                    except json.JSONDecodeError:
                        tc = tc.model_copy(update={"arguments": {}})
                pending_tool_calls.append(tc)

            elif event.type == "done":
                done = True

            elif event.type == "error":
                # Yield the error and stop
                return

        # ---- If no tool calls, conversation is complete ----------------
        if not pending_tool_calls:
            # Append the final assistant message to conversation
            assistant_text = "".join(assistant_text_parts)
            if assistant_text:
                conversation.messages.append(
                    Message(role="assistant", content=assistant_text)
                )
            return

        # ---- Execute tool calls and build messages --------------------
        assistant_text = "".join(assistant_text_parts)

        # Append assistant message with tool calls
        conversation.messages.append(
            Message(
                role="assistant",
                content=assistant_text,
                tool_calls=pending_tool_calls,
            )
        )

        # Execute each tool call
        tool_results: list[ToolResult] = []
        for tc in pending_tool_calls:
            tool = tool_map.get(tc.name)
            if tool is None:
                result = ToolResult(
                    tool_call_id=tc.id,
                    content=f"[error] Unknown tool: {tc.name}",
                    is_error=True,
                )
            else:
                result = await _execute_tool(tool, tc.arguments)
                result = result.model_copy(update={"tool_call_id": tc.id})
            tool_results.append(result)

        # Append tool result message
        conversation.messages.append(
            Message(
                role="tool",
                tool_results=tool_results,
            )
        )

        if done and not pending_tool_calls:
            return

    # Max iterations reached
    yield StreamEvent(
        type="error",
        error=f"Agent loop reached maximum iterations ({max_iterations})",
    )


# ----------------------------------------------------------------------- #
#  Non-streaming agent loop (uses provider.complete)
# ----------------------------------------------------------------------- #

async def agent_loop_sync(
    provider: BaseProvider,
    conversation: Conversation,
    tools: list[BaseTool],
    *,
    system_prompt: str | None = None,
    max_iterations: int = 25,
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> Message:
    """Run the tool-use agent loop (non-streaming variant).

    Returns the final assistant ``Message`` after all tool calls have
    been resolved.
    """
    tool_defs = _build_tool_defs(tools)
    tool_map = {t.name: t for t in tools}

    for iteration in range(max_iterations):
        response = await provider.complete(
            conversation.messages,
            tools=tool_defs,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # No tool calls — we're done
        if not response.tool_calls:
            conversation.messages.append(response)
            return response

        # Append assistant message with tool calls
        conversation.messages.append(response)

        # Execute each tool call
        tool_results: list[ToolResult] = []
        for tc in response.tool_calls:
            tool = tool_map.get(tc.name)
            if tool is None:
                result = ToolResult(
                    tool_call_id=tc.id,
                    content=f"[error] Unknown tool: {tc.name}",
                    is_error=True,
                )
            else:
                result = await _execute_tool(tool, tc.arguments)
                result = result.model_copy(update={"tool_call_id": tc.id})
            tool_results.append(result)

        # Append tool result message
        conversation.messages.append(
            Message(
                role="tool",
                tool_results=tool_results,
            )
        )

    # Max iterations reached
    return Message(
        role="assistant",
        content=f"[error] Agent loop reached maximum iterations ({max_iterations})",
    )
