"""Hook system for Karna lifecycle events.

Provides a dispatcher that fires user-defined callbacks at well-known
points (pre/post tool use, session start/end, errors).  Hooks can
observe, modify arguments, or block actions.

Exports
-------
HookDispatcher : class
    Central registry and executor for hooks.
HookType : enum
    Lifecycle event types (PRE_TOOL_USE, POST_TOOL_USE, etc.).
HookResult : dataclass
    Aggregated result from dispatching hooks.

Called by the agent loop and REPL for extensible behavior.
"""
