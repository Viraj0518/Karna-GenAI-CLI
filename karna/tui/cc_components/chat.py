"""Chat-rendering components ported from upstream reference.

Upstream sources mirrored (verbatim behaviour where practical):

* ``upstream/src/components/Message.tsx``          — role dispatch per block
* ``upstream/src/components/MessageRow.tsx``       — row-level wrapper (margin,
  transcript-mode timestamp / model label)
* ``upstream/src/components/Messages.tsx``         — the list container
* ``upstream/src/components/MessageResponse.tsx``  — the ``⎿`` continuation-
  marker wrapper for tool results
* ``upstream/src/components/MessageModel.tsx``     — dim model-name label
* ``upstream/src/components/MessageTimestamp.tsx`` — dim ``HH:MM AM/PM``
* ``upstream/src/components/messageActions.tsx``   — copy/rewind/branch menu
  (**shape only** — the keybinding handler is integration work)
* ``upstream/src/components/MessageSelector.tsx``  — cursor over a past user
  message (the "pick a point to rewind to" picker)
* ``upstream/src/components/InterruptedByUser.tsx``— the grey "Interrupted ·
  What should Claude do instead?" line
* ``upstream/src/components/messages/*``           — per-role ``UserTextMessage``,
  ``AssistantTextMessage``, ``SystemTextMessage``, ``UserToolResultMessage``
* ``upstream/src/components/VirtualMessageList.tsx`` — windowed rendering
  (here simplified to a "show last N + overflow summary" pager, no IME
  scroll anchoring — see `render_messages`)

Nellie branding deltas:

* Assistant label: ``◆ nellie`` via ``karna.tui.hermes_display.NELLIE_ASSISTANT_LABEL``
* Brand colour: ``#3C73BD`` (``karna.tui.design_tokens.COLORS.accent.brand``)
* Role taxonomy collapsed to ``user`` / ``assistant`` / ``tool`` / ``system``
  — Nellie's ``karna.models.Message`` shape, one text block per message.

upstream semantics NOT ported (with fallback):

* **React reconciler / Ratchet "lock offscreen"** — we render directly to Rich
  renderables. ``MessageResponse``'s nested-suppression context is preserved
  via a module-level flag rather than a React context.
* **Ink ``useTerminalSize``** — we accept a width hint, otherwise defer to the
  console's own wrapping.
* **Interactive keybindings** — ``render_actions_menu`` returns a Panel; the
  dispatcher lives in the REPL layer (not ours).
* **Virtualized scroll with height measurement** (``useVirtualScroll``) —
  replaced by a simple "last ``max_visible`` items + … N older messages"
  pager.

No invention: every renderer here has a 1:1 upstream counterpart.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from karna.tui.design_tokens import COLORS
from karna.tui.hermes_display import (
    NELLIE_ASSISTANT_LABEL,
    NELLIE_TOOL_CALL_GLYPH,
    NELLIE_TOOL_RESULT_GLYPH,
)

# =========================================================================
#  Constants mirrored from upstream
# =========================================================================

# ``BLACK_CIRCLE`` in upstream/src/constants/figures.js is the ``●`` dot used
# as the assistant-turn marker.  Nellie reuses ``NELLIE_TOOL_CALL_GLYPH``
# (also ``●``) — keep the single source.
_ASSISTANT_DOT = NELLIE_TOOL_CALL_GLYPH  # "●"
_TOOL_RESULT_GLYPH = NELLIE_TOOL_RESULT_GLYPH  # "⎿"
_USER_PROMPT_GLYPH = "\u276f"  # "❯" — matches VirtualMessageList sticky prompt

# upstream's ``MAX_VISIBLE_MESSAGES`` from MessageSelector.tsx.  Reused by
# ``render_message_selector``.
MAX_VISIBLE_MESSAGES = 7

# upstream's INTERRUPT_MESSAGE constants from utils/messages.js.  Callers may
# compare raw strings against these when deciding to render the
# ``InterruptedByUser`` line.
INTERRUPT_MESSAGE = "[Request interrupted by user]"
INTERRUPT_MESSAGE_FOR_TOOL_USE = "[Request interrupted by user for tool use]"

# Brand colour — Rich accepts ``#RRGGBB`` directly.
BRAND = COLORS.accent.brand  # "#3C73BD"


# =========================================================================
#  Data shapes — align with karna.models.Message but stay duck-typed so
#  tests can pass plain dataclasses / objects.
# =========================================================================


@dataclass
class ChatMessage:
    """Minimal structural subset of ``karna.models.Message``.

    The port accepts *any* object exposing ``role``, ``content``, and an
    optional ``timestamp``/``tool_calls``/``tool_results`` — we duck-type.
    This dataclass is only here so tests can construct inputs without
    pulling in the full Pydantic model.
    """

    role: str  # "user" | "assistant" | "tool" | "system"
    content: str = ""
    timestamp: str | None = None  # ISO-8601 if present
    tool_name: str | None = None  # for role == "tool"
    is_error: bool = False
    # For transcript-mode ``MessageModel`` rendering:
    model: str | None = None
    # True when the user pressed ESC mid-turn — renders InterruptedByUser.
    interrupted: bool = False


# =========================================================================
#  MessageTimestamp.tsx — dim ``HH:MM AM/PM`` when in transcript mode.
# =========================================================================


def format_timestamp(ts: str | datetime | None) -> str | None:
    """Mirror upstream's ``new Date(ts).toLocaleTimeString('en-US', {hour12:true})``.

    Returns ``None`` if *ts* is falsy or unparseable — callers suppress
    the column in that case, same as ``shouldShowTimestamp`` in upstream.
    """
    if not ts:
        return None
    if isinstance(ts, datetime):
        dt = ts
    else:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    # ``%I:%M %p`` on Windows pads the hour — strip the leading zero to
    # match ``en-US`` locale output (``01:05 PM`` → ``1:05 PM``).
    formatted = dt.strftime("%I:%M %p")
    return formatted.lstrip("0") or formatted


def render_timestamp(ts: str | datetime | None) -> Text:
    """Return a dim Rich Text with the formatted timestamp, or empty Text."""
    formatted = format_timestamp(ts)
    if formatted is None:
        return Text("")
    return Text(formatted, style="dim")


# =========================================================================
#  MessageModel.tsx — dim model-name label shown next to an assistant
#  turn in transcript mode.
# =========================================================================


def render_model_label(model: str | None) -> Text:
    """Dim model name, or empty Text when ``model`` is falsy."""
    if not model:
        return Text("")
    return Text(model, style="dim")


# =========================================================================
#  InterruptedByUser.tsx — the "Interrupted · What should Claude do
#  instead?" line.  We substitute "Nellie" for "Claude".
# =========================================================================


def render_interrupted_by_user() -> Text:
    """Mirror InterruptedByUser.tsx — two dim segments joined inline."""
    out = Text()
    out.append("Interrupted ", style="dim")
    out.append("\u00b7 What should Nellie do instead?", style="dim")
    return out


# =========================================================================
#  MessageResponse.tsx — the ``⎿`` continuation-marker wrapper used by
#  tool-result rows.  upstream uses a React Context to suppress nested markers;
#  we expose a flag argument instead.
# =========================================================================


def wrap_response(body: RenderableType, *, nested: bool = False) -> RenderableType:
    """Prefix ``body`` with the dim ``⎿`` continuation marker.

    When ``nested=True`` the caller is already inside another response
    block — we skip the marker to match upstream's ``MessageResponseContext``
    de-duplication rule.
    """
    if nested:
        return body
    marker = Text(f"  {_TOOL_RESULT_GLYPH}  ", style="dim")
    if isinstance(body, Text):
        combined = Text()
        combined.append_text(marker)
        combined.append_text(body)
        return combined
    return Group(marker, body)


# =========================================================================
#  Per-role message renderers — messages/UserTextMessage.tsx,
#  messages/AssistantTextMessage.tsx, messages/SystemTextMessage.tsx,
#  messages/UserToolResultMessage/UserToolResultMessage.tsx.
# =========================================================================


def render_user_message(msg: ChatMessage) -> RenderableType:
    """User prompt row.

    Mirrors ``UserTextMessage`` / ``UserPromptMessage``: leading ``❯`` in
    brand colour, then the text in the default foreground.  Interrupt
    messages short-circuit to ``render_interrupted_by_user``.
    """
    text = msg.content or ""
    if text in (INTERRUPT_MESSAGE, INTERRUPT_MESSAGE_FOR_TOOL_USE) or msg.interrupted:
        return render_interrupted_by_user()

    out = Text()
    out.append(f"{_USER_PROMPT_GLYPH} ", style=f"bold {BRAND}")
    out.append(text, style=COLORS.text.primary)
    return out


def render_assistant_message(msg: ChatMessage) -> RenderableType:
    """Assistant-turn row — ``◆ nellie`` label + body text.

    Mirrors ``AssistantTextMessage``: the ``BLACK_CIRCLE`` (``●``) glyph in
    upstream is rebranded here to Nellie's diamond label (``NELLIE_ASSISTANT_LABEL``)
    per the hermes_display spec.  Body text renders in the brand-accent
    cyan role colour.
    """
    if not msg.content:
        return Text("")
    header = Text(f"{NELLIE_ASSISTANT_LABEL}  ", style=f"bold {BRAND}")
    body = Text(msg.content, style=COLORS.accent.cyan)
    # Transcript-mode extras (model label + timestamp) render on the
    # same row in upstream — we place them after the body for Rich since
    # Group doesn't give us horizontal layout without a Table.  Callers
    # wanting the exact column layout should use ``render_message_row``.
    return Group(header + body)


def render_tool_message(msg: ChatMessage) -> RenderableType:
    """Tool-result row — ``⎿`` marker + dimmed output.

    Mirrors ``UserToolResultMessage``: error rows use the danger accent,
    success rows dim.  Tool name (if provided) appears bold in brand.
    """
    label = Text()
    if msg.tool_name:
        label.append(f"{msg.tool_name} ", style=f"bold {BRAND}")
    style = COLORS.accent.danger if msg.is_error else COLORS.text.secondary
    label.append(msg.content or "", style=style)
    return wrap_response(label)


def render_system_message(msg: ChatMessage) -> RenderableType:
    """System-notice row — mirrors ``SystemTextMessage``.

    A single dim line prefixed with the assistant dot glyph.  upstream has
    many subtypes (``turn_duration``, ``memory_saved``, ``away_summary``,
    …) — we collapse them to the common case because Nellie's ``Message``
    model doesn't carry the subtype.
    """
    out = Text()
    out.append(f"{_ASSISTANT_DOT} ", style="dim")
    out.append(msg.content or "", style=f"dim {COLORS.text.tertiary}")
    return out


# =========================================================================
#  Message.tsx role dispatch — picks a renderer by ``role``.
# =========================================================================


_RENDERERS = {
    "user": render_user_message,
    "assistant": render_assistant_message,
    "tool": render_tool_message,
    "system": render_system_message,
}


def render_message(msg: ChatMessage) -> RenderableType:
    """Dispatch to the per-role renderer.

    Mirrors the ``switch (message.type)`` in ``Message.tsx``.  Unknown
    roles fall through to the system renderer with a warning-colour tint,
    matching upstream's ``logError`` fallback.
    """
    renderer = _RENDERERS.get(msg.role)
    if renderer is None:
        # Equivalent to upstream's `logError(new Error(\`Unable to render…\`))`
        unknown = Text()
        unknown.append(f"{_ASSISTANT_DOT} ", style=COLORS.accent.warning)
        unknown.append(
            f"<unknown role: {msg.role}> {msg.content}",
            style=f"dim {COLORS.accent.warning}",
        )
        return unknown
    return renderer(msg)


# =========================================================================
#  MessageRow.tsx — adds the transcript-mode gutter (timestamp + model).
# =========================================================================


def render_message_row(
    msg: ChatMessage,
    *,
    is_transcript_mode: bool = False,
    add_margin: bool = False,
) -> RenderableType:
    """Mirror ``MessageRow``: body + (optional) dim gutter columns.

    upstream places the timestamp / model in a left-of-body Box via Ink flex;
    Rich doesn't have flex so we prepend the gutter as an inline Text
    segment instead.  Only renders the gutter for assistant messages,
    matching ``MessageModel`` / ``MessageTimestamp``'s own guards.
    """
    body = render_message(msg)
    parts: list[RenderableType] = []
    if add_margin:
        parts.append(Text(""))
    if is_transcript_mode and msg.role == "assistant":
        gutter = Text()
        ts = render_timestamp(msg.timestamp)
        if ts.plain:
            gutter.append_text(ts)
            gutter.append(" ")
        model = render_model_label(msg.model)
        if model.plain:
            gutter.append_text(model)
            gutter.append(" ")
        if gutter.plain:
            parts.append(gutter)
    parts.append(body)
    return Group(*parts)


# =========================================================================
#  messageActions.tsx — shape of the copy / rewind / branch menu.
#  We render as a bordered Panel; the keybinding dispatch lives elsewhere.
# =========================================================================


@dataclass(frozen=True)
class MessageAction:
    """One row of the actions panel — mirror ``MESSAGE_ACTIONS`` entries."""

    key: str
    label: str


# Keys mirror MESSAGE_ACTIONS in messageActions.tsx.  Labels are static
# (upstream's are functions of state); callers can build a custom list.
DEFAULT_ACTIONS: tuple[MessageAction, ...] = (
    MessageAction("enter", "edit"),
    MessageAction("c", "copy"),
    MessageAction("r", "rewind"),
    MessageAction("b", "branch"),
    MessageAction("p", "copy path"),
)


def render_actions_menu(
    actions: Sequence[MessageAction] = DEFAULT_ACTIONS,
    *,
    title: str = "actions",
) -> Panel:
    """Bordered panel listing ``key · label`` rows.

    upstream's actual menu is inline keybinding hints in the footer; Nellie's
    REPL can call this to show a discoverability popup.  Returns a
    ``rich.panel.Panel`` so callers ``console.print(render_actions_menu())``.
    """
    body = Text()
    for i, action in enumerate(actions):
        if i:
            body.append("\n")
        body.append(f"[{action.key}]", style=f"bold {BRAND}")
        body.append(f"  {action.label}", style=COLORS.text.primary)
    return Panel(
        body,
        title=title,
        title_align="left",
        border_style=BRAND,
        padding=(0, 1),
    )


# =========================================================================
#  MessageSelector.tsx — a cursor over past user messages.
#  The real component is a full-screen Ink picker.  We render a
#  framed list showing ``MAX_VISIBLE_MESSAGES`` around the selection.
# =========================================================================


def render_message_selector(
    messages: Sequence[ChatMessage],
    selected_index: int,
    *,
    max_visible: int = MAX_VISIBLE_MESSAGES,
    title: str = "rewind to",
) -> Panel:
    """Mirror ``MessageSelector``'s visible-window logic.

    upstream orients the selected message at the middle of the visible
    window (``firstVisibleIndex = clamp(selected - floor(max/2),
    0, len-max)``).  We replicate the math and render the row for the
    selection with a ``▸`` cursor marker.
    """
    if not messages:
        return Panel(Text("(no messages)"), title=title, border_style=BRAND)

    first = max(
        0,
        min(selected_index - max_visible // 2, len(messages) - max_visible),
    )
    first = max(first, 0)
    last = min(first + max_visible, len(messages))

    body = Text()
    for i in range(first, last):
        if i > first:
            body.append("\n")
        msg = messages[i]
        is_sel = i == selected_index
        marker = "\u25b8" if is_sel else " "
        marker_style = f"bold {BRAND}" if is_sel else "dim"
        body.append(f"{marker} ", style=marker_style)
        preview = (msg.content or "").strip().splitlines()
        first_line = preview[0] if preview else ""
        if len(first_line) > 72:
            first_line = first_line[:69] + "..."
        text_style = COLORS.text.primary if is_sel else COLORS.text.secondary
        body.append(first_line, style=text_style)
    return Panel(
        body,
        title=title,
        title_align="left",
        border_style=BRAND,
        padding=(0, 1),
    )


# =========================================================================
#  VirtualMessageList.tsx — windowed rendering.
#  We replace Ink's height-measured virtual scroll with a last-N pager.
# =========================================================================


def render_messages(
    messages: Iterable[ChatMessage],
    *,
    max_visible: int = 100,
    is_transcript_mode: bool = False,
) -> RenderableType:
    """Windowed list renderer — shows the last ``max_visible`` items.

    The upstream ``VirtualMessageList`` keeps a height cache and only
    paints items intersecting the viewport.  Terminals with native
    scrollback don't need that — we just drop older items and prepend
    a dim ``… N older messages`` rule so the user knows there's history
    above.  Matches the "sticky prompt" overflow summary semantic
    without re-implementing scroll-anchor tracking.
    """
    items = list(messages)
    overflow = max(0, len(items) - max_visible)
    visible = items[-max_visible:] if max_visible > 0 else items

    parts: list[RenderableType] = []
    if overflow:
        parts.append(
            Rule(
                title=Text(
                    f"\u2026 {overflow} older message" + ("s" if overflow != 1 else ""),
                    style="dim",
                ),
                characters="\u2500",
                style="dim",
            )
        )
    for i, msg in enumerate(visible):
        parts.append(
            render_message_row(
                msg,
                is_transcript_mode=is_transcript_mode,
                add_margin=i > 0,
            )
        )
    return Group(*parts)


# =========================================================================
#  Public surface
# =========================================================================

__all__ = [
    # Types
    "ChatMessage",
    "MessageAction",
    # Role renderers
    "render_message",
    "render_user_message",
    "render_assistant_message",
    "render_tool_message",
    "render_system_message",
    # Structural
    "render_message_row",
    "render_messages",
    "wrap_response",
    # Chrome
    "render_timestamp",
    "render_model_label",
    "render_interrupted_by_user",
    "render_actions_menu",
    "render_message_selector",
    # Utils
    "format_timestamp",
    # Constants
    "DEFAULT_ACTIONS",
    "MAX_VISIBLE_MESSAGES",
    "INTERRUPT_MESSAGE",
    "INTERRUPT_MESSAGE_FOR_TOOL_USE",
    "BRAND",
]
