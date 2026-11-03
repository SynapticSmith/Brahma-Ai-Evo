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
#   .\build_all.ps1 -DryRun
#
# Outputs:
#   dist\BrahmaEvo\BrahmaEvo.exe        onedir application payload
#   dist\BrahmaEvo_Setup.exe            onefile setup wizard
#   dist\SHA256SUMS.txt                 sha256 of both, for release notes
#   dist\release.json                   signed Ed25519 release manifest

param(
    [switch]$Clean,
    [switch]$DryRun,
    [string]$Version = "1.1.0",
    [string]$Channel = "stable",
    [string]$SignKey,
    [string]$PythonExe
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

$VenvPy = if ($PythonExe) {
    $PythonExe
} else {
    $defaultVenv = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    $candidate = $null
    if (Test-Path $defaultVenv) {
        $v = (& $defaultVenv -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
        if ($v -and $v.Trim() -eq "3.11") {
            $candidate = $defaultVenv
        }
    }
    if (-not $candidate) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $sysPy = (py -3.11 -c "import sys; print(sys.executable)" 2>$null)
            if ($sysPy) {
                $candidate = $sysPy.Trim()
            }
        }
    }
    if (-not $candidate) {
        $curPy = (Get-Command python -ErrorAction SilentlyContinue)
        if ($curPy) {
            $candidate = $curPy.Source
        }
    }
    if (-not $candidate) {
        $candidate = $defaultVenv
    }
    $candidate
}

if (-not (Test-Path $VenvPy)) {
    Stop-WithError "No Python interpreter found at $VenvPy. Specify -PythonExe or run .\scripts\setup_dev.ps1."
}

$VenvVersion = (& $VenvPy -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($VenvVersion -ne "3.11") {
    $has311 = if (Get-Command py -ErrorAction SilentlyContinue) { (py -3.11 -c "import sys; print(sys.version)" 2>$null) } else { $null }
    if ($has311) {
        Stop-WithError ".venv runs Python $VenvVersion but packaging requires 3.11. Run .\scripts\setup_dev.ps1 -Force to recreate it."
    } else {
        Write-Host "Notice: Python 3.11 not found on system. Building with Python $VenvVersion ($VenvPy)." -ForegroundColor Yellow
    }
}

$Specs = @(
    @{ Spec = "installer\Uninstall.spec";       Label = "uninstaller";       Artifact = "dist\Uninstall.exe" },
    @{ Spec = "installer\BrahmaEvo.spec";       Label = "main application"; Artifact = "dist\BrahmaEvo\BrahmaEvo.exe" },
    @{ Spec = "installer\BrahmaEvo_Setup.spec"; Label = "setup wizard";      Artifact = "dist\BrahmaEvo_Setup.exe" }
)

# --- DRY RUN MODE (WS-6 & SEC-11 Verification) ------------------------------
if ($DryRun) {
    Write-Host "=== Brahma Evo Release Packaging DRY-RUN ===" -ForegroundColor Magenta
    Write-Host "Interpreter: $VenvPy (Python $VenvVersion)" -ForegroundColor Cyan

    # 1. Spec files validation
    Write-Host "`n1. Validating PyInstaller spec files..." -ForegroundColor Cyan
    foreach ($entry in $Specs) {
        if (-not (Test-Path $entry.Spec)) { Stop-WithError "Missing spec file: $($entry.Spec)" }
        Write-Host "  OK $($entry.Spec)" -ForegroundColor Green
    }

    # 2. SBOM Verification
    Write-Host "`n2. Verifying Software Bill of Materials (SBOM)..." -ForegroundColor Cyan
    & $VenvPy scripts/generate_sbom.py --verify
    if ($LASTEXITCODE -ne 0) { Stop-WithError "SBOM generation/verification failed." }

    # 3. Mock artifact staging for pipeline verification
    Write-Host "`n3. Staging mock artifacts for manifest & signature testing..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Path "dist\BrahmaEvo" -Force | Out-Null
    Set-Content -Path "dist\Uninstall.exe" -Value "MZ_MOCK_UNINSTALL" -Encoding ascii
    Set-Content -Path "dist\BrahmaEvo\BrahmaEvo.exe" -Value "MZ_MOCK_BRAHMA_EVO" -Encoding ascii
    Set-Content -Path "dist\BrahmaEvo_Setup.exe" -Value "MZ_MOCK_SETUP" -Encoding ascii

    # 4. Generate SHA256SUMS
    $Artifacts = $Specs | ForEach-Object { $_.Artifact }
    $ShaFile = Join-Path $RepoRoot "dist\SHA256SUMS.txt"
    $Lines = foreach ($artifact in $Artifacts) {
        $hash = if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) {
            (Get-FileHash -Algorithm SHA256 -Path $artifact).Hash.ToLower()
        } else {
            ([BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.IO.File]::ReadAllBytes((Resolve-Path $artifact)))) -replace "-", "").ToLower()
        }
        "$hash  $(Split-Path -Leaf $artifact)"
    }
    Set-Content -Path $ShaFile -Value $Lines -Encoding ascii
    Write-Host "OK $ShaFile" -ForegroundColor Green

    # 5. Build Release Manifest
    Write-Host "`n4. Generating canonical release manifest..." -ForegroundColor Cyan
    $ReleaseJson = Join-Path $RepoRoot "dist\release.json"
    $BuildReleaseScript = Join-Path $RepoRoot "tools\build_release.py"
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
    if ($LASTEXITCODE -ne 0 -or (-not (Test-Path $ReleaseJson))) {
        Stop-WithError "Failed to generate release manifest."
    }
    Write-Host "OK $ReleaseJson" -ForegroundColor Green

    # 6. Ed25519 Signing Drill
    Write-Host "`n5. Signing release manifest with Ed25519 drill key..." -ForegroundColor Cyan
    $SignReleaseScript = Join-Path $RepoRoot "tools\sign_release.py"
    $KeyOutput = (& $VenvPy $SignReleaseScript --generate-key)
    $KeyText = ($KeyOutput -join "`n")
    $PrivKey = [regex]::Match($KeyText, "Private Key.*?([0-9a-f]{64})").Groups[1].Value
    $PubKey = [regex]::Match($KeyText, "Public Key.*?([0-9a-f]{64})").Groups[1].Value

    & $VenvPy $SignReleaseScript --manifest $ReleaseJson --key $PrivKey
    if ($LASTEXITCODE -ne 0) { Stop-WithError "Failed to sign manifest." }
    Write-Host "OK ${ReleaseJson}.sig" -ForegroundColor Green

    # 7. Signature verification test
    Write-Host "`n6. Verifying cryptographic signature..." -ForegroundColor Cyan
    $VerifyScript = @"
import json
from pathlib import Path
from core.release_manager import canonical_json_bytes, verify_signature

manifest = json.loads(Path('dist/release.json').read_text(encoding='utf-8'))
raw = canonical_json_bytes(manifest)
sig = Path('dist/release.json.sig').read_text(encoding='utf-8').strip()
ok = verify_signature('$PubKey', sig, raw)
assert ok, 'Ed25519 signature verification failed'
print('Cryptographic signature verification OK')
"@
    & $VenvPy -c $VerifyScript
    if ($LASTEXITCODE -ne 0) { Stop-WithError "Signature verification failed." }

    Write-Host ""
    Write-Host "=== Dry-Run Packaging & Cryptographic Provenance Passed ===" -ForegroundColor Green
    exit 0
}

# --- FULL BUILD MODE --------------------------------------------------------
& $VenvPy -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-WithError "PyInstaller is not installed in packaging Python. Run: $VenvPy -m pip install -r requirements-build.txt"
}

$PyiVersion = (& $VenvPy -m PyInstaller --version).Trim()
Write-Host "Building with PyInstaller $PyiVersion ($VenvPy)" -ForegroundColor Cyan

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
    $hash = if (Get-Command Get-FileHash -ErrorAction SilentlyContinue) {
        (Get-FileHash -Algorithm SHA256 -Path $artifact).Hash.ToLower()
    } else {
        ([BitConverter]::ToString([System.Security.Cryptography.SHA256]::Create().ComputeHash([System.IO.File]::ReadAllBytes((Resolve-Path $artifact)))) -replace "-", "").ToLower()
    }
    "$hash  $(Split-Path -Leaf $artifact)"
}
Set-Content -Path $ShaFile -Value $Lines -Encoding ascii

# --- Software Bill of Materials (SBOM) --------------------------------------
$SbomFile = Join-Path $RepoRoot "dist\sbom.json"
$SbomScript = Join-Path $RepoRoot "scripts\generate_sbom.py"
if (Test-Path $SbomScript) {
    Write-Host ""
    Write-Host "Generating Software Bill of Materials (SBOM)..." -ForegroundColor Cyan
    & $VenvPy $SbomScript --verify --output $SbomFile
    if ($LASTEXITCODE -eq 0 -and (Test-Path $SbomFile)) {
        Write-Host "OK $SbomFile" -ForegroundColor Green
    }
}

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
    if (Test-Path $SbomFile) {
        $BuildArgs += @("--sbom", $SbomFile)
    }
    & $VenvPy @BuildArgs
    if ($LASTEXITCODE -eq 0 -and (Test-Path $ReleaseJson)) {
        Write-Host "OK $ReleaseJson" -ForegroundColor Green
    }
}

# --- Release Signature (WS-6) -----------------------------------------------
$EffectiveKey = if ($SignKey) { $SignKey } else { $env:BRAHMA_RELEASE_SIGN_KEY }
$SignReleaseScript = Join-Path $RepoRoot "tools\sign_release.py"
if (-not $EffectiveKey -and (Test-Path $SignReleaseScript)) {
    Write-Host ""
    Write-Host "Notice: No signing key supplied (-SignKey or env:BRAHMA_RELEASE_SIGN_KEY). Generating ephemeral drill key..." -ForegroundColor Yellow
    $KeyOutput = (& $VenvPy $SignReleaseScript --generate-key)
    $KeyText = ($KeyOutput -join "`n")
    $EffectiveKey = [regex]::Match($KeyText, "Private Key.*?([0-9a-f]{64})").Groups[1].Value
    $PubKey = [regex]::Match($KeyText, "Public Key.*?([0-9a-f]{64})").Groups[1].Value
    Write-Host "Drill Public Key: $PubKey" -ForegroundColor Gray
}
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
