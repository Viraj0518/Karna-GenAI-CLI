"""Model-aware token counting with graceful fallback.

Exports
-------
count_tokens : function
    Count tokens for a single string.
TokenCounter : class
    Model-aware counter with message-level helpers.
"""

from karna.tokens.counter import TokenCounter, count_tokens

__all__ = ["TokenCounter", "count_tokens"]
