"""Task list + compact summary + session chrome, ported from Claude Code.

Mirrors the visuals of CC's `TaskListV2.tsx`, `CompactSummary.tsx`,
`ResumeTask.tsx`, `SessionPreview.tsx`, `SessionBackgroundHint.tsx` and
the `agents/` cluster — but skinned to Nellie's palette. Brand color is
`#3C73BD`; status glyphs match the spec:

    pending      ○   (dim)
    in_progress  ◐   (brand, bold subject)
    completed    ●   (success, strikethrough subject)
    deleted      ×   (dim, strikethrough subject)

Design notes
------------
* Library only. No Rich `Live`, no polling, no side effects. Every
  renderer returns a Rich `RenderableType` (or `Text | None`) that the
  caller can pipe into an existing console.
* CC's `TaskListV2` sorts by recently-completed + in-progress + pending +
  older-completed. We preserve that priority here so the rendered list
  matches CC byte-for-byte up to the glyph mapping (CC uses figures.tick
  / squareSmallFilled; we use the spec's ○ / ◐ / ● / ×).
* Owner tags render as `@owner` (CC uses `(@owner)`). We keep the `@`
  prefix but drop the parens — CC only shows the parens inside a larger
  status line, which we don't reproduce since our rows are standalone.
* Subtitle (dim per-task) captures `blockedBy` (ids) or any extra
  freeform `subtitle` field callers may set on the dict.

Runtime gaps
------------
Hooks the caller must still wire up when promoting these renderers into
the main REPL loop:

* `render_compact_summary` needs Nellie's compaction subsystem to supply
  `before_tokens` / `after_tokens` / `messages_removed` / `summary_text`.
  CC computes these inside `CompactSummary.tsx` from a
  `NormalizedUserMessage.summarizeMetadata` blob. -> requires
  integration with Nellie's `auto_compact` pipeline
* `render_session_preview` expects a list of already-materialised
  message dicts. CC calls `loadFullLog(log)` to fetch lite logs on
  demand; the equivalent in Nellie is `session_picker.load_session` but
  we don't call it from this library. -> requires session-storage
  integration in the caller
* `render_session_background_hint` is a pure renderer — the actual
  background-task plumbing (Ctrl+B double-press, `hasForegroundTasks`)
  lives elsewhere. CC keys this off `AppState`. -> requires a task
  manager to surface the `active_count`
* `render_agent_list` renders a snapshot; CC's real-time updates flow
  through `useAppState(s => s.agents)`. -> requires agent-registry
  integration
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.text import Text

from karna.tui.design_tokens import COLORS

# --------------------------------------------------------------------------- #
#  Glyph vocabulary — locked to the spec.
# --------------------------------------------------------------------------- #

GLYPH_PENDING = "\u25cb"  # ○
GLYPH_IN_PROGRESS = "\u25d0"  # ◐
GLYPH_COMPLETED = "\u25cf"  # ●
GLYPH_DELETED = "\u00d7"  # ×

# CC parity glyphs (re-used from status.py's vocabulary).
GLYPH_SEP = "\u00b7"  # ·  — between header segments
GLYPH_SPARKLE = "\u2726"  # ✦ — compact-summary "thinking" marker
GLYPH_ARROW = "\u2192"  # → — "before → after" in compact summary
GLYPH_BRANCH = "\u23bf"  # ⎿ — dim subtitle joiner (matches CC's tree)
GLYPH_DOT = "\u2022"  # • — agent-list bullet
# Default in_progress marker for agents without an explicit status
GLYPH_AGENT_ACTIVE = "\u25c9"  # ◉

STATUS_GLYPHS: Mapping[str, str] = {
    "pending": GLYPH_PENDING,
    "in_progress": GLYPH_IN_PROGRESS,
    "completed": GLYPH_COMPLETED,
    "deleted": GLYPH_DELETED,
}

# Sort priority — CC's TaskListV2 prefers in-progress then pending then
# completed. "deleted" sinks below everything.
_STATUS_ORDER: Mapping[str, int] = {
    "in_progress": 0,
    "pending": 1,
    "completed": 2,
    "deleted": 3,
}


# --------------------------------------------------------------------------- #
#  Palette
# --------------------------------------------------------------------------- #

BRAND = COLORS.accent.brand  # "#3C73BD"
MUTED = COLORS.text.secondary
SUBTLE = COLORS.text.tertiary
DISABLED = COLORS.text.disabled
SUCCESS = COLORS.accent.success
WARNING = COLORS.accent.warning
DANGER = COLORS.accent.danger
CYAN = COLORS.accent.cyan
THINKING = COLORS.accent.thinking


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #


def _format_tokens(n: int) -> str:
    """Matches status.py's `_format_tokens` — '12.3k', '1.2M'."""
    if n < 1_000:
        return str(n)
    if n < 1_000_000:
        v = n / 1_000.0
        s = f"{v:.1f}".rstrip("0").rstrip(".")
        return f"{s}k"
    v = n / 1_000_000.0
    s = f"{v:.1f}".rstrip("0").rstrip(".")
    return f"{s}M"


def _status_color(status: str) -> str:
    """Pick a hex color for a task status."""
    if status == "completed":
        return SUCCESS
    if status == "in_progress":
        return BRAND
    if status == "deleted":
        return DISABLED
    return SUBTLE  # pending


def _sort_tasks(tasks: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """CC priority: in_progress > pending > completed > deleted, stable by id."""

    def key(t: Mapping[str, Any]) -> tuple[int, Any]:
        status = t.get("status", "pending")
        rank = _STATUS_ORDER.get(status, 99)
        raw = t.get("id", "")
        try:
            ordering: Any = (0, int(raw))
        except (TypeError, ValueError):
            ordering = (1, str(raw))
        return (rank, ordering)

    return sorted(tasks, key=key)


# --------------------------------------------------------------------------- #
#  1. render_task_list
# --------------------------------------------------------------------------- #


def render_task_list(tasks: list[dict]) -> RenderableType:
    """Checkbox-style task list, matching CC's TaskListV2.

    Each task dict supports::

        {
            "id": str,
            "subject": str,
            "status": "pending" | "in_progress" | "completed" | "deleted",
            "owner": str | None,          # optional @handle
            "blockedBy": list[str] | None,  # optional blocker task ids
            "subtitle": str | None,       # optional extra dim caption
        }

    Visual layout (one row per task)::

        ◐ Wire up auto-compact pipeline  @alpha
          ⎿ blocked by #42

    Returns a Rich `Group` of `Text` rows (never a Panel — CC renders the
    list inline). Empty lists return a dim placeholder so callers can
    still safely print the result.
    """
    if not tasks:
        placeholder = Text()
        placeholder.append(f"{GLYPH_PENDING} ", style=f"dim {SUBTLE}")
        placeholder.append("No tasks yet", style=f"dim {SUBTLE}")
        return placeholder

    rows: list[RenderableType] = []
    for task in _sort_tasks(tasks):
        status = str(task.get("status", "pending"))
        glyph = STATUS_GLYPHS.get(status, GLYPH_PENDING)
        color = _status_color(status)
        subject = str(task.get("subject", ""))
        owner = task.get("owner")
        blocked_by = task.get("blockedBy") or []
        subtitle = task.get("subtitle")

        line = Text()
        # Glyph — colored per status
        if status == "deleted":
            line.append(f"{glyph} ", style=f"dim {color}")
        else:
            line.append(f"{glyph} ", style=color)

        # Subject — bold for in_progress, strikethrough for completed/deleted,
        # dim for completed/deleted/blocked. CC's exact formula.
        is_completed = status == "completed"
        is_in_progress = status == "in_progress"
        is_deleted = status == "deleted"
        is_blocked = bool(blocked_by)

        subject_style_parts: list[str] = []
        if is_in_progress:
            subject_style_parts.append("bold")
        if is_completed or is_deleted:
            subject_style_parts.append("strikethrough")
        if is_completed or is_deleted or is_blocked:
            subject_style_parts.append(f"dim {MUTED}")
        elif is_in_progress:
            subject_style_parts.append(BRAND)
        else:
            subject_style_parts.append(COLORS.text.primary)
        line.append(subject, style=" ".join(subject_style_parts))

        if owner:
            line.append("  ")
            owner_str = str(owner).lstrip("@")
            line.append(f"@{owner_str}", style=f"dim {CYAN}")

        rows.append(line)

        # Subtitle line — blockers first, then any freeform subtitle. Dim,
        # tree-branch joiner.
        sub_parts: list[str] = []
        if blocked_by:
            blockers = ", ".join(f"#{b}" for b in blocked_by)
            sub_parts.append(f"blocked by {blockers}")
        if subtitle:
            sub_parts.append(str(subtitle))
        if sub_parts:
            sub_line = Text()
            sub_line.append(f"  {GLYPH_BRANCH} ", style=f"dim {SUBTLE}")
            sub_line.append(" · ".join(sub_parts), style=f"dim {MUTED}")
            rows.append(sub_line)

    return Group(*rows)


# --------------------------------------------------------------------------- #
#  2. render_compact_summary
# --------------------------------------------------------------------------- #


def render_compact_summary(
    before_tokens: int,
    after_tokens: int,
    messages_removed: int,
    summary_text: str,
) -> RenderableType:
    """Panel shown after auto-compact — CC's `CompactSummary.tsx`.

    Visual layout::

        ┌ ✦ Auto-compact · 45k → 8k · 18 messages summarised ────
        │ <summary_text, dim>
        └─

    Matches CC's bold "Compact summary" heading plus a dim body. Token
    counts formatted with the same `formatTokens` heuristic as the
    status line.

    requires integration with Nellie's compaction pipeline so that the
    caller can supply `summary_text` (CC pulls this from
    `NormalizedUserMessage.summarizeMetadata.userContext`).
    """
    before = _format_tokens(max(0, before_tokens))
    after = _format_tokens(max(0, after_tokens))
    msg_label = "message" if messages_removed == 1 else "messages"

    title = Text()
    title.append(f"{GLYPH_SPARKLE} ", style=BRAND)
    title.append("Auto-compact", style=f"bold {BRAND}")
    title.append(f" {GLYPH_SEP} ", style=f"dim {SUBTLE}")
    title.append(f"{before} {GLYPH_ARROW} {after}", style=MUTED)
    title.append(f" {GLYPH_SEP} ", style=f"dim {SUBTLE}")
    title.append(
        f"{messages_removed} {msg_label} summarised",
        style=f"dim {MUTED}",
    )

    body = Text(summary_text.strip() if summary_text else "(no summary)", style=MUTED)

    return Panel(
        body,
        title=title,
        title_align="left",
        border_style=BRAND,
        padding=(0, 1),
    )


# --------------------------------------------------------------------------- #
#  3. render_resume_task_prompt
# --------------------------------------------------------------------------- #


def render_resume_task_prompt(last_task: dict) -> RenderableType:
    """`Resume <task>? [y/N]` — CC's ResumeTask confirmation row.

    Renders as a single-line `Text` so the caller can drop it straight
    into the prompt area. Status glyph + truncated subject + inline
    shortcut hint.
    """
    status = str(last_task.get("status", "in_progress"))
    glyph = STATUS_GLYPHS.get(status, GLYPH_IN_PROGRESS)
    color = _status_color(status)
    subject = str(last_task.get("subject", "last task")).strip() or "last task"

    t = Text()
    t.append("Resume ", style=f"bold {BRAND}")
    t.append(f"{glyph} ", style=color)
    t.append(subject, style=COLORS.text.primary)
    t.append("?", style=f"bold {BRAND}")
    t.append("  ", style=MUTED)
    t.append("[", style=f"dim {SUBTLE}")
    t.append("y", style=f"bold {SUCCESS}")
    t.append("/", style=f"dim {SUBTLE}")
    t.append("N", style=f"bold {DANGER}")
    t.append("]", style=f"dim {SUBTLE}")
    return t


# --------------------------------------------------------------------------- #
#  4. render_session_preview
# --------------------------------------------------------------------------- #


def render_session_preview(
    session_id: str,
    messages: list,
    *,
    max_messages: int = 3,
) -> RenderableType:
    """One-line-per-message preview of the last N turns.

    Matches CC's session-switcher row (`SessionPreview.tsx`): a header
    with the session id, then up to `max_messages` trimmed message rows,
    oldest-first. Each message is rendered on a single line as
    ``<role-glyph> <role>: <preview>``.

    Accepts loose `Message`-shaped inputs — either dicts with
    `{role, content}` or objects exposing those attrs. Anything missing
    is coerced to empty so the renderer never raises on half-formed
    records from `session_picker`.
    """
    header = Text()
    header.append(f"{GLYPH_DOT} ", style=BRAND)
    header.append("session ", style=f"dim {SUBTLE}")
    header.append(session_id or "(unknown)", style=f"bold {BRAND}")
    header.append(f"  {GLYPH_SEP}  ", style=f"dim {SUBTLE}")
    header.append(f"{len(messages)} message" + ("" if len(messages) == 1 else "s"),
                  style=MUTED)

    rows: list[RenderableType] = [header]

    if not messages:
        empty = Text()
        empty.append(f"  {GLYPH_BRANCH} ", style=f"dim {SUBTLE}")
        empty.append("(no messages yet)", style=f"dim {MUTED}")
        rows.append(empty)
        return Group(*rows)

    # Keep the last `max_messages`, render oldest-first so the preview
    # reads like a transcript.
    tail = messages[-max(1, max_messages):]
    for msg in tail:
        role = _get_attr(msg, "role", default="?")
        content = _get_attr(msg, "content", default="")
        text = _message_preview(content)

        role_color, role_glyph = _role_style(str(role))
        row = Text()
        row.append(f"  {role_glyph} ", style=role_color)
        row.append(f"{role}: ", style=f"bold {role_color}")
        # Truncate to a reasonable width — 80 chars feels like CC's feel.
        if len(text) > 80:
            text = text[:77] + "..."
        row.append(text, style=MUTED)
        rows.append(row)

    return Group(*rows)


def _get_attr(obj: Any, name: str, *, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _message_preview(content: Any) -> str:
    """Flatten a message's content into a single display string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.replace("\n", " ").strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text") or block.get("content") or ""
                if text:
                    parts.append(str(text))
            else:
                text = getattr(block, "text", None) or getattr(block, "content", None)
                if text:
                    parts.append(str(text))
        return " ".join(parts).replace("\n", " ").strip()
    return str(content).replace("\n", " ").strip()


def _role_style(role: str) -> tuple[str, str]:
    r = role.lower()
    if r in ("assistant", "ai", "model"):
        return BRAND, GLYPH_DOT
    if r in ("user", "human"):
        return CYAN, GLYPH_DOT
    if r in ("tool", "tool_result", "function"):
        return WARNING, GLYPH_DOT
    if r == "system":
        return THINKING, GLYPH_DOT
    return SUBTLE, GLYPH_DOT


# --------------------------------------------------------------------------- #
#  5. render_session_background_hint
# --------------------------------------------------------------------------- #


def render_session_background_hint(active_count: int) -> Optional[Text]:
    """Tiny "N sessions running in background" marker above the input.

    CC's `SessionBackgroundHint.tsx` only renders when there's a reason
    to — we mirror that: `active_count <= 0` returns `None` so the
    caller can skip rendering entirely.

    requires integration with Nellie's task manager to supply the live
    `active_count` (CC pulls it from `useAppState(hasForegroundTasks)`).
    """
    if active_count <= 0:
        return None

    label = "session" if active_count == 1 else "sessions"
    t = Text()
    t.append(f"{GLYPH_AGENT_ACTIVE} ", style=WARNING)
    t.append(f"{active_count} {label} running in background",
             style=f"dim {WARNING}")
    t.append(f"  {GLYPH_SEP}  ", style=f"dim {SUBTLE}")
    t.append("ctrl+b to toggle", style=f"dim {SUBTLE}")
    return t


# --------------------------------------------------------------------------- #
#  6. render_agent_list
# --------------------------------------------------------------------------- #


def render_agent_list(agents: list[dict]) -> RenderableType:
    """Running-agents list — status glyph + name + current tool.

    Each agent dict supports::

        {
            "name": str,              # required
            "status": "running" | "idle" | "error" | "done",
            "currentTool": str | None,  # e.g. "Bash", "Read"
            "subtitle": str | None,   # freeform dim caption
        }

    Empty list renders a dim placeholder so the caller can safely
    concat it into a dashboard.
    """
    if not agents:
        placeholder = Text()
        placeholder.append(f"{GLYPH_PENDING} ", style=f"dim {SUBTLE}")
        placeholder.append("No agents running", style=f"dim {SUBTLE}")
        return placeholder

    rows: list[RenderableType] = []
    for agent in agents:
        name = str(agent.get("name", "agent"))
        status = str(agent.get("status", "running")).lower()
        tool = agent.get("currentTool")
        subtitle = agent.get("subtitle")

        if status in ("running", "active", "in_progress"):
            glyph, color = GLYPH_IN_PROGRESS, BRAND
        elif status in ("done", "completed", "complete", "finished"):
            glyph, color = GLYPH_COMPLETED, SUCCESS
        elif status in ("error", "failed", "failing"):
            glyph, color = GLYPH_DELETED, DANGER
        elif status in ("idle", "waiting"):
            glyph, color = GLYPH_PENDING, SUBTLE
        else:
            glyph, color = GLYPH_AGENT_ACTIVE, WARNING

        row = Text()
        row.append(f"{glyph} ", style=color)
        row.append(name, style=f"bold {color}")
        if tool:
            row.append(f"  {GLYPH_SEP} ", style=f"dim {SUBTLE}")
            row.append(str(tool), style=MUTED)
        rows.append(row)

        if subtitle:
            sub = Text()
            sub.append(f"  {GLYPH_BRANCH} ", style=f"dim {SUBTLE}")
            sub.append(str(subtitle), style=f"dim {MUTED}")
            rows.append(sub)

    return Group(*rows)


__all__ = [
    "render_task_list",
    "render_compact_summary",
    "render_resume_task_prompt",
    "render_session_preview",
    "render_session_background_hint",
    "render_agent_list",
    "GLYPH_PENDING",
    "GLYPH_IN_PROGRESS",
    "GLYPH_COMPLETED",
    "GLYPH_DELETED",
    "STATUS_GLYPHS",
    "BRAND",
]
