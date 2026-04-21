#!/usr/bin/env bash
# Nellie installer — Karna Engineering
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --version 0.1.2
#   curl -fsSL .../install.sh | bash -s -- --force
#   curl -fsSL .../install.sh | bash -s -- --no-venv
#   curl -fsSL .../install.sh | bash -s -- --dry-run
#   curl -fsSL .../install.sh | bash -s -- --help
#
# Exit codes:
#   0  success
#   1  user abort
#   2  environment missing (python/pip/OS)
#   3  install failed

set -euo pipefail

REPO_URL="https://github.com/Viraj0518/Karna-GenAI-CLI.git"
PACKAGE="karna"
BINARY="nellie"
DEFAULT_VENV_PATH="$HOME/.nellie-venv"

# --- flags ----------------------------------------------------------------
VENV_PATH="$DEFAULT_VENV_PATH"
USE_VENV=1
PIN_VERSION=""
FORCE=0
DRY_RUN=0
SHOW_HELP=0

usage() {
    cat <<'EOF'
Nellie installer — Karna Engineering

Usage:
  install.sh [OPTIONS]

Options:
  --venv <path>       Install into a venv at <path> (default: ~/.nellie-venv)
  --no-venv           Skip venv; install with --user (not recommended)
  --version <X.Y.Z>   Pin a specific release tag
  --force             Reinstall / upgrade even if nellie is already on PATH
  --dry-run           Print what would run; make no changes
  --help, -h          Show this help and exit

Examples:
  # Default: installs into ~/.nellie-venv and symlinks into ~/.local/bin
  curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash

  # Pin a specific version
  curl -fsSL .../install.sh | bash -s -- --version 0.1.2

  # Force re-install, custom venv path
  curl -fsSL .../install.sh | bash -s -- --venv ~/my-venv --force

  # Preview what would happen without making changes
  curl -fsSL .../install.sh | bash -s -- --dry-run
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --venv)       VENV_PATH="${2:-}"; shift 2 ;;
        --venv=*)     VENV_PATH="${1#*=}"; shift ;;
        --no-venv)    USE_VENV=0; shift ;;
        --version)    PIN_VERSION="${2:-}"; shift 2 ;;
        --version=*)  PIN_VERSION="${1#*=}"; shift ;;
        --force)      FORCE=1; shift ;;
        --dry-run)    DRY_RUN=1; shift ;;
        -h|--help)    SHOW_HELP=1; shift ;;
        *)            echo "Unknown arg: $1" >&2; usage; exit 1 ;;
    esac
done

if [ "$SHOW_HELP" -eq 1 ]; then
    usage
    exit 0
fi

# --- colors (ANSI fallback when no tty) -----------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_RESET=$'\033[0m'
    C_BOLD=$'\033[1m'
    C_DIM=$'\033[2m'
    C_GREEN=$'\033[32m'
    C_YELLOW=$'\033[33m'
    C_RED=$'\033[31m'
    C_BLUE=$'\033[34m'
else
    C_RESET=""; C_BOLD=""; C_DIM=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""
fi

say()    { printf "%s%s%s\n" "$C_BLUE"  "$*" "$C_RESET"; }
ok()     { printf "%s[ok]%s %s\n"   "$C_GREEN"  "$C_RESET" "$*"; }
warn()   { printf "%s[warn]%s %s\n" "$C_YELLOW" "$C_RESET" "$*"; }
err()    { printf "%s[err]%s %s\n"  "$C_RED"    "$C_RESET" "$*" >&2; }
step()   { printf "%s==>%s %s\n"    "$C_BOLD"   "$C_RESET" "$*"; }

run() {
    # echo + exec; skip in dry-run
    printf "%s$ %s%s\n" "$C_DIM" "$*" "$C_RESET"
    if [ "$DRY_RUN" -eq 0 ]; then
        eval "$@"
    fi
}

# --- banner ---------------------------------------------------------------
printf "%s" "$C_BOLD"
cat <<'EOF'
+---------------------------------+
|  Installing Nellie (nellie)     |
|  Karna Engineering              |
+---------------------------------+
EOF
printf "%s" "$C_RESET"

# --- OS detect ------------------------------------------------------------
OS_KIND="$(uname -s 2>/dev/null || echo unknown)"
case "$OS_KIND" in
    Linux*)                 ok "OS: Linux ($OS_KIND)" ;;
    Darwin*)                ok "OS: macOS ($OS_KIND)" ;;
    MINGW*|MSYS*|CYGWIN*)
        warn "OS: $OS_KIND detected — Git Bash / MSYS may have issues."
        warn "Consider using install.ps1 in Windows Terminal instead."
        warn "Continuing anyway..."
        ;;
    *)                      warn "OS: $OS_KIND — unrecognized. Continuing, but expect rough edges." ;;
esac

# --- python 3.10+ ---------------------------------------------------------
step "Checking Python 3.10+"
PY_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY_BIN="$candidate"
        break
    fi
done

if [ -z "$PY_BIN" ]; then
    err "Python 3.10+ is required but was not found on PATH."
    err ""
    err "Install Python:"
    case "$OS_KIND" in
        Darwin*)  err "  brew install python@3.12   (or download from https://python.org)" ;;
        Linux*)   err "  sudo apt install python3   (Debian/Ubuntu)" ;
                  err "  sudo dnf install python3   (Fedora/RHEL)" ;;
        *)        err "  Download from https://python.org" ;;
    esac
    exit 2
fi

PY_VERSION="$("$PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"

if [ "${PY_MAJOR:-0}" -lt 3 ] || { [ "${PY_MAJOR:-0}" -eq 3 ] && [ "${PY_MINOR:-0}" -lt 10 ]; }; then
    err "Python $PY_VERSION found at $("$PY_BIN" -c 'import sys; print(sys.executable)')."
    err "Nellie requires Python 3.10 or newer."
    err ""
    err "Upgrade Python:"
    case "$OS_KIND" in
        Darwin*)  err "  brew install python@3.12" ;;
        Linux*)   err "  sudo apt install python3.12  (or use pyenv/asdf)" ;;
        *)        err "  Download from https://python.org" ;;
    esac
    exit 2
fi
ok "Python $PY_VERSION ($PY_BIN)"

# --- venv module ----------------------------------------------------------
if [ "$USE_VENV" -eq 1 ]; then
    step "Checking venv module"
    if ! "$PY_BIN" -c "import venv" >/dev/null 2>&1; then
        warn "venv module not found. This is common on Debian/Ubuntu."
        err ""
        err "Install the venv module:"
        err "  sudo apt install python3-venv   (Debian/Ubuntu)"
        err "  sudo dnf install python3-venv   (Fedora)"
        err ""
        err "Or re-run with --no-venv to skip venv isolation (not recommended)."
        exit 2
    fi
    ok "venv module available"
fi

# --- pip ------------------------------------------------------------------
step "Checking pip"
if ! "$PY_BIN" -m pip --version >/dev/null 2>&1; then
    warn "pip is missing -- attempting ensurepip"
    if ! run "$PY_BIN -m ensurepip --upgrade"; then
        err "pip is not available and ensurepip failed."
        err ""
        err "Install pip manually:"
        case "$OS_KIND" in
            Darwin*)  err "  python3 -m ensurepip  (or: brew install python@3.12)" ;;
            Linux*)   err "  sudo apt install python3-pip   (Debian/Ubuntu)" ;
                      err "  sudo dnf install python3-pip   (Fedora)" ;;
            *)        err "  curl https://bootstrap.pypa.io/get-pip.py | python3" ;;
        esac
        exit 2
    fi
fi
PIP_VERSION="$("$PY_BIN" -m pip --version 2>/dev/null | head -1 || echo 'unknown')"
ok "pip available ($PIP_VERSION)"

# --- already installed? ---------------------------------------------------
if command -v "$BINARY" >/dev/null 2>&1; then
    EXISTING_PATH="$(command -v "$BINARY")"
    EXISTING_VERSION="$("$BINARY" --version 2>&1 || echo 'unknown')"
    warn "$BINARY already installed at $EXISTING_PATH ($EXISTING_VERSION)"
    if [ "$FORCE" -ne 1 ]; then
        say "Re-run with --force to upgrade/reinstall, or exit."
        if [ -t 0 ]; then
            printf "Upgrade now? [y/N] "
            read -r ANSWER || ANSWER="n"
            case "$ANSWER" in
                y|Y|yes|YES) : ;;
                *) say "Aborted by user."; exit 1 ;;
            esac
        else
            # non-tty (curl|bash): bail cleanly unless --force
            say "stdin is not a tty; skipping upgrade. Pass --force to overwrite."
            exit 0
        fi
    fi
fi

# --- build install target -------------------------------------------------
INSTALL_URL="git+${REPO_URL}"
if [ -n "$PIN_VERSION" ]; then
    PIN_CLEAN="${PIN_VERSION#v}"
    INSTALL_URL="git+${REPO_URL}@v${PIN_CLEAN}"
    say "Pinning to version v${PIN_CLEAN}"
fi

# --- install --------------------------------------------------------------
if [ "$USE_VENV" -eq 1 ]; then
    step "Creating venv at $VENV_PATH"

    if [ -d "$VENV_PATH" ] && [ "$FORCE" -eq 1 ]; then
        warn "Removing existing venv at $VENV_PATH"
        run "rm -rf \"$VENV_PATH\""
    elif [ -d "$VENV_PATH" ]; then
        say "Venv already exists at $VENV_PATH; reusing it. Pass --force to recreate."
    fi

    run "$PY_BIN -m venv \"$VENV_PATH\""

    VENV_PY="$VENV_PATH/bin/python"
    VENV_BIN_DIR="$VENV_PATH/bin"
    if [ ! -x "$VENV_PY" ] && [ -x "$VENV_PATH/Scripts/python.exe" ]; then
        VENV_PY="$VENV_PATH/Scripts/python.exe"
        VENV_BIN_DIR="$VENV_PATH/Scripts"
    fi

    step "Upgrading pip inside venv"
    if ! run "\"$VENV_PY\" -m pip install --upgrade pip"; then
        err "Failed to upgrade pip inside the venv."
        err "This sometimes happens with old system Python. Try upgrading Python first."
        exit 3
    fi

    step "Installing $PACKAGE into venv"
    if ! run "\"$VENV_PY\" -m pip install --upgrade \"$INSTALL_URL\""; then
        err "pip install failed."
        err ""
        err "Common causes:"
        err "  - No internet connection"
        err "  - git not installed (needed for git+https:// URLs)"
        err "  - Firewall blocking GitHub"
        err ""
        err "Check the error above for details."
        exit 3
    fi

    BINARY_PATH="$VENV_BIN_DIR/$BINARY"

    # Symlink into ~/.local/bin so it's on PATH without activating venv
    LOCAL_BIN="$HOME/.local/bin"
    step "Creating symlink in $LOCAL_BIN"
    if [ "$DRY_RUN" -eq 0 ]; then
        mkdir -p "$LOCAL_BIN"
        if [ -L "$LOCAL_BIN/$BINARY" ] || [ -e "$LOCAL_BIN/$BINARY" ]; then
            rm -f "$LOCAL_BIN/$BINARY"
        fi
        ln -s "$BINARY_PATH" "$LOCAL_BIN/$BINARY"
        ok "Symlinked: $LOCAL_BIN/$BINARY -> $BINARY_PATH"
    else
        say "Would symlink: $LOCAL_BIN/$BINARY -> $BINARY_PATH"
    fi

    POST_INSTALL_HINT="If '$BINARY' is not found, add ~/.local/bin to PATH:\n  export PATH=\"\$HOME/.local/bin:\$PATH\"\n  (Add this line to your ~/.bashrc or ~/.zshrc to make it permanent)"
else
    step "Installing $PACKAGE (--user) from GitHub"
    warn "Installing without venv isolation. Consider using the default venv mode."
    if ! run "$PY_BIN -m pip install --user --upgrade \"$INSTALL_URL\""; then
        err "pip install failed."
        err ""
        err "Common causes:"
        err "  - No internet connection"
        err "  - git not installed (needed for git+https:// URLs)"
        err "  - Firewall blocking GitHub"
        err ""
        err "Check the error above for details."
        exit 3
    fi
    BINARY_PATH=""
    POST_INSTALL_HINT="If '$BINARY' is not found, add: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# --- verify ---------------------------------------------------------------
step "Verifying install"
VERIFY_BIN="$BINARY"
if [ -n "${BINARY_PATH:-}" ] && [ -x "${BINARY_PATH:-}" ]; then
    VERIFY_BIN="$BINARY_PATH"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    warn "Dry-run: skipping verification. No changes were made."
    exit 0
fi

# Try PATH first, then direct path
if command -v "$BINARY" >/dev/null 2>&1; then
    VERIFY_BIN="$(command -v "$BINARY")"
fi

if [ -x "$VERIFY_BIN" ]; then
    INSTALLED_VERSION="$("$VERIFY_BIN" --version 2>&1 || echo 'unknown')"
    INSTALLED_WHERE="$(command -v "$VERIFY_BIN" 2>/dev/null || echo "$VERIFY_BIN")"
    ok "$BINARY installed successfully!"
    ok "Version: $INSTALLED_VERSION"
    ok "Path: $INSTALLED_WHERE"
else
    warn "$BINARY was installed but is not on PATH yet."
    printf "%b\n" "$POST_INSTALL_HINT"
fi

cat <<EOF

${C_BOLD}Quick start:${C_RESET}
  1. export OPENROUTER_API_KEY=sk-or-v1-...
  2. $BINARY model set openrouter:qwen/qwen3-coder
  3. $BINARY

Run '$BINARY --help' for all options.
Docs: https://github.com/Viraj0518/Karna-GenAI-CLI/blob/main/docs/INSTALL.md
EOF

exit 0
