"""Nellie -- Karna's internal AI agent harness. CLI binary: nellie.

PRIVACY: Nellie sends NO telemetry, NO analytics, NO usage data anywhere.
All data stays on the local machine. The only network requests are:
1. Provider API calls (to the user-configured LLM provider)
2. Web search/fetch (only when the user explicitly uses those tools)
3. MCP connections (only to user-configured MCP servers)
"""

__version__ = "0.1.1"
