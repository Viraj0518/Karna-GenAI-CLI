"""Task-specific MCP stdio servers.

Each module here exposes a focused tool surface to clients that speak
the Model Context Protocol — distinct from ``karna.mcp_server`` (singular),
which wraps the full Nellie agent loop as a single ``nellie_agent`` tool.

Current inhabitants:
- ``computer_controller_server`` — screen capture + keyboard/mouse input
"""
