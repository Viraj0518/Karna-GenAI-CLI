"""Spinners + tool-use loaders, ported verbatim-in-spirit from upstream reference.

Mirrors upstream's spinner glyph, status-bar thinking row, `ToolUseLoader`,
`BashModeProgress`, `AgentProgressLine`, and the `CoordinatorAgentStatus`
panel — all skinned for Nellie's palette (`#3C73BD` brand + design tokens).

Source files (`the upstream project/components/`):
    * Spinner.tsx                 -> thinking status line, `✦ verb · Ns · ↑ Ntok`
    * Spinner/SpinnerGlyph.tsx    -> DEFAULT_CHARACTERS, the mirrored frame set
    * Spinner/utils.ts            -> `getDefaultCharacters()` (platform-aware)
    * constants/spinnerVerbs.ts   -> `SPINNER_VERBS` (173 verbs)
    * ToolUseLoader.tsx           -> blinking black-circle tool-status dot
    * BashModeProgress.tsx        -> bash input + live output panel
    * AgentProgressLine.tsx       -> `├─ agent · N tool uses · tokens` tree row
    * CoordinatorAgentStatus.tsx  -> panel listing active panel-mode agents

Design notes
------------
* Library only. No `rich.live.Live`, no side effects, no polling. Every
  renderer returns either an ANSI string or a Rich renderable; callers
  drive the frame/time updates themselves.
* Brand accent is the Karna blue (`#3C73BD` from `design_tokens.Accent.brand`).
* `✦` is upstream's ANT-only thinking sparkle (`TEARDROP_ASTERISK` in
  `constants/figures.js`). upstream switches the thinking-row leading glyph
  between `*` (external) and `✦` (internal); Nellie uses `✦`.
* `BRAILLE_FRAMES` is re-exported for callers that want a braille spinner;
  the upstream port itself uses `SPINNER_FRAMES` (the mirrored
  `· ✢ ✳ ✶ ✻ ✽` set from `getDefaultCharacters()`).

upstream's per-tool "cute messages" dict
----------------------------------
upstream does not ship a per-tool cute-message dictionary: each tool call shows
a single random verb from `SPINNER_VERBS` (173 entries), and the
tool's own `renderToolUseProgressMessage()` renders the arg context on
the same line. Ported faithfully — `TOOL_MESSAGES` keys each Nellie tool
to a curated subset of upstream's verb list so callers can pick one
deterministically per tool while still matching upstream's feel.
"""

from __future__ import annotations

import random
import sys
from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from karna.tui.design_tokens import COLORS

# --------------------------------------------------------------------------- #
#  Palette shortcuts — keep the module cheap to import
# --------------------------------------------------------------------------- #

_BRAND = COLORS.accent.brand  # "#3C73BD"
_BRAND_DIM = COLORS.accent.hover  # lighter, for dim frames
_TEXT_PRIMARY = COLORS.text.primary
_TEXT_SECONDARY = COLORS.text.secondary
_TEXT_TERTIARY = COLORS.text.tertiary
_SUCCESS = COLORS.accent.success
_WARNING = COLORS.accent.warning
_DANGER = COLORS.accent.danger
_THINKING = COLORS.accent.thinking

#: upstream's thinking sparkle (`TEARDROP_ASTERISK` in constants/figures.js).
THINKING_GLYPH = "✦"


# --------------------------------------------------------------------------- #
#  Frame sets — ported from upstream's Spinner/utils.ts + SpinnerGlyph.tsx
# --------------------------------------------------------------------------- #

#: Braille dot-spinner frames (already present in `karna.tui.output`).
#: Re-exported here so consumers of the upstream-port module don't have to reach
#: back into `output.py` and accidentally import the REPL.
BRAILLE_FRAMES: list[str] = [
    "⠋",
    "⠙",
    "⠹",
    "⠸",
    "⠼",
    "⠴",
    "⠦",
    "⠧",
    "⠇",
    "⠏",
]


def _get_default_characters() -> list[str]:
    """Platform-aware frame picker, ported from `Spinner/utils.ts`.

    upstream's mapping:
        * `TERM=xterm-ghostty` -> ['·', '✢', '✳', '✶', '✻', '*']
        * `process.platform === 'darwin'` -> ['·', '✢', '✳', '✶', '✻', '✽']
        * else (Windows / Linux) -> ['·', '✢', '*', '✶', '✻', '✽']
    """
    import os

    if os.environ.get("TERM") == "xterm-ghostty":
        return ["·", "✢", "✳", "✶", "✻", "*"]
    if sys.platform == "darwin":
        return ["·", "✢", "✳", "✶", "✻", "✽"]
    return ["·", "✢", "*", "✶", "✻", "✽"]


#: Full upstream frame list — forward then reversed, same construction upstream uses in
#: `SpinnerGlyph.tsx` and `Spinner.tsx` (`SPINNER_FRAMES = [...chars,
#: ...[...chars].reverse()]`).
_DEFAULT_CHARACTERS = _get_default_characters()
SPINNER_FRAMES: list[str] = list(_DEFAULT_CHARACTERS) + list(reversed(_DEFAULT_CHARACTERS))


# --------------------------------------------------------------------------- #
#  Tool message dictionary — curated slice of upstream's SPINNER_VERBS
# --------------------------------------------------------------------------- #
#
#  upstream's `constants/spinnerVerbs.ts` exports 173 gerunds. Any of them may
#  surface for any tool — upstream picks one with `sample(getSpinnerVerbs())`
#  per mount. For a Nellie port we want deterministic "tool → vocabulary"
#  mappings so the ui feels curated rather than random. The values below
#  are pulled directly from upstream's list; no words are invented.
# --------------------------------------------------------------------------- #

#: Master list — every gerund from upstream's `SPINNER_VERBS` (173 entries,
#: verbatim). Callers that want upstream's exact "pick any" behaviour can do
#: ``random.choice(ALL_SPINNER_VERBS)``.
ALL_SPINNER_VERBS: list[str] = [
    "Accomplishing",
    "Actioning",
    "Actualizing",
    "Architecting",
    "Baking",
    "Beaming",
    "Beboppin'",
    "Befuddling",
    "Billowing",
    "Blanching",
    "Bloviating",
    "Boogieing",
    "Boondoggling",
    "Booping",
    "Bootstrapping",
    "Brewing",
    "Bunning",
    "Burrowing",
    "Calculating",
    "Canoodling",
    "Caramelizing",
    "Cascading",
    "Catapulting",
    "Cerebrating",
    "Channeling",
    "Channelling",
    "Choreographing",
    "Churning",
    "Clauding",
    "Coalescing",
    "Cogitating",
    "Combobulating",
    "Composing",
    "Computing",
    "Concocting",
    "Considering",
    "Contemplating",
    "Cooking",
    "Crafting",
    "Creating",
    "Crunching",
    "Crystallizing",
    "Cultivating",
    "Deciphering",
    "Deliberating",
    "Determining",
    "Dilly-dallying",
    "Discombobulating",
    "Doing",
    "Doodling",
    "Drizzling",
    "Ebbing",
    "Effecting",
    "Elucidating",
    "Embellishing",
    "Enchanting",
    "Envisioning",
    "Evaporating",
    "Fermenting",
    "Fiddle-faddling",
    "Finagling",
    "Flambéing",
    "Flibbertigibbeting",
    "Flowing",
    "Flummoxing",
    "Fluttering",
    "Forging",
    "Forming",
    "Frolicking",
    "Frosting",
    "Gallivanting",
    "Galloping",
    "Garnishing",
    "Generating",
    "Gesticulating",
    "Germinating",
    "Gitifying",
    "Grooving",
    "Gusting",
    "Harmonizing",
    "Hashing",
    "Hatching",
    "Herding",
    "Honking",
    "Hullaballooing",
    "Hyperspacing",
    "Ideating",
    "Imagining",
    "Improvising",
    "Incubating",
    "Inferring",
    "Infusing",
    "Ionizing",
    "Jitterbugging",
    "Julienning",
    "Kneading",
    "Leavening",
    "Levitating",
    "Lollygagging",
    "Manifesting",
    "Marinating",
    "Meandering",
    "Metamorphosing",
    "Misting",
    "Moonwalking",
    "Moseying",
    "Mulling",
    "Mustering",
    "Musing",
    "Nebulizing",
    "Nesting",
    "Newspapering",
    "Noodling",
    "Nucleating",
    "Orbiting",
    "Orchestrating",
    "Osmosing",
    "Perambulating",
    "Percolating",
    "Perusing",
    "Philosophising",
    "Photosynthesizing",
    "Pollinating",
    "Pondering",
    "Pontificating",
    "Pouncing",
    "Precipitating",
    "Prestidigitating",
    "Processing",
    "Proofing",
    "Propagating",
    "Puttering",
    "Puzzling",
    "Quantumizing",
    "Razzle-dazzling",
    "Razzmatazzing",
    "Recombobulating",
    "Reticulating",
    "Roosting",
    "Ruminating",
    "Sautéing",
    "Scampering",
    "Schlepping",
    "Scurrying",
    "Seasoning",
    "Shenaniganing",
    "Shimmying",
    "Simmering",
    "Skedaddling",
    "Sketching",
    "Slithering",
    "Smooshing",
    "Sock-hopping",
    "Spelunking",
    "Spinning",
    "Sprouting",
    "Stewing",
    "Sublimating",
    "Swirling",
    "Swooping",
    "Symbioting",
    "Synthesizing",
    "Tempering",
    "Thinking",
    "Thundering",
    "Tinkering",
    "Tomfoolering",
    "Topsy-turvying",
    "Transfiguring",
    "Transmuting",
    "Twisting",
    "Undulating",
    "Unfurling",
    "Unravelling",
    "Vibing",
    "Waddling",
    "Wandering",
    "Warping",
    "Whatchamacalliting",
    "Whirlpooling",
    "Whirring",
    "Whisking",
    "Wibbling",
    "Working",
    "Wrangling",
    "Zesting",
    "Zigzagging",
]

#: Per-tool "cute message" lists. Each list is a hand-picked slice of
#: `ALL_SPINNER_VERBS` (no invented words) chosen to feel apt for the
#: tool's domain. Fall through to `ALL_SPINNER_VERBS` for unknown tools
#: via `pick_tool_message()`.
TOOL_MESSAGES: dict[str, list[str]] = {
    "bash": ["Actioning", "Doing", "Effecting", "Processing", "Working", "Wrangling", "Computing"],
    "read": ["Perusing", "Deciphering", "Considering", "Contemplating", "Inferring", "Musing"],
    "write": ["Composing", "Crafting", "Creating", "Sketching", "Forging", "Generating", "Doodling"],
    "edit": ["Embellishing", "Tinkering", "Tempering", "Whisking", "Smooshing", "Forming"],
    "grep": ["Spelunking", "Deciphering", "Perusing", "Scampering", "Scurrying"],
    "glob": ["Burrowing", "Wandering", "Meandering", "Herding", "Gallivanting"],
    "git": ["Gitifying", "Orchestrating", "Hatching", "Forging", "Propagating"],
    "web_search": ["Perusing", "Spelunking", "Wandering", "Orbiting", "Scampering"],
    "web_fetch": ["Perusing", "Osmosing", "Infusing", "Propagating"],
    "monitor": ["Orbiting", "Whirring", "Percolating", "Flowing", "Fluttering"],
    "task": ["Orchestrating", "Choreographing", "Herding", "Coalescing"],
    "mcp": ["Channeling", "Channelling", "Infusing", "Propagating"],
    "image": ["Envisioning", "Imagining", "Perusing", "Contemplating"],
    "clipboard": ["Smooshing", "Cascading", "Swirling", "Flowing"],
    "notebook": ["Noodling", "Ideating", "Cogitating", "Crafting"],
    "thinking": [
        "Thinking",
        "Pondering",
        "Contemplating",
        "Cogitating",
        "Musing",
        "Ruminating",
        "Deliberating",
        "Mulling",
        "Considering",
        "Reticulating",
    ],
}


def pick_tool_message(tool_name: str, seed: int | None = None) -> str:
    """Choose a cute verb for *tool_name*.

    Falls back to `ALL_SPINNER_VERBS` for unknown tools. When *seed* is
    provided, selection is deterministic (useful for tests).
    """
    bucket = TOOL_MESSAGES.get(tool_name.lower(), ALL_SPINNER_VERBS)
    if not bucket:
        bucket = ALL_SPINNER_VERBS
    if seed is not None:
        return bucket[seed % len(bucket)]
    return random.choice(bucket)


# --------------------------------------------------------------------------- #
#  Formatting helpers — mirror `utils/format.ts` shape
# --------------------------------------------------------------------------- #


def _format_tokens(count: int) -> str:
    """upstream's `formatTokens`: compact notation with lowercase suffix.

    Mirrors `formatNumber` → ``1321`` → ``"1.3k"``, ``900`` → ``"900"``.
    """
    if count < 1000:
        return str(count)
    if count < 1_000_000:
        thousands = count / 1000
        # one decimal, strip trailing ".0" (upstream's `.replace('.0', '')`)
        s = f"{thousands:.1f}"
        if s.endswith(".0"):
            s = s[:-2]
        return f"{s}k"
    millions = count / 1_000_000
    s = f"{millions:.1f}"
    if s.endswith(".0"):
        s = s[:-2]
    return f"{s}m"


def _format_seconds(seconds: float) -> str:
    """upstream's `formatSecondsShort`: integer seconds, `"4s"` shape."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    rem = int(seconds % 60)
    if rem == 0:
        return f"{minutes}m"
    return f"{minutes}m{rem}s"


# --------------------------------------------------------------------------- #
#  ANSI helpers — we write our own so we don't need a Rich Console to
#  render the single-line "✦ Thinking · 4s · ↑ 2.1k tok · esc" row.
# --------------------------------------------------------------------------- #


def _ansi_fg(hex_color: str) -> str:
    """Return a truecolor ANSI SGR prefix for *hex_color*.

    Matches what Rich emits via `Style(color=hex_color)`.
    """
    if not hex_color or not hex_color.startswith("#") or len(hex_color) != 7:
        return ""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"\x1b[38;2;{r};{g};{b}m"


_ANSI_RESET = "\x1b[0m"
_ANSI_DIM = "\x1b[2m"


# --------------------------------------------------------------------------- #
#  Thinking status-line — `render_thinking_line`
# --------------------------------------------------------------------------- #


def render_thinking_line(
    elapsed_s: float,
    token_count: int | None = None,
    *,
    verb: str = "Thinking",
    show_esc_hint: bool = True,
) -> str:
    """Render upstream's one-line thinking status: `"✦ Thinking · 4s · ↑ 2.1k tok · esc"`.

    Mirrors the shape produced by `BriefSpinner` + `SpinnerAnimationRow` in
    upstream's `Spinner.tsx`: leading sparkle (`✦`), verb, elapsed time, optional
    token counter (with the upward arrow upstream uses for output tokens), then
    the dim `esc` interrupt hint.

    Returns a raw ANSI string — callers can `print()` it directly. Use this
    shape anywhere you'd otherwise write a plain "thinking..." line.
    """
    brand = _ansi_fg(_BRAND)
    dim = _ansi_fg(_TEXT_TERTIARY)
    body = _ansi_fg(_TEXT_PRIMARY)

    parts: list[str] = []
    parts.append(f"{brand}{THINKING_GLYPH}{_ANSI_RESET}")
    parts.append(f"{body}{verb}{_ANSI_RESET}")
    parts.append(f"{dim}·{_ANSI_RESET}")
    parts.append(f"{dim}{_format_seconds(elapsed_s)}{_ANSI_RESET}")
    if token_count is not None:
        parts.append(f"{dim}·{_ANSI_RESET}")
        parts.append(f"{dim}↑ {_format_tokens(token_count)} tok{_ANSI_RESET}")
    if show_esc_hint:
        parts.append(f"{dim}·{_ANSI_RESET}")
        parts.append(f"{dim}esc{_ANSI_RESET}")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
#  Tool-use loader — `render_tool_loader`
# --------------------------------------------------------------------------- #


def _pick_frame(elapsed_s: float, frames: list[str], fps: int = 20) -> str:
    """Pick a frame deterministically based on elapsed time.

    upstream's `SpinnerAnimationRow` ticks on a ~50 ms animation clock — we
    default to the same cadence (20 fps ≈ 50 ms/frame).
    """
    if not frames:
        return ""
    frame_idx = int(elapsed_s * fps) % len(frames)
    return frames[frame_idx]


def render_tool_loader(
    tool_name: str,
    context: str,
    elapsed_s: float,
    *,
    message: str | None = None,
    is_error: bool = False,
    is_done: bool = False,
) -> RenderableType:
    """Render upstream's inline "running <tool>..." block.

    Shape (from upstream's `ToolUseLoader.tsx` + `AssistantToolUseMessage.tsx`)::

        ● ToolName(context)   <spinner-frame>
          ⎿ <cute-message>…

    * On success, the spinner becomes a green `●`; on error, a red `●`.
    * `message` defaults to `pick_tool_message(tool_name)` if not provided.

    Returned as a Rich renderable (`Group`) so callers can `console.print(...)`
    it, embed in a panel, etc.
    """
    if message is None:
        # Use a deterministic seed (0) when called without a message so repeated
        # renders during a single tool call don't jitter between cute verbs.
        message = pick_tool_message(tool_name, seed=0)

    # Bullet color: running=brand, ok=success, err=danger, blinking while
    # running (we approximate via the spinner frame cadence rather than the
    # useBlink hook — plain library, no timers).
    if is_error:
        dot_style = _DANGER
    elif is_done:
        dot_style = _SUCCESS
    else:
        dot_style = _BRAND

    header = Text()
    header.append("● ", style=dot_style)
    header.append(tool_name, style=f"bold {_TEXT_PRIMARY}")
    if context:
        header.append("(", style=_TEXT_SECONDARY)
        header.append(context, style=_TEXT_SECONDARY)
        header.append(")", style=_TEXT_SECONDARY)

    if not is_done and not is_error:
        frame = _pick_frame(elapsed_s, SPINNER_FRAMES)
        header.append(f"   {frame}", style=_BRAND)
        header.append(f"  {_format_seconds(elapsed_s)}", style=_TEXT_TERTIARY)

    detail = Text()
    # upstream's tree-branch joiner (⎿) comes from `constants/figures.js`.
    detail.append("  ⎿ ", style=_TEXT_TERTIARY)
    detail.append(f"{message}…", style=_TEXT_SECONDARY)

    return Group(header, detail)


# --------------------------------------------------------------------------- #
#  Bash progress — `render_bash_progress`
# --------------------------------------------------------------------------- #


def render_bash_progress(
    command: str,
    elapsed_s: float,
    output_lines: int,
) -> RenderableType:
    """Render upstream's `BashModeProgress` block.

    Shape (from upstream's `BashModeProgress.tsx` + `ShellProgressMessage`):

        $ <command>
        ⎿ running · 12s · 42 lines so far

    The actual upstream component wraps a streaming `ShellProgressMessage` that
    renders live stdout. The Nellie port is a pure renderer — callers feed
    the (already-captured) `output_lines` count.
    """
    header = Text()
    header.append("$ ", style=_BRAND)
    header.append(command, style=f"bold {_TEXT_PRIMARY}")

    frame = _pick_frame(elapsed_s, BRAILLE_FRAMES)
    status = Text()
    status.append("  ⎿ ", style=_TEXT_TERTIARY)
    status.append(f"{frame} ", style=_BRAND)
    status.append("running", style=_WARNING)
    status.append(" · ", style=_TEXT_TERTIARY)
    status.append(_format_seconds(elapsed_s), style=_TEXT_SECONDARY)
    status.append(" · ", style=_TEXT_TERTIARY)
    plural = "" if output_lines == 1 else "s"
    status.append(f"{output_lines} line{plural} so far", style=_TEXT_SECONDARY)

    return Group(header, status)


# --------------------------------------------------------------------------- #
#  Agent progress — `render_agent_progress_line`
# --------------------------------------------------------------------------- #


def render_agent_progress_line(
    agent_id: str,
    status: str,
    current_tool: str | None = None,
    *,
    is_last: bool = False,
    tool_use_count: int = 0,
    tokens: int | None = None,
) -> Text:
    """Render upstream's `AgentProgressLine` tree row.

    Shape (from `AgentProgressLine.tsx`)::

        ├─ agent-id · 3 tool uses · 2.1k tokens
           ⎿ Reading src/main.py

    * ``is_last`` swaps `├─` for `└─` (upstream's ``isLast ? '└─' : '├─'``).
    * ``status`` is the status string upstream shows when unresolved — e.g.
      `"Running"`, `"Initializing…"`, `"Done"`.
    """
    tree_char = "└─" if is_last else "├─"

    line = Text()
    line.append(f"   {tree_char} ", style=_TEXT_TERTIARY)
    line.append(agent_id, style=f"bold {_BRAND}")
    line.append(" · ", style=_TEXT_TERTIARY)
    plural = "use" if tool_use_count == 1 else "uses"
    line.append(f"{tool_use_count} tool {plural}", style=_TEXT_SECONDARY)
    if tokens is not None:
        line.append(" · ", style=_TEXT_TERTIARY)
        line.append(f"{_format_tokens(tokens)} tokens", style=_TEXT_SECONDARY)
    line.append("\n", style="")

    # Sub-status line (upstream's ⎿ branch).
    joiner = "   " if is_last else "│  "
    line.append(f"   {joiner}⎿  ", style=_TEXT_TERTIARY)
    if current_tool:
        line.append(f"{status} ", style=_TEXT_SECONDARY)
        line.append(f"({current_tool})", style=_TEXT_TERTIARY)
    else:
        line.append(status, style=_TEXT_SECONDARY)

    return line


# --------------------------------------------------------------------------- #
#  Coordinator status panel — `render_coordinator_status`
# --------------------------------------------------------------------------- #


def render_coordinator_status(agents: list[dict[str, Any]]) -> RenderableType:
    """Render upstream's `CoordinatorAgentStatus` panel.

    Shape (from `CoordinatorAgentStatus.tsx`)::

        ┌ Agents ─────────────────────────────────┐
        │ ● main                                    │
        │ ● agent-1 · writing · 3 tool uses         │
        │ ● agent-2 · idle                          │
        └───────────────────────────────────────────┘

    Each dict is expected to provide ``id`` (str) and ``status`` (str),
    plus optional ``current_tool``, ``tool_use_count``, ``tokens``, and
    ``is_resolved``.
    """
    if not agents:
        return Panel(
            Text("(no agents running)", style=_TEXT_TERTIARY),
            title="Agents",
            border_style=_BRAND,
            padding=(0, 1),
        )

    body = Text()
    for i, agent in enumerate(agents):
        is_resolved = bool(agent.get("is_resolved", False))
        bullet_style = _TEXT_TERTIARY if is_resolved else _BRAND

        body.append("● ", style=bullet_style)
        body.append(str(agent.get("id", "?")), style=f"bold {_TEXT_PRIMARY}")
        status_text = agent.get("status", "")
        if status_text:
            body.append(" · ", style=_TEXT_TERTIARY)
            body.append(str(status_text), style=_TEXT_SECONDARY)
        current_tool = agent.get("current_tool")
        if current_tool:
            body.append(" ", style="")
            body.append(f"({current_tool})", style=_TEXT_TERTIARY)
        tool_use_count = int(agent.get("tool_use_count", 0) or 0)
        if tool_use_count:
            plural = "use" if tool_use_count == 1 else "uses"
            body.append(" · ", style=_TEXT_TERTIARY)
            body.append(f"{tool_use_count} tool {plural}", style=_TEXT_SECONDARY)
        tokens = agent.get("tokens")
        if isinstance(tokens, int):
            body.append(" · ", style=_TEXT_TERTIARY)
            body.append(f"{_format_tokens(tokens)} tok", style=_TEXT_SECONDARY)
        if i < len(agents) - 1:
            body.append("\n", style="")

    return Panel(
        body,
        title="Agents",
        border_style=_BRAND,
        padding=(0, 1),
    )


# --------------------------------------------------------------------------- #
#  Public surface
# --------------------------------------------------------------------------- #

__all__ = [
    "THINKING_GLYPH",
    "BRAILLE_FRAMES",
    "SPINNER_FRAMES",
    "TOOL_MESSAGES",
    "ALL_SPINNER_VERBS",
    "pick_tool_message",
    "render_thinking_line",
    "render_tool_loader",
    "render_bash_progress",
    "render_agent_progress_line",
    "render_coordinator_status",
]
