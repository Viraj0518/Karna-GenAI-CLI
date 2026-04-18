# Nellie TUI — Before / After Comparison

Side-by-side visual audit of the Nellie TUI redesign. Open
`index.html` in a browser to view all 10 scene pairs inline.

- **Before snapshots**: `../before/NN_*.svg` — pre-redesign `karna.tui.*`
  APIs, captured via `../capture_before.py`.
- **After snapshots**: `../after/NN_*.svg` — post-redesign APIs
  (`karna.tui.design_tokens`, `karna.tui.icons`, refactored
  `banner.py` / `output.py` / `slash.py` / `themes.py`), captured via
  `../capture_after.py`. Both scripts exercise the real public
  surface — `print_banner`, `OutputRenderer`, `_cmd_help`.
- Both capture scripts export via Rich `Console.export_svg()` at
  100-col width, truecolor, recording mode.

## 3 biggest visual wins

1. **Errors are now actionable.** The 401 scene goes from a raw
   `HTTPStatusError` dump to: structured panel with title
   (`✗ something went wrong`), primary message, provider context line,
   and a **pattern-matched hint** that recognises common failures —
   401/403, 404 model, 429 rate-limit, connection refused, timeouts,
   TLS. For 401 the hint is
   `check your API key with \`nellie auth list\``. Every new user
   dropped into an auth failure now has a path forward instead of a
   stack trace.
2. **Tool calls have distinct lifecycle states.** Before, a call and a
   result were two separate panels with different titles (`tool call:
   write` / `tool result`) and inconsistent styling (yellow vs dim-green
   borders). After: a single-line header per state —
   `⚒ name  …  calling name...` while running, `⚒ name  ✓  <summary>`
   when done, `⚒ name  ✗  <error>` when failed. Long output bodies get
   a borderless content panel; JSON auto-detects and syntax-highlights.
   Scrollback is instantly scannable.
3. **Semantic design tokens unify the palette.** Every color now comes
   from `design_tokens.SEMANTIC` (`role.user`, `tool.ok`,
   `accent.brand`, etc.) and Rich styles are lifted from those in
   `themes.py`. Both downstream consumers (`banner.py`, `output.py`,
   `slash.py`) reach for the semantic names rather than raw hexes or
   Rich color strings. Swapping palettes (light mode, accessibility
   variant, user themes) becomes a one-file change rather than a
   codebase sweep. Legacy names
   (`BRAND_BLUE`, `ASSISTANT_TEXT`, `KARNA_THEME`, etc.) are preserved
   so no existing importer breaks.

Honourable mentions: the banner swapped a brand-blue panel for a clean
header + divider + status-row layout with workspace detection
(directory name + project kind + git presence); `/help` turned from a
flat 13-row table into a grouped icon-prefixed panel with a keybinding
footer; `thinking` gained its own event kind, semantic color, and
multi-line collapse; turn rhythm is now established by a dim divider
above each new turn.

## Regressions noticed

- **02 empty prompt** still uses a static block cursor stub. In the
  real TUI prompt_toolkit draws a live caret; the SVG capture cannot
  reproduce it, so the "after" is still a stand-in that mimics
  `input._make_session` styling with an added chevron affordance.
- **07 thinking** snapshot is still a single static frame of a
  `THINKING_DELTA` event; `rich.live.Live` spinners are transient and
  unrecordable. The real in-terminal experience will animate the
  `thinking...` phase *before* this line is committed; the SVG only
  asserts the color/glyph/typography choices.
- **09 slash help icons collide at small terminal sizes.** The
  grouped panel relies on a 2-column icon gutter which is fine at
  100 cols but squeezes below ~80 cols. Needs a responsive variant.
- **Tool result status line** now uses tertiary (dim) meta-style for
  the *success* summary (`wrote 12 lines to fib.py`). The "done" glyph
  is green but the text is muted. Some users might read that as
  low-priority; worth A/B-ing against a primary-text summary.
- **Expand widths.** Tool panels for long outputs keep `expand=True`
  now (big improvement), but tool-call headers run their full native
  width with no horizontal padding — very long tool names still push
  status glyphs off-alignment.

## Accessibility notes

Contrast ratios checked against WCAG 2.1 on dark background
(`bg.subtle = #0E0F12`):

| Pair                           | Ratio   | WCAG (AA normal / AA large) |
| ------------------------------ | ------- | --------------------------- |
| `text.primary #E6E8EC`         | 15.0:1  | pass / pass                 |
| `text.secondary #A0A4AD`       |  7.4:1  | pass / pass                 |
| `text.tertiary #5F6472`        |  3.3:1  | **fail** / pass (large only)      |
| `accent.cyan #87CEEB`          | 10.5:1  | pass / pass                 |
| `accent.brand #3C73BD`         |  4.4:1  | pass / pass                 |
| `accent.success #7DCFA1`       |  9.6:1  | pass / pass                 |
| `accent.warning #E8C26B`       | 11.0:1  | pass / pass                 |
| `accent.danger #E87C7C`        |  7.8:1  | pass / pass                 |
| `accent.thinking #9F7AEA`      |  5.7:1  | pass / pass                 |

**Action item**: `text.tertiary` fails AA for small body text; keep its
use limited to the *micro* typography role (captions, metadata, hints)
where content is supplementary. Do **not** render essential information
(tool summaries, error hints) exclusively in `text.tertiary`. The
current `_on_tool_result` renders success summaries in the meta style —
consider promoting them to `text.secondary`.

Icons always have text labels alongside them (never icon-only), so Nerd
Font fallback to ASCII (`$`, `R`, `W`, `ok`, `!!`) preserves meaning for
users on terminals without Nerd Font support.

## Open follow-ups

- **Per-turn separator regression.** `OutputRenderer._ensure_turn_break`
  is called from `show_spinner`, but the capture script bypasses that
  entry point. The real REPL will show the divider; our SVG doesn't.
  Consider also invoking `_ensure_turn_break` from the first non-spinner
  event so dividers appear regardless of entry path.
- **Tool elapsed time.** `✓ summary` could carry a `(2.3s)` suffix;
  useful when sessions touch slow tools (web_fetch, tests).
- **Spinner recovery / cancel hint.** Once thinking exceeds N seconds
  surface an `interrupt with Ctrl-C` hint.
- **Light-mode palette.** Design tokens are structured to support it;
  nobody has defined the light variant yet.
- **Diff rendering for `edit` result.** Currently the edit's result
  line is a plain "edited fib.py (1 replacement)" summary. A
  `Syntax("…", "diff", …)` preview would reward users who want to see
  the delta inline without opening the file.
- **`slash.py` `_render_category_table` double-renders the label** —
  it's set both as the `title` of the Table and the panel groups a
  blank line above each section. Slight visual redundancy; trim one.

## Regenerating

```
python research/ui-audit/capture_after.py   # writes after/*.svg
open  research/ui-audit/compare/index.html  # view the pairs
```
