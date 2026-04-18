#!/usr/bin/env bash
# Nellie installer — Karna Engineering
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --venv ~/.nellie-venv
#   curl -fsSL .../install.sh | bash -s -- --version 0.1.2
#   curl -fsSL .../install.sh | bash -s -- --force
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

# --- flags ----------------------------------------------------------------
VENV_PATH=""
PIN_VERSION=""
FORCE=0
DRY_RUN=0
SHOW_HELP=0

usage() {
    cat <<'EOF'
Nellie installer — Karna Engineering

Usage:
  install.sh [--venv <path>] [--version <X.Y.Z>] [--force] [--dry-run] [--help]

Options:
  --venv <path>       Install into an isolated venv at <path>
  --version <X.Y.Z>   Pin a specific git tag (appends @vX.Y.Z to the URL)
  --force             Reinstall / upgrade even if nellie is already on PATH
  --dry-run           Print what would run; make no changes
  --help, -h          Show this help and exit

Examples:
  curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash
  curl -fsSL .../install.sh | bash -s -- --venv ~/.nellie-venv
  curl -fsSL .../install.sh | bash -s -- --version 0.1.2 --force
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --venv)       VENV_PATH="${2:-}"; shift 2 ;;
        --venv=*)     VENV_PATH="${1#*=}"; shift ;;
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
    MINGW*|MSYS*|CYGWIN*)   warn "OS: $OS_KIND detected — Git Bash / MSYS is NOT supported. Use install.ps1 in Windows Terminal." ;;
    *)                      warn "OS: $OS_KIND — unrecognized. Continuing, but you're on your own." ;;
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
    err "Python 3.10+ required. Install from https://python.org"
    exit 2
fi

PY_VERSION="$("$PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")"
PY_MAJOR="${PY_VERSION%%.*}"
PY_MINOR="${PY_VERSION##*.}"

if [ "${PY_MAJOR:-0}" -lt 3 ] || { [ "${PY_MAJOR:-0}" -eq 3 ] && [ "${PY_MINOR:-0}" -lt 10 ]; }; then
    err "Python $PY_VERSION found, but 3.10+ required."
    exit 2
fi
ok "Python $PY_VERSION ($PY_BIN)"

# --- pip ------------------------------------------------------------------
step "Checking pip"
if ! "$PY_BIN" -m pip --version >/dev/null 2>&1; then
    warn "pip missing — attempting ensurepip"
    if ! run "$PY_BIN -m ensurepip --upgrade"; then
        err "pip not available and ensurepip failed. Install pip manually."
        exit 2
    fi
fi
ok "pip available"

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
fi

# --- install --------------------------------------------------------------
if [ -n "$VENV_PATH" ]; then
    step "Creating venv at $VENV_PATH"
    run "$PY_BIN -m venv \"$VENV_PATH\""
    VENV_PY="$VENV_PATH/bin/python"
    VENV_BIN_DIR="$VENV_PATH/bin"
    if [ ! -x "$VENV_PY" ] && [ -x "$VENV_PATH/Scripts/python.exe" ]; then
        VENV_PY="$VENV_PATH/Scripts/python.exe"
        VENV_BIN_DIR="$VENV_PATH/Scripts"
    fi
    step "Installing $PACKAGE into venv"
    if ! run "\"$VENV_PY\" -m pip install --upgrade pip"; then
        err "Failed to upgrade pip in venv"
        exit 3
    fi
    if ! run "\"$VENV_PY\" -m pip install --upgrade \"$INSTALL_URL\""; then
        err "pip install failed"
        exit 3
    fi
    BINARY_PATH="$VENV_BIN_DIR/$BINARY"
    POST_INSTALL_HINT="Activate venv: source \"$VENV_PATH/bin/activate\"   (then: $BINARY)"
else
    step "Installing $PACKAGE (--user) from GitHub"
    if ! run "$PY_BIN -m pip install --user --upgrade \"$INSTALL_URL\""; then
        err "pip install failed"
        exit 3
    fi
    BINARY_PATH=""
    POST_INSTALL_HINT="If '$BINARY' is not found, add: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# --- verify ---------------------------------------------------------------
step "Verifying install"
VERIFY_BIN="$BINARY"
if [ -n "$BINARY_PATH" ] && [ -x "$BINARY_PATH" ]; then
    VERIFY_BIN="$BINARY_PATH"
fi

if [ "$DRY_RUN" -eq 1 ]; then
    warn "Dry-run: skipping verification."
    exit 0
fi

if command -v "$VERIFY_BIN" >/dev/null 2>&1 || [ -x "$VERIFY_BIN" ]; then
    INSTALLED_VERSION="$("$VERIFY_BIN" --version 2>&1 || echo 'unknown')"
    INSTALLED_WHERE="$(command -v "$VERIFY_BIN" 2>/dev/null || echo "$VERIFY_BIN")"
    ok "$BINARY installed: $INSTALLED_VERSION"
    ok "Path: $INSTALLED_WHERE"
else
    warn "$BINARY installed but not on PATH."
    say "$POST_INSTALL_HINT"
fi

cat <<EOF

${C_BOLD}Quick start:${C_RESET}
  1. export OPENROUTER_API_KEY=sk-or-v1-...
  2. $BINARY model set openrouter:qwen/qwen3-coder
  3. $BINARY

Run '$BINARY --help' for all options.
EOF

exit 0
