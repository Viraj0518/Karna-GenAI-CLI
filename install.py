#!/usr/bin/env python3
"""Cross-platform Nellie installer — uses only stdlib.

Works on Linux, macOS, Windows, WSL, Docker, and CI.
Falls back gracefully when bash or PowerShell aren't available.

Usage:
    python3 install.py
    python3 install.py --version 0.1.2
    python3 install.py --force
    python3 install.py --no-venv
    python3 install.py --venv /custom/path
    python3 install.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_URL = "https://github.com/Viraj0518/Karna-GenAI-CLI.git"
PACKAGE = "karna"
BINARY = "nellie"
MIN_PYTHON = (3, 10)

if platform.system() == "Windows":
    DEFAULT_VENV = Path(os.environ.get("USERPROFILE", Path.home())) / ".nellie-venv"
else:
    DEFAULT_VENV = Path.home() / ".nellie-venv"

# ---------------------------------------------------------------------------
# Color helpers (respects NO_COLOR, non-TTY)
# ---------------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str, text: str) -> str:
    if _USE_COLOR:
        return f"\033[{code}m{text}\033[0m"
    return text


def say(msg: str) -> None:
    print(_c("34", msg))


def ok(msg: str) -> None:
    print(_c("32", f"[ok] {msg}"))


def warn(msg: str) -> None:
    print(_c("33", f"[warn] {msg}"))


def err(msg: str) -> None:
    print(_c("31", f"[err] {msg}"), file=sys.stderr)


def step(msg: str) -> None:
    print(_c("1", f"==> {msg}"))


def banner() -> None:
    text = """
+---------------------------------+
|  Installing Nellie (nellie)     |
|  Karna Engineering              |
+---------------------------------+
"""
    print(_c("1", text))


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def run_cmd(
    cmd: list[str],
    *,
    dry_run: bool = False,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    """Run a command, optionally in dry-run mode."""
    display = " ".join(cmd)
    print(_c("2", f"$ {display}"))
    if dry_run:
        return None
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def find_binary(name: str) -> Path | None:
    """Find a binary on PATH."""
    found = shutil.which(name)
    return Path(found) if found else None


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_python_version() -> None:
    """Ensure Python >= 3.10."""
    step(f"Checking Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")
    v = sys.version_info
    if (v.major, v.minor) < MIN_PYTHON:
        err(f"Python {v.major}.{v.minor} found, but {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required.")
        err("")
        system = platform.system()
        if system == "Darwin":
            err("Upgrade:  brew install python@3.12")
        elif system == "Linux":
            err("Upgrade:  sudo apt install python3.12   (Debian/Ubuntu)")
            err("          sudo dnf install python3       (Fedora)")
        else:
            err("Download from https://python.org")
        sys.exit(2)
    ok(f"Python {v.major}.{v.minor}.{v.micro} ({sys.executable})")


def check_pip(python: str) -> None:
    """Verify pip is available; try ensurepip if not."""
    step("Checking pip")
    result = subprocess.run(
        [python, "-m", "pip", "--version"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        version_line = result.stdout.strip().split("\n")[0]
        ok(f"pip available ({version_line})")
        return

    warn("pip missing -- attempting ensurepip")
    result2 = subprocess.run(
        [python, "-m", "ensurepip", "--upgrade"],
        capture_output=True,
        text=True,
    )
    if result2.returncode != 0:
        err("pip is not available and ensurepip failed.")
        err("")
        system = platform.system()
        if system == "Linux":
            err("Fix:  sudo apt install python3-pip   (Debian/Ubuntu)")
        elif system == "Darwin":
            err("Fix:  python3 -m ensurepip")
        else:
            err("Fix:  Download get-pip.py from https://bootstrap.pypa.io/get-pip.py")
        sys.exit(2)
    ok("pip available (via ensurepip)")


def check_git() -> None:
    """Verify git is available (needed for git+https:// install)."""
    step("Checking git")
    if find_binary("git"):
        ok("git available")
    else:
        warn("git not found on PATH.")
        warn("git is needed to install from GitHub.")
        warn("")
        system = platform.system()
        if system == "Darwin":
            warn("Install:  xcode-select --install  (or: brew install git)")
        elif system == "Linux":
            warn("Install:  sudo apt install git   (Debian/Ubuntu)")
        elif system == "Windows":
            warn("Install:  winget install Git.Git  (or download from https://git-scm.com)")
        err("Cannot proceed without git. Install it and try again.")
        sys.exit(2)


def check_existing(force: bool) -> None:
    """Check if nellie is already installed."""
    existing = find_binary(BINARY)
    if not existing:
        return

    version = "unknown"
    try:
        result = subprocess.run(
            [str(existing), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
    except Exception:
        pass

    warn(f"{BINARY} already installed at {existing} ({version})")
    if not force:
        if sys.stdin.isatty():
            answer = input("Upgrade now? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                say("Aborted by user.")
                sys.exit(1)
        else:
            say("Non-interactive session; skipping upgrade. Pass --force to overwrite.")
            sys.exit(0)


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------


def create_venv(venv_path: Path, *, force: bool, dry_run: bool) -> str:
    """Create a venv and return the path to the Python binary inside it."""
    step(f"Creating venv at {venv_path}")

    if venv_path.exists() and force:
        warn(f"Removing existing venv at {venv_path}")
        if not dry_run:
            shutil.rmtree(venv_path)
    elif venv_path.exists():
        say(f"Venv already exists at {venv_path}; reusing. Pass --force to recreate.")

    if not dry_run:
        venv.create(str(venv_path), with_pip=True, clear=False)

    # Determine path to Python inside the venv
    if platform.system() == "Windows":
        venv_python = venv_path / "Scripts" / "python.exe"
    else:
        venv_python = venv_path / "bin" / "python"

    if not dry_run and not venv_python.exists():
        err(f"Venv was created but Python not found at {venv_python}")
        err("This is unexpected. Try deleting the venv and running again with --force.")
        sys.exit(3)

    ok(f"Venv ready at {venv_path}")
    return str(venv_python)


def install_package(
    python: str,
    install_url: str,
    *,
    user_install: bool = False,
    dry_run: bool = False,
) -> None:
    """pip install the package."""
    cmd = [python, "-m", "pip", "install", "--upgrade"]
    if user_install:
        cmd.append("--user")
    cmd.append(install_url)

    step(f"Installing {PACKAGE}")
    result = run_cmd(cmd, dry_run=dry_run, check=False)
    if result and result.returncode != 0:
        err("pip install failed.")
        err("")
        err("Common causes:")
        err("  - No internet connection")
        err("  - git not installed (needed for git+https:// URLs)")
        err("  - Firewall blocking GitHub")
        err("")
        err("Check the output above for details.")
        sys.exit(3)


def create_symlink(venv_path: Path, *, dry_run: bool) -> None:
    """Create a symlink/script in a PATH-accessible location."""
    system = platform.system()

    if system == "Windows":
        venv_bin = venv_path / "Scripts"
        binary_src = venv_bin / f"{BINARY}.exe"
        # On Windows, add to user PATH instead of symlinking
        step(f"Ensuring {venv_bin} is in user PATH")
        if dry_run:
            say(f"Would add {venv_bin} to user PATH")
            return
        # Read current user PATH
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Environment",
                0,
                winreg.KEY_ALL_ACCESS,
            ) as key:
                current_path, _ = winreg.QueryValueEx(key, "Path")
                if str(venv_bin) not in current_path:
                    new_path = f"{venv_bin};{current_path}"
                    winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
                    ok(f"Added {venv_bin} to user PATH")
                    warn("Restart your terminal for PATH changes to take effect.")
                else:
                    ok(f"{venv_bin} already in user PATH")
        except Exception as exc:
            warn(f"Could not update PATH automatically: {exc}")
            warn(f"Manually add {venv_bin} to your PATH.")
    else:
        # Unix: symlink into ~/.local/bin
        venv_bin = venv_path / "bin"
        binary_src = venv_bin / BINARY
        local_bin = Path.home() / ".local" / "bin"

        step(f"Creating symlink in {local_bin}")
        if dry_run:
            say(f"Would symlink: {local_bin / BINARY} -> {binary_src}")
            return

        local_bin.mkdir(parents=True, exist_ok=True)
        link = local_bin / BINARY
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(binary_src)
        ok(f"Symlinked: {link} -> {binary_src}")


def verify_install(venv_path: Path | None, *, dry_run: bool) -> None:
    """Run nellie --version to confirm the install works."""
    step("Verifying install")
    if dry_run:
        warn("Dry-run: skipping verification. No changes were made.")
        return

    # Try PATH first
    binary = find_binary(BINARY)

    # Fall back to venv binary
    if not binary and venv_path:
        if platform.system() == "Windows":
            candidate = venv_path / "Scripts" / f"{BINARY}.exe"
        else:
            candidate = venv_path / "bin" / BINARY
        if candidate.exists():
            binary = candidate

    if not binary:
        warn(f"{BINARY} was installed but is not on PATH yet.")
        if platform.system() == "Windows":
            say("Open a new terminal for PATH changes to take effect.")
        else:
            say("Add ~/.local/bin to PATH:")
            say('  export PATH="$HOME/.local/bin:$PATH"')
            say("  (Add this to your ~/.bashrc or ~/.zshrc to make it permanent)")
        return

    try:
        result = subprocess.run(
            [str(binary), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            ok(f"{BINARY} installed successfully!")
            ok(f"Version: {version}")
            ok(f"Path: {binary}")
        else:
            warn(f"{BINARY} found at {binary} but --version returned exit code {result.returncode}")
            if result.stderr:
                warn(f"stderr: {result.stderr.strip()}")
    except subprocess.TimeoutExpired:
        warn(f"{BINARY} found at {binary} but --version timed out")
    except Exception as exc:
        warn(f"Verification failed: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-platform Nellie installer (stdlib only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 install.py                            # Default: venv at ~/.nellie-venv
  python3 install.py --version 0.1.2            # Pin a version
  python3 install.py --force                    # Force reinstall
  python3 install.py --venv /custom/path        # Custom venv path
  python3 install.py --no-venv                  # Skip venv (not recommended)
  python3 install.py --dry-run                  # Preview what would happen
""",
    )
    parser.add_argument(
        "--version",
        dest="pin_version",
        default="",
        help="Pin a specific release tag (e.g. 0.1.2)",
    )
    parser.add_argument(
        "--venv",
        dest="venv_path",
        default=str(DEFAULT_VENV),
        help=f"Path for the venv (default: {DEFAULT_VENV})",
    )
    parser.add_argument(
        "--no-venv",
        action="store_true",
        help="Skip venv; install with --user (not recommended)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reinstall / upgrade even if nellie is already on PATH",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would run; make no changes",
    )
    args = parser.parse_args()

    banner()

    # Detect OS
    system = platform.system()
    ok(f"OS: {system} ({platform.platform()})")

    # Pre-flight checks
    check_python_version()
    check_git()
    check_existing(args.force)

    # Build install URL
    install_url = f"git+{REPO_URL}"
    if args.pin_version:
        clean = args.pin_version.lstrip("v")
        install_url = f"git+{REPO_URL}@v{clean}"
        say(f"Pinning to version v{clean}")

    venv_path: Path | None = None

    if not args.no_venv:
        venv_path = Path(args.venv_path)
        venv_python = create_venv(venv_path, force=args.force, dry_run=args.dry_run)

        if not args.dry_run:
            # Upgrade pip in venv
            step("Upgrading pip inside venv")
            run_cmd(
                [venv_python, "-m", "pip", "install", "--upgrade", "pip"],
                dry_run=False,
                check=False,
            )
            check_pip(venv_python)
        else:
            step("Upgrading pip inside venv")
            run_cmd(
                [venv_python, "-m", "pip", "install", "--upgrade", "pip"],
                dry_run=True,
            )

        install_package(venv_python, install_url, dry_run=args.dry_run)
        create_symlink(venv_path, dry_run=args.dry_run)
    else:
        warn("Installing without venv isolation. Consider using the default venv mode.")
        check_pip(sys.executable)
        install_package(
            sys.executable,
            install_url,
            user_install=True,
            dry_run=args.dry_run,
        )

    verify_install(venv_path, dry_run=args.dry_run)

    # Quick start
    print()
    print(_c("1", "Quick start:"))
    if system == "Windows":
        print("  1. $env:OPENROUTER_API_KEY = 'sk-or-v1-...'")
    else:
        print("  1. export OPENROUTER_API_KEY=sk-or-v1-...")
    print(f"  2. {BINARY} model set openrouter:qwen/qwen3-coder")
    print(f"  3. {BINARY}")
    print()
    print(f"Run '{BINARY} --help' for all options.")
    print("Docs: https://github.com/Viraj0518/Karna-GenAI-CLI/blob/main/docs/INSTALL.md")


if __name__ == "__main__":
    main()
