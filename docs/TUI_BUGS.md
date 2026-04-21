# TUI Bugs Found During Dogfooding

> Date: 2026-04-20 | Method: PTY-simulated Nellie session building a real project
>
> Status updated 2026-04-20 [alpha] — walked each bug against the TUI commits on
> this branch and on `dev`. Resolved items are marked inline with the fixing
> SHA; anything still open keeps its original severity.

## Critical

### BUG-1: Spinner + prompt_toolkit prompt fight for cursor
**Severity:** Critical — makes the TUI unusable during tool calls
**Status:** FIXED in [9ffb432](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/9ffb432) (before this branch; carried in on `dev`).
**What happens:** Rich's Spinner and prompt_toolkit's `patch_stdout` both write to the terminal. Every spinner frame triggers a prompt redraw (`❯ Ask anything...`), causing visual noise.
**Root cause:** `patch_stdout` intercepts stdout writes and re-renders the prompt after each one. Rich's `Live` spinner writes \r-based frame updates which trigger this.
**Fix applied:** Replaced `patch_stdout` + `PromptSession.prompt_async` with a full-screen `prompt_toolkit.Application` using an `HSplit` layout — output pane (top), status bar (middle), input pane (bottom). All Rich output is captured via `RedirectedConsole` -> [`TUIOutputWriter`](../karna/tui/repl.py) -> ANSI `FormattedTextControl`. Spinner is now a static "... thinking" indicator. See [karna/tui/repl.py](../karna/tui/repl.py).

### BUG-2: Tool result shows raw dict
**Severity:** High — displays `{}` or `{'command': 'ls ...'}` as text
**Status:** FIXED in [23b9135](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/23b9135).
**What happens:** Tool results render the Python dict repr instead of formatted content
**Root cause:** `_on_tool_result` receives a dict but the content extraction isn't handling all cases
**Fix applied:** [`_on_tool_result`](../karna/tui/output.py) now handles `str`, `dict` (including nested dict content), and `None`/other types without crashing or showing raw repr.

### BUG-3: Tool name shows "tool" instead of actual name
**Severity:** Medium — `⚒ tool ✓` instead of `⚒ bash ✓`
**Status:** FIXED as part of the [9ffb432](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/9ffb432) output-renderer rewrite — the `_ToolState` in [karna/tui/output.py](../karna/tui/output.py) now holds `self.name` through the full lifecycle (`TOOL_CALL_START` → `TOOL_CALL_END` → `TOOL_RESULT`) rather than clearing it.

## Medium

### BUG-4: Write tool success shows no file path
**Severity:** Medium — `⚒ write ✓` with no indication of what was written
**Status:** FIXED in [23b9135](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/23b9135) (part of the "robust tool result parsing" fix). Args are now accumulated via `TOOL_CALL_ARGS_DELTA` into `_ToolState.args_buffer` ([karna/tui/output.py](../karna/tui/output.py)) and `_extract_tool_context()` pulls the file path out for display.

### BUG-5: ANSI escape codes partially broken
**Severity:** Medium — `?[` appears instead of proper escapes in some places
**Status:** FIXED in [9ffb432](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/9ffb432). Rich + raw `print()` mixing is gone — all renderable output now goes through `RedirectedConsole` → `TUIOutputWriter`, which feeds a single ANSI `FormattedTextControl`.

### BUG-6: openrouter/openrouter/auto doubled model path
**Severity:** Low — cosmetic
**Status:** FIXED in [7421103](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/7421103). Status-bar formatting now strips the `provider/` prefix when the model string already carries it, so `openrouter/openrouter/auto` renders as `openrouter/auto`.

## Low

### BUG-7: Tool call args shown as empty `{}` for bash
**Severity:** Low — the args panel shows `{}` when it should show the command
**Status:** FIXED — same fix as BUG-4. [`_on_tool_call_args_delta`](../karna/tui/output.py) now appends to `args_buffer` before the header is re-rendered.

### BUG-8: Thinking spinner redundant with patch_stdout
**Severity:** Low — spinner text `thinking...` redisplays unnecessarily
**Status:** FIXED — resolved alongside BUG-1. Once the Rich `Live` spinner was dropped in [9ffb432](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/9ffb432) the redisplay churn went away. The thinking indicator is now a static line in the status bar, refreshed by the agent loop rather than the renderer.

---

## Follow-up issues found on this branch

Tracking these separately from the original dogfooding list since they were
flagged by later audits (`research/karna/TUI_AUDIT_20260420.md` etc.) rather
than the initial PTY session.

### TUI-P0: Scrollbar thumb didn't track, keyboard/mouse scroll were no-ops
**Status:** FIXED in [34b2e8d](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/34b2e8d).
The output window's `FormattedTextControl` had `focusable=False`, so prompt_toolkit never tracked `vertical_scroll` — scrollbar and keybindings were decorative. Fix: window content is now `focusable=True` with `focused_element=input_window` on the Layout so typing still goes to the input. PageUp/PageDown, Home, End, Ctrl-Up/Ctrl-Down bindings added.

### TUI-P0: Esc-to-interrupt
**Status:** FIXED in [c79c082](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/c79c082), regression fixed in [8d46d19](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/8d46d19).
Cooperative interrupt distinct from Ctrl-C: Esc sets `state.interrupt_requested` so the agent loop stops at its next event boundary. Ctrl-C keeps hard-cancel semantics. `eager=True` so prompt_toolkit fires without waiting for a follow-on keystroke.

### TUI-P1: Autoscroll lock
**Status:** FIXED in [c79c082](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/c79c082), comment corrected in [97515e1](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/97515e1).
New output sticks to the bottom unless the user scrolls up; End re-enables autoscroll. Behaviour matches `tail -f`, `less +F`, and most chat TUIs.

### TUI-P2: Unbounded output buffer
**Status:** FIXED in [c79c082](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/c79c082), over-eviction fix in [97515e1](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/97515e1).
[`TUIOutputWriter`](../karna/tui/repl.py) now holds a 5000-line ring buffer; overflow evicts exactly the overflow (previously could trim up to 10% in one slice for no speed gain).

### TUI-P1: Queued-message indicator
**Status:** FIXED in [00aa306](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/00aa306).
Status bar shows "✉ N queued" in amber when `input_queue` has pending steering messages. Drops off the bar once the queue drains.

### TUI-P3: Empty-reply warning
**Status:** FIXED in [00aa306](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/00aa306).
If the agent finishes a turn with no `TEXT_DELTA` events (silent max-iterations halt, tool-only turn) the TUI now surfaces a yellow hint rather than leaving the user staring at a bare divider.

### TUI-P2: Rotating placeholder text was distracting
**Status:** FIXED in [f497f4b](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/f497f4b).
Removed the rotating placeholder and hid scrollbar arrows.

### Notebook: stderr hidden on nbconvert/papermill failure
**Status:** FIXED in [97515e1](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/97515e1).
Not strictly a TUI bug but surfaced via TUI. When a subprocess backend was present but exited non-zero, the generic refusal message hid the real reason. [`_run_subprocess_execution`](../karna/tools/notebook.py) now returns `(result, diagnostics)` with per-backend status (missing / exit code / stderr tail, truncated to 500 bytes).

---

## Still open

None from the original list — all 8 dogfooding bugs have been resolved on
this branch or earlier on `dev`.

If you hit something new, drop it above with a severity and reproduction, and
we'll track fixes by commit SHA the same way.
