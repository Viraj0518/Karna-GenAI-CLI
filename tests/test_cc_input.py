"""Tests for the upstream-ported input primitives.

The module is a library (no REPL wiring), so these are pure unit tests:

1. VimTextInput mode transitions (insert ↔ normal ↔ visual)
2. ScrollKeybindings dispatch table + PAGE_LINES size
3. attach_configurable_shortcut_hint content + separators
4. render_clickable_image_ref embeds an OSC-8-ready file URL
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.text import Text

from karna.tui.cc_components.input import (
    ScrollKeybindings,
    VimMode,
    VimTextInput,
    attach_configurable_shortcut_hint,
    render_clickable_image_ref,
)


def _render(obj) -> str:
    buf = Console(record=True, width=120, color_system=None, force_terminal=False)
    buf.print(obj)
    return buf.export_text(clear=True)


# --------------------------------------------------------------------------- #
#  1. VimTextInput — mode state machine
# --------------------------------------------------------------------------- #


def test_vim_text_input_mode_transitions_mirror_cc_key_table() -> None:
    observed: list[VimMode] = []
    vim = VimTextInput(on_mode_change=observed.append)

    # Default mode is INSERT (matches upstream's default).
    assert vim.mode == VimMode.INSERT

    # INSERT -> NORMAL via Esc.
    vim.handle_escape()
    assert vim.mode == VimMode.NORMAL
    assert observed[-1] == VimMode.NORMAL

    # NORMAL -> INSERT via each of i/I/a/A/o/O/s/S/c/C.
    for key in ("i", "I", "a", "A", "o", "O", "s", "S", "c", "C"):
        vim.set_mode(VimMode.NORMAL)
        changed = vim.handle_key(key)
        assert changed is True, f"key {key!r} should enter INSERT"
        assert vim.mode == VimMode.INSERT

    # NORMAL -> VISUAL via v/V.
    vim.set_mode(VimMode.NORMAL)
    assert vim.handle_key("v") is True
    assert vim.mode == VimMode.VISUAL

    # VISUAL -> NORMAL via Esc; noop -> NORMAL in NORMAL.
    vim.handle_escape()
    assert vim.mode == VimMode.NORMAL
    vim.handle_escape()  # noop
    assert vim.mode == VimMode.NORMAL

    # Unknown key in NORMAL does not transition.
    vim.set_mode(VimMode.NORMAL)
    assert vim.handle_key("z") is False
    assert vim.mode == VimMode.NORMAL

    # Callback fired at least once per real transition above.
    assert len(observed) >= 5


# --------------------------------------------------------------------------- #
#  2. ScrollKeybindings — page size + dispatch
# --------------------------------------------------------------------------- #


def test_scroll_keybindings_dispatch_table() -> None:
    scroll = ScrollKeybindings()

    # PAGE_LINES matches upstream's default paging (20 lines).
    assert scroll.PAGE_LINES == 20

    # Dispatch returns signed delta for paging keys.
    assert scroll.dispatch("pageup") == -20
    assert scroll.dispatch("pagedown") == 20

    # Home/End + their Ctrl variants → ±inf (jump-to-edge semantics).
    assert scroll.dispatch("home") == float("-inf")
    assert scroll.dispatch("end") == float("inf")
    assert scroll.dispatch("c-home") == float("-inf")
    assert scroll.dispatch("c-end") == float("inf")

    # Unknown keys return None.
    assert scroll.dispatch("space") is None
    assert scroll.dispatch("") is None


# --------------------------------------------------------------------------- #
#  3. attach_configurable_shortcut_hint — rendering
# --------------------------------------------------------------------------- #


def test_attach_configurable_shortcut_hint_joins_with_dot_separator() -> None:
    hints = [
        ("Ctrl-O", "expand"),
        ("Esc", "interrupt"),
        ("Ctrl-C", "exit"),
    ]
    rendered = attach_configurable_shortcut_hint(None, hints)
    assert isinstance(rendered, Text)
    plain = _render(rendered)

    assert "Ctrl-O" in plain and "expand" in plain
    assert "Esc" in plain and "interrupt" in plain
    assert "Ctrl-C" in plain and "exit" in plain
    # upstream-compatible centered-dot separator.
    assert "\u00b7" in plain

    # With parens=True, each chip gets wrapped.
    wrapped = attach_configurable_shortcut_hint(None, hints[:1], parens=True)
    wrapped_plain = _render(wrapped)
    assert "(Ctrl-O" in wrapped_plain and "expand)" in wrapped_plain


def test_attach_configurable_shortcut_hint_attaches_to_app_like_object() -> None:
    class FakeApp:
        def __init__(self) -> None:
            self.bottom_toolbar = None

    app = FakeApp()
    attach_configurable_shortcut_hint(app, [("q", "quit")])
    assert app.bottom_toolbar is not None
    assert "quit" in _render(app.bottom_toolbar)


# --------------------------------------------------------------------------- #
#  4. render_clickable_image_ref — OSC-8 hyperlink + fallback label
# --------------------------------------------------------------------------- #


def test_render_clickable_image_ref_embeds_file_url(tmp_path: Path) -> None:
    img = tmp_path / "screenshot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # just enough to look like a PNG

    rendered = render_clickable_image_ref(img, image_id=7)
    plain = _render(rendered)
    assert "[Image #7]" in plain

    # The Rich Text carries a link style pointing at the file:// URL.
    style_str = str(rendered.style)
    assert "link" in style_str
    assert "file://" in style_str

    # Auto-generated id when omitted — stable + reasonable bounds.
    auto = render_clickable_image_ref(img)
    auto_plain = _render(auto)
    assert auto_plain.startswith("[Image #")
    # Selected state reverses + bolds.
    sel = render_clickable_image_ref(img, image_id=1, selected=True)
    assert "reverse" in str(sel.style) and "bold" in str(sel.style)
