# Build the Brahma Evo application and setup wizard.
#
# Invokes PyInstaller through the .venv interpreter (`-m PyInstaller`) instead of
# a console-script path. That is the difference between "works on the machine
# where it was pip-installed once" and "works from a clean clone": the old
# `.venv\Scripts\pyinstaller.exe` reference fails outright when the venv was
# rebuilt with a different toolchain.
#
# Prerequisites:
#   .\scripts\setup_dev.ps1
#
# Usage:
#   .\build_all.ps1
#   .\build_all.ps1 -Clean
#
# Outputs:
#   dist\BrahmaEvo\BrahmaEvo.exe        onedir application payload
#   dist\BrahmaEvo_Setup.exe            onefile setup wizard
#   dist\SHA256SUMS.txt                 sha256 of both, for release notes

param(
    [switch]$Clean,
    [string]$Version = "1.1.0",
    [string]$Channel = "stable",
    [string]$SignKey
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -Path $RepoRoot

function Stop-WithError([string]$Message) {
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host ""
    exit 1
}

$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Stop-WithError "No .venv interpreter at $VenvPy. Run .\scripts\setup_dev.ps1 first."
}

$VenvVersion = (& $VenvPy -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($VenvVersion -ne "3.11") {
    Stop-WithError ".venv runs Python $VenvVersion but packaging requires 3.11. Run .\scripts\setup_dev.ps1 -Force to recreate it."
}

& $VenvPy -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-WithError "PyInstaller is not installed in .venv. Run: .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt"
}

$PyiVersion = (& $VenvPy -m PyInstaller --version).Trim()
Write-Host "Building with PyInstaller $PyiVersion ($VenvPy)" -ForegroundColor Cyan

$Specs = @(
    @{ Spec = "installer\Uninstall.spec";       Label = "uninstaller";       Artifact = "dist\Uninstall.exe" },
    @{ Spec = "installer\BrahmaEvo.spec";       Label = "main application"; Artifact = "dist\BrahmaEvo\BrahmaEvo.exe" },
    @{ Spec = "installer\BrahmaEvo_Setup.spec"; Label = "setup wizard";      Artifact = "dist\BrahmaEvo_Setup.exe" }
)

foreach ($entry in $Specs) {
    if (-not (Test-Path $entry.Spec)) { Stop-WithError "missing spec file: $($entry.Spec)" }

    # If about to build the setup wizard, ensure the uninstaller is bundled into the main application directory
    if ($entry.Spec -eq "installer\BrahmaEvo_Setup.spec") {
        if (Test-Path "dist\Uninstall.exe") {
            Copy-Item "dist\Uninstall.exe" -Destination "dist\BrahmaEvo\Uninstall.exe" -Force
        }
        if (Test-Path "installer\uninstall.cmd") {
            Copy-Item "installer\uninstall.cmd" -Destination "dist\BrahmaEvo\uninstall.cmd" -Force
        }
    }

    $pyiArgs = @("-m", "PyInstaller", $entry.Spec, "--noconfirm")
    if ($Clean) { $pyiArgs += "--clean" }

    Write-Host ""
    Write-Host "Building $($entry.Label)..." -ForegroundColor Cyan
    & $VenvPy @pyiArgs
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError "PyInstaller failed for $($entry.Label) (exit $LASTEXITCODE)."
    }

    if (-not (Test-Path $entry.Artifact)) {
        Stop-WithError "PyInstaller reported success but $($entry.Artifact) is missing."
    }
    Write-Host "OK $($entry.Artifact)" -ForegroundColor Green
}

# --- Hashes -----------------------------------------------------------------
$Artifacts = $Specs | ForEach-Object { $_.Artifact }
$ShaFile = Join-Path $RepoRoot "dist\SHA256SUMS.txt"
$Lines = foreach ($artifact in $Artifacts) {
    $hash = (Get-FileHash -Algorithm SHA256 -Path $artifact).Hash.ToLower()
    "$hash  $(Split-Path -Leaf $artifact)"
}
Set-Content -Path $ShaFile -Value $Lines -Encoding ascii

# --- Release Manifest (WS-6) ------------------------------------------------
$ReleaseJson = Join-Path $RepoRoot "dist\release.json"
$BuildReleaseScript = Join-Path $RepoRoot "tools\build_release.py"
if (Test-Path $BuildReleaseScript) {
    Write-Host ""
    Write-Host "Generating release manifest..." -ForegroundColor Cyan
    $BuildArgs = @(
        $BuildReleaseScript,
        "--version", $Version,
        "--channel", $Channel,
        "--output", $ReleaseJson
    )
    foreach ($a in $Artifacts) {
        if (Test-Path $a) {
            $BuildArgs += @("--artifact", $a)
        }
    }
    & $VenvPy @BuildArgs
    if ($LASTEXITCODE -eq 0 -and (Test-Path $ReleaseJson)) {
        Write-Host "OK $ReleaseJson" -ForegroundColor Green
    }
}

# --- Release Signature (WS-6) -----------------------------------------------
$EffectiveKey = if ($SignKey) { $SignKey } else { $env:BRAHMA_RELEASE_SIGN_KEY }
$SignReleaseScript = Join-Path $RepoRoot "tools\sign_release.py"
if ($EffectiveKey -and (Test-Path $SignReleaseScript) -and (Test-Path $ReleaseJson)) {
    Write-Host ""
    Write-Host "Signing release manifest..." -ForegroundColor Cyan
    & $VenvPy $SignReleaseScript --manifest $ReleaseJson --key $EffectiveKey
    if ($LASTEXITCODE -eq 0) {
        Write-Host "OK ${ReleaseJson}.sig" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Build complete." -ForegroundColor Green
$Lines | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
Write-Host "Hashes written to $ShaFile" -ForegroundColor Gray
