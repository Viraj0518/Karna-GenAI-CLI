"""Picker dialogs, ported from Claude Code and skinned for Nellie.

Mirrors the visuals of CC's ``ModelPicker.tsx``, ``ThemePicker.tsx``,
``OutputStylePicker.tsx``, ``LanguagePicker.tsx`` and the shared
``CustomSelect/`` widget. Source under ``/c/cc-src/src/components/``.

Library only — no wiring into the REPL. Callers `await` one of the
``pick_*`` coroutines (or instantiate :class:`Picker` directly) and get
back the selected option id, or ``None`` on cancel.

Design
------
* Built on top of ``prompt_toolkit.Application`` with
  ``full_screen=False`` so the dialog anchors above the REPL and the
  terminal's scrollback keeps working — same pattern as
  :mod:`karna.tui.hermes_repl` (see its ``patch_stdout``/``run_async``
  usage). Nothing is printed outside the prompt_toolkit render cycle.
* Visual chrome matches CC:
    - boxed dialog with a title line on top,
    - options rendered as ``  label`` with an inline ``▸`` cursor,
    - current selection highlighted with Nellie's brand ``#3C73BD``,
    - descriptions dimmed beneath each option,
    - keyboard hints on the last line.
* Arrow keys navigate, Enter selects, Esc / Ctrl-C cancel.
* Accepts options as ``list[tuple[id, label, description]]`` — any
  hashable id works. The picker returns the id (or ``None``).

CC behaviours intentionally omitted (see the report at the bottom of
this file's docstring in the commit message):

* **Live theme preview** — CC's ``ThemePicker`` previews the theme via
  ``setPreviewTheme`` on focus change and reverts on cancel. Nellie has
  no reactive theme system yet, so selection is final; no preview.
* **Fast-mode banner + effort cycling** — CC's ``ModelPicker`` shows a
  Fast-Mode notice and lets ``Shift-Tab``/``Tab`` cycle effort levels.
  Nellie's providers don't plumb effort through yet, so the model
  picker shows provider/context/max-output columns only.
* **External-editor `ctrl+g` on input options** and **image-paste on
  input rows** — CC's `select.tsx` supports these for composition-style
  inputs; our pickers are read-only selects, so they're not applicable.
* **Search/filter typing** — CC's ``/model`` picker supports free-text
  filtering. The legacy :mod:`karna.tui.model_picker` already provides
  that; this port mirrors CC's *dialog* flavour (keyboard navigation
  only). Search is a natural extension and is deliberately out of
  scope here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style as PTStyle

from karna.tui.design_tokens import COLORS

try:  # Optional — only needed for ``pick_model`` callers.
    from karna.models import ModelInfo  # noqa: F401
except Exception:  # pragma: no cover - import is optional at module load
    ModelInfo = Any  # type: ignore[misc,assignment]


# --------------------------------------------------------------------------- #
#  Palette — single source of truth, matches CC's Dialog/Select palette
# --------------------------------------------------------------------------- #

BRAND = COLORS.accent.brand  # #3C73BD — Nellie blue
BORDER = COLORS.border.accent
DIM = COLORS.text.tertiary
TEXT = COLORS.text.primary
MUTED = COLORS.text.secondary


# Box-drawing glyphs (match CC's design-system `Dialog` chrome).
_BOX_TL, _BOX_TR, _BOX_BL, _BOX_BR = "\u256d", "\u256e", "\u2570", "\u256f"  # ╭ ╮ ╰ ╯
_BOX_H, _BOX_V = "\u2500", "\u2502"  # ─ │
# Pointer — CC uses `figures.pointer` which resolves to "❯" on modern
# terminals (same Unicode char used by inquirer.js).
_POINTER = "\u276f"  # ❯


# --------------------------------------------------------------------------- #
#  Option tuple
# --------------------------------------------------------------------------- #


Option = tuple[str, str, str]  # (id, label, description)


def _coerce_options(options: Iterable[Any]) -> list[Option]:
    """Normalise user-supplied options into ``(id, label, description)`` tuples.

    Accepts 2-tuples (``(id, label)``) or 3-tuples. Any other arity or
    type raises ``ValueError`` early so callers see the problem before
    the Application spins up.
    """
    out: list[Option] = []
    for i, opt in enumerate(options):
        if not isinstance(opt, tuple):
            raise ValueError(f"option #{i} is not a tuple: {opt!r}")
        if len(opt) == 2:
            oid, label = opt
            desc = ""
        elif len(opt) == 3:
            oid, label, desc = opt
        else:
            raise ValueError(f"option #{i} must be 2- or 3-tuple, got {len(opt)}")
        out.append((str(oid), str(label), str(desc or "")))
    return out


# --------------------------------------------------------------------------- #
#  Picker — the base widget
# --------------------------------------------------------------------------- #


class Picker:
    """Inline select-from-list widget. CC's ``<Select>`` + ``<Dialog>``.

    Call :meth:`prompt` (it's async) to show the picker and await the
    selected option id. Returns ``None`` if the user presses Esc or
    Ctrl-C.

    The widget is built on ``prompt_toolkit.Application`` with
    ``full_screen=False``; this matches Hermes's pattern so rendering
    composes cleanly with ``patch_stdout()`` wrappers already installed
    by :mod:`karna.tui.hermes_repl`.

    Keyboard:
        ↑/↓ or k/j  navigate
        Enter        select
        Esc / Ctrl-C cancel
        Home/End     first/last
    """

    def __init__(self, *, initial_index: int = 0, visible_option_count: int = 10) -> None:
        self._initial_index = max(0, initial_index)
        self._visible = max(1, visible_option_count)

    # -- public API -------------------------------------------------------- #

    async def prompt(
        self,
        title: str,
        options: Sequence[tuple],
        *,
        initial_id: Optional[str] = None,
    ) -> Optional[str]:
        """Show the picker and return the selected option id, or ``None``.

        ``options`` is a list of 2- or 3-tuples: ``(id, label)`` or
        ``(id, label, description)``. Any hashable id works — the
        returned value is the id as-is (stringified).

        Arrow keys navigate; Enter selects; Esc / Ctrl-C cancel.
        """
        norm = _coerce_options(options)
        if not norm:
            return None

        idx = self._initial_index
        if initial_id is not None:
            for i, (oid, _, _) in enumerate(norm):
                if oid == initial_id:
                    idx = i
                    break
        idx = min(idx, len(norm) - 1)

        state: dict[str, Any] = {
            "idx": idx,
            "result": None,
            "cancelled": False,
            "_ids": [o[0] for o in norm],
        }

        kb = _build_keybindings(state, len(norm))
        control = FormattedTextControl(
            text=lambda: _render_dialog(title, norm, state["idx"], self._visible),
            focusable=True,
            show_cursor=False,
        )
        layout = Layout(HSplit([Window(control, dont_extend_height=True, height=Dimension(min=1))]))
        style = PTStyle.from_dict(
            {
                "picker.border": BORDER,
                "picker.title": f"bold {BRAND}",
                "picker.selected": f"bold {BRAND}",
                "picker.pointer": BRAND,
                "picker.option": TEXT,
                "picker.desc": f"italic {DIM}",
                "picker.hint": DIM,
            }
        )

        app: Application[Any] = Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=False,
            mouse_support=False,
            erase_when_done=True,
        )
        state["app"] = app

        try:
            await app.run_async()
        except (EOFError, KeyboardInterrupt):
            return None

        if state["cancelled"] or state["result"] is None:
            return None
        return state["result"]


# --------------------------------------------------------------------------- #
#  Rendering
# --------------------------------------------------------------------------- #


def _render_dialog(
    title: str,
    options: Sequence[Option],
    focused_idx: int,
    visible: int,
) -> FormattedText:
    """Render the whole boxed dialog as ``FormattedText`` fragments.

    CC's ``design-system/Dialog`` draws a rounded-corner box around its
    children. We emulate that chrome with Unicode box-drawing glyphs.
    """
    # Compute a scroll window so the focused option is always visible.
    n = len(options)
    start = 0
    if n > visible:
        half = visible // 2
        if focused_idx >= half:
            start = min(n - visible, focused_idx - half)
        else:
            start = 0
    end = min(n, start + visible)

    # Width = max(title, longest label+desc) + padding, bounded.
    body_width = max(len(title) + 4, *(len(_fmt_line(o, False, False)) for o in options))
    body_width = min(max(body_width, 32), 80)

    frags: list[tuple[str, str]] = []

    # --- Top border + title ------------------------------------------------
    frags.append(("class:picker.border", _BOX_TL + _BOX_H * (body_width - 2) + _BOX_TR + "\n"))
    title_pad = body_width - 2 - len(title) - 1
    frags.append(("class:picker.border", _BOX_V))
    frags.append(("class:picker.title", " " + title))
    frags.append(("", " " * max(title_pad, 0)))
    frags.append(("class:picker.border", _BOX_V + "\n"))

    # Separator under title — CC's Dialog uses a thin rule.
    frags.append(("class:picker.border", _BOX_V))
    frags.append(("class:picker.border", _BOX_H * (body_width - 2)))
    frags.append(("class:picker.border", _BOX_V + "\n"))

    # --- Options -----------------------------------------------------------
    for i in range(start, end):
        oid, label, desc = options[i]
        is_focused = i == focused_idx

        # Pointer + label row
        pointer = _POINTER if is_focused else " "
        prefix = f" {pointer} "
        row_text = f"{label}"
        filler = body_width - 2 - len(prefix) - len(row_text)
        frags.append(("class:picker.border", _BOX_V))
        frags.append(("class:picker.pointer" if is_focused else "", prefix))
        frags.append(("class:picker.selected" if is_focused else "class:picker.option", row_text))
        frags.append(("", " " * max(filler, 0)))
        frags.append(("class:picker.border", _BOX_V + "\n"))

        # Description row — dim italic
        if desc:
            dtxt = desc if len(desc) <= body_width - 8 else desc[: body_width - 9] + "…"
            d_prefix = "     "  # align under label (pointer + 2 spaces + 1)
            d_filler = body_width - 2 - len(d_prefix) - len(dtxt)
            frags.append(("class:picker.border", _BOX_V))
            frags.append(("", d_prefix))
            frags.append(("class:picker.desc", dtxt))
            frags.append(("", " " * max(d_filler, 0)))
            frags.append(("class:picker.border", _BOX_V + "\n"))

    # Scroll indicator if truncated
    if end < n or start > 0:
        hint = f"  … {n - end} below" if end < n else f"  … {start} above"
        frags.append(("class:picker.border", _BOX_V))
        frags.append(("class:picker.hint", hint))
        frags.append(("", " " * max(body_width - 2 - len(hint), 0)))
        frags.append(("class:picker.border", _BOX_V + "\n"))

    # --- Footer — keyboard hints ------------------------------------------
    hint_text = " \u2191\u2193 navigate \u00b7 \u21b5 select \u00b7 esc cancel "
    htrunc = hint_text if len(hint_text) <= body_width - 2 else hint_text[: body_width - 2]
    hfill = body_width - 2 - len(htrunc)
    frags.append(("class:picker.border", _BOX_V))
    frags.append(("class:picker.hint", htrunc))
    frags.append(("", " " * max(hfill, 0)))
    frags.append(("class:picker.border", _BOX_V + "\n"))

    # --- Bottom border -----------------------------------------------------
    frags.append(("class:picker.border", _BOX_BL + _BOX_H * (body_width - 2) + _BOX_BR))

    return FormattedText(frags)


def _fmt_line(opt: Option, focused: bool, sep: bool) -> str:
    """Return the plain-text length estimator for an option row."""
    _, label, _ = opt
    return f"   {label}"


# --------------------------------------------------------------------------- #
#  Key bindings
# --------------------------------------------------------------------------- #


def _build_keybindings(state: dict, n_options: int) -> KeyBindings:
    kb = KeyBindings()

    def _exit(ok: bool) -> None:
        app = state.get("app")
        if app is None:
            return
        if ok:
            pass  # result already populated by caller
        else:
            state["cancelled"] = True
        app.exit()

    @kb.add("up")
    @kb.add("k")
    def _(event):  # noqa: ANN001
        state["idx"] = (state["idx"] - 1) % n_options

    @kb.add("down")
    @kb.add("j")
    def _(event):  # noqa: ANN001
        state["idx"] = (state["idx"] + 1) % n_options

    @kb.add("home")
    def _(event):  # noqa: ANN001
        state["idx"] = 0

    @kb.add("end")
    def _(event):  # noqa: ANN001
        state["idx"] = n_options - 1

    @kb.add("pageup")
    def _(event):  # noqa: ANN001
        state["idx"] = max(0, state["idx"] - 5)

    @kb.add("pagedown")
    def _(event):  # noqa: ANN001
        state["idx"] = min(n_options - 1, state["idx"] + 5)

    @kb.add("enter")
    def _(event):  # noqa: ANN001
        state["result"] = state["_ids"][state["idx"]] if "_ids" in state else None
        _exit(True)

    @kb.add("escape", eager=True)
    @kb.add("c-c")
    def _(event):  # noqa: ANN001
        _exit(False)

    return kb


# --------------------------------------------------------------------------- #
#  Convenience wrappers
# --------------------------------------------------------------------------- #


@dataclass
class _ModelRow:
    """Helper: `list[ModelInfo]` -> display row tuple."""

    id: str
    label: str
    description: str


def _format_ctx(n: Optional[int]) -> str:
    if n is None:
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1000}k"
    return str(n)


def _model_rows(available: Sequence[Any]) -> list[Option]:
    """Group by provider, emit ``(id, label, description)`` rows.

    Labels pad the model id to the widest provider column so the
    context-window + max-output columns line up visually — same as
    CC's ``ModelPicker`` table.
    """
    # Group-sort by provider, preserving intra-provider order.
    buckets: dict[str, list[Any]] = {}
    for m in available:
        buckets.setdefault(getattr(m, "provider", "") or "?", []).append(m)

    # Compute widths for the "id  ctx  max" label.
    id_w = max((len(getattr(m, "id", "")) for m in available), default=8)
    id_w = min(max(id_w, 8), 42)

    rows: list[Option] = []
    for provider in sorted(buckets):
        for m in buckets[provider]:
            mid = getattr(m, "id", "")
            ctx = _format_ctx(getattr(m, "context_window", None))
            cap = _format_ctx(getattr(m, "max_output_tokens", None))
            label = f"{mid:<{id_w}}  ctx {ctx:>5}  max {cap:>5}"
            desc = f"{provider}  {getattr(m, 'name', '') or ''}".strip()
            rows.append((mid, label, desc))
    return rows


async def pick_model(current: str, available: Sequence[Any]) -> Optional[str]:
    """Specialised picker: model selection grouped by provider.

    CC analogue: ``ModelPicker.tsx``. Shows context-window + output-cap
    columns. Fast-mode / effort controls omitted (see module docstring).
    """
    rows = _model_rows(available)
    return await _run_picker("Select model", rows, current)


async def pick_theme(current: str) -> Optional[str]:
    """Pick a Rich theme. CC analogue: ``ThemePicker.tsx``."""
    options: list[Option] = [
        ("dark", "Dark mode", "High-contrast dark — Nellie's default"),
        ("light", "Light mode", "Light background with brand accents"),
        ("dark-daltonized", "Dark (colorblind-friendly)", "Dark palette tuned for deuteranopia"),
        ("light-daltonized", "Light (colorblind-friendly)", "Light palette tuned for deuteranopia"),
        ("dark-ansi", "Dark (ANSI colors only)", "Falls back to 16-color ANSI on dumb terminals"),
        ("light-ansi", "Light (ANSI colors only)", "Falls back to 16-color ANSI on dumb terminals"),
    ]
    return await _run_picker("Theme", options, current)


async def pick_output_style(current: str) -> Optional[str]:
    """Pick an output presentation style. CC analogue: ``OutputStylePicker.tsx``."""
    try:
        from karna.tui.output_style import BUILTIN_STYLES
    except Exception:  # pragma: no cover
        BUILTIN_STYLES = {}  # type: ignore[assignment]

    descriptions = {
        "default": "Brand-accented panels, the standard Nellie look",
        "minimal": "Plain text, no borders or panels",
        "verbose": "Full tool args expanded, timestamps, cost per turn",
        "compact": "Single-line tool calls, no blank lines between turns",
        "dark-code": "Dim prose, bright code blocks",
    }
    options: list[Option] = [
        (name, name.replace("-", " ").title(), descriptions.get(name, "")) for name in BUILTIN_STYLES
    ]
    return await _run_picker("Preferred output style", options, current)


async def pick_language(current: str, available: Sequence[str]) -> Optional[str]:
    """Pick a syntax-highlight language override. CC analogue: ``LanguagePicker.tsx``.

    CC's component is a free-text input; Nellie already exposes text
    input via the main prompt, so the dialog form here is a simple list
    pick. Pass an empty-string option to represent "auto/default".
    """
    options: list[Option] = [(lang, lang if lang else "auto (default)", "") for lang in available]
    return await _run_picker("Syntax highlight language", options, current)


# --------------------------------------------------------------------------- #
#  Internal runner — used by the convenience wrappers and tests
# --------------------------------------------------------------------------- #


async def _run_picker(
    title: str,
    options: Sequence[tuple],
    current: Optional[str],
) -> Optional[str]:
    return await Picker().prompt(title, options, initial_id=current)


__all__ = [
    "Picker",
    "Option",
    "pick_model",
    "pick_theme",
    "pick_output_style",
    "pick_language",
    "BRAND",
    "BORDER",
]
