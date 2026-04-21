"""End-to-end audit of every ``nellie`` CLI subcommand.

Goals:

1. Every subcommand under ``nellie --help`` runs at least once with
   reasonable args and exits cleanly (or returns a documented error).
2. Help output for every command contains no mojibake (the
   ``openrouter/openrouter/auto`` + ``�`` artifacts we've caught
   before).
3. Startup time of the binary is measured so any regression in
   ``nellie --help`` latency (user's #1 complaint proxy) shows up.

Run::

    python tools/cli_surface_audit.py

Writes ``_cli_audit/REPORT.md`` + per-command stdout captures.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

_OUT = Path(__file__).resolve().parent.parent / "_cli_audit"
# ANSI escape sequences (CSI ``\x1b[...m``, OSC, etc.) are legitimate in
# rich-formatted help output — we strip them before checking for real
# mojibake. What remains should be printable text; any U+FFFD (replacement
# char) or C0 control (except TAB / LF / CR) is a genuine encoding issue.
_ANSI_ESC = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-_]")
_MOJIBAKE = re.compile(r"[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]")

# Commands we audit. Each entry: (argv tail, expected zero-exit)
# Some commands need external state (auth login, model set etc.) so
# we stick to read-only / help-oriented invocations that can always run.
_COMMANDS: list[tuple[list[str], bool]] = [
    (["--version"], True),
    (["--help"], True),
    (["auth", "--help"], True),
    (["auth", "list"], True),
    (["model", "--help"], True),
    (["model"], True),  # shows active model
    (["config", "--help"], True),
    (["config", "show"], True),
    (["mcp", "--help"], True),
    (["mcp", "list"], True),
    (["acp", "--help"], True),
    (["history", "--help"], True),
    (["cost", "--help"], True),
    (["cron", "--help"], True),
    (["index", "--help"], True),
    (["run", "--help"], True),
    (["serve", "--help"], True),
    (["web", "--help"], True),
    (["init", "--help"], True),
    (["resume", "--help"], True),
    (["fork", "--help"], True),
    (["replay", "--help"], True),
    (["think", "--help"], True),
]


def _run(argv: list[str], timeout: float = 15.0) -> dict:
    cmd = ["nellie", *argv]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        elapsed = time.time() - t0
        out = proc.stdout + proc.stderr
        # Strip legitimate ANSI escape sequences before checking — rich
        # help output is dense with them.
        stripped = _ANSI_ESC.sub("", out)
        mojibake = bool(_MOJIBAKE.search(stripped))
        return {
            "argv": argv,
            "exit": proc.returncode,
            "elapsed": round(elapsed, 3),
            "mojibake": mojibake,
            "head": out[:500],
            "full": out,
        }
    except subprocess.TimeoutExpired:
        return {
            "argv": argv,
            "exit": -1,
            "elapsed": timeout,
            "mojibake": False,
            "head": "(timeout)",
            "full": "(timeout)",
            "error": "timeout",
        }
    except FileNotFoundError:
        return {
            "argv": argv,
            "exit": -2,
            "elapsed": 0.0,
            "mojibake": False,
            "head": "(nellie binary not on PATH)",
            "full": "",
            "error": "binary-missing",
        }


def main() -> int:
    _OUT.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    ok = 0
    failed = 0
    mojibake_count = 0
    total_time = 0.0

    for argv, expect_ok in _COMMANDS:
        r = _run(argv)
        results.append(r)
        total_time += r["elapsed"]
        if r.get("error") == "binary-missing":
            print(f"{'FAIL':4} nellie {' '.join(argv):35} (binary not on PATH)")
            failed += 1
            continue
        ok_exit = (r["exit"] == 0) == expect_ok
        if r["mojibake"]:
            mojibake_count += 1
            flag = "MOJI"
            failed += 1
        elif ok_exit:
            flag = "OK"
            ok += 1
        else:
            flag = "EXIT"
            failed += 1
        print(f"{flag:4} nellie {' '.join(argv):35} exit={r['exit']:3} {r['elapsed']:.2f}s")

    (_OUT / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    # Markdown report
    lines = [
        "# nellie CLI surface audit",
        "",
        f"- Commands: **{len(_COMMANDS)}**",
        f"- OK: **{ok}**",
        f"- Failed: **{failed}**",
        f"- Mojibake hits: **{mojibake_count}**",
        f"- Total wall time: **{total_time:.1f}s**",
        "",
        "| Command | Exit | Elapsed | Mojibake | Preview |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        prev = r["head"].replace("|", "\\|").replace("\n", " ")[:80]
        lines.append(
            f"| `nellie {' '.join(r['argv'])}` | {r['exit']} | {r['elapsed']}s | "
            f"{'⚠' if r['mojibake'] else ''} | {prev} |"
        )
    (_OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nreport: {_OUT / 'REPORT.md'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    sys.exit(main())
