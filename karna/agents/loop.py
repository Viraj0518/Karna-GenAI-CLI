"""Core tool-use agent loop — the heart of Karna.

Implements the iterative tool-call cycle:

    1. Send messages to the provider with tool definitions
    2. If the response contains tool_calls, execute each tool
    3. Append tool results to the conversation
    4. Loop back to step 1
    5. If the response has no tool_calls, yield final text and stop

Hardened with:
- Granular tool-execution error recovery (timeout, permission, file-not-found)
- Provider API retry with exponential backoff (429, 5xx, connection errors)
- Malformed tool-call JSON recovery
- Infinite tool-call loop detection
- Empty/null model response handling with retry nudge
- Context overflow auto-truncation
- Pre-execution safety checks (via ``safety.py``)

Supports both streaming (``stream``) and non-streaming (``complete``)
providers.  Works with any provider that follows the
``BaseProvider`` interface and any tools that follow ``BaseTool``.

Ported from the cc-src coordinator pattern with attribution to the
Anthropic Claude Code codebase.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any, AsyncIterator

import httpx

from karna.agents.safety import pre_tool_check
from karna.compaction.compactor import Compactor  # noqa: F401
from karna.models import Conversation, Message, StreamEvent, ToolCall, ToolResult
from karna.permissions.manager import PermissionLevel, PermissionManager
from karna.providers.base import BaseProvider
from karna.tools.base import BaseTool

logger = logging.getLogger(__name__)

# How many identical consecutive tool calls before we break the loop
_LOOP_DETECTION_THRESHOLD = 3

# Default tool execution timeout (seconds)
_DEFAULT_TOOL_TIMEOUT = 120

# Auto-compaction fires when estimated tokens exceed this fraction of
# the context window.
_AUTO_COMPACT_THRESHOLD = 0.80


# ----------------------------------------------------------------------- #
#  Auto-compaction helper
# ----------------------------------------------------------------------- #


async def _maybe_auto_compact(
    conversation: Conversation,
    context_window: int,
    compactor: Compactor,
) -> bool:
    """Run auto-compaction if estimated tokens exceed the threshold.

    Returns ``True`` if compaction was performed, ``False`` otherwise.
    The Compactor's circuit breaker is respected -- if it has tripped
    this is a no-op.
    """
    if compactor.circuit_breaker_tripped:
        return False

    estimated_before = _estimate_message_tokens(conversation.messages)
    limit = int(context_window * _AUTO_COMPACT_THRESHOLD)

    if estimated_before <= limit:
        return False

    logger.info(
        "Auto-compaction triggered: ~%d tokens (threshold %d, window %d)",
        estimated_before,
        limit,
        context_window,
    )

    original_count = len(conversation.messages)
    await compactor.compact(conversation, context_window)

    if len(conversation.messages) < original_count:
        estimated_after = _estimate_message_tokens(conversation.messages)
        saved = estimated_before - estimated_after
        logger.info(
            "Auto-compaction complete: ~%d tokens saved (%d -> %d)",
            saved,
            estimated_before,
            estimated_after,
        )
        return True

    return False


# ----------------------------------------------------------------------- #
#  Tool execution helper
# ----------------------------------------------------------------------- #


async def _execute_tool(
    tool: BaseTool,
    arguments: dict[str, Any],
    *,
    timeout: float = _DEFAULT_TOOL_TIMEOUT,
    permission_manager: PermissionManager | None = None,
    console: Any = None,
) -> ToolResult:
    """Run a single tool and return a ``ToolResult``.

    Catches all exceptions so the agent loop never crashes from tool
    errors — granular error types are preserved so the model can
    reason about what went wrong and adapt its approach.

    When *permission_manager* is provided, the 3-tier permission check
    runs before the safety check:
    - DENY  -> immediately blocked
    - ASK   -> user prompted via *console*
    - ALLOW -> auto-approved
    """
    # ---- Permission check (3-tier) -----------------------------------
    if permission_manager is not None:
        level = permission_manager.check(tool.name, arguments)
        if level == PermissionLevel.DENY:
            return ToolResult(
                tool_call_id="",
                content=f"Permission denied: {tool.name} is blocked by your permission config.",
                is_error=True,
            )
        if level == PermissionLevel.ASK:
            approved = await permission_manager.request_approval(
                tool.name,
                arguments,
                console,
            )
            if not approved:
                return ToolResult(
                    tool_call_id="",
                    content=f"User declined: {tool.name} call was not approved.",
                    is_error=True,
                )

    # Pre-execution safety check
    proceed, warning = await pre_tool_check(tool, arguments)
    if not proceed:
        return ToolResult(
            tool_call_id="",
            content=warning or "Blocked by safety check.",
            is_error=True,
        )

    try:
        result_text = await asyncio.wait_for(
            tool.execute(**arguments),
            timeout=timeout,
        )
        return ToolResult(tool_call_id="", content=result_text, is_error=False)
    except asyncio.TimeoutError:
        msg = f"Tool '{tool.name}' timed out after {timeout:.0f}s. The command may still be running."
        logger.warning(msg)
        return ToolResult(tool_call_id="", content=msg, is_error=True)
    except PermissionError as exc:
        msg = f"Permission denied: {exc}. Try running with appropriate permissions."
        logger.warning("Tool %s permission error: %s", tool.name, exc)
        return ToolResult(tool_call_id="", content=msg, is_error=True)
    except FileNotFoundError as exc:
        msg = f"File not found: {exc}"
        logger.warning("Tool %s file not found: %s", tool.name, exc)
        return ToolResult(tool_call_id="", content=msg, is_error=True)
    except Exception as exc:
        msg = f"Tool '{tool.name}' failed: {type(exc).__name__}: {exc}"
        logger.exception("Tool %s raised: %s", tool.name, exc)
        return ToolResult(tool_call_id="", content=msg, is_error=True)


# ----------------------------------------------------------------------- #
#  Parallel / sequential tool dispatch
# ----------------------------------------------------------------------- #


async def _execute_tool_calls(
    tool_calls: list[ToolCall],
    tool_map: dict[str, BaseTool],
) -> list[ToolResult]:
    """Execute a batch of tool calls, running independent ones in parallel.

    Strategy:
    1. Resolve each ``ToolCall`` to its ``BaseTool`` instance and handle
       immediate errors (malformed JSON, unknown tool) without scheduling.
    2. Partition resolved calls into *parallel* (``tool.sequential is False``)
       and *sequential* (``tool.sequential is True``) groups.
    3. Fire all parallel calls concurrently via ``asyncio.gather``.
    4. Run sequential calls one-at-a-time in order.
    5. Return results in the original tool-call order so that the
       conversation history is deterministic.

    Errors in one parallel call never affect others — each coroutine
    catches its own exceptions via ``_execute_tool``.
    """
    # --- Phase 1: Resolution ---
    # Pre-resolve every call to its BaseTool instance.  Calls that fail
    # resolution (unknown tool, malformed JSON) get an immediate error
    # result and skip execution entirely.
    resolved: list[tuple[ToolCall, BaseTool | None, ToolResult | None]] = []
    for tc in tool_calls:
        # Malformed JSON that couldn't be parsed during streaming —
        # the streaming layer stored the raw text in __parse_error__
        if "__parse_error__" in tc.arguments:
            raw_preview = tc.arguments["__parse_error__"]
            immediate = ToolResult(
                tool_call_id=tc.id,
                content=f"Invalid tool arguments (malformed JSON): {raw_preview}",
                is_error=True,
            )
            resolved.append((tc, None, immediate))
            continue

        # Look up the tool by name in the registry
        tool = tool_map.get(tc.name)
        if tool is None:
            immediate = ToolResult(
                tool_call_id=tc.id,
                content=f"[error] Unknown tool: {tc.name}",
                is_error=True,
            )
            resolved.append((tc, None, immediate))
        else:
            # Successful resolution — will be executed below
            resolved.append((tc, tool, None))

    # --- Phase 2: Index-keyed result map ---
    # We use an index-keyed dict so results can be returned in the
    # original tool-call order regardless of execution order.
    results: dict[int, ToolResult] = {}

    # Immediately fill in pre-resolved errors (no execution needed)
    for idx, (tc, tool, immediate) in enumerate(resolved):
        if immediate is not None:
            results[idx] = immediate

    # --- Phase 3: Partition into parallel vs sequential ---
    # Tools with sequential=True (bash, write, edit) must run one-at-a-time
    # to prevent race conditions on shared state (filesystem, cwd).
    parallel_indices: list[int] = []
    sequential_indices: list[int] = []
    for idx, (tc, tool, immediate) in enumerate(resolved):
        if immediate is not None:
            continue  # already resolved as error
        assert tool is not None
        if tool.sequential:
            sequential_indices.append(idx)
        else:
            parallel_indices.append(idx)

    # --- Phase 4: Execute parallel batch via asyncio.gather ---
    # Read-only tools (read, grep, glob) are safe to run concurrently.
    # Each coroutine catches its own exceptions via _execute_tool.
    if parallel_indices:

        async def _run_parallel(idx: int) -> tuple[int, ToolResult]:
            tc, tool, _ = resolved[idx]
            assert tool is not None
            result = await _execute_tool(tool, tc.arguments)
            # Stamp the tool_call_id onto the result for conversation tracking
            return idx, result.model_copy(update={"tool_call_id": tc.id})

        gathered = await asyncio.gather(
            *[_run_parallel(i) for i in parallel_indices],
            return_exceptions=False,  # _execute_tool already catches everything
        )
        for idx, result in gathered:
            results[idx] = result

    # --- Phase 5: Execute sequential calls one-at-a-time ---
    # Order is preserved so side effects (e.g., cd in bash) compose correctly.
    for idx in sequential_indices:
        tc, tool, _ = resolved[idx]
        assert tool is not None
        result = await _execute_tool(tool, tc.arguments)
        results[idx] = result.model_copy(update={"tool_call_id": tc.id})

    # --- Phase 6: Return results in original call order ---
    # Deterministic ordering ensures reproducible conversation history.
    return [results[i] for i in range(len(tool_calls))]


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
#  Malformed JSON recovery
# ----------------------------------------------------------------------- #


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    """Parse tool call arguments with fallback for common JSON issues.

    Models sometimes emit single-quoted JSON, trailing commas, or other
    minor deviations.  We attempt a repair before giving up.
    """
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass

    # Attempt common fixes: single quotes -> double quotes
    if isinstance(raw, str):
        fixed = raw.replace("'", '"')
        try:
            return json.loads(fixed)
        except (json.JSONDecodeError, TypeError):
            pass

    raise json.JSONDecodeError("Unfixable JSON", raw if isinstance(raw, str) else "", 0)


# ----------------------------------------------------------------------- #
#  Infinite-loop detection
# ----------------------------------------------------------------------- #


def _detect_tool_loop(
    recent_calls: list[ToolCall],
    threshold: int = _LOOP_DETECTION_THRESHOLD,
) -> bool:
    """Return True if the last *threshold* tool calls are identical."""
    if len(recent_calls) < threshold:
        return False
    tail = recent_calls[-threshold:]
    signatures = [(tc.name, json.dumps(tc.arguments, sort_keys=True)) for tc in tail]
    return len(set(signatures)) == 1


# ----------------------------------------------------------------------- #
#  Context overflow guard
# ----------------------------------------------------------------------- #


def _estimate_message_tokens(messages: list[Message]) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    total_chars = sum(len(m.content) for m in messages)
    # Also count tool results
    for m in messages:
        for tr in m.tool_results:
            total_chars += len(tr.content)
    return total_chars // 4


def _truncate_messages_to_fit(
    messages: list[Message],
    target_tokens: int,
) -> list[Message]:
    """Drop oldest non-system messages until estimated tokens fit *target_tokens*.

    Always preserves the first message (system prompt / initial user msg)
    and the most recent messages.
    """
    if not messages:
        return messages

    while _estimate_message_tokens(messages) > target_tokens and len(messages) > 2:
        # Remove the second message (preserve first, trim from the front)
        messages.pop(1)

    return messages


# ----------------------------------------------------------------------- #
#  Provider call with retry
# ----------------------------------------------------------------------- #


async def _call_provider_with_retry(
    provider: BaseProvider,
    messages: list[Message],
    *,
    tools: list[dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    max_retries: int = 3,
    thinking: bool = False,
    thinking_budget: int | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream from the provider with exponential backoff on transient errors.

    Handles 429 (rate limit), 5xx (server errors), and connection errors.
    Non-retryable errors (4xx except 429) are raised immediately.

    ``thinking`` / ``thinking_budget`` are forwarded verbatim to
    :meth:`BaseProvider.stream`; providers that don't support reasoning
    silently ignore the kwargs.
    """
    # Only forward the thinking kwargs when something is actually requested.
    # Older provider implementations (and the in-tree test doubles) don't
    # accept these kwargs yet; preserving the kwargs-free call path for
    # the default state keeps this change fully additive.
    extra: dict[str, Any] = {}
    if thinking or thinking_budget is not None:
        extra["thinking"] = thinking
        extra["thinking_budget"] = thinking_budget

    for attempt in range(max_retries):
        try:
            async for event in provider.stream(
                messages,
                tools=tools,
                system_prompt=system_prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                **extra,
            ):
                yield event
            return  # Stream completed successfully
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                wait = (2**attempt) + random.random()
                yield StreamEvent(
                    type="error",
                    error=f"Rate limited (429). Retrying in {wait:.0f}s...",
                )
                await asyncio.sleep(wait)
            elif exc.response.status_code >= 500:
                wait = 2**attempt
                yield StreamEvent(
                    type="error",
                    error=f"Server error ({exc.response.status_code}). Retry {attempt + 1}/{max_retries}...",
                )
                await asyncio.sleep(wait)
            else:
                # 4xx (except 429) — don't retry
                raise
        except (httpx.ConnectError, httpx.ReadTimeout) as exc:
            wait = 2**attempt
            yield StreamEvent(
                type="error",
                error=f"Connection error: {exc}. Retry {attempt + 1}/{max_retries}...",
            )
            await asyncio.sleep(wait)

    # All retries exhausted
    yield StreamEvent(
        type="error",
        error="Provider unreachable after retries. Check your network and API key.",
    )


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
    context_window: int | None = None,
    provider_max_retries: int = 3,
    thinking: bool = False,
    thinking_budget: int | None = None,
    compactor: Compactor | None = None,
) -> AsyncIterator[StreamEvent]:
    """Run the tool-use agent loop (streaming variant).

    Yields ``StreamEvent`` objects. The caller can process text deltas
    and tool-call events in real time.

    The loop terminates when the model produces a response with no
    tool calls, or when *max_iterations* is reached.

    When *compactor* is provided alongside *context_window*, the loop
    automatically triggers summarization-based compaction after each
    tool-execution turn if estimated token usage exceeds 80%% of the
    context window.

    Error recovery:
    - Tool execution failures are captured and sent to the model
    - Provider transient errors (429, 5xx) trigger retry with backoff
    - Malformed tool-call JSON is repaired when possible
    - Repeated identical tool calls are detected and broken
    - Empty model responses are retried with a nudge message
    - Context overflow triggers automatic message truncation
    """
    tool_defs = _build_tool_defs(tools)
    tool_map = {t.name: t for t in tools}

    # Track recent tool calls for loop detection
    recent_tool_calls: list[ToolCall] = []
    # Track consecutive empty responses for backoff
    consecutive_empty = 0
    _MAX_CONSECUTIVE_EMPTY = 3

    for iteration in range(max_iterations):
        # ---- Auto-compaction before provider call --------------------
        if compactor is not None and context_window is not None:
            compacted = await _maybe_auto_compact(
                conversation,
                context_window,
                compactor,
            )
            if compacted:
                yield StreamEvent(
                    type="text",
                    text="[Context compacted -- older messages summarized to free space]\n",
                )

        # ---- Context overflow check --------------------------------
        if context_window is not None:
            estimated = _estimate_message_tokens(conversation.messages)
            if estimated > int(context_window * 0.95):
                target = int(context_window * 0.8)
                conversation.messages = _truncate_messages_to_fit(
                    conversation.messages,
                    target,
                )
                yield StreamEvent(
                    type="text",
                    text="[Context trimmed -- older messages removed to fit model window]\n",
                )

        # ---- Collect events from one provider turn --------------------
        pending_tool_calls: list[ToolCall] = []
        assistant_text_parts: list[str] = []
        # Accumulate argument fragments keyed by tool call id
        arg_buffers: dict[str, str] = {}
        done = False
        provider_error = False

        async for event in _call_provider_with_retry(
            provider,
            conversation.messages,
            tools=tool_defs,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            max_retries=provider_max_retries,
            thinking=thinking,
            thinking_budget=thinking_budget,
        ):
            yield event

            if event.type == "text" and event.text:
                assistant_text_parts.append(event.text)

            elif event.type == "tool_call_start" and event.tool_call:
                tc = event.tool_call
                arg_buffers[tc.id] = json.dumps(tc.arguments) if tc.arguments is not None else ""

            elif event.type == "tool_call_delta" and event.tool_call:
                tc = event.tool_call
                if tc.id in arg_buffers:
                    delta = event.text or ""
                    arg_buffers[tc.id] += delta

            elif event.type == "tool_call_end" and event.tool_call:
                tc = event.tool_call
                # Merge any accumulated argument fragments
                if tc.id in arg_buffers and not tc.arguments and arg_buffers[tc.id]:
                    raw = arg_buffers[tc.id]
                    try:
                        parsed = _parse_tool_arguments(raw)
                        tc = tc.model_copy(update={"arguments": parsed})
                    except json.JSONDecodeError:
                        # Send malformed JSON error back to model
                        tc = tc.model_copy(update={"arguments": {"__parse_error__": raw[:200]}})
                pending_tool_calls.append(tc)

            elif event.type == "done":
                done = True

            elif event.type == "error":
                provider_error = True

        # If provider errored out after retries, stop
        if provider_error and not assistant_text_parts and not pending_tool_calls:
            return

        # ---- Empty/null response handling ----------------------------
        assistant_text = "".join(assistant_text_parts)
        if not assistant_text and not pending_tool_calls:
            consecutive_empty += 1
            if consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                yield StreamEvent(
                    type="error",
                    error="Model returned empty responses repeatedly. Stopping.",
                )
                return
            yield StreamEvent(
                type="text",
                text="[Model returned empty response. Retrying...]\n",
            )
            conversation.messages.append(
                Message(
                    role="user",
                    content="Your response was empty. Please try again.",
                )
            )
            continue  # Re-prompt

        # Got a real response — reset empty counter
        consecutive_empty = 0

        # ---- If no tool calls, conversation is complete ----------------
        if not pending_tool_calls:
            if assistant_text:
                conversation.messages.append(Message(role="assistant", content=assistant_text))
            return

        # ---- Infinite loop detection ---------------------------------
        recent_tool_calls.extend(pending_tool_calls)
        if _detect_tool_loop(recent_tool_calls):
            yield StreamEvent(
                type="text",
                text="[Detected repeated tool call loop. Breaking.]\n",
            )
            conversation.messages.append(
                Message(
                    role="assistant",
                    content=assistant_text,
                    tool_calls=pending_tool_calls,
                )
            )
            conversation.messages.append(
                Message(
                    role="user",
                    content=(
                        "You appear to be in a loop calling the same tool "
                        "repeatedly with identical arguments. Please try a "
                        "different approach or ask for help."
                    ),
                )
            )
            # Clear recent calls and continue — model will see the nudge
            recent_tool_calls.clear()
            continue

        # ---- Execute tool calls and build messages --------------------
        # Append assistant message with tool calls
        conversation.messages.append(
            Message(
                role="assistant",
                content=assistant_text,
                tool_calls=pending_tool_calls,
            )
        )

        # Execute tool calls — parallel when safe, sequential otherwise
        tool_results = await _execute_tool_calls(pending_tool_calls, tool_map)

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
    context_window: int | None = None,
    thinking: bool = False,
    thinking_budget: int | None = None,
    compactor: Compactor | None = None,
) -> Message:
    """Run the tool-use agent loop (non-streaming variant).

    Returns the final assistant ``Message`` after all tool calls have
    been resolved.  Includes the same error recovery as the streaming
    variant: granular tool errors, malformed JSON recovery, loop
    detection, empty-response retry, and context overflow truncation.
    """
    tool_defs = _build_tool_defs(tools)
    tool_map = {t.name: t for t in tools}

    recent_tool_calls: list[ToolCall] = []
    consecutive_empty = 0
    _MAX_CONSECUTIVE_EMPTY = 3

    for iteration in range(max_iterations):
        # ---- Auto-compaction before provider call --------------------
        if compactor is not None and context_window is not None:
            await _maybe_auto_compact(
                conversation,
                context_window,
                compactor,
            )

        # ---- Context overflow check --------------------------------
        if context_window is not None:
            estimated = _estimate_message_tokens(conversation.messages)
            if estimated > int(context_window * 0.95):
                target = int(context_window * 0.8)
                conversation.messages = _truncate_messages_to_fit(
                    conversation.messages,
                    target,
                )

        # ---- Call provider with retry on transient errors -----------
        response: Message | None = None
        last_exc: Exception | None = None
        # Only pass thinking kwargs when actually requested so legacy
        # provider doubles that predate the signature still work.
        extra: dict[str, Any] = {}
        if thinking or thinking_budget is not None:
            extra["thinking"] = thinking
            extra["thinking_budget"] = thinking_budget
        for attempt in range(3):
            try:
                response = await provider.complete(
                    conversation.messages,
                    tools=tool_defs,
                    system_prompt=system_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **extra,
                )
                break
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code == 429 or exc.response.status_code >= 500:
                    await asyncio.sleep(2**attempt + random.random())
                else:
                    raise
            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                last_exc = exc
                await asyncio.sleep(2**attempt)

        if response is None:
            return Message(
                role="assistant",
                content=f"[error] Provider unreachable after retries: {last_exc}",
            )

        # ---- Empty response handling --------------------------------
        if not response.content and not response.tool_calls:
            consecutive_empty += 1
            if consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                return Message(
                    role="assistant",
                    content="[error] Model returned empty responses repeatedly.",
                )
            conversation.messages.append(
                Message(
                    role="user",
                    content="Your response was empty. Please try again.",
                )
            )
            continue

        consecutive_empty = 0

        # No tool calls — we're done
        if not response.tool_calls:
            conversation.messages.append(response)
            return response

        # ---- Infinite loop detection --------------------------------
        recent_tool_calls.extend(response.tool_calls)
        if _detect_tool_loop(recent_tool_calls):
            conversation.messages.append(response)
            conversation.messages.append(
                Message(
                    role="user",
                    content=(
                        "You appear to be in a loop calling the same tool "
                        "repeatedly with identical arguments. Please try a "
                        "different approach or ask for help."
                    ),
                )
            )
            recent_tool_calls.clear()
            continue

        # Append assistant message with tool calls
        conversation.messages.append(response)

        # Execute tool calls — parallel when safe, sequential otherwise
        tool_results = await _execute_tool_calls(response.tool_calls, tool_map)

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
