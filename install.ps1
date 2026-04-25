# Nellie installer (Windows) - Karna Engineering
# Run with: iwr https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.ps1 | iex
#
# Usage (after downloading or piping):
#   iwr https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.ps1 | iex
#   .\install.ps1 -Version 0.1.2
#   .\install.ps1 -Force
#   .\install.ps1 -NoVenv
#   .\install.ps1 -DryRun
#   .\install.ps1 -Help
#
# Exit codes:
#   0  success
#   1  user abort
#   2  environment missing (python/pip/shell)
#   3  install failed

[CmdletBinding()]
param(
    [string]$Version = "",
    [string]$VenvPath = "",
    [switch]$NoVenv,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/Viraj0518/Karna-GenAI-CLI.git"
$Package = "karna"
$Binary  = "nellie"
$DefaultVenvPath = Join-Path $env:USERPROFILE ".nellie-venv"

function Show-Usage {
    Write-Host @"
Nellie installer (Windows) - Karna Engineering

Usage:
  install.ps1 [OPTIONS]

Options:
  -Version <X.Y.Z>   Pin a specific release tag
  -VenvPath <path>    Install into a venv at <path> (default: ~\.nellie-venv)
  -NoVenv             Skip venv; install with --user (not recommended)
  -Force              Reinstall / upgrade even if nellie is already on PATH
  -DryRun             Print what would run; make no changes
  -Help               Show this help and exit

One-liner:
  iwr https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.ps1 | iex

Examples:
  .\install.ps1                         # Default: venv at ~\.nellie-venv
  .\install.ps1 -Version 0.1.2         # Pin a version
  .\install.ps1 -Force                  # Force reinstall
  .\install.ps1 -VenvPath C:\my-venv   # Custom venv path
  .\install.ps1 -NoVenv                 # Skip venv isolation
  .\install.ps1 -DryRun                 # Preview changes
"@
}

if ($Help) { Show-Usage; exit 0 }

# --- resolve venv path ----------------------------------------------------
$UseVenv = -not $NoVenv
if ($UseVenv -and -not $VenvPath) {
    $VenvPath = $DefaultVenvPath
}

# --- color helpers --------------------------------------------------------
function Say  ($m) { Write-Host $m -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "[ok] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "[warn] $m" -ForegroundColor Yellow }
function ErrX ($m) { Write-Host "[err] $m" -ForegroundColor Red }
function Step ($m) { Write-Host "==> $m" -ForegroundColor White }

function Invoke-Cmd {
    param([string]$Cmd)
    Write-Host "`$ $Cmd" -ForegroundColor DarkGray
    if (-not $DryRun) {
        & powershell.exe -NoProfile -Command $Cmd
        if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Cmd" }
    }
}

# --- banner ---------------------------------------------------------------
Write-Host @"
+---------------------------------+
|  Installing Nellie (nellie)     |
|  Karna Engineering (Windows)    |
+---------------------------------+
"@ -ForegroundColor White

# --- shell sanity: warn if in Git Bash / MSYS -----------------------------
if ($env:MSYSTEM -or $env:TERM_PROGRAM -eq "mintty") {
    Warn "Git Bash / MSYS detected. This installer is designed for PowerShell."
    Warn "Consider using Windows Terminal or plain PowerShell instead."
    Warn "Continuing anyway..."
}

# --- python 3.10+ ---------------------------------------------------------
Step "Checking Python 3.10+"
$PyBin = $null
$PyVersion = $null

# prefer `py` launcher (lets us pin to 3)
$pyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pyLauncher) {
    try {
        $v = & py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $v) {
            $PyBin = "py -3"
            $PyVersion = $v.Trim()
        }
    } catch {}
}

if (-not $PyBin) {
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        try {
            $v = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) {
                $PyBin = "python"
                $PyVersion = $v.Trim()
            }
        } catch {}
    }
}

if (-not $PyBin) {
    ErrX "Python 3.10+ is required but was not found."
    ErrX ""
    ErrX "Install Python:"
    ErrX "  winget install Python.Python.3.12"
    ErrX "  -- or download from https://python.org"
    ErrX ""
    ErrX "Make sure to check 'Add python.exe to PATH' during installation."
    exit 2
}

$parts = $PyVersion.Split(".")
$major = [int]$parts[0]
$minor = [int]$parts[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    ErrX "Python $PyVersion found, but Nellie requires 3.10 or newer."
    ErrX ""
    ErrX "Upgrade Python:"
    ErrX "  winget install Python.Python.3.12"
    ErrX "  -- or download from https://python.org"
    exit 2
}
Ok "Python $PyVersion ($PyBin)"

# --- pip ------------------------------------------------------------------
Step "Checking pip"
try {
    & cmd /c "$PyBin -m pip --version" 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { throw "no pip" }
} catch {
    Warn "pip is missing -- attempting ensurepip"
    try { Invoke-Cmd "$PyBin -m ensurepip --upgrade" }
    catch {
        ErrX "pip is not available and ensurepip failed."
        ErrX ""
        ErrX "Fix: Reinstall Python from https://python.org"
        ErrX "     Make sure 'pip' is checked in the installer options."
        exit 2
    }
}
Ok "pip available"

# --- already installed? ---------------------------------------------------
$existing = Get-Command $Binary -ErrorAction SilentlyContinue
if (-not $existing) {
    $existing = Get-Command "$Binary.exe" -ErrorAction SilentlyContinue
}
if ($existing) {
    $existingPath = $existing.Source
    $existingVer = ""
    try { $existingVer = (& $existingPath --version) 2>&1 | Out-String } catch {}
    Warn "$Binary already installed at $existingPath ($($existingVer.Trim()))"
    if (-not $Force) {
        Say "Re-run with -Force to upgrade/reinstall, or exit."
        if ([Environment]::UserInteractive -and $Host.UI.RawUI) {
            $answer = Read-Host "Upgrade now? [y/N]"
            if ($answer -notmatch '^(y|Y|yes|YES)$') {
                Say "Aborted by user."
                exit 1
            }
        } else {
            Say "Non-interactive session; skipping upgrade. Pass -Force to overwrite."
            exit 0
        }
    }
}

# --- build install URL ----------------------------------------------------
$InstallUrl = "git+$RepoUrl"
if ($Version) {
    $clean = $Version.TrimStart("v")
    $InstallUrl = "git+$RepoUrl@v$clean"
    Say "Pinning to version v$clean"
}

# --- install --------------------------------------------------------------
if ($UseVenv) {
    Step "Creating venv at $VenvPath"

    if ((Test-Path $VenvPath) -and $Force) {
        Warn "Removing existing venv at $VenvPath"
        if (-not $DryRun) {
            Remove-Item -Recurse -Force $VenvPath
        }
    } elseif (Test-Path $VenvPath) {
        Say "Venv already exists at $VenvPath; reusing it. Pass -Force to recreate."
    }

    Invoke-Cmd "$PyBin -m venv `"$VenvPath`""

    $VenvPy = Join-Path $VenvPath "Scripts\python.exe"
    $VenvBinDir = Join-Path $VenvPath "Scripts"

    if (-not (Test-Path $VenvPy)) {
        # Fallback for non-Windows venv layout (shouldn't happen on Windows)
        $VenvPy = Join-Path $VenvPath "bin/python"
        $VenvBinDir = Join-Path $VenvPath "bin"
    }

    Step "Upgrading pip inside venv"
    try {
        Invoke-Cmd "`"$VenvPy`" -m pip install --upgrade pip"
    } catch {
        ErrX "Failed to upgrade pip inside the venv."
        exit 3
    }

    Step "Installing $Package into venv"
    try {
        Invoke-Cmd "`"$VenvPy`" -m pip install --upgrade `"$InstallUrl`""
    } catch {
        ErrX "pip install failed: $_"
        ErrX ""
        ErrX "Common causes:"
        ErrX "  - No internet connection"
        ErrX "  - git not installed (needed for git+https:// URLs)"
        ErrX "  - Firewall blocking GitHub"
        exit 3
    }

    $BinaryPath = Join-Path $VenvBinDir "$Binary.exe"

    # Add venv Scripts to user PATH so nellie is available without activation
    Step "Adding $VenvBinDir to user PATH"
    if (-not $DryRun) {
        $currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if ($currentPath -notlike "*$VenvBinDir*") {
            [Environment]::SetEnvironmentVariable("Path", "$VenvBinDir;$currentPath", "User")
            $env:Path = "$VenvBinDir;$env:Path"
            Ok "Added $VenvBinDir to user PATH"
            Warn "You may need to restart your terminal for PATH changes to take effect."
        } else {
            Ok "$VenvBinDir already in user PATH"
        }
    } else {
        Say "Would add $VenvBinDir to user PATH"
    }

    $PostInstallHint = "If '$Binary' is not found, open a new terminal (PATH was updated)."
} else {
    Step "Installing $Package (--user) from GitHub"
    Warn "Installing without venv isolation. Consider using the default venv mode."
    try {
        Invoke-Cmd "$PyBin -m pip install --user --upgrade `"$InstallUrl`""
    } catch {
        ErrX "pip install failed: $_"
        ErrX ""
        ErrX "Common causes:"
        ErrX "  - No internet connection"
        ErrX "  - git not installed (needed for git+https:// URLs)"
        ErrX "  - Firewall blocking GitHub"
        exit 3
    }
    $BinaryPath = $null
    $PostInstallHint = @"
If '$Binary' is not found, the Python user scripts dir is typically:
  %APPDATA%\Python\Python3X\Scripts
Add it to PATH (System Properties > Environment Variables), then open a new terminal.
"@
}

# --- verify ---------------------------------------------------------------
Step "Verifying install"
if ($DryRun) { Warn "Dry-run: skipping verification. No changes were made."; exit 0 }

$found = Get-Command $Binary -ErrorAction SilentlyContinue
if (-not $found) { $found = Get-Command "$Binary.exe" -ErrorAction SilentlyContinue }
if (-not $found) { $found = Get-Command "$Binary.cmd" -ErrorAction SilentlyContinue }

if ($found) {
    $ver = ""
    try { $ver = (& $found.Source --version) 2>&1 | Out-String } catch {}
    Ok "$Binary installed successfully!"
    Ok "Version: $($ver.Trim())"
    Ok "Path: $($found.Source)"
} elseif ($BinaryPath -and (Test-Path $BinaryPath)) {
    $ver = ""
    try { $ver = (& $BinaryPath --version) 2>&1 | Out-String } catch {}
    Ok "$Binary installed successfully!"
    Ok "Version: $($ver.Trim())"
    Ok "Path: $BinaryPath"
    Warn $PostInstallHint
} else {
    Warn "$Binary was installed but is not on PATH yet."
    Say $PostInstallHint
}

Write-Host @"

Quick start:
  1. `$env:OPENROUTER_API_KEY = 'sk-or-v1-...'
  2. $Binary model set openrouter:qwen/qwen3-coder
  3. $Binary

Run '$Binary --help' for all options.
Docs: https://github.com/Viraj0518/Karna-GenAI-CLI/blob/main/docs/INSTALL.md
"@ -ForegroundColor White

exit 0
