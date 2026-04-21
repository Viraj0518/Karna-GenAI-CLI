"""Status-line + context indicators, ported verbatim-in-spirit from Claude Code.

Mirrors the visuals of CC's `StatusLine.tsx`, `ContextVisualization.tsx`,
`TokenWarning.tsx`, `EffortCallout.tsx` / `EffortIndicator.ts`, `PrBadge.tsx`,
`CostThresholdDialog.tsx`, `MemoryUsageIndicator.tsx`, and
`IdeStatusIndicator.tsx` — but skinned to Nellie's palette (`#3C73BD` brand,
warning/danger/success tokens from `design_tokens`).

Design notes
------------
* Library only. No Rich `Live`, no polling, no side effects. Every renderer
  returns either an ANSI string (for the one-line status bar that agents may
  print directly) or a Rich renderable (for components that integrate into
  the existing REPL output pipeline).
* CC's glyph vocabulary is preserved: `●` for online/model dot, `⎿` for
  tree-branch joiners, `✦` for "thinking/effort sparkle", `⧉` for IDE
  selection, `◐ ◑ ◒` for effort levels (low/medium/high/max — matches
  `EffortIndicator.ts`'s `EFFORT_*` figures from CC's `constants/figures.js`).
* CC's threshold colors for context usage live in `TokenWarning.tsx` via
  `calculateTokenWarningState`: warning at 80 %, error at 95 %. We expose
  the same three-band system plus an additional "quiet/green" band below
  50 % for a visible progress bar that matches CC's `ContextVisualization`.

Runtime gaps
------------
Several CC patterns rely on subsystems Nellie doesn't have yet:

* `IdeStatusIndicator` polls `useIdeConnectionStatus(mcpClients)` — needs an
  IDE-MCP bridge. Not ported.
* `PrBadge` consumes `PrReviewState` from `ghPrStatus.ts`, which polls GitHub
  via `gh`. We expose `render_pr_badge` as a pure renderer; callers must
  supply the status. -> requires `gh pr status` polling integration
* `MemoryUsageIndicator` uses `useMemoryUsage` (10 s interval poll against
  Node's `process.memoryUsage`). The Python port exposes a pure renderer;
  callers pass `used_bytes` / `limit_bytes`. -> requires a process-resource
  sampler integration
* `CostThresholdDialog` fires on a hook event; we stub the pure renderer.
  -> requires cost-hook integration
"""

from __future__ import annotations

from typing import Optional

from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text

from karna.tui.design_tokens import COLORS

# --------------------------------------------------------------------------- #
#  Glyph vocabulary (CC-compatible)
# --------------------------------------------------------------------------- #

# Model dot (CC uses a filled circle in the status bar)
GLYPH_MODEL_DOT = "\u25cf"  # ●
# Tree-branch joiner (CC's `⎿` appears in tool-call call-outs)
GLYPH_BRANCH = "\u23bf"  # ⎿
# Sparkle — thinking / effort indicator (matches CC's ✦)
GLYPH_SPARKLE = "\u2726"  # ✦
# IDE selection marker (matches `IdeStatusIndicator.tsx`'s `⧉`)
GLYPH_IDE_SELECT = "\u29c9"  # ⧉
# Effort-level symbols — CC's `EffortIndicator.ts` emits these from
# `constants/figures.js` (EFFORT_LOW/MEDIUM/HIGH/MAX). The exact private
# glyphs aren't exported, so we use the closest generic half-circle trio
# that renders consistently across mono terminals.
GLYPH_EFFORT_LOW = "\u25d0"  # ◐ — low
GLYPH_EFFORT_MEDIUM = "\u25d1"  # ◑ — medium
GLYPH_EFFORT_HIGH = "\u25d2"  # ◒ — high
GLYPH_EFFORT_MAX = "\u25c6"  # ◆ — max
# Divider used between status-bar segments (CC's centered dot)
GLYPH_SEP = "\u00b7"  # ·


# --------------------------------------------------------------------------- #
#  Brand palette — sourced from design_tokens so themeing flows through.
# --------------------------------------------------------------------------- #

BRAND = COLORS.accent.brand  # "#3C73BD"
MUTED = COLORS.text.secondary
SUBTLE = COLORS.text.tertiary
SUCCESS = COLORS.accent.success
WARNING = COLORS.accent.warning
DANGER = COLORS.accent.danger
CYAN = COLORS.accent.cyan


# --------------------------------------------------------------------------- #
#  Threshold bands — mirrors CC's TokenWarning logic:
#      < 50 %  : quiet/green
#      50-80 % : neutral/brand (the "heads-up" band)
#      80-95 % : warning
#      >= 95 % : error
# --------------------------------------------------------------------------- #

CTX_WARNING_THRESHOLD = 80
CTX_ERROR_THRESHOLD = 95
CTX_QUIET_THRESHOLD = 50


def _context_color(used_pct: float) -> str:
    """Pick a hex color for a context-usage percentage [0, 100]."""
    if used_pct >= CTX_ERROR_THRESHOLD:
        return DANGER
    if used_pct >= CTX_WARNING_THRESHOLD:
        return WARNING
    if used_pct >= CTX_QUIET_THRESHOLD:
        return BRAND
    return SUCCESS


def _pct(used: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, (used / total) * 100.0))


def _format_tokens(n: int) -> str:
    """CC's `formatTokens()` — '12.3k', '1.2M', etc."""
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        # Match CC: one decimal place, drop trailing .0
        v = n / 1_000.0
        s = f"{v:.1f}".rstrip("0").rstrip(".")
        return f"{s}k"
    v = n / 1_000_000.0
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{s}M"


def _format_bytes(n: int) -> str:
    """CC's `formatFileSize()` — 'KB'/'MB'/'GB'."""
    if n < 1024:
        return f"{n} B"
    kb = n / 1024.0
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024.0
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024.0
    return f"{gb:.2f} GB"


# --------------------------------------------------------------------------- #
#  ANSI helpers — the status line is a raw string so it can be printed
#  by any consumer (e.g. a daemon) without requiring Rich.
# --------------------------------------------------------------------------- #


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _ansi_fg(hex_color: str) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"\x1b[38;2;{r};{g};{b}m"


def _ansi_bold() -> str:
    return "\x1b[1m"


def _ansi_dim() -> str:
    return "\x1b[2m"


def _ansi_reset() -> str:
    return "\x1b[0m"


# --------------------------------------------------------------------------- #
#  Public renderers
# --------------------------------------------------------------------------- #


def render_status_line(
    *,
    model: str,
    session_time: str,
    tokens_used: int,
    context_window: int,
    cost_usd: float,
    agent_running: bool,
    queued: int = 0,
) -> str:
    """One-line status bar, ANSI-colored, matching CC's visual density.

    Layout (CC-style, left to right, separated by `·`):

        ● model · ⏱ 12m34s · ctx 42% (12.3k/200k) · $0.42 · ▶ running (2 queued)

    Args:
        model: display name of the model (e.g. 'Opus 4.7').
        session_time: pre-formatted duration ('12m34s').
        tokens_used: current context usage (input+output + reserved).
        context_window: total window size for the model.
        cost_usd: session cost in USD.
        agent_running: True when an agent turn is in flight.
        queued: number of queued tool calls / follow-ups.
    """
    used_pct = _pct(tokens_used, context_window)
    ctx_color = _context_color(used_pct)

    dot = _ansi_fg(BRAND) + GLYPH_MODEL_DOT + _ansi_reset()
    model_seg = f"{dot} {_ansi_bold()}{_ansi_fg(BRAND)}{model}{_ansi_reset()}"
    time_seg = f"{_ansi_dim()}{session_time}{_ansi_reset()}"

    ctx_label = (
        f"{_ansi_fg(ctx_color)}ctx {int(round(used_pct))}%{_ansi_reset()} "
        f"{_ansi_dim()}({_format_tokens(tokens_used)}/"
        f"{_format_tokens(context_window)}){_ansi_reset()}"
    )
    cost_seg = f"{_ansi_fg(MUTED)}${cost_usd:.2f}{_ansi_reset()}"

    if agent_running:
        run_color = WARNING
        state = f"{_ansi_fg(run_color)}\u25b6 running{_ansi_reset()}"
        if queued:
            state += f" {_ansi_dim()}({queued} queued){_ansi_reset()}"
    else:
        state = f"{_ansi_fg(SUBTLE)}\u25cb idle{_ansi_reset()}"

    sep = f" {_ansi_fg(SUBTLE)}{GLYPH_SEP}{_ansi_reset()} "
    return sep.join([model_seg, time_seg, ctx_label, cost_seg, state])


def render_context_bar(tokens_used: int, context_window: int) -> Text:
    """Colored progress bar for context usage.

    Matches the feel of CC's `ContextVisualization.tsx` header — one-line
    summary with a unicode block bar and the "{model} · tokens/max (pct%)"
    caption pattern. Color transitions green -> brand -> yellow -> red at
    the 50/80/95 thresholds.
    """
    pct = _pct(tokens_used, context_window)
    color = _context_color(pct)

    width = 20
    filled = int(round((pct / 100.0) * width))
    filled = max(0, min(width, filled))
    bar = "\u2588" * filled + "\u2591" * (width - filled)

    t = Text()
    t.append("[", style=f"dim {MUTED}")
    t.append(bar, style=color)
    t.append("]", style=f"dim {MUTED}")
    t.append(" ")
    t.append(
        f"{_format_tokens(tokens_used)}/{_format_tokens(context_window)}",
        style=MUTED,
    )
    t.append(f"  ({int(round(pct))}%)", style=f"dim {color}")
    return t


def render_token_warning(
    tokens_used: int, context_window: int
) -> Optional[RenderableType]:
    """Return a `Panel` only when we've crossed the warning/error threshold.

    Mirrors CC's `TokenWarning.tsx` — silent under 80 %, muted warning at
    80 %, errory red at 95 %. Returns `None` otherwise so the caller can
    skip rendering.
    """
    pct = _pct(tokens_used, context_window)
    if pct < CTX_WARNING_THRESHOLD:
        return None

    is_error = pct >= CTX_ERROR_THRESHOLD
    color = DANGER if is_error else WARNING
    remaining_pct = max(0, 100 - int(round(pct)))

    body = Text()
    body.append(
        f"Context low ({remaining_pct}% remaining) ",
        style=f"bold {color}",
    )
    body.append(GLYPH_SEP + " ", style=f"dim {MUTED}")
    body.append(
        "Run /compact to compact & continue" if not is_error else "Run /compact now",
        style=MUTED,
    )

    title = Text(
        f"{GLYPH_SPARKLE} token budget",
        style=f"bold {color}",
    )
    return Panel(
        body,
        title=title,
        title_align="left",
        border_style=color,
        padding=(0, 1),
    )


def render_effort_indicator(
    thinking_enabled: bool, thinking_budget: Optional[int]
) -> Text:
    """Inline effort-level pill (low/medium/high/max + ✦ sparkle).

    Mirrors `EffortIndicator.ts`'s `effortLevelToSymbol` + `EffortCallout.tsx`
    ordering: `low < medium < high < max`. Budget thresholds match CC's
    default schedule (<=1024 low, <=4096 medium, <=16384 high, >16384 max).
    """
    t = Text()
    if not thinking_enabled or thinking_budget is None or thinking_budget <= 0:
        t.append(f"{GLYPH_SPARKLE} thinking: off", style=f"dim {SUBTLE}")
        return t

    if thinking_budget <= 1024:
        glyph, label, color = GLYPH_EFFORT_LOW, "low", SUCCESS
    elif thinking_budget <= 4096:
        glyph, label, color = GLYPH_EFFORT_MEDIUM, "medium", BRAND
    elif thinking_budget <= 16384:
        glyph, label, color = GLYPH_EFFORT_HIGH, "high", WARNING
    else:
        glyph, label, color = GLYPH_EFFORT_MAX, "max", DANGER

    t.append(f"{glyph} ", style=color)
    t.append(label, style=f"bold {color}")
    t.append(f" {GLYPH_SEP} ", style=f"dim {SUBTLE}")
    t.append(f"{_format_tokens(thinking_budget)} tok", style=MUTED)
    return t


def render_pr_badge(pr_number: int, status: str) -> Text:
    """`PR #123` badge colored by CI / review state.

    Matches `PrBadge.tsx`'s `getPrStatusColor`:
      approved -> success   changes_requested -> error
      pending  -> warning   merged            -> brand/merged
      anything else -> dim

    requires `gh pr status` polling integration (pure renderer; caller
    supplies the status string).
    """
    status_norm = (status or "").lower().strip()
    color_map = {
        "approved": SUCCESS,
        "success": SUCCESS,
        "merged": BRAND,
        "changes_requested": DANGER,
        "failing": DANGER,
        "error": DANGER,
        "pending": WARNING,
        "in_review": WARNING,
    }
    color = color_map.get(status_norm, SUBTLE)

    t = Text()
    t.append("PR ", style=f"dim {MUTED}")
    t.append(f"#{pr_number}", style=f"bold underline {color}")
    if status_norm:
        t.append(f" {GLYPH_SEP} ", style=f"dim {SUBTLE}")
        t.append(status_norm.replace("_", " "), style=color)
    return t


def render_cost_threshold_alert(
    current_usd: float, threshold_usd: float
) -> RenderableType:
    """Panel shown when session cost crosses a user-configured threshold.

    Mirrors CC's `CostThresholdDialog.tsx` (pure renderer — no Select/Link
    affordances; those belong to a future dialog shell).

    requires cost-hook integration to fire at the right moment.
    """
    body = Text()
    body.append(
        f"You've spent ${current_usd:.2f} this session ",
        style=f"bold {DANGER}",
    )
    body.append(f"({GLYPH_SEP} threshold ${threshold_usd:.2f}).\n", style=MUTED)
    body.append(
        "Learn more about monitoring spend: ",
        style=MUTED,
    )
    body.append(
        "https://docs.anthropic.com/claude-code/costs",
        style=f"underline {CYAN}",
    )

    return Panel(
        body,
        title=Text(
            f"{GLYPH_SPARKLE} cost threshold reached",
            style=f"bold {DANGER}",
        ),
        title_align="left",
        border_style=DANGER,
        padding=(0, 1),
    )


def render_memory_usage(used_bytes: int, limit_bytes: Optional[int]) -> Text:
    """Inline memory indicator — only "loud" when usage is high/critical.

    Mirrors `MemoryUsageIndicator.tsx` which hides under 'normal' status
    and shifts color between `warning` and `error`. Without a `limit_bytes`
    we can't compute a ratio, so we fall back to absolute thresholds
    (500 MB warn, 1 GB crit) that match CC's `/heapdump` heuristic.

    requires a process-resource sampler integration (caller supplies
    `used_bytes`).
    """
    t = Text()
    if limit_bytes and limit_bytes > 0:
        ratio = used_bytes / limit_bytes
        if ratio >= 0.90:
            color, level = DANGER, "critical"
        elif ratio >= 0.75:
            color, level = WARNING, "high"
        else:
            # CC hides the component entirely when status == 'normal'.
            # We return a dim one-liner so the caller can decide.
            t.append(
                f"mem {_format_bytes(used_bytes)}/{_format_bytes(limit_bytes)}",
                style=f"dim {SUBTLE}",
            )
            return t
        t.append(f"High memory ({level}) ", style=f"bold {color}")
        t.append(
            f"{_format_bytes(used_bytes)}/{_format_bytes(limit_bytes)}",
            style=color,
        )
        t.append(f"  {GLYPH_SEP} /heapdump", style=f"dim {MUTED}")
        return t

    # No limit — absolute thresholds.
    if used_bytes >= 1024 * 1024 * 1024:
        color, level = DANGER, "critical"
    elif used_bytes >= 500 * 1024 * 1024:
        color, level = WARNING, "high"
    else:
        t.append(f"mem {_format_bytes(used_bytes)}", style=f"dim {SUBTLE}")
        return t
    t.append(f"High memory ({level}) ", style=f"bold {color}")
    t.append(_format_bytes(used_bytes), style=color)
    t.append(f"  {GLYPH_SEP} /heapdump", style=f"dim {MUTED}")
    return t


__all__ = [
    "render_status_line",
    "render_context_bar",
    "render_token_warning",
    "render_effort_indicator",
    "render_pr_badge",
    "render_cost_threshold_alert",
    "render_memory_usage",
    "CTX_WARNING_THRESHOLD",
    "CTX_ERROR_THRESHOLD",
    "CTX_QUIET_THRESHOLD",
    "BRAND",
]
