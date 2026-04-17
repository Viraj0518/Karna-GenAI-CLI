"""E2E hook lifecycle: pre/post dispatch, fail-open post, fail-closed pre.

Uses the real ``HookDispatcher``. Note that the production
``HookResult`` exposes ``proceed=False`` (not ``allow=False``) as the
"block" signal; this test targets the actual contract.

The fail-closed invariant for ``pre_tool_use`` hooks that raise is
currently a known gap in ``dispatcher.py`` (the dispatcher logs the
exception and continues -- effectively fail-open). The third test
below is marked ``xfail`` so CI surfaces the missing behavior without
blocking the suite while another agent fixes the dispatcher.
"""

from __future__ import annotations

import pytest

from karna.hooks.dispatcher import HookDispatcher, HookResult, HookType


@pytest.mark.asyncio
async def test_pre_tool_use_block_surfaces_message() -> None:
    """A pre-tool-use hook returning proceed=False must block the call
    and propagate the message back to the caller."""
    dispatcher = HookDispatcher()

    def blocking_hook(**_: object) -> HookResult:
        return HookResult(proceed=False, message="blocked by policy")

    dispatcher.register(HookType.PRE_TOOL_USE, blocking_hook)

    result = await dispatcher.dispatch(
        HookType.PRE_TOOL_USE,
        tool="bash",
        args={"cmd": "rm -rf /"},
    )

    assert result.proceed is False
    assert result.message is not None
    assert "blocked by policy" in result.message


@pytest.mark.asyncio
async def test_post_tool_use_raising_hook_fails_open() -> None:
    """A post-tool-use hook that raises must not break the pipeline.

    Tool result should still flow through -- the dispatcher catches
    and logs the exception, returning a non-blocking result. This is
    the intentional "fail-open post" semantic (observability hooks
    shouldn't destroy side effects that already happened).
    """
    dispatcher = HookDispatcher()

    def broken_hook(**_: object) -> HookResult:
        raise RuntimeError("hook exploded")

    def good_hook(**_: object) -> HookResult:
        return HookResult(proceed=True, message="logged ok")

    dispatcher.register(HookType.POST_TOOL_USE, broken_hook)
    dispatcher.register(HookType.POST_TOOL_USE, good_hook)

    result = await dispatcher.dispatch(
        HookType.POST_TOOL_USE,
        tool="read",
        result="file body",
    )

    # Pipeline did not abort -- the good hook still ran and its
    # message survived.
    assert result.proceed is True
    assert result.message == "logged ok"


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason=(
        "Fail-closed semantic for pre-tool-use hooks that raise is not "
        "implemented in dispatcher.py yet. Another agent owns that fix; "
        "this test documents the expected behaviour."
    ),
    strict=False,
)
async def test_pre_tool_use_raising_hook_fails_closed() -> None:
    """When a pre-tool-use hook raises, the call MUST be blocked.

    Current behaviour: exceptions are swallowed (fail-open). Expected:
    the dispatcher should treat raised exceptions from a ``pre_*`` hook
    as an implicit block.
    """
    dispatcher = HookDispatcher()

    def explodes(**_: object) -> HookResult:
        raise RuntimeError("pre-hook exploded")

    dispatcher.register(HookType.PRE_TOOL_USE, explodes)

    result = await dispatcher.dispatch(
        HookType.PRE_TOOL_USE,
        tool="bash",
        args={"cmd": "echo hi"},
    )

    # Expected: proceed flips to False when a pre-hook raises.
    assert result.proceed is False


@pytest.mark.asyncio
async def test_modified_args_flow_to_next_hook() -> None:
    """If a hook returns modified_args, downstream hooks must see them."""
    dispatcher = HookDispatcher()

    seen_args: list[dict[str, object]] = []

    def rewriter(**kwargs: object) -> HookResult:
        return HookResult(modified_args={"cmd": "echo safe"})

    def observer(**kwargs: object) -> HookResult:
        seen_args.append(dict(kwargs.get("args", {})))  # type: ignore[arg-type]
        return HookResult()

    dispatcher.register(HookType.PRE_TOOL_USE, rewriter)
    dispatcher.register(HookType.PRE_TOOL_USE, observer)

    result = await dispatcher.dispatch(
        HookType.PRE_TOOL_USE,
        tool="bash",
        args={"cmd": "echo original"},
    )

    assert result.proceed is True
    assert seen_args and seen_args[0]["cmd"] == "echo safe"
    assert result.modified_args == {"cmd": "echo safe"}
