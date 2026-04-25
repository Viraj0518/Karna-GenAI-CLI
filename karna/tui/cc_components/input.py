"""Input primitives ported from upstream TUI.

Mirrors the behaviour of upstream's ``VimTextInput.tsx`` (mode state machine, key
tables), ``ScrollKeybindingHandler.tsx`` (PgUp/PgDn/Home/End on a scrollable
view), ``ConfigurableShortcutHint.tsx`` (the "<shortcut> to <action>" chip
that sits above the input), and ``ClickableImageRef.tsx`` (OSC-8 hyperlinked
image references) — but re-implemented on top of prompt_toolkit + rich so
the rest of Nellie's TUI stack can consume them.

Design notes
------------
* Library only. Nothing here touches the global app, spawns coroutines, or
  wires into the REPL. Callers pass in a ``PromptSession`` / ``Buffer`` /
  ``Application`` and get back a configured ``KeyBindings`` object.
* The vim layer is a thin contract: it stores mode state on a plain object
  and emits transitions as callbacks. prompt_toolkit ships a full vi-mode
  implementation, so on production paths callers should prefer
  :mod:`karna.tui.vim`. This class exists so unit tests (and embedded text
  widgets that don't use a full ``PromptSession``) can still get mode
  tracking.
* The configurable-shortcut-hint follows upstream's format: ``<kbd> <action>``
  with an optional parens wrapper and bolding. upstream pulls the actual
  keybinding string from the user's keybindings.json via
  ``useShortcutDisplay`` — we accept the resolved string directly.
* Clickable image refs emit OSC-8 hyperlinks (``\\x1b]8;;<url>\\x07...\\x1b]8;;\\x07``).
  On terminals that don't speak OSC-8, the fallback is the bracketed label
  without the hyperlink envelope.

Runtime gaps
------------
* upstream's ``useVimInput`` hook is a ~900-line state machine implementing
  counted motions (``5dd``), text objects (``diw``, ``da"``), marks, and
  registers. The Python port intentionally stops at mode tracking +
  insert/normal/visual transitions — prompt_toolkit's vi implementation
  handles the rest. -> promote to :mod:`karna.tui.vim` for full parity.
* ``BaseTextInput`` binds clipboard-image paste (``useClipboardImageHint``).
  Not ported; requires a clipboard-sampler integration.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from rich.text import Text

from karna.tui.design_tokens import COLORS

try:  # prompt_toolkit is optional at import time (for test envs without it)
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys

    _HAS_PT = True
except ImportError:  # pragma: no cover
    Buffer = Any  # type: ignore[assignment,misc]
    KeyBindings = Any  # type: ignore[assignment,misc]
    Keys = Any  # type: ignore[assignment,misc]
    _HAS_PT = False


# --------------------------------------------------------------------------- #
#  VimTextInput — mode state machine
# --------------------------------------------------------------------------- #


class VimMode(str, Enum):
    """Vim mode names, matching upstream's ``VimInputMode`` type (INSERT/NORMAL/VISUAL)."""

    INSERT = "insert"
    NORMAL = "normal"
    VISUAL = "visual"


# The printable ASCII keys that trigger a mode change out of normal mode.
# Matches the bindings in upstream's ``useVimInput.ts`` normal-mode table.
_NORMAL_TO_INSERT_KEYS: frozenset[str] = frozenset(
    {
        "i",  # insert before cursor
        "I",  # insert at start of line
        "a",  # append after cursor
        "A",  # append at end of line
        "o",  # open line below
        "O",  # open line above
        "s",  # substitute char
        "S",  # substitute line
        "c",  # change (enters insert after motion — handled as prefix in upstream)
        "C",  # change to end of line
    }
)

_NORMAL_TO_VISUAL_KEYS: frozenset[str] = frozenset({"v", "V"})


class VimTextInput:
    """prompt_toolkit ``Buffer`` wrapper exposing upstream-compatible vim mode state.

    This class does *not* try to re-implement prompt_toolkit's vi engine. It
    tracks ``mode`` (INSERT / NORMAL / VISUAL) and exposes a small transition
    API — enough for tests, for embedded widgets that don't run a full
    ``PromptSession`` (e.g. modal dialogs), and for rendering mode indicators.

    Parameters
    ----------
    buffer:
        Underlying prompt_toolkit ``Buffer``. Optional at construction time so
        the class can be instantiated in tests without prompt_toolkit.
    initial_mode:
        Starting mode. upstream defaults to INSERT.
    on_mode_change:
        Callback invoked with the new :class:`VimMode` whenever the mode
        transitions. Mirrors upstream's ``onModeChange`` prop.
    """

    def __init__(
        self,
        buffer: Optional["Buffer"] = None,
        *,
        initial_mode: VimMode = VimMode.INSERT,
        on_mode_change: Optional[Callable[[VimMode], None]] = None,
    ) -> None:
        self._buffer = buffer
        self._mode: VimMode = initial_mode
        self._on_mode_change = on_mode_change

    # -- mode accessors -------------------------------------------------- #

    @property
    def mode(self) -> VimMode:
        return self._mode

    @property
    def buffer(self) -> Optional["Buffer"]:
        return self._buffer

    def set_mode(self, new_mode: VimMode) -> None:
        """Transition to ``new_mode`` and fire the change callback."""
        if new_mode == self._mode:
            return
        self._mode = new_mode
        if self._on_mode_change is not None:
            self._on_mode_change(new_mode)

    # -- transition helpers mapped to upstream's useVimInput table ------------- #

    def handle_key(self, key: str) -> bool:
        """Process a single printable key in the current mode.

        Returns ``True`` if the key caused a mode transition, ``False``
        otherwise (caller should then feed the key to the underlying buffer).
        Mirrors the subset of ``useVimInput`` we need for mode tracking.
        """
        if self._mode == VimMode.INSERT:
            # Only Esc exits insert mode; printable keys pass through.
            return False

        if self._mode == VimMode.NORMAL:
            if key in _NORMAL_TO_INSERT_KEYS:
                self.set_mode(VimMode.INSERT)
                return True
            if key in _NORMAL_TO_VISUAL_KEYS:
                self.set_mode(VimMode.VISUAL)
                return True
            return False

        if self._mode == VimMode.VISUAL:
            if key == "\x1b" or key == "esc":
                self.set_mode(VimMode.NORMAL)
                return True
            if key in _NORMAL_TO_INSERT_KEYS:
                self.set_mode(VimMode.INSERT)
                return True
            return False

        return False  # pragma: no cover - exhaustive

    def handle_escape(self) -> None:
        """``Esc`` in upstream returns INSERT/VISUAL -> NORMAL; no-op in NORMAL."""
        if self._mode in (VimMode.INSERT, VimMode.VISUAL):
            self.set_mode(VimMode.NORMAL)

    # -- prompt_toolkit integration ------------------------------------- #

    def install(self, kb: "KeyBindings") -> "KeyBindings":
        """Layer the mode transitions onto an existing ``KeyBindings``.

        prompt_toolkit's native ``vi_mode=True`` covers cursor/motion — this
        just forwards the mode-change callback so external UI (status line,
        mode pill) stays in sync.
        """
        if not _HAS_PT:  # pragma: no cover
            raise RuntimeError("prompt_toolkit is required to install vim bindings")

        @kb.add(Keys.Escape, eager=True)
        def _on_escape(event: Any) -> None:  # pragma: no cover - wired via pt
            self.handle_escape()

        return kb


# --------------------------------------------------------------------------- #
#  ScrollKeybindings — PgUp/PgDn/Home/End on a scrollable buffer
# --------------------------------------------------------------------------- #


class ScrollKeybindings:
    """Install scroll shortcuts on a prompt_toolkit ``BufferControl`` / ``Buffer``.

    Matches the shortcuts handled by upstream's ``ScrollKeybindingHandler.tsx``:

    ============  ===================================
    Key           Effect
    ============  ===================================
    PgUp          scroll up one page (20 lines)
    PgDn          scroll down one page (20 lines)
    Home          jump to first line
    End           jump to last line
    Ctrl+Home     jump to first line (alt binding)
    Ctrl+End      jump to last line (alt binding)
    ============  ===================================

    The instance exposes an ``install(kb)`` method that mutates a
    ``KeyBindings`` object and also returns it — so it can be chained with
    ``VimTextInput.install``.
    """

    PAGE_LINES: int = 20

    def __init__(self, on_scroll: Optional[Callable[[int], None]] = None) -> None:
        """``on_scroll(delta_lines)`` fires after each scroll action.

        Positive delta = down, negative = up, ``float('inf')`` / ``-inf``
        for Home/End jumps (matching how upstream encodes "jump to edge" events).
        """
        self._on_scroll = on_scroll

    def install(self, kb: "KeyBindings") -> "KeyBindings":
        if not _HAS_PT:  # pragma: no cover
            raise RuntimeError("prompt_toolkit is required to install scroll bindings")

        cb = self._on_scroll or (lambda _delta: None)

        @kb.add(Keys.PageUp)
        def _pg_up(event: Any) -> None:  # pragma: no cover - wired via pt
            cb(-self.PAGE_LINES)

        @kb.add(Keys.PageDown)
        def _pg_down(event: Any) -> None:  # pragma: no cover - wired via pt
            cb(self.PAGE_LINES)

        @kb.add(Keys.Home)
        def _home(event: Any) -> None:  # pragma: no cover - wired via pt
            cb(float("-inf"))

        @kb.add(Keys.End)
        def _end(event: Any) -> None:  # pragma: no cover - wired via pt
            cb(float("inf"))

        @kb.add(Keys.ControlHome)
        def _c_home(event: Any) -> None:  # pragma: no cover - wired via pt
            cb(float("-inf"))

        @kb.add(Keys.ControlEnd)
        def _c_end(event: Any) -> None:  # pragma: no cover - wired via pt
            cb(float("inf"))

        return kb

    def dispatch(self, key: str) -> Optional[int | float]:
        """Return the scroll delta for ``key`` (for tests / headless callers).

        Returns ``None`` when ``key`` isn't a scroll shortcut we handle.
        """
        mapping: dict[str, int | float] = {
            "pageup": -self.PAGE_LINES,
            "pagedown": self.PAGE_LINES,
            "home": float("-inf"),
            "end": float("inf"),
            "c-home": float("-inf"),
            "c-end": float("inf"),
        }
        return mapping.get(key.lower())


# --------------------------------------------------------------------------- #
#  attach_configurable_shortcut_hint — the "Ctrl-O to expand" line
# --------------------------------------------------------------------------- #


def _format_shortcut_hint(
    hints: Iterable[tuple[str, str]],
    *,
    parens: bool = False,
    bold: bool = False,
    separator: str = "  \u00b7  ",
) -> Text:
    """Build a Rich ``Text`` matching upstream's ``KeyboardShortcutHint`` rendering.

    Each hint is rendered as ``<kbd> <action>`` with the keybinding in the
    cyan accent and the action text dim. Hints are joined by ``separator``
    (defaults to upstream's centered-dot).
    """
    txt = Text()
    for i, (key, action) in enumerate(hints):
        if i > 0:
            txt.append(separator, style=COLORS.text.tertiary)
        if parens:
            txt.append("(", style=COLORS.text.tertiary)
        key_style = f"{COLORS.accent.cyan}"
        if bold:
            key_style += " bold"
        txt.append(key, style=key_style)
        txt.append(" ", style=COLORS.text.tertiary)
        txt.append(action, style=COLORS.text.secondary)
        if parens:
            txt.append(")", style=COLORS.text.tertiary)
    return txt


def attach_configurable_shortcut_hint(
    app: Any,
    hints: list[tuple[str, str]],
    *,
    parens: bool = False,
    bold: bool = False,
) -> Text:
    """Attach a one-line hint strip to ``app``.

    ``hints`` is a list of ``(keybinding, action)`` tuples, e.g.
    ``[("Ctrl-O", "expand"), ("Esc", "interrupt"), ("Ctrl-C", "exit")]``.

    ``app`` is accepted as a generic object so this function can be used with
    prompt_toolkit ``Application`` instances, Rich ``Live`` displays, or any
    other target that exposes either:

    * ``add_bottom_toolbar(renderable)``  — preferred
    * ``bottom_toolbar = renderable``      — assigned attribute
    * otherwise: returned ``Text`` for the caller to print

    Returns the rendered :class:`rich.text.Text` regardless — callers that
    don't need live attachment can simply discard ``app=None`` and print it.
    """
    rendered = _format_shortcut_hint(hints, parens=parens, bold=bold)
    if app is None:
        return rendered
    if hasattr(app, "add_bottom_toolbar"):
        try:
            app.add_bottom_toolbar(rendered)
        except Exception:  # pragma: no cover - best effort
            pass
    else:
        try:
            setattr(app, "bottom_toolbar", rendered)
        except Exception:  # pragma: no cover - attrs may be frozen
            pass
    return rendered


# --------------------------------------------------------------------------- #
#  render_clickable_image_ref — OSC-8 hyperlink to a local image
# --------------------------------------------------------------------------- #


def _path_to_file_url(path: Path) -> str:
    """Python port of Node's ``pathToFileURL``. Handles Windows drive letters."""
    p = Path(path).resolve()
    # ``Path.as_uri`` handles UNC and drive letters correctly on all OSes,
    # and raises for relative paths (which we've already resolved).
    return p.as_uri()


def render_clickable_image_ref(
    path: Path,
    *,
    image_id: Optional[int] = None,
    background: Optional[str] = None,
    selected: bool = False,
) -> Text:
    """Render a ``[Image #N]`` clickable reference (OSC-8 hyperlink).

    Mirrors upstream's ``ClickableImageRef.tsx``:

    * Text is ``[Image #<id>]`` — ``id`` defaults to a hash of the path so
      the label is stable across runs.
    * The link target is the local ``file://`` URL for ``path``.
    * ``selected=True`` reverses colors (upstream's ``inverse=isSelected``) and
      bolds the label.

    Rich renders OSC-8 natively via the ``link`` style; terminals that don't
    support it fall back to the styled label.
    """
    if image_id is None:
        image_id = abs(hash(str(path))) % 10_000
    label = f"[Image #{image_id}]"

    try:
        url = _path_to_file_url(path)
    except Exception:
        url = None  # fallback: styled text with no link

    style_parts: list[str] = []
    if background:
        style_parts.append(f"on {background}")
    if selected:
        style_parts.append("reverse bold")
    if url:
        style_parts.append(f"link {url}")
    style = " ".join(style_parts) or COLORS.accent.cyan

    return Text(label, style=style)


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #


__all__ = [
    "VimMode",
    "VimTextInput",
    "ScrollKeybindings",
    "attach_configurable_shortcut_hint",
    "render_clickable_image_ref",
]
