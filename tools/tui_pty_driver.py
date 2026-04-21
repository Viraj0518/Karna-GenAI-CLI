"""PTY-driven harness for driving ``nellie`` (or any TTY program) from tests.

Why this exists
---------------
``prompt_toolkit`` refuses to boot without a real terminal — a plain
``subprocess.Popen`` pipe looks like a regular file descriptor and the
``Application`` bails out immediately. To drive the TUI programmatically
we need a **pseudo-terminal** so the child sees an interactive TTY on
the other end. This module provides that abstraction on both platforms:

* **Windows** — ``pywinpty`` (ConPTY under the hood).
* **POSIX**   — ``ptyprocess``.

If neither is importable, :class:`PtyDriver` constructs but raises
:class:`PtySupportUnavailable` on :meth:`spawn`, so individual tests can
skip gracefully. The consumer API is identical on both platforms.

Typical use in a test::

    from tools.tui_pty_driver import PtyDriver

    cmd = [sys.executable, "-m", "karna.cli"]
    with PtyDriver(cmd, env={"NO_COLOR": "1", **os.environ}) as pty:
        pty.read_until(r"Type a prompt or /help", timeout=5)
        pty.send("/help")
        pty.read_until("slash commands", timeout=5)
        pty.send("/exit")
        pty.wait_exit(timeout=3)

The driver captures the *raw* ANSI stream in an ever-growing buffer —
:meth:`raw_buffer` returns it verbatim, :meth:`screen` strips ANSI and
returns plain text for easy ``in`` / regex assertions.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Iterable

# --------------------------------------------------------------------------- #
#  Backend selection
# --------------------------------------------------------------------------- #


class PtySupportUnavailable(RuntimeError):
    """Raised when no PTY backend is importable on this platform."""

_BACKEND: str | None = None  # "winpty" | "ptyprocess" | None
_WINPTY = None
_PTYPROCESS = None

try:  # Windows — pywinpty exposes the ``winpty`` module at import time
    import winpty as _winpty  # type: ignore

    _WINPTY = _winpty
    _BACKEND = "winpty"
except Exception:  # noqa: BLE001 - any import path blowup is "unavailable"
    try:
        import ptyprocess as _ptyprocess  # type: ignore

        _PTYPROCESS = _ptyprocess
        _BACKEND = "ptyprocess"
    except Exception:  # noqa: BLE001
        _BACKEND = None


def backend_name() -> str | None:
    """Return the currently-selected backend, or ``None`` if unavailable."""
    return _BACKEND


# --------------------------------------------------------------------------- #
#  ANSI helpers
# --------------------------------------------------------------------------- #


# Strip CSI / OSC / SGR escape sequences. Intentionally loose — we want
# assertions to survive wildly varying renderers (Windows Terminal,
# Windows 10 legacy console, xterm, tmux …) without prescribing colours.
# Covers CSI (``\x1b[ … final``), OSC (``\x1b] … BEL|ST``), and stray
# two-byte escapes like ``\x1b(B``.
_ANSI_RE = re.compile(
    r"\x1B\[[0-?]*[ -/]*[@-~]"  # CSI
    r"|\x1B\][^\x07\x1b]*(?:\x07|\x1B\\)"  # OSC … BEL or ST
    r"|\x1B[@-Z\\-_]"  # two-byte ESC + final
)


def strip_ansi(text: str) -> str:
    """Remove ANSI control sequences from *text*."""
    return _ANSI_RE.sub("", text)


# --------------------------------------------------------------------------- #
#  Public driver
# --------------------------------------------------------------------------- #


class PtyDriver:
    """Drive a child process through a pseudo-terminal.

    Args:
        cmd: argv to execute.
        env: Environment for the child (merged on top of nothing — pass
            ``{**os.environ, ...}`` explicitly if you want inheritance).
        cwd: Working directory.
        cols / rows: Initial terminal size.
        encoding: Decoding for reads (``errors="replace"`` — we never
            want a stray byte to crash a regression test).
    """

    def __init__(
        self,
        cmd: list[str],
        env: dict | None = None,
        cwd: str | None = None,
        cols: int = 120,
        rows: int = 40,
        encoding: str = "utf-8",
    ) -> None:
        self.cmd = list(cmd)
        self.env = env
        self.cwd = cwd
        self.cols = cols
        self.rows = rows
        self.encoding = encoding

        self._proc = None  # backend-specific process handle
        self._buffer = ""
        self._buffer_lock = threading.Lock()
        self._reader: threading.Thread | None = None
        self._alive = False
        self._exit_status: int | None = None

    # -- context manager ------------------------------------------------

    def __enter__(self) -> "PtyDriver":
        self.spawn()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- lifecycle ------------------------------------------------------

    def spawn(self) -> None:
        """Start the child under a PTY."""
        if _BACKEND is None:
            raise PtySupportUnavailable(
                "No PTY backend available. Install one of: `pywinpty` (Windows) or `ptyprocess` (POSIX)."
            )

        if _BACKEND == "winpty":
            self._spawn_winpty()
        else:
            self._spawn_ptyprocess()

        self._alive = True
        self._reader = threading.Thread(target=self._pump, name="pty-reader", daemon=True)
        self._reader.start()

    def _spawn_winpty(self) -> None:
        assert _WINPTY is not None
        # pywinpty expects a single command line string OR an argv list;
        # its shim handles both. We pass a list so paths with spaces are
        # safely escaped.
        self._proc = _WINPTY.PtyProcess.spawn(
            self.cmd,
            cwd=self.cwd,
            env=self.env,
            dimensions=(self.rows, self.cols),
        )

    def _spawn_ptyprocess(self) -> None:
        assert _PTYPROCESS is not None
        self._proc = _PTYPROCESS.PtyProcess.spawn(
            self.cmd,
            cwd=self.cwd,
            env=self.env,
            dimensions=(self.rows, self.cols),
        )

    def _pump(self) -> None:
        """Background reader — drains PTY output into ``self._buffer``."""
        while self._alive:
            try:
                data = self._proc.read(4096)  # type: ignore[union-attr]
            except EOFError:
                break
            except OSError:
                break
            except Exception:  # noqa: BLE001
                break
            if data is None or data == "" or data == b"":
                # Child probably exited. Tiny sleep so we don't spin hot
                # while the reaper catches up.
                if not self._is_alive_backend():
                    break
                time.sleep(0.02)
                continue
            if isinstance(data, bytes):
                data = data.decode(self.encoding, errors="replace")
            with self._buffer_lock:
                self._buffer += data
        self._alive = False

    # -- I/O ------------------------------------------------------------

    def send(self, text: str, press_enter: bool = True) -> None:
        """Write *text* to the child's stdin, optionally with a trailing CR.

        On Windows ConPTY, a bare ``\\n`` is not interpreted as "Enter"
        by prompt_toolkit — the accept-handler fires on carriage return.
        We always send ``\\r`` for Enter to keep behaviour identical on
        both backends.
        """
        if self._proc is None:
            raise RuntimeError("spawn() has not been called")
        payload = text
        if press_enter:
            payload += "\r"
        self._write(payload)

    def send_raw(self, data: str) -> None:
        """Write *data* verbatim (no Enter, no munging)."""
        if self._proc is None:
            raise RuntimeError("spawn() has not been called")
        self._write(data)

    def _write(self, payload: str) -> None:
        # pywinpty accepts str directly.
        # ptyprocess wants bytes — str raises ``TypeError: a bytes-like
        # object is required, not 'str'``. Encode per backend.
        assert self._proc is not None
        if _BACKEND == "ptyprocess":
            self._proc.write(payload.encode("utf-8"))
        else:
            self._proc.write(payload)

    def read_until(
        self,
        pattern: "str | re.Pattern[str]",
        timeout: float = 10.0,
        poll: float = 0.05,
        strip: bool = True,
    ) -> str:
        """Block until *pattern* appears in the buffer (or raise).

        If *strip* is True (default), matching is done against the
        ANSI-stripped screen so tests can write human-readable patterns
        without worrying about embedded colour codes.

        Returns the buffer contents up to the end of the match.
        """
        deadline = time.monotonic() + timeout
        regex = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
        while time.monotonic() < deadline:
            haystack = self.screen() if strip else self.raw_buffer()
            m = regex.search(haystack)
            if m:
                return haystack[: m.end()]
            if not self._alive and not self._is_alive_backend():
                # child died — one last read attempt then give up
                haystack = self.screen() if strip else self.raw_buffer()
                m = regex.search(haystack)
                if m:
                    return haystack[: m.end()]
                raise TimeoutError(
                    f"child exited before pattern {pattern!r} matched.\n--- screen ---\n{self.screen()[-2000:]}"
                )
            time.sleep(poll)
        raise TimeoutError(f"pattern {pattern!r} not seen within {timeout}s.\n--- screen ---\n{self.screen()[-2000:]}")

    def expect_absent(
        self,
        pattern: "str | re.Pattern[str]",
        window: float = 0.5,
        poll: float = 0.05,
        strip: bool = True,
    ) -> None:
        """Assert *pattern* does not appear during the next *window* seconds.

        Handy for negative assertions like "the status bar does NOT
        flash an error".
        """
        end = time.monotonic() + window
        regex = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
        while time.monotonic() < end:
            haystack = self.screen() if strip else self.raw_buffer()
            if regex.search(haystack):
                raise AssertionError(f"pattern {pattern!r} appeared within {window}s window")
            time.sleep(poll)

    def screen(self) -> str:
        """Current buffer with ANSI stripped out."""
        with self._buffer_lock:
            return strip_ansi(self._buffer)

    def raw_buffer(self) -> str:
        """Current buffer verbatim (including ANSI)."""
        with self._buffer_lock:
            return self._buffer

    def clear_buffer(self) -> None:
        """Drop accumulated output. Useful between phases of a test."""
        with self._buffer_lock:
            self._buffer = ""

    # -- process control ------------------------------------------------

    def _is_alive_backend(self) -> bool:
        if self._proc is None:
            return False
        try:
            return bool(self._proc.isalive())  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            return False

    def is_alive(self) -> bool:
        return self._is_alive_backend()

    def wait_exit(self, timeout: float = 5.0) -> int | None:
        """Wait for the child to exit; returns exit status or ``None``."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self._is_alive_backend():
                break
            time.sleep(0.05)
        return self._get_exit_status()

    def _get_exit_status(self) -> int | None:
        if self._proc is None:
            return self._exit_status
        try:
            # pywinpty exposes ``.exitstatus``; ptyprocess exposes the
            # same attribute after ``wait()``.
            if hasattr(self._proc, "exitstatus") and self._proc.exitstatus is not None:
                self._exit_status = int(self._proc.exitstatus)
        except Exception:  # noqa: BLE001
            pass
        return self._exit_status

    def close(self) -> None:
        """Terminate the child and clean up the reader thread."""
        self._alive = False
        if self._proc is not None:
            try:
                if self._proc.isalive():
                    try:
                        self._proc.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                # Best-effort reap
                try:
                    self._proc.wait()
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass
            self._get_exit_status()
            try:
                self._proc.close()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
        if self._reader is not None:
            self._reader.join(timeout=2.0)
            self._reader = None


# --------------------------------------------------------------------------- #
#  Convenience: build the argv for launching nellie from any checkout
# --------------------------------------------------------------------------- #


def nellie_cmd(python: str | None = None) -> list[str]:
    """argv to launch the Nellie REPL via the in-tree ``karna.cli`` module.

    Uses ``python -m karna.cli`` rather than the installed ``nellie``
    script so tests run against the *checkout* without needing an
    editable install.
    """
    return [python or sys.executable, "-m", "karna.cli"]


def default_env(overrides: dict | None = None) -> dict:
    """Environment that keeps the TUI deterministic in tests.

    * ``PYTHONIOENCODING=utf-8`` — avoid Windows cp1252 mojibake on
      Unicode glyphs (``✦``, ``◆``) used by the banner and spinner.
    * ``PYTHONUNBUFFERED=1`` — writes show up in the PTY buffer
      immediately rather than being held back by block buffering.
    * ``TERM=xterm-256color`` — prompt_toolkit picks a richer renderer
      when it sees a known TERM, which matches what users experience.
    """
    env = {**os.environ}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("TERM", "xterm-256color")
    if overrides:
        env.update(overrides)
    return env


# --------------------------------------------------------------------------- #
#  Quick manual smoke (``python tools/tui_pty_driver.py``)
# --------------------------------------------------------------------------- #


def _manual_smoke(extra: Iterable[str] = ()) -> int:
    """Developer-facing smoke: spawn nellie, print banner, exit."""
    if _BACKEND is None:
        print("no PTY backend available", file=sys.stderr)
        return 2
    print(f"backend: {_BACKEND}")
    cmd = nellie_cmd() + list(extra)
    with PtyDriver(cmd, env=default_env()) as pty:
        try:
            pty.read_until(r"Type a prompt or /help", timeout=10)
            print(pty.screen())
        except TimeoutError as e:
            print(f"timeout: {e}", file=sys.stderr)
            return 1
        pty.send("/exit")
        pty.wait_exit(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(_manual_smoke(sys.argv[1:]))


__all__ = [
    "PtyDriver",
    "PtySupportUnavailable",
    "backend_name",
    "strip_ansi",
    "nellie_cmd",
    "default_env",
]
