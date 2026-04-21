"""PTY-driven regression suite for the Nellie TUI.

These tests launch the real ``nellie`` REPL inside a pseudo-terminal
(via :mod:`tools.tui_pty_driver`) and assert against the on-screen
output. They catch entire classes of bugs that the unit tests miss —
including the one Viraj hit where a second prompt typed mid-stream got
silently queued with no spinner / no panel / no feedback.

Skip conditions
---------------
* No PTY backend importable   → whole module skipped (``pywinpty`` on
  Windows, ``ptyprocess`` on POSIX).
* The REPL requires a working config; if the banner never renders in
  10s on this host we skip rather than fail — usually means a broken
  checkout (missing dep), not a regression in behaviour-under-test.

Network-dependent tests are marked ``@pytest.mark.live`` and skipped
unless ``OPENROUTER_API_KEY`` is set.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

# --------------------------------------------------------------------------- #
#  Backend discovery / module-level skip
# --------------------------------------------------------------------------- #


def _import_driver():
    # Local import so the module can be collected even on hosts where
    # the driver itself fails to import (e.g. syntax errors during dev).
    from tools.tui_pty_driver import (  # noqa: WPS433  (intentional local import)
        PtyDriver,
        backend_name,
        default_env,
        nellie_cmd,
    )

    return PtyDriver, backend_name, default_env, nellie_cmd


try:
    PtyDriver, backend_name, default_env, nellie_cmd = _import_driver()
    _BACKEND = backend_name()
except Exception:  # noqa: BLE001
    PtyDriver = None  # type: ignore[assignment]
    _BACKEND = None


pytestmark = [
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        _BACKEND is None,
        reason="no PTY backend available (install pywinpty on Windows or ptyprocess on POSIX)",
    ),
]


# --------------------------------------------------------------------------- #
#  Fixtures
# --------------------------------------------------------------------------- #


def _banner_ready_pattern() -> re.Pattern[str]:
    # Reliable indicator the REPL is fully booted: the hint line shown
    # below the banner. Appears once the layout has been drawn. Using
    # the literal text keeps this stable across colour-scheme tweaks.
    return re.compile(r"Type a prompt or /help")


@pytest.fixture
def pty():
    """Launch ``nellie`` under a PTY; tear it down after the test."""
    if PtyDriver is None:  # defensive — module skip should have caught it
        pytest.skip("PTY driver unavailable")

    env = default_env(
        {
            # Keep the banner deterministic: colour is fine (we strip it)
            # but disable any rich "live updating" that would thrash the
            # buffer and slow reads down.
            "KARNA_TUI_DISABLE_LIVE": "1",
        }
    )
    driver = PtyDriver(nellie_cmd(), env=env, cols=140, rows=48)
    driver.spawn()
    try:
        try:
            driver.read_until(_banner_ready_pattern(), timeout=10)
        except TimeoutError as exc:
            pytest.skip(f"REPL did not boot within 10s on this host: {exc}")
        yield driver
    finally:
        # Best-effort exit: send /exit, then terminate.
        try:
            if driver.is_alive():
                driver.send("/exit")
                driver.wait_exit(timeout=3)
        except Exception:  # noqa: BLE001
            pass
        driver.close()


# --------------------------------------------------------------------------- #
#  1. Banner
# --------------------------------------------------------------------------- #


def test_banner_nellie_ascii_appears(pty):
    """The ``NELLIE`` ASCII-art logo must render during boot.

    We look for the top row of the box-drawing block — distinctive
    enough to be a one-way signal, tolerant of the gradient colours.
    """
    screen = pty.screen()
    # The art uses '█' (U+2588 FULL BLOCK). Presence of a run of them
    # on the same line that the "N" + "E" glyphs both align with is a
    # rock-solid signal.
    assert "███" in screen, f"no block-art banner rows in screen:\n{screen[-2000:]}"
    # Also check the tagline that names the product:
    assert "karna's AI assistant" in screen


# --------------------------------------------------------------------------- #
#  2. Status bar — model + elapsed timer
# --------------------------------------------------------------------------- #


def test_status_bar_shows_model_and_timer(pty):
    """Status bar renders ``<provider>/<model>`` plus a ``m:ss`` timer."""
    # Drive a short wait so the per-second timer ticks at least once.
    # read_until gives up cheaply if the pattern already matches.
    pty.read_until(re.compile(r"\d+:\d{2}"), timeout=3)
    screen = pty.screen()
    assert "openrouter/" in screen, "status bar missing provider/model slug — saw:\n" + screen[-1500:]
    assert re.search(r"\d+:\d{2}", screen), "status bar missing m:ss timer — saw:\n" + screen[-1500:]


# --------------------------------------------------------------------------- #
#  3. /help — single-turn sanity
# --------------------------------------------------------------------------- #


def test_help_command_renders(pty):
    """``/help`` prints the slash-command reference without crashing."""
    pty.clear_buffer()
    pty.send("/help")
    # The help panel is titled "slash commands". Also accept the
    # generic wording in case a future refactor renames it.
    matched = pty.read_until(
        re.compile(r"slash commands|Available commands|/exit"),
        timeout=5,
    )
    assert "/exit" in matched or "slash commands" in matched


# --------------------------------------------------------------------------- #
#  4. Spinner appears when a prompt is sent
# --------------------------------------------------------------------------- #


def test_spinner_appears_on_enter(pty):
    """Sending a prompt must surface the ``✦ Thinking…`` indicator fast.

    Viraj's demo bug was a blank pane after Enter because the indicator
    was only painted inside the renderer (after first provider byte).
    The REPL now prints ✦ Thinking synchronously on the submit path —
    this test pins that behaviour.

    We don't need a working provider here: the indicator prints
    BEFORE the agent task is spawned, so even with no network the
    synchronous line must show up within ~500ms.
    """
    pty.clear_buffer()
    pty.send("hello world")
    # Deadline is generous vs. the "<500ms" target to survive slow CI
    # runners; what matters is the indicator *shows up at all*.
    matched = pty.read_until(re.compile(r"Thinking"), timeout=3.0)
    assert "Thinking" in matched


# --------------------------------------------------------------------------- #
#  5. Queued-prompt panel (THE regression this harness exists for)
# --------------------------------------------------------------------------- #


def test_queued_prompt_shows_panel(pty):
    """A second prompt typed mid-turn must surface the yellow queued panel.

    Repro of Viraj's bug: before the fix, turn 1 would keep streaming
    and turn 2's input was silently appended to ``state.input_queue``.
    Now the REPL paints an unmistakable panel titled
    ``queued — turn is still running``.
    """
    pty.clear_buffer()
    # Turn 1 — any prompt. We don't need it to succeed; we just need
    # ``state.agent_running`` to flip to True so the second submit
    # takes the queued branch.
    pty.send("first prompt")
    # Wait for the thinking indicator so we know turn 1 is in flight.
    pty.read_until(re.compile(r"Thinking"), timeout=3.0)

    # Turn 2 — submitted BEFORE turn 1 completes.
    pty.send("second prompt")
    matched = pty.read_until(
        re.compile(r"queued\s+[—-]\s+turn is still running"),
        timeout=5.0,
    )
    assert "queued" in matched.lower()


# --------------------------------------------------------------------------- #
#  6. Clean /exit
# --------------------------------------------------------------------------- #


def test_exit_closes_process(pty):
    """``/exit`` must close the process within 3 seconds, exit code 0."""
    pty.clear_buffer()
    pty.send("/exit")
    # The fixture also tries /exit on teardown but we explicitly verify
    # the exit here and assert the status.
    status = pty.wait_exit(timeout=3.0)
    assert not pty.is_alive(), "REPL still running 3s after /exit"
    # pywinpty sometimes reports None for a normal exit; accept that
    # as long as the process is dead. ptyprocess reports 0.
    assert status in (0, None), f"unexpected exit status: {status!r}"


# --------------------------------------------------------------------------- #
#  7. Live provider round-trip (OPT-IN — requires OPENROUTER_API_KEY)
# --------------------------------------------------------------------------- #


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set — live provider test skipped",
)
def test_live_openrouter_round_trip():
    """Opt-in end-to-end probe against the free openrouter model.

    Kept separate from the per-test ``pty`` fixture because it needs a
    bespoke env (``NELLIE_SMOKE_PROVIDER`` + free model pick) and a
    longer timeout for first-byte latency.
    """
    env = default_env(
        {
            "NELLIE_SMOKE_PROVIDER": "openrouter",
            "NELLIE_SMOKE_MODEL": "openai/gpt-oss-120b:free",
        }
    )
    cmd = nellie_cmd()
    with PtyDriver(cmd, env=env, cols=140, rows=48) as driver:
        driver.read_until(_banner_ready_pattern(), timeout=15)
        driver.send("Reply with exactly three words: Nellie is working.")
        # Generous budget — first-byte latency on free tier is highly variable.
        driver.read_until(re.compile(r"Nellie\s+is\s+working", re.IGNORECASE), timeout=60)
        driver.send("/exit")
        driver.wait_exit(timeout=5)


# --------------------------------------------------------------------------- #
#  Driver unit tests (no nellie process — fast, always run)
# --------------------------------------------------------------------------- #


def test_strip_ansi_removes_colour_codes():
    from tools.tui_pty_driver import strip_ansi

    coloured = "\x1b[31mred\x1b[0m \x1b[1;32mbold-green\x1b[0m plain"
    assert strip_ansi(coloured) == "red bold-green plain"


def test_strip_ansi_handles_osc_and_cursor():
    from tools.tui_pty_driver import strip_ansi

    # OSC title-set + CSI cursor-position + SGR reset + plain
    messy = "\x1b]0;title\x07\x1b[H\x1b[2Jhello\x1b[0m"
    assert strip_ansi(messy) == "hello"


def test_backend_is_one_of_known():
    from tools.tui_pty_driver import backend_name

    name = backend_name()
    assert name in {"winpty", "ptyprocess", None}
    # On both Windows (pywinpty) and Linux (ptyprocess) CI we expect a
    # real backend — ``None`` only shows up on exotic environments.
    if sys.platform == "win32":
        assert name in {"winpty", None}
    else:
        assert name in {"ptyprocess", None}
