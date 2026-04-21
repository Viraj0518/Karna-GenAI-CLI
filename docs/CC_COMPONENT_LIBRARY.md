# CC Component Library (`karna.tui.cc_components`)

**Status:** Library-only port. Rendering primitives land here; REPL wiring lives in `karna/tui/hermes_repl.py` and is a deliberate next-step decision point.

A faithful Python / Rich port of the Claude Code TUI chrome from `/c/cc-src/src/components/`, re-skinned for Nellie:

- **Brand accent:** `#3C73BD` (via `karna.tui.design_tokens.COLORS.accent.brand`)
- **Assistant label:** `◆ nellie`
- **Glyph vocabulary:** `✦` thinking · `●` tool call · `⎿` tool result · brand-colored pointer / selected rows
- **Layout engine:** Rich renderables (no Ink / React reconciler)

Every renderer has a 1:1 upstream CC counterpart. Departures are documented per-module and summarised at the bottom of this file.

---

## Modules

| Module | Upstream CC mapping | Public surface | Tests |
|---|---|---|---|
| `chat.py` | `Message.tsx`, `MessageRow.tsx`, `Messages.tsx`, `MessageResponse.tsx`, `MessageTimestamp.tsx`, `InterruptedByUser.tsx`, `MessageSelector.tsx`, `messages/*`, `VirtualMessageList.tsx`, `messageActions.tsx` | `ChatMessage`, `render_user_message`, `render_assistant_message`, `render_tool_message`, `render_system_message`, `render_message_row`, `render_messages`, `wrap_response`, `render_timestamp`, `render_model_label`, `render_interrupted_by_user`, `render_actions_menu`, `render_message_selector`, `MessageAction`, `format_timestamp` | 25 |
| `markdown.py` | `Markdown.tsx`, `MarkdownTable.tsx`, `HighlightedCode.tsx`, `HighlightedCode/Fallback.tsx`, `utils/markdown.ts`, `utils/cliHighlight.ts`, `utils/hyperlink.ts` | `render_markdown`, `render_table`, `highlight_code`, `detect_language_from_path` | 32 |
| `diffs.py` | `StructuredDiff.tsx`, `StructuredDiff/*`, `FileEditToolDiff.tsx`, `FileEditToolUpdatedMessage.tsx`, `FileEditToolUseRejectedMessage.tsx`, `FallbackToolUseErrorMessage.tsx`, `FallbackToolUseRejectedMessage.tsx`, `NotebookEditToolUseRejectedMessage.tsx`, `FilePathLink.tsx` | `render_structured_diff`, `render_file_edit_accepted`, `render_file_edit_rejected`, `render_tool_error`, `render_tool_rejected`, `render_file_path_link` | 8 |
| `status.py` | `StatusLine`, `ContextVisualization`, `TokenWarning`, `EffortIndicator`, `PRBadge`, `CostThresholdDialog`, `useMemoryUsage` | `render_status_line`, `render_context_bar`, `render_token_warning`, `render_effort_indicator`, `render_pr_badge`, `render_cost_threshold_alert`, `render_memory_usage` | 12 |
| `spinners.py` | `Spinner`, `ToolUseLoader`, `agentProgressLine`, `constants/spinnerVerbs.ts` (173 verbs, verbatim) | `BRAILLE_FRAMES`, `SPINNER_FRAMES`, `THINKING_GLYPH`, `TOOL_MESSAGES`, `ALL_SPINNER_VERBS`, `pick_tool_message`, `render_thinking_line`, `render_tool_loader`, `render_bash_progress`, `render_agent_progress_line`, `render_coordinator_status` | 11 |
| `permissions.py` | `permissions/*`, `TrustDialog`, `MCPServerApprovalDialog`, `BypassPermissionsModeDialog`, `ApproveApiKey` | `ToolPermissionChoice`, `prompt_tool_permission`, `prompt_mcp_server_approval`, `prompt_api_key_trust`, `prompt_bypass_permissions`, `render_permission_allowlist` | 6 |
| `pickers.py` | `ModelPicker`, `ThemePicker`, `OutputStylePicker`, `LanguagePicker`, `CustomSelect` | `Picker`, `pick_model`, `pick_theme`, `pick_output_style`, `pick_language` | 9 |
| `search.py` | `HistorySearchDialog`, `GlobalSearchDialog`, `QuickOpenDialog`, `SearchBox`, `TagTabs` | `history_search`, `global_search`, `quick_open_file`, `TagTabs`, `render_search_box`, `fuzzy_match` | 10 |
| `tasks.py` | `TaskListV2`, `tasks/*`, `agents/*`, `CompactSummary`, `ResumeTask`, `SessionPreview`, `SessionBackgroundHint` | `render_task_list`, `render_compact_summary`, `render_resume_task_prompt`, `render_session_preview`, `render_session_background_hint`, `render_agent_list` | 7 |
| `input.py` | `PromptInput`, `VimTextInput`, `ScrollKeybindingHandler`, `ConfigurableShortcutHint`, `ClickableImageRef` | `VimMode`, `VimTextInput`, `ScrollKeybindings`, `attach_configurable_shortcut_hint`, `render_clickable_image_ref` | 5 |
| `dialogs.py` | `ExitFlow`, `IdleReturnDialog`, `PressEnterToContinue`, `ThinkingToggle`, `Wizard`/`WizardProvider`, `KeybindingWarnings`, small prompts | `confirm`, `press_enter_to_continue`, `exit_flow`, `idle_return`, `render_thinking_toggle`, `render_ctrl_o_to_expand`, `run_wizard`, `render_keybinding_warnings`, `IdleReturnAction`, `random_goodbye` | 7 |

**Totals:** 11 modules · 81 exported symbols · 132 tests green · ~5,500 LOC.

All public symbols are also re-exported from `karna.tui.cc_components.__init__` so callers can do `from karna.tui.cc_components import render_markdown`.

---

## Commit trail on `dev`

| # | Commit | Cluster |
|---|---|---|
| 1 | `773fd10` | `[cc-port-status]` |
| 2 | `98d49bb` | `[cc-port-markdown]` |
| 3 | `a13cf1d` | `[cc-port-diffs]` |
| 4 | `9cad6c8` | `[cc-port-chat]` |
| 5 | `3d08f7b` | `[cc-port-permissions]` |
| 6 | `c64640c` | `[cc-port-spinners]` |
| 7 | `f2bdd5f` | `[cc-port-tasks]` |
| 8 | `5a8bf5d` | `[cc-port-input-dialogs]` |
| 9 | `8810e70` | `[cc-port-pickers]` |
| 10 | `ab68363` | `[cc-port-search]` |
| 11 | `dced0d6` | `[cc-port-init]` (chat/markdown/diffs re-export wire-up) |

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
| `render_memory_usage` needs a process-resource sampler (CC's `useMemoryUsage` polls every 10s) | new `karna/telemetry/process.py` | `status.py` |
| `render_cost_threshold_alert` needs a cost hook that fires at the threshold crossing | `karna/costs/hooks.py` (exists) | `status.py` |
| `IdeStatusIndicator` (not ported) | IDE-MCP bridge | `status.py` |
| `PermissionManager.request_approval` is bool-only — CC's 4-way `ToolPermissionChoice` surfaces `deny_always` → `session_denies` | `karna/permissions/manager.py` | `permissions.py` |
| `render_compact_summary` expects before/after token counts + summary text | `karna/compaction/compactor.py` | `tasks.py` |
| `render_session_preview` expects pre-loaded lite logs | `karna/sessions/picker.py` | `tasks.py` |
| `render_session_background_hint` needs `active_count` from a task-manager snapshot | `karna/tui/taskmanager.py` | `tasks.py` |
| `render_agent_list` needs an agent-registry snapshot feed | `karna/agents/registry.py` | `tasks.py` |
| `rapidfuzz` is an optional dep — module degrades to pure-Python ranker | `pyproject.toml [project.optional-dependencies]` | `search.py` |

---

## CC semantics not mapped 1:1 (with fallbacks)

Rich is not a React reconciler. Where CC's behaviour depends on Ink-only primitives, the port picks a sensible Rich equivalent:

- **React reconciler / `VirtualMessageList` / `useVirtualScroll`** — replaced by a "show last N + overflow summary" pager. Native terminal scrollback covers the rest.
- **Ink `useTerminalSize` + horizontal flex** — transcript-mode gutter (timestamp + model) stacks above the body instead of sitting left of it.
- **`cachedLexer` / `cachedHighlight` token cache** — Rich re-renders on every print; there's no unmount/remount lifecycle to defeat. Wrap `render_markdown` in `functools.lru_cache` if this becomes a hotspot.
- **`StreamingMarkdown` monotonic block-boundary trick** — no Rich equivalent for the stable-prefix / unstable-suffix split. Re-render on each delta.
- **`color-diff` NAPI module (node-only)** — no per-token syntax highlighting in diffs. Block colour only, matching CC's own `StructuredDiffFallback` env-gated degradation path.
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

The ported components mirror the shape of Claude Code's TSX components. Claude Code is proprietary Anthropic software; the port is derivative only insofar as function shapes and glyph vocabularies match. No CC source is included. See `NOTICES.md` for MIT-licensed third-party code (Hermes, OpenClaw, etc.) and `LICENSE.md` for Nellie's proprietary terms.

---

## What's next (not started)

1. **REPL integration pass** — wire each cluster into `karna/tui/hermes_repl.py` streaming event handlers. Biggest wins: `render_structured_diff` for Edit/Write tool results, `render_tool_loader` during tool runs, `render_status_line` for the bottom bar, `render_thinking_line` replacing the current thinking indicator.
2. **Runtime-subsystem hookups** — see the Integration Gaps table above. Eight caller-side dependencies with named owners.
3. **REPL-layer keybinding dispatch** — `messageActions` keys (copy / rewind / branch) need a dispatcher in `hermes_repl.py` that routes into the ported `render_actions_menu` shape.
4. **Optional-dep declaration** — add `rapidfuzz` to `pyproject.toml [project.optional-dependencies].search`.

This list is the source of truth for what the library port leaves to runtime work.
