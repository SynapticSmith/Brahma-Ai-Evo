# Install or verify external system tools for Brahma AI Evo.
# Installs: ripgrep, ffmpeg, yt-dlp, pandoc, sqlite3
param(
    [switch]$CheckOnly
)

$ErrorActionPreference = "Continue"

$Tools = @(
    @{ Name = "rg"; PackageId = "BurntSushi.ripgrep.MSVC"; Description = "ripgrep (ultra-fast code & symbol search)" },
    @{ Name = "ffmpeg"; PackageId = "yt-dlp.FFmpeg"; Description = "FFmpeg (audio/video processing & normalization)" },
    @{ Name = "yt-dlp"; PackageId = "yt-dlp.yt-dlp"; Description = "yt-dlp (multimodal subtitle & media crawler)" },
    @{ Name = "pandoc"; PackageId = "JohnMacFarlane.Pandoc"; Description = "Pandoc (universal document conversion)" },
    @{ Name = "sqlite3"; PackageId = "SQLite.SQLite"; Description = "SQLite CLI (database inspection & FTS tuning)" },
    @{ Name = "openscad"; PackageId = "OpenSCAD.OpenSCAD"; Description = "OpenSCAD (headless 3D CAD modeling & STL compilation)" }
)

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " Brahma AI Evo - External System Tools Verification" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# Refresh environment PATH from registry in case packages were just installed
$env:PATH = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

$Missing = @()

foreach ($item in $Tools) {
    $cmd = Get-Command $item.Name -ErrorAction SilentlyContinue
    if ($cmd) {
        Write-Host "  [OK] $($item.Name): $($item.Description)" -ForegroundColor Green
        Write-Host "       Path: $($cmd.Source)" -ForegroundColor Gray
    } else {
        Write-Host "  [MISSING] $($item.Name): $($item.Description)" -ForegroundColor Yellow
        $Missing += $item
    }
}

if ($Missing.Count -eq 0) {
    Write-Host ""
    Write-Host "All recommended system tools are installed and available in PATH." -ForegroundColor Green
    exit 0
}

if ($CheckOnly) {
    Write-Host ""
    Write-Host "Run without -CheckOnly to install missing tools via winget." -ForegroundColor Yellow
    exit 1
}

$Winget = Get-Command "winget" -ErrorAction SilentlyContinue
if (-not $Winget) {
    Write-Host ""
    Write-Host "WARNING: Windows Package Manager (winget) was not found." -ForegroundColor Red
    Write-Host "Please install missing tools manually or install winget from the Microsoft Store." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Installing missing tools via winget..." -ForegroundColor Cyan

foreach ($item in $Missing) {
    if ($item.Name -eq "openscad") {
        Write-Host "Installing openscad (official portable zip)..." -ForegroundColor Cyan
        $dest = "$env:LOCALAPPDATA\OpenSCAD"
        $zip = "$env:TEMP\OpenSCAD-portable.zip"
        try {
            Invoke-WebRequest -Uri "https://files.openscad.org/OpenSCAD-2021.01-x86-64.zip" -OutFile $zip
            Expand-Archive -Path $zip -DestinationPath "$env:LOCALAPPDATA" -Force
            if (Test-Path "$env:LOCALAPPDATA\openscad-2021.01") {
                Move-Item -Path "$env:LOCALAPPDATA\openscad-2021.01" -Destination $dest -Force
            }
            $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
            if ($userPath -notmatch "OpenSCAD") {
                [Environment]::SetEnvironmentVariable("Path", "$userPath;$dest", "User")
            }
            Remove-Item $zip -ErrorAction SilentlyContinue
            Write-Host "Successfully installed openscad portable." -ForegroundColor Green
        } catch {
            Write-Host "Failed to install OpenSCAD portable: $_" -ForegroundColor Red
        }
        continue
    }

    Write-Host "Installing $($item.Name) ($($item.PackageId))..." -ForegroundColor Cyan
    & winget install --id $item.PackageId --exact --accept-source-agreements --accept-package-agreements --silent
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Successfully installed $($item.Name)." -ForegroundColor Green
    } else {
        Write-Host "Failed to install $($item.Name) (exit code $LASTEXITCODE)." -ForegroundColor Red
    }
}

# Final refresh of PATH
$env:PATH = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
Write-Host ""
Write-Host "System tools setup complete. Restart your shell to use all new commands." -ForegroundColor Green
