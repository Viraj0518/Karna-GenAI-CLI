"""Context compaction subsystem for Karna.

Auto-summarises older conversation messages when the context window
fills up, preserving the system prompt and most recent messages.
Uses a circuit breaker to stop retrying after repeated failures.

Exports
-------
Compactor : class
    Threshold-based auto-compaction with provider-driven summarisation.

Called by the agent loop when estimated tokens approach the context limit.
"""
