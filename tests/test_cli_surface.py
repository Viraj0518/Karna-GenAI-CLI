"""Every ``nellie`` subcommand exits clean and emits no mojibake.

Backed by ``tools/cli_surface_audit.py``. Runs all 23 subcommand
invocations (``--help``, ``list``, ``show``, etc.) and asserts:

1. exit code 0 (documented error paths use separate tests)
2. no mojibake bytes (``\ufffd`` or raw control chars) in stdout/stderr

This is the regression guard for the Rich em-dash → Windows cp1252 ``�``
class of bug Viraj caught in ``nellie web --help``. If anyone lands a
Rich glyph outside the portable ASCII set in a help-docstring, this
test fails in under 30s.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"


def test_cli_surface_audit_passes(tmp_path: Path) -> None:
    """Run tools/cli_surface_audit.py and assert zero exit + zero mojibake."""
    if not _TOOLS_DIR.joinpath("cli_surface_audit.py").is_file():
        pytest.skip("cli_surface_audit.py missing")

    # Run with the repo as cwd so the `nellie` binary from the editable
    # install resolves.
    import os

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(_TOOLS_DIR / "cli_surface_audit.py")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    if "binary-missing" in result.stdout or result.returncode == 127:
        pytest.skip("nellie binary not on PATH (no editable install?)")

    # Parse the JSON the audit writes for richer diagnostics.
    report_path = _TOOLS_DIR.parent / "_cli_audit" / "results.json"
    if report_path.exists():
        entries = json.loads(report_path.read_text(encoding="utf-8"))
        failures = [e for e in entries if e["exit"] != 0 or e.get("mojibake")]
        if failures:
            msg_parts = []
            for f in failures:
                msg_parts.append(
                    f"  nellie {' '.join(f['argv'])} exit={f['exit']} "
                    f"mojibake={f.get('mojibake')} head={f['head'][:100]!r}"
                )
            pytest.fail("CLI surface audit reported failures:\n" + "\n".join(msg_parts))

    assert result.returncode == 0, f"CLI audit exited {result.returncode}. Tail of stdout:\n{result.stdout[-1000:]}"
