# Bootstrap the reproducible Python 3.11 dev environment for Brahma.
#
# This script exists to stop the "which Python / which venv" guessing game.
# It prefers `py -3.11` (the interpreter AGENTS.md mandates for every gate),
# refuses anything else, creates `.venv` from it, installs runtime + dev + build
# requirements, and prints the exact commands the repo expects you to run.
#
# Usage:
#   .\scripts\setup_dev.ps1
#   .\scripts\setup_dev.ps1 -Force        # delete and recreate .venv
#   .\scripts\setup_dev.ps1 -SkipPlaywright
#
# The created `.venv` is a local build artifact and must never be committed.

param(
    [switch]$Force,
    [switch]$SkipPlaywright,
    [switch]$NoUpgradePip,
    [switch]$InstallSystemTools
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -Path $RepoRoot

function Stop-WithError([string]$Message) {
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host ""
    exit 1
}

function Get-PythonVersion([string]$Exe, [string[]]$PreArgs = @()) {
    try {
        $out = & $Exe @PreArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0) { return ($out | Select-Object -First 1).Trim() }
    } catch { }
    return $null
}

# --- 1. Resolve a Python 3.11 launcher ------------------------------------
$PythonExe = $null
$PythonPreArgs = @()

if (Get-Command "py" -ErrorAction SilentlyContinue) {
    if ((Get-PythonVersion "py" @("-3.11")) -eq "3.11") {
        $PythonExe = "py"
        $PythonPreArgs = @("-3.11")
    } elseif ((Get-PythonVersion "py") -eq "3.11") {
        $PythonExe = "py"
    }
}

if (-not $PythonExe -and (Get-Command "python" -ErrorAction SilentlyContinue)) {
    if ((Get-PythonVersion "python") -eq "3.11") {
        $PythonExe = "python"
    }
}

if (-not $PythonExe) {
    $found = Get-PythonVersion "py"
    if (-not $found) { $found = Get-PythonVersion "python" }
    if ($found) {
        Stop-WithError "Python $found is the default interpreter, but Brahma requires 3.11 (several runtime wheels have no 3.12+ build). Install Python 3.11 and re-run."
    }
    Stop-WithError "No Python found. Install Python 3.11 from https://www.python.org/downloads/ (or run bootstrap.ps1)."
}

$PythonPath = (& $PythonExe @PythonPreArgs -c "import sys; print(sys.executable)").Trim()
Write-Host "Interpreter: $PythonPath" -ForegroundColor Cyan

# --- 2. Create the venv from that exact interpreter -----------------------
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"

if ($Force -and (Test-Path $VenvDir)) {
    Write-Host "Removing existing .venv (-Force)..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $VenvDir
}

if (-not (Test-Path $VenvPy)) {
    Write-Host "Creating .venv..." -ForegroundColor Cyan
    & $PythonExe @PythonPreArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Stop-WithError "venv creation failed (exit $LASTEXITCODE)." }
} else {
    Write-Host "Reusing existing .venv" -ForegroundColor Cyan
}

$VenvVersion = Get-PythonVersion $VenvPy
if ($VenvVersion -ne "3.11") {
    Stop-WithError ".venv runs Python $VenvVersion but the gates require 3.11. Re-run with -Force to recreate it."
}

# --- 3. Install the three requirement sets --------------------------------
if (-not $NoUpgradePip) {
    Write-Host "Upgrading pip/setuptools/wheel..." -ForegroundColor Cyan
    & $VenvPy -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { Stop-WithError "pip upgrade failed (exit $LASTEXITCODE)." }
}

foreach ($req in @("requirements.txt", "requirements-dev.txt", "requirements-build.txt")) {
    $path = Join-Path $RepoRoot $req
    if (-not (Test-Path $path)) { Stop-WithError "missing $req" }
    Write-Host "Installing $req..." -ForegroundColor Cyan
    & $VenvPy -m pip install -r $path
    if ($LASTEXITCODE -ne 0) { Stop-WithError "installing $req failed (exit $LASTEXITCODE)." }
}

if (-not $SkipPlaywright) {
    Write-Host "Installing Playwright browsers..." -ForegroundColor Cyan
    & $VenvPy -m playwright install
    if ($LASTEXITCODE -ne 0) { Write-Host "WARN: playwright install failed; browser automation will not work." -ForegroundColor Yellow }
}

# --- 4. Verify the toolchain actually landed ------------------------------
$pyVersion = (& $VenvPy -c "import sys; print(sys.version.split()[0])").Trim()
$pyiVersion = (& $VenvPy -m PyInstaller --version 2>$null | Select-Object -First 1)
$ruffVersion = (& $VenvPy -m ruff --version 2>$null | Select-Object -First 1)
$pytestVersion = (& $VenvPy -m pytest --version 2>$null | Select-Object -First 1)

Write-Host ""
Write-Host "Python:     $pyVersion" -ForegroundColor Green
Write-Host "PyInstaller: $pyiVersion" -ForegroundColor Green
Write-Host "Ruff:       $ruffVersion" -ForegroundColor Green
Write-Host "Pytest:     $pytestVersion" -ForegroundColor Green
Write-Host ""

# --- 5. External CLI tools verification -----------------------------------
$installToolsScript = Join-Path $RepoRoot "scripts\install_system_tools.ps1"
if (Test-Path $installToolsScript) {
    if ($InstallSystemTools) {
        & powershell -ExecutionPolicy Bypass -File $installToolsScript
    } else {
        & powershell -ExecutionPolicy Bypass -File $installToolsScript -CheckOnly
    }
}
Write-Host "Gates:" -ForegroundColor Cyan
Write-Host "  .\.venv\Scripts\python.exe -m ruff check ." -ForegroundColor Gray
Write-Host "  `$env:QT_QPA_PLATFORM='offscreen'; .\.venv\Scripts\python.exe -m pytest tests -q" -ForegroundColor Gray
Write-Host "Build:" -ForegroundColor Cyan
Write-Host "  .\build_all.ps1" -ForegroundColor Gray
Write-Host ""
