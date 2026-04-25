# Nellie TUI Component Library (`karna.tui.cc_components`)

**Status:** Library-only port. Rendering primitives land here; REPL wiring lives in `karna/tui/hermes_repl.py` and is a deliberate next-step decision point.

A faithful Python / Rich port of Nellie TUI chrome from `the upstream project/components/`, re-skinned for Nellie:

- **Brand accent:** `#3C73BD` (via `karna.tui.design_tokens.COLORS.accent.brand`)
- **Assistant label:** `◆ nellie`
- **Glyph vocabulary:** `✦` thinking · `●` tool call · `⎿` tool result · brand-colored pointer / selected rows
- **Layout engine:** Rich renderables (no Ink / React reconciler)

Every renderer is documented per-module. Departures are documented per-module and summarised at the bottom of this file.

---

## Modules

| Module | Description | Public surface | Tests |
|---|---|---|---|
| `chat.py` | Message rendering: user / assistant / tool / system messages, row + timestamp + model label, response wrapper with nested suppression, interrupted-by-user marker, message selector | `ChatMessage`, `render_user_message`, `render_assistant_message`, `render_tool_message`, `render_system_message`, `render_message_row`, `render_messages`, `wrap_response`, `render_timestamp`, `render_model_label`, `render_interrupted_by_user`, `render_actions_menu`, `render_message_selector`, `MessageAction`, `format_timestamp` | 25 |
| `markdown.py` | Markdown rendering via Rich: fenced code blocks with syntax highlighting, tables with bold headers + zebra rows, OSC-8 hyperlinks, inline code with dim-background | `render_markdown`, `render_table`, `highlight_code`, `detect_language_from_path` | 32 |
| `diffs.py` | Structured diff rendering: +/-/context gutter, hunk panels, file-edit accepted/rejected messages, tool error/rejected messages, file-path links (OSC-8) | `render_structured_diff`, `render_file_edit_accepted`, `render_file_edit_rejected`, `render_tool_error`, `render_tool_rejected`, `render_file_path_link` | 8 |
| `status.py` | One-line status bar: model dot, context progress bar (50/80/95% bands), token warning panel, effort indicator, PR badge, cost-threshold alert, memory-usage indicator | `render_status_line`, `render_context_bar`, `render_token_warning`, `render_effort_indicator`, `render_pr_badge`, `render_cost_threshold_alert`, `render_memory_usage` | 12 |
| `spinners.py` | Spinners + tool-use loaders: braille frames, mirrored spinner set, thinking glyph, 173-verb tool-message vocabulary, render_thinking_line / render_tool_loader / render_bash_progress / agent progress line / coordinator status | `BRAILLE_FRAMES`, `SPINNER_FRAMES`, `THINKING_GLYPH`, `TOOL_MESSAGES`, `ALL_SPINNER_VERBS`, `pick_tool_message`, `render_thinking_line`, `render_tool_loader`, `render_bash_progress`, `render_agent_progress_line`, `render_coordinator_status` | 11 |
| `permissions.py` | Permission dialogs: 4-way tool permission prompt (allow-once/always, deny-once/always), MCP server approval, API key trust, bypass-permissions modal, permission allowlist renderer | `ToolPermissionChoice`, `prompt_tool_permission`, `prompt_mcp_server_approval`, `prompt_api_key_trust`, `prompt_bypass_permissions`, `render_permission_allowlist` | 6 |
| `pickers.py` | Async pickers: model, theme, output-style, language. Base Picker class with scroll window, rounded borders, brand-colored selection | `Picker`, `pick_model`, `pick_theme`, `pick_output_style`, `pick_language` | 9 |
| `search.py` | History search (Ctrl-R), global search (FTS5 across sessions), quick-open file picker, SearchBox with `⌕` prefix, TagTabs rendering | `history_search`, `global_search`, `quick_open_file`, `TagTabs`, `render_search_box`, `fuzzy_match` | 10 |
| `tasks.py` | Task list, compact summary (before/after token counts), resume-task prompt, session preview (tail-of-N messages), session background hint, agent list | `render_task_list`, `render_compact_summary`, `render_resume_task_prompt`, `render_session_preview`, `render_session_background_hint`, `render_agent_list` | 7 |
| `input.py` | Input primitives: Vim mode tracker, scroll keybindings, configurable shortcut hint helper, clickable image reference (OSC-8 file:// link) | `VimMode`, `VimTextInput`, `ScrollKeybindings`, `attach_configurable_shortcut_hint`, `render_clickable_image_ref` | 5 |
| `dialogs.py` | Small dialogs: confirm, press-enter-to-continue, exit flow, idle return, thinking toggle, Ctrl-O-to-expand, wizard runner, keybinding warnings, random goodbye | `confirm`, `press_enter_to_continue`, `exit_flow`, `idle_return`, `render_thinking_toggle`, `render_ctrl_o_to_expand`, `run_wizard`, `render_keybinding_warnings`, `IdleReturnAction`, `random_goodbye` | 7 |

**Totals:** 11 modules · 81 exported symbols · 132 tests green · ~5,500 LOC.

All public symbols are also re-exported from `karna.tui.cc_components.__init__` so callers can do `from karna.tui.cc_components import render_markdown`.

---

## Commit trail on `dev`

| # | Commit | Module |
|---|---|---|
| 1 | `773fd10` | `[tui-port-status]` |
| 2 | `98d49bb` | `[tui-port-markdown]` |
| 3 | `a13cf1d` | `[tui-port-diffs]` |
| 4 | `9cad6c8` | `[tui-port-chat]` |
| 5 | `3d08f7b` | `[tui-port-permissions]` |
| 6 | `c64640c` | `[tui-port-spinners]` |
| 7 | `f2bdd5f` | `[tui-port-tasks]` |
| 8 | `5a8bf5d` | `[tui-port-input-dialogs]` |
| 9 | `8810e70` | `[tui-port-pickers]` |
| 10 | `ab68363` | `[tui-port-search]` |
| 11 | `dced0d6` | `[tui-port-init]` (chat/markdown/diffs re-export wire-up) |

---

## Usage (library is live; wiring is next)

```python
from karna.tui.cc_components import (
    render_markdown,                   # replaces hermes_display ad-hoc renderer
    render_structured_diff,            # for Edit / Write tool results
    render_file_edit_accepted,         # header + diff panel on accepted edits
    render_status_line,                # one-line bottom bar
    render_thinking_line,              # "✦ Thinking · 4s · ↑ 2.1k tok · esc"
    render_tool_loader,                # "● Tool(ctx) <frame>" + verb below
    prompt_tool_permission,            # 4-way allow/deny choice
    history_search, global_search,     # Ctrl-R / slash search flows
    pick_model, pick_theme,            # async pickers
)
```

Callers supply data (message lists, diff hunks, token counts, tool names). Renderers return `rich.console.RenderableType`; interactive dialogs return concrete choices via `prompt_toolkit` Applications running under `patch_stdout()`.

---

## Integration gaps (by design, for the next pass)

These are runtime-subsystem hooks the library intentionally does NOT own. Each is called out in the responsible module's docstring:

| Gap | Owner subsystem | Flagged by |
|---|---|---|
| `render_pr_badge` needs `gh pr status` polling | new `karna/integrations/gh_status.py` | `status.py` |
| `render_memory_usage` needs a process-resource sampler (upstream's `useMemoryUsage` polls every 10s) | new `karna/telemetry/process.py` | `status.py` |
| `render_cost_threshold_alert` needs a cost hook that fires at the threshold crossing | `karna/costs/hooks.py` (exists) | `status.py` |
| `IdeStatusIndicator` (not ported) | IDE-MCP bridge | `status.py` |
| `PermissionManager.request_approval` is bool-only — upstream's 4-way `ToolPermissionChoice` surfaces `deny_always` → `session_denies` | `karna/permissions/manager.py` | `permissions.py` |
| `render_compact_summary` expects before/after token counts + summary text | `karna/compaction/compactor.py` | `tasks.py` |
| `render_session_preview` expects pre-loaded lite logs | `karna/sessions/picker.py` | `tasks.py` |
| `render_session_background_hint` needs `active_count` from a task-manager snapshot | `karna/tui/taskmanager.py` | `tasks.py` |
| `render_agent_list` needs an agent-registry snapshot feed | `karna/agents/registry.py` | `tasks.py` |
| `rapidfuzz` is an optional dep — module degrades to pure-Python ranker | `pyproject.toml [project.optional-dependencies]` | `search.py` |

---

## Design compromises (with fallbacks)

Rich is not a React reconciler. Where the design depends on Ink-only primitives, the library picks a sensible Rich equivalent:

- **React reconciler / `VirtualMessageList` / `useVirtualScroll`** — replaced by a "show last N + overflow summary" pager. Native terminal scrollback covers the rest.
- **Ink `useTerminalSize` + horizontal flex** — transcript-mode gutter (timestamp + model) stacks above the body instead of sitting left of it.
- **`cachedLexer` / `cachedHighlight` token cache** — Rich re-renders on every print; there's no unmount/remount lifecycle to defeat. Wrap `render_markdown` in `functools.lru_cache` if this becomes a hotspot.
- **`StreamingMarkdown` monotonic block-boundary trick** — no Rich equivalent for the stable-prefix / unstable-suffix split. Re-render on each delta.
- **`color-diff` NAPI module (node-only)** — no per-token syntax highlighting in diffs. Block colour only, matching upstream's own `StructuredDiffFallback` env-gated degradation path.
- **Partial borders (`borderStyle="dashed" borderLeft={false}`)** — Rich `Panel` is full border or nothing. Rendered as dim full-border panel.
- **Word-level paired-line diff (`diffAddedWord` / `diffRemovedWord`, CHANGE_THRESHOLD)** — ~200 LOC of paired-line logic skipped. Line-level only.
- **Live theme preview (`setPreviewTheme` / `cancelPreview`)** — Nellie lacks a reactive theme bus; picker selection is final.
- **Free-text filter typing in pickers** — deferred to the legacy `karna.tui.model_picker`.
- **`Onboarding.tsx`** — pure flow orchestration over 6 Nellie-incompatible steps. Its render primitives (`PressEnterToContinue`, `SkippableStep`) are ported; the flow isn't.
- **`messageActions.tsx` keybinding dispatcher** — shape ported; actual key dispatch lives in the REPL layer.
- **`MessageSelector` file-history / summarize sub-flows** — cursor windowing ported; restore / summarize picker left for a follow-up.
- **`linkifyIssueReferences` (owner/repo#123 auto-linking)** — belongs in a separate enrichment pass; Rich's Markdown wouldn't see the rewrites without a custom element.

---

## Running the tests

```bash
# Full cc_components suite
pytest tests/test_cc_status.py tests/test_cc_markdown.py tests/test_cc_diffs.py \
       tests/test_cc_chat.py tests/test_cc_permissions.py tests/test_cc_spinners.py \
       tests/test_cc_tasks.py tests/test_cc_input.py tests/test_cc_dialogs.py \
       tests/test_cc_pickers.py tests/test_cc_search.py -q
# → 132 passed in ~1.5s

# One-liner (they all match the pattern):
pytest tests/test_cc_*.py -q
```

---

## Licensing

The ported components mirror the shape of upstream TSX components. Nellie is proprietary Anthropic software; the port is derivative only insofar as function shapes and glyph vocabularies match. No upstream source is included. See `NOTICES.md` for MIT-licensed third-party code (Hermes, OpenClaw, etc.) and `LICENSE.md` for Nellie's proprietary terms.

---

## What's next (not started)

1. **REPL integration pass** — wire each cluster into `karna/tui/hermes_repl.py` streaming event handlers. Biggest wins: `render_structured_diff` for Edit/Write tool results, `render_tool_loader` during tool runs, `render_status_line` for the bottom bar, `render_thinking_line` replacing the current thinking indicator.
2. **Runtime-subsystem hookups** — see the Integration Gaps table above. Eight caller-side dependencies with named owners.
3. **REPL-layer keybinding dispatch** — `messageActions` keys (copy / rewind / branch) need a dispatcher in `hermes_repl.py` that routes into the ported `render_actions_menu` shape.
4. **Optional-dep declaration** — add `rapidfuzz` to `pyproject.toml [project.optional-dependencies].search`.

This list is the source of truth for what the library port leaves to runtime work.
