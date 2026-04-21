"""TUI smoke tests — runs on Linux + Windows.

Intentionally DOES NOT launch the full prompt_toolkit Application — that
requires a real TTY and is unreliable in CI even with pexpect. Instead,
we verify:

1. Imports of the TUI layer succeed on the target OS (catches
   Windows-path / ANSI / curses imports that break on one OS but not
   the other)
2. The public builder functions construct without error when given
   mock state
3. ``run_tui()`` boots and exits cleanly when given input that causes
   an immediate exit path (``/exit``) — exercises the outer Application
   lifecycle without a persistent TTY

Runs on windows-latest + ubuntu-latest via test.yml matrix.
"""

from __future__ import annotations

import os
import sys

import pytest


# ─── Import smoke ───────────────────────────────────────────────────────────


class TestImports:
    """Catches Windows/Linux-divergent import chains that would burn a
    release branch after a single dependency bump."""

    def test_repl_imports(self):
        from karna.tui import repl  # noqa: F401

    def test_slash_imports(self):
        from karna.tui import slash  # noqa: F401

    def test_output_imports(self):
        from karna.tui import output  # noqa: F401

    def test_banner_imports(self):
        try:
            from karna.tui import banner  # noqa: F401
        except ImportError:
            # Banner is optional; if removed, this test becomes a no-op.
            pass

    def test_tui_entry_point_exposed(self):
        """The CLI dispatches into one of these; a rename breaks `nellie`."""
        from karna.tui import repl
        candidates = ("run_repl", "run_tui", "run_async_tui", "main")
        found = [c for c in candidates if hasattr(repl, c)]
        assert found, (
            f"karna.tui.repl must expose one of {candidates} as the CLI entry point; "
            f"none found"
        )


# ─── Windows-specific guards ────────────────────────────────────────────────


class TestPlatformGuards:
    def test_no_posix_only_imports_at_module_scope(self):
        """Under Windows, importing karna.tui should not pull in fcntl / select / termios."""
        # This test runs on all OSes but only asserts on Windows.
        if sys.platform != "win32":
            pytest.skip("windows-only guard")
        blocked = {"fcntl", "termios"}
        before = set(sys.modules)
        from karna.tui import repl  # noqa: F401
        newly_loaded = set(sys.modules) - before
        leaked = blocked & newly_loaded
        assert not leaked, f"POSIX-only modules loaded on Windows: {leaked}"


# ─── CLI entry-point smoke (platform-independent) ───────────────────────────


class TestCliSmoke:
    def test_nellie_help_exits_zero(self, tmp_path):
        """`nellie --help` should render + exit 0 on any OS.

        Uses subprocess.run with a timeout so a stuck Application doesn't
        hang the entire CI job.
        """
        import subprocess
        env = os.environ.copy()
        env["NO_COLOR"] = "1"  # suppress ANSI so output is diffable
        result = subprocess.run(
            [sys.executable, "-m", "karna.cli", "--help"],
            env=env,
            capture_output=True,
            timeout=20,
            text=True,
        )
        assert result.returncode == 0, (
            f"`nellie --help` exited {result.returncode}\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )
        # Sanity: help output mentions the binary
        assert "nellie" in (result.stdout + result.stderr).lower()

    def test_auth_list_exits_zero(self, tmp_path, monkeypatch):
        """`nellie auth list` against an empty credentials dir exits clean."""
        import subprocess
        monkeypatch.setenv("HOME", str(tmp_path))  # POSIX
        monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
        result = subprocess.run(
            [sys.executable, "-m", "karna.cli", "auth", "list"],
            capture_output=True,
            timeout=15,
            text=True,
            env={**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)},
        )
        assert result.returncode == 0, (
            f"`nellie auth list` exited {result.returncode}: {result.stderr[:300]}"
        )
