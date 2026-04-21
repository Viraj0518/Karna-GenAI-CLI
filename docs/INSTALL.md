# Installing Nellie

Nellie is Karna's AI coding agent. This guide covers every way to install it.

---

## Quick start (one-liner)

**macOS / Linux:**

```bash
curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash
```

**Windows (PowerShell):**

```powershell
iwr https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.ps1 | iex
```

**Any platform with Python 3.10+:**

```bash
python3 install.py
```

All installers create an isolated venv at `~/.nellie-venv` by default, so Nellie
never pollutes your system Python.

---

## Prerequisites

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| Python      | 3.10+   | `python3 --version` to check |
| pip         | any     | Installers try `ensurepip` if missing |
| git         | any     | Needed for GitHub installs; not needed for `pip install karna` |

---

## Per-OS instructions

### macOS

```bash
# Option A: one-liner (recommended)
curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash

# Option B: Homebrew (once the tap is published)
brew install Viraj0518/tap/nellie

# Option C: pip (from PyPI, once published)
pip install karna
```

If Python is missing:

```bash
brew install python@3.12
```

### Linux (Ubuntu / Debian)

```bash
# Ensure prerequisites
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git

# Install
curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash
```

### Linux (Fedora / RHEL)

```bash
sudo dnf install -y python3 python3-pip git
curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash
```

### Windows

Open PowerShell (not CMD) and run:

```powershell
iwr https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.ps1 | iex
```

Or download and run directly:

```powershell
.\install.ps1
```

If Python is missing:

```powershell
winget install Python.Python.3.12
```

**Important:** Do not use Git Bash / MSYS. Use Windows Terminal or plain PowerShell.

### WSL (Windows Subsystem for Linux)

WSL behaves like Linux. Use the bash installer:

```bash
curl -fsSL https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.sh | bash
```

---

## Venv isolation (default)

By default, all installers create a Python virtual environment:

| OS      | Default venv path       |
|---------|------------------------|
| Linux   | `~/.nellie-venv`       |
| macOS   | `~/.nellie-venv`       |
| Windows | `%USERPROFILE%\.nellie-venv` |

The `nellie` binary is symlinked into `~/.local/bin` (Linux/macOS) or the venv's
`Scripts` directory is added to your user PATH (Windows).

To skip venv isolation (not recommended):

```bash
# Bash
curl -fsSL .../install.sh | bash -s -- --no-venv

# PowerShell
.\install.ps1 -NoVenv

# Python
python3 install.py --no-venv
```

---

## Docker

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir karna

# Or install from Git:
# RUN pip install --no-cache-dir "git+https://github.com/Viraj0518/Karna-GenAI-CLI.git"

ENTRYPOINT ["nellie"]
```

Build and run:

```bash
docker build -t nellie .
docker run -it -e OPENROUTER_API_KEY=sk-or-v1-... nellie
```

Or use the project's Dockerfile directly:

```bash
git clone https://github.com/Viraj0518/Karna-GenAI-CLI.git
cd Karna-GenAI-CLI
docker build -t nellie .
docker run -it -e OPENROUTER_API_KEY=sk-or-v1-... nellie
```

---

## Homebrew

Once the Homebrew tap is published:

```bash
brew tap Viraj0518/tap
brew install nellie
```

Or install directly from the formula:

```bash
brew install --formula packaging/homebrew/nellie.rb
```

---

## From source (development)

```bash
git clone https://github.com/Viraj0518/Karna-GenAI-CLI.git
cd Karna-GenAI-CLI
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev,all]"
nellie --version
```

---

## pip install (from PyPI)

Once published to PyPI:

```bash
pip install karna
```

Pin a version:

```bash
pip install karna==0.1.0
```

With optional extras:

```bash
pip install "karna[web,tokens]"        # Web search + token counting
pip install "karna[all]"               # Everything
```

---

## Installer flags

All three installers (bash, PowerShell, Python) support these options:

| Flag / Option | Bash | PowerShell | Python | Description |
|---------------|------|-----------|--------|-------------|
| Version pin   | `--version 0.1.2` | `-Version 0.1.2` | `--version 0.1.2` | Install a specific release |
| Force         | `--force` | `-Force` | `--force` | Overwrite existing install |
| Dry run       | `--dry-run` | `-DryRun` | `--dry-run` | Show what would happen |
| Custom venv   | `--venv /path` | `-VenvPath C:\path` | `--venv /path` | Custom venv location |
| No venv       | `--no-venv` | `-NoVenv` | `--no-venv` | Skip venv (--user install) |
| Help          | `--help` | `-Help` | `--help` | Show help text |

---

## Troubleshooting

### "Python not found"

Install Python 3.10+:
- **macOS:** `brew install python@3.12`
- **Ubuntu:** `sudo apt install python3 python3-venv`
- **Fedora:** `sudo dnf install python3`
- **Windows:** `winget install Python.Python.3.12`

### "pip not found" or "No module named pip"

```bash
python3 -m ensurepip --upgrade
# Or on Debian/Ubuntu:
sudo apt install python3-pip
```

### "No module named venv"

Common on Debian/Ubuntu minimal installs:

```bash
sudo apt install python3-venv
```

Or use `--no-venv` to skip venv isolation.

### "nellie: command not found" after install

The binary is in `~/.local/bin`. Add it to PATH:

```bash
# Add to ~/.bashrc or ~/.zshrc:
export PATH="$HOME/.local/bin:$PATH"
```

Then restart your terminal or run `source ~/.bashrc`.

On Windows, restart your terminal after installation (the installer updates PATH
automatically).

### "git: command not found"

The installer fetches from GitHub via `git+https://`. Install git:
- **macOS:** `xcode-select --install`
- **Ubuntu:** `sudo apt install git`
- **Windows:** `winget install Git.Git`

### Permission errors on Linux

Do **not** use `sudo` with the installer. The default venv installation does not
require root. If you used `--no-venv` and see permission errors, run with `--user`:

```bash
pip install --user karna
```

### SSL certificate errors

Usually means your Python lacks SSL support or your system CA certs are outdated:

```bash
# Ubuntu/Debian
sudo apt install ca-certificates
# macOS
/Applications/Python\ 3.X/Install\ Certificates.command
```

### Install hangs / is very slow

The installer clones the full repo via git. If your connection is slow:

1. Try `pip install karna` (from PyPI, much smaller download).
2. Or pin a specific version: `--version 0.1.0`

---

## Upgrading

```bash
# Re-run the installer with --force
curl -fsSL .../install.sh | bash -s -- --force

# Or via pip (in the venv)
~/.nellie-venv/bin/pip install --upgrade karna

# Or via pip (if installed globally)
pip install --upgrade karna
```

---

## Uninstalling

### Venv install (default)

```bash
# Remove the venv and symlink
rm -rf ~/.nellie-venv
rm -f ~/.local/bin/nellie
```

On Windows:

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.nellie-venv"
# And remove the venv Scripts dir from your PATH in System Properties
```

### pip install

```bash
pip uninstall karna
```

### Homebrew

```bash
brew uninstall nellie
```

### Docker

```bash
docker rmi nellie
```

---

## Verifying your install

```bash
nellie --version
# Expected output: nellie 0.1.0 (or your installed version)
```

If that works, you're ready:

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
nellie model set openrouter:qwen/qwen3-coder
nellie
```
