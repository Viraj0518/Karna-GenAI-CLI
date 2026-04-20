#!/usr/bin/env python3
"""Visual TUI test harness — renders 20 scenarios as SVG screenshots.

Exercises every TUI rendering feature:
- Banner, text streaming, thinking blocks
- Tool call rendering (all tool types)
- Inline diffs, error panels
- Slash commands, permissions prompts
- Spinner, completion, cost display

Each scenario is rendered using Rich Console(record=True) and exported
to ~/.karna/tui-screenshots/ as SVG files for visual inspection.
"""

from __future__ import annotations

import os
import sys
from io import StringIO
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console

from karna.config import KarnaConfig
from karna.models import Conversation, Message, ToolCall, ToolResult
from karna.tui.banner import print_banner
from karna.tui.output import EventKind, OutputRenderer, StreamEvent

SCREENSHOT_DIR = Path.home() / ".karna" / "tui-screenshots"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def make_console() -> Console:
    """Create a recording console."""
    return Console(record=True, width=100, force_terminal=True)


def save(console: Console, name: str) -> Path:
    """Export console recording as SVG."""
    path = SCREENSHOT_DIR / f"{name}.svg"
    console.save_svg(str(path), title=name)
    print(f"  saved: {path}")
    return path


# ── Scenarios ──────────────────────────────────────────────────────────────


def scenario_01_banner():
    """Banner rendering on startup."""
    console = make_console()
    config = KarnaConfig(active_provider="openrouter", active_model="qwen/qwen3-coder")
    print_banner(console, config, tool_names=[
        "bash", "read", "write", "edit", "grep", "glob", "git",
        "web_search", "web_fetch", "clipboard", "image", "notebook",
        "monitor", "task", "mcp", "voice", "browser",
    ])
    save(console, "01_banner")


def scenario_02_simple_text():
    """Simple text response streaming."""
    console = make_console()
    r = OutputRenderer(console)
    r.show_spinner()
    for word in "Hello! I can help you with that. Let me take a look at your code.".split():
        r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data=word + " "))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "02_simple_text")


def scenario_03_thinking_stream():
    """Thinking/reasoning block that streams live."""
    console = make_console()
    r = OutputRenderer(console)
    r.show_spinner()

    # Thinking deltas
    thinking_text = (
        "Let me analyze this problem step by step. "
        "First, I need to understand the current codebase structure. "
        "The user is asking about a bug in the authentication module. "
        "I should check the middleware configuration and the JWT validation logic. "
        "Looking at the error message, it seems like the token expiry check is failing "
        "because the clock skew tolerance is set too low."
    )
    for chunk in [thinking_text[i:i+20] for i in range(0, len(thinking_text), 20)]:
        r.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data=chunk))

    # Then text response
    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="The issue is in your JWT middleware. "))
    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="The clock skew tolerance is set to 0 seconds, "))
    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="which causes intermittent auth failures."))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "03_thinking_stream")


def scenario_04_tool_bash():
    """Bash tool call rendering."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "bash", "id": "tc1"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"command": "git status --porcelain"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": " M src/auth.py\n M tests/test_auth.py\n?? config/new.toml\n",
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="You have 2 modified files and 1 untracked file."))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "04_tool_bash")


def scenario_05_tool_read():
    """Read file tool call."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "read", "id": "tc2"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"file_path": "/home/viraj/karna/karna/config.py", "limit": 20}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": '1\t"""Karna configuration — TOML-backed."""\n2\t\n3\tfrom __future__ import annotations\n4\t\n5\timport os\n',
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "05_tool_read")


def scenario_06_tool_edit():
    """Edit/patch tool with diff preview."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "edit", "id": "tc3"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"file_path": "src/auth.py", "old_string": "timeout = 0", "new_string": "timeout = 30"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "File edited successfully.",
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="Fixed the timeout value from 0 to 30 seconds."))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "06_tool_edit")


def scenario_07_tool_grep():
    """Grep/search tool call."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "grep", "id": "tc4"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"pattern": "TODO|FIXME|HACK", "path": "src/"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "src/auth.py:42: # TODO: add rate limiting\nsrc/db.py:15: # FIXME: connection pool leak\nsrc/api.py:88: # HACK: temporary workaround\n",
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "07_tool_grep")


def scenario_08_tool_web_search():
    """Web search tool call."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "web_search", "id": "tc5"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"query": "python asyncio best practices 2026"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "1. Real Python - Async IO in Python\n2. Python docs - asyncio\n3. Stack Overflow - asyncio patterns\n",
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "08_tool_web_search")


def scenario_09_tool_error():
    """Tool call that fails."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "bash", "id": "tc6"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"command": "cat /nonexistent/file.py"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "cat: /nonexistent/file.py: No such file or directory",
        "is_error": True,
    }))
    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="The file doesn't exist. Let me check the correct path."))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "09_tool_error")


def scenario_10_multiple_tools():
    """Multiple tool calls in sequence."""
    console = make_console()
    r = OutputRenderer(console)

    # First tool: glob
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "glob", "id": "tc7"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"pattern": "**/*.py"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "src/main.py\nsrc/auth.py\nsrc/db.py\ntests/test_main.py\n",
        "is_error": False,
    }))

    # Second tool: read
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "read", "id": "tc8"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"file_path": "src/main.py"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "def main():\n    print('hello world')\n",
        "is_error": False,
    }))

    # Third tool: edit
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "edit", "id": "tc9"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"file_path": "src/main.py", "old_string": "hello world", "new_string": "hello nellie"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "File edited successfully.",
        "is_error": False,
    }))

    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="Done! I found your Python files, read main.py, and updated the greeting."))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "10_multiple_tools")


def scenario_11_markdown_response():
    """Response with markdown formatting (code blocks, lists, headers)."""
    console = make_console()
    r = OutputRenderer(console)
    md = (
        "## Solution\n\n"
        "Here's how to fix the authentication issue:\n\n"
        "1. Update the JWT config:\n\n"
        "```python\n"
        "JWT_CONFIG = {\n"
        '    "algorithm": "HS256",\n'
        '    "expiry": 3600,\n'
        '    "clock_skew": 30,  # 30 second tolerance\n'
        "}\n"
        "```\n\n"
        "2. Restart the server\n"
        "3. Run the test suite to verify\n\n"
        "**Note:** The clock skew should be at least 30 seconds for distributed systems.\n"
    )
    for chunk in [md[i:i+30] for i in range(0, len(md), 30)]:
        r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data=chunk))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "11_markdown_response")


def scenario_12_git_tool():
    """Git operations tool call."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "git", "id": "tc10"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"subcommand": "log", "args": "--oneline -5"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "fb54cac feat: add banner\n7890f0e fix: async handlers\ncd89e10 docs: update README\n188b6e6 feat: skills wiring\n01291c8 fix: ruff lint\n",
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "12_git_tool")


def scenario_13_write_tool():
    """Write new file tool call."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "write", "id": "tc11"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"file_path": "src/new_module.py", "content": "def hello():\\n    return \\"world\\"\\n"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "File written successfully.",
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "13_write_tool")


def scenario_14_error_response():
    """Error event rendering."""
    console = make_console()
    r = OutputRenderer(console)
    r.show_spinner()
    r.handle(StreamEvent(kind=EventKind.ERROR, data="Provider error: 429 Rate Limited. Retrying in 2 seconds..."))
    r.finish()
    save(console, "14_error_response")


def scenario_15_long_thinking():
    """Extended thinking block (>200 chars) with summary."""
    console = make_console()
    r = OutputRenderer(console)
    r.show_spinner()

    long_thinking = (
        "This is a complex problem that requires careful analysis. "
        "I need to consider multiple factors including the database schema, "
        "the API contract, the frontend state management, and the deployment "
        "pipeline. Let me trace through the request lifecycle: "
        "1) User submits form 2) Frontend validates 3) API receives POST "
        "4) Middleware checks auth 5) Controller processes 6) Model validates "
        "7) Database writes 8) Response returns. The bug is likely in step 4 "
        "where the middleware is checking an expired token cache."
    )
    for chunk in [long_thinking[i:i+15] for i in range(0, len(long_thinking), 15)]:
        r.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data=chunk))

    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="Found it — the token cache TTL is misconfigured."))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "15_long_thinking")


def scenario_16_usage_cost():
    """Usage/cost event rendering."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="Here's your answer."))
    r.handle(StreamEvent(kind=EventKind.USAGE, data={
        "prompt_tokens": 1250,
        "completion_tokens": 340,
        "total_usd": 0.0042,
    }))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "16_usage_cost")


def scenario_17_monitor_tool():
    """Monitor/background task tool call."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "monitor", "id": "tc12"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"command": "tail -f /var/log/app.log | grep ERROR", "description": "Watch for errors"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "Monitor started (id: mon_abc123). Events will be delivered as notifications.",
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="I've started monitoring your error log. I'll notify you when errors appear."))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "17_monitor_tool")


def scenario_18_subagent_tool():
    """Subagent/task spawning tool call."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "task", "id": "tc13"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"action": "create", "description": "Fix failing tests", "prompt": "Run pytest and fix any failures", "run_in_background": true}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": '{"agent_id": "a1b2c3d4", "status": "started", "description": "Fix failing tests"}',
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data="I've spawned a background agent to fix the tests. You'll be notified when it completes."))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "18_subagent_tool")


def scenario_19_mcp_tool():
    """MCP external tool call."""
    console = make_console()
    r = OutputRenderer(console)
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "mcp__puppeteer__navigate", "id": "tc14"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"url": "https://sam.gov/search"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "Navigated to https://sam.gov/search. Page title: SAM.gov | Search",
        "is_error": False,
    }))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "19_mcp_tool")


def scenario_20_full_conversation():
    """Full multi-turn conversation with thinking + tools + text."""
    console = make_console()
    config = KarnaConfig(active_provider="openrouter", active_model="qwen/qwen3-coder")

    # Banner
    print_banner(console, config, tool_names=["bash", "read", "write", "edit", "grep", "glob", "git"])

    # User prompt
    console.print("\n[bold #E6E8EC]> [/]Fix the authentication bug in src/auth.py\n")

    # Assistant response
    r = OutputRenderer(console)
    r.show_spinner()

    # Thinking
    thinking = "The user wants me to fix an auth bug. Let me read the file first and understand the issue."
    for chunk in [thinking[i:i+20] for i in range(0, len(thinking), 20)]:
        r.handle(StreamEvent(kind=EventKind.THINKING_DELTA, data=chunk))

    # Tool: read
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "read", "id": "tc20a"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"file_path": "src/auth.py"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "1\tdef verify_token(token):\n2\t    decoded = jwt.decode(token, SECRET, algorithms=['HS256'])\n3\t    if decoded['exp'] < time.time():\n4\t        raise AuthError('Token expired')\n5\t    return decoded\n",
        "is_error": False,
    }))

    # Tool: edit
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_START, data={"name": "edit", "id": "tc20b"}))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_ARGS_DELTA, data='{"file_path": "src/auth.py", "old_string": "if decoded[\'exp\'] < time.time():", "new_string": "if decoded[\'exp\'] < time.time() - 30:  # 30s clock skew tolerance"}'))
    r.handle(StreamEvent(kind=EventKind.TOOL_CALL_END))
    r.handle(StreamEvent(kind=EventKind.TOOL_RESULT, data={
        "content": "File edited successfully.",
        "is_error": False,
    }))

    # Final text
    response = (
        "Fixed the authentication bug. The issue was that the token expiry check "
        "had no clock skew tolerance, causing intermittent failures in distributed "
        "systems. Added a 30-second tolerance window."
    )
    for chunk in [response[i:i+25] for i in range(0, len(response), 25)]:
        r.handle(StreamEvent(kind=EventKind.TEXT_DELTA, data=chunk))

    r.handle(StreamEvent(kind=EventKind.USAGE, data={
        "prompt_tokens": 2100,
        "completion_tokens": 580,
        "total_usd": 0.0089,
    }))
    r.handle(StreamEvent(kind=EventKind.DONE))
    r.finish()
    save(console, "20_full_conversation")


# ── Runner ─────────────────────────────────────────────────────────────────

SCENARIOS = [
    scenario_01_banner,
    scenario_02_simple_text,
    scenario_03_thinking_stream,
    scenario_04_tool_bash,
    scenario_05_tool_read,
    scenario_06_tool_edit,
    scenario_07_tool_grep,
    scenario_08_tool_web_search,
    scenario_09_tool_error,
    scenario_10_multiple_tools,
    scenario_11_markdown_response,
    scenario_12_git_tool,
    scenario_13_write_tool,
    scenario_14_error_response,
    scenario_15_long_thinking,
    scenario_16_usage_cost,
    scenario_17_monitor_tool,
    scenario_18_subagent_tool,
    scenario_19_mcp_tool,
    scenario_20_full_conversation,
]

if __name__ == "__main__":
    print(f"Running {len(SCENARIOS)} TUI visual tests...")
    print(f"Output: {SCREENSHOT_DIR}/\n")

    for i, scenario in enumerate(SCENARIOS, 1):
        name = scenario.__doc__ or scenario.__name__
        print(f"[{i:2d}/20] {name.strip()}")
        try:
            scenario()
        except Exception as e:
            print(f"  ERROR: {e}")

    print(f"\nDone! {len(SCENARIOS)} SVGs saved to {SCREENSHOT_DIR}/")
    print("Open them in a browser to inspect.")
