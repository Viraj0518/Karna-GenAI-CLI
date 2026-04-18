# Nellie installer (Windows) - Karna Engineering
# Run with: iwr https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.ps1 | iex
#
# Usage (after downloading or piping):
#   iwr https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.ps1 | iex
#   .\install.ps1 -Version 0.1.2
#   .\install.ps1 -Force
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
    [switch]$Force,
    [switch]$DryRun,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

$RepoUrl = "https://github.com/Viraj0518/Karna-GenAI-CLI.git"
$Package = "karna"
$Binary  = "nellie"

function Show-Usage {
    Write-Host @"
Nellie installer (Windows) - Karna Engineering

Usage:
  install.ps1 [-Version <X.Y.Z>] [-Force] [-DryRun] [-Help]

Options:
  -Version <X.Y.Z>   Pin a specific git tag (appends @vX.Y.Z to the URL)
  -Force             Reinstall / upgrade even if nellie is already on PATH
  -DryRun            Print what would run; make no changes
  -Help              Show this help and exit

One-liner:
  iwr https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.ps1 | iex
"@
}

if ($Help) { Show-Usage; exit 0 }

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
    Warn "Git Bash / MSYS detected - NOT supported per README."
    Warn "Use Windows Terminal or plain PowerShell, then re-run:"
    Warn "  iwr https://raw.githubusercontent.com/Viraj0518/Karna-GenAI-CLI/main/install.ps1 | iex"
    exit 2
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
    ErrX "Python 3.10+ required. Install from https://python.org or 'winget install Python.Python.3.12'"
    exit 2
}

$parts = $PyVersion.Split(".")
$major = [int]$parts[0]
$minor = [int]$parts[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    ErrX "Python $PyVersion found, but 3.10+ required."
    exit 2
}
Ok "Python $PyVersion ($PyBin)"

# --- pip ------------------------------------------------------------------
Step "Checking pip"
try {
    & cmd /c "$PyBin -m pip --version" 1>$null 2>$null
    if ($LASTEXITCODE -ne 0) { throw "no pip" }
} catch {
    Warn "pip missing - attempting ensurepip"
    try { Invoke-Cmd "$PyBin -m ensurepip --upgrade" }
    catch { ErrX "pip not available and ensurepip failed."; exit 2 }
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
}

# --- install --------------------------------------------------------------
Step "Installing $Package (--user) from GitHub"
try {
    Invoke-Cmd "$PyBin -m pip install --user --upgrade `"$InstallUrl`""
} catch {
    ErrX "pip install failed: $_"
    exit 3
}

# --- verify ---------------------------------------------------------------
Step "Verifying install"
if ($DryRun) { Warn "Dry-run: skipping verification."; exit 0 }

$found = Get-Command $Binary -ErrorAction SilentlyContinue
if (-not $found) { $found = Get-Command "$Binary.exe" -ErrorAction SilentlyContinue }
if (-not $found) { $found = Get-Command "$Binary.cmd" -ErrorAction SilentlyContinue }

if ($found) {
    $ver = ""
    try { $ver = (& $found.Source --version) 2>&1 | Out-String } catch {}
    Ok "$Binary installed: $($ver.Trim())"
    Ok "Path: $($found.Source)"
} else {
    Warn "$Binary installed but not on PATH."
    Say "Python user scripts dir is typically:"
    Say "  %APPDATA%\Python\Python3X\Scripts"
    Say "Add it to PATH (System Properties > Environment Variables), then open a new terminal."
}

Write-Host @"

Quick start:
  1. `$env:OPENROUTER_API_KEY = 'sk-or-v1-...'
  2. $Binary model set openrouter:qwen/qwen3-coder
  3. $Binary

Run '$Binary --help' for all options.
"@ -ForegroundColor White

exit 0
