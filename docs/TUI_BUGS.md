# TUI Bugs Found During Dogfooding

> Date: 2026-04-20 | Method: PTY-simulated Nellie session building a real project

## Critical

### BUG-1: Spinner + prompt_toolkit prompt fight for cursor
**Severity:** Critical — makes the TUI unusable during tool calls
**What happens:** Rich's Spinner and prompt_toolkit's `patch_stdout` both write to the terminal. Every spinner frame triggers a prompt redraw (`❯ Ask anything...`), causing visual noise.
**Root cause:** `patch_stdout` intercepts stdout writes and re-renders the prompt after each one. Rich's `Live` spinner writes \r-based frame updates which trigger this.
**Fix:** Replace Rich's Live spinner with a raw \r-based spinner (like hermes KawaiiSpinner) that works with patch_stdout, OR disable the prompt during agent execution and re-enable after.

### BUG-2: Tool result shows raw dict
**Severity:** High — displays `{}` or `{'command': 'ls ...'}` as text
**What happens:** Tool results render the Python dict repr instead of formatted content
**Root cause:** `_on_tool_result` receives a dict but the content extraction isn't handling all cases
**Fix:** Better content extraction from tool result dicts

### BUG-3: Tool name shows "tool" instead of actual name
**Severity:** Medium — `⚒ tool ✓` instead of `⚒ bash ✓`
**What happens:** After tool completion, the status line says "tool" generically
**Root cause:** `self._tool` is being cleared or not tracked correctly through the tool lifecycle

## Medium

### BUG-4: Write tool success shows no file path
**Severity:** Medium — `⚒ write ✓` with no indication of what was written
**What happens:** The write tool success handler tries to extract file_path from args but fails
**Root cause:** `self._tool.args_buffer` may not be populated by the time result arrives

### BUG-5: ANSI escape codes partially broken
**Severity:** Medium — `?[` appears instead of proper escapes in some places
**What happens:** Mixed Rich + print() output causes encoding issues
**Root cause:** Rich uses its own ANSI handling, but tool results printed via print() don't go through Rich

### BUG-6: openrouter/openrouter/auto doubled model path
**Severity:** Low — cosmetic
**What happens:** Banner shows `openrouter/openrouter/auto` instead of `openrouter/auto`
**Root cause:** Model path construction in banner.py or config

## Low

### BUG-7: Tool call args shown as empty `{}` for bash
**Severity:** Low — the args panel shows `{}` when it should show the command
**What happens:** Args buffer not populated before result renders

### BUG-8: Thinking spinner redundant with patch_stdout
**Severity:** Low — spinner text `thinking...` redisplays unnecessarily
