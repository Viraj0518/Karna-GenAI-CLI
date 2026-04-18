# Nellie TUI — "Before" Visual Audit

SVG snapshots of the current Nellie TUI, captured via
`research/ui-audit/capture_before.py` using Rich's `Console.export_svg()`.
Width 100 cols, `KARNA_THEME`, `force_terminal=True`. All scenes exercise
real `karna.tui.*` APIs (banner, `OutputRenderer`, slash command table).

## Scenes

| File | What it shows |
| --- | --- |
| `01_banner.svg` | Startup banner — `print_banner(...)` with model `openrouter:openai/gpt-oss-120b`, 14 tools loaded, `/help for commands` hint. Brand-blue bordered Rich Panel. |
| `02_empty_prompt.svg` | Idle prompt line — `gpt-oss-120b> ` in bold `#87CEEB` with a dim block-cursor stub. Simulated (prompt_toolkit isn't renderable under record mode). |
| `03_user_msg_echo.svg` | Scrollback view right after the user hits Enter. The prompt line stays in history with the typed message inline. |
| `04_assistant_streaming.svg` | Assistant text flushed through `OutputRenderer` via `TEXT_DELTA` events — rendered as Markdown (numbered list, inline code). |
| `05_tool_call.svg` | `write` tool invocation: yellow-bordered Panel, monokai JSON syntax-highlighted args with `path` + `content`. |
| `06_tool_result.svg` | `tool result` panel (dim green border, dim green title) showing `wrote 12 lines to fib.py`. |
| `07_thinking.svg` | Static render of the spinner state (`⠋ thinking...` in bold brand-blue). The real UI uses `rich.live.Live`, which is transient and can't be recorded; this captures one equivalent frame. |
| `08_error.svg` | `EventKind.ERROR` path — red-bordered Panel with title `Error` and an HTTPX 401 Unauthorized message. |
| `09_slash_help.svg` | `/help` table — brand-blue bordered `rich.table.Table` with `Command` / `Description` columns, lists all 13 slash commands from `karna.tui.slash.COMMANDS`. |
| `10_multi_tool.svg` | Two tool calls in sequence: `read` (with result) then `edit` (with result). Four consecutive Panels, no visual separator between them. |

## Visual issues noticed

- **Banner is text-only and cramped.** Four left-aligned key/value lines with no
  ASCII art, no app tagline, and padding `(0, 2)`. Nothing signals "Nellie" vs a
  generic Rich panel — the brand accent is just the border.
- **Prompt lacks affordance.** `gpt-oss-120b> ` is a plain label; no hint that
  Esc+Enter inserts a newline, no multi-line gutter, no `/` hint for slash
  commands. New users won't discover either feature.
- **Tool call vs tool result styling is inconsistent.** Tool call uses `yellow`
  border, full-bold title, and a Monokai-themed JSON syntax block. Tool result
  uses `dim green` border + dim green body. Two tools in a row render as four
  stacked panels with no grouping, no tool-call index, no elapsed time. Hard
  to tell "active" vs "finished" and hard to scan long sessions.
- **Tool-call title says "tool call:" even after the result lands.** There's no
  state transition — past calls look identical to in-flight ones.
- **Error panel shows raw HTTPX string.** Dumps "HTTPStatusError: Client error
  '401 Unauthorized' for url https://openrouter.ai/..." verbatim. No
  actionable guidance ("check `OPENROUTER_API_KEY`"), no provider context, no
  remediation link tailored to Nellie.
- **Assistant markdown rendering is plain.** Bullets/lists use Rich's default
  glyphs; no brand blue accent on headers, no indent rail, no "assistant" label
  — can blur with tool-result text in long scrollback.
- **Spinner is invisible to recording and likely flashes at the top of scroll.**
  `Live(..., transient=True)` with no anchor means it disappears with zero
  trace of how long the wait was.
- **/help table is dense.** 13 rows, no grouping (session vs model vs
  clipboard), no keyboard shortcut column, Description column is dim gray and
  hard to read on dark-on-dark.
- **Panels all use `expand=False`.** Widths hug content, producing a ragged
  left-aligned column of different-width boxes down the transcript. Either
  align all panels to a common gutter or enable `expand=True` for readability.
- **No per-turn separator.** User line, assistant block, tool calls, and usage
  footer run together. Long multi-turn sessions will be a wall of panels.
- **`thinking...` has no elapsed timer or model name.** Users get no feedback
  on slow providers.

## Regenerating

```
python research/ui-audit/capture_before.py
```
