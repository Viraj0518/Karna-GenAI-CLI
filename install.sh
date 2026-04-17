#!/usr/bin/env bash
# Nellie installer — Karna Engineering
# Usage: curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash
set -euo pipefail

REPO="https://github.com/Viraj0518/Karna-GenAI-CLI.git"
PACKAGE="karna"
BINARY="nellie"

echo "╭─────────────────────────────────╮"
echo "│  Installing Nellie ($BINARY)    │"
echo "╰─────────────────────────────────╯"

# Check Python 3.10+
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3.10+ required. Install from https://python.org" && exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "❌ Python $PY_VERSION found, but 3.10+ required." && exit 1
fi
echo "✓ Python $PY_VERSION"

# Check pip
if ! python3 -m pip --version &>/dev/null; then
    echo "❌ pip not found. Install: python3 -m ensurepip --upgrade" && exit 1
fi
echo "✓ pip available"

# Install from GitHub (or PyPI when published)
echo ""
echo "Installing $PACKAGE from GitHub..."
python3 -m pip install --user --upgrade "git+${REPO}" 2>&1 | tail -5

# Verify
if command -v "$BINARY" &>/dev/null; then
    VERSION=$($BINARY --version 2>&1)
    echo ""
    echo "✅ $BINARY installed successfully ($VERSION)"
    echo ""
    echo "Quick start:"
    echo "  1. Set your API key:"
    echo "     export OPENROUTER_API_KEY=sk-or-v1-..."
    echo ""
    echo "  2. Start a conversation:"
    echo "     $BINARY"
    echo ""
    echo "  3. Or set a specific model:"
    echo "     $BINARY model openrouter:minimax/minimax-m2.7"
    echo ""
    echo "  Run '$BINARY --help' for all options."
else
    echo ""
    echo "⚠ $BINARY installed but not on PATH."
    echo "  Add to your PATH: export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "  Then try: $BINARY --version"
fi
