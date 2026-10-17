param(
    [switch]$IsElevated = $false
)

# 1. Self-Elevate to Admin (Required for silent global installs)
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Elevating privileges for installation..."
    $arguments = "& '" + $myinvocation.mycommand.definition + "'"
    Start-Process powershell -Verb runAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command $arguments"
    exit
}

$ErrorActionPreference = "Stop"
$WorkingDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location -Path $WorkingDir

Write-Host "==========================================================================" -ForegroundColor Yellow
Write-Host "  ____  ____      _    _   _ __  __    _      _    ___ " -ForegroundColor Yellow
Write-Host " | __ )|  _ \    / \  | | | |  \/  |  / \    / \  |_ _|" -ForegroundColor Yellow
Write-Host " |  _ \| |_) |  / _ \ | |_| | |\/| | / _ \  / _ \  | | " -ForegroundColor Yellow
Write-Host " | |_) |  _ <  / ___ \|  _  | |  | |/ ___ \/ ___ \ | | " -ForegroundColor Yellow
Write-Host " |____/|_| \_\/_/   \_\_| |_|_|  |_/_/   \_\_/   \_\___|" -ForegroundColor Yellow
Write-Host "                      BRAHMA EVO" -ForegroundColor Cyan
Write-Host "         AUTONOMOUS SELF-EVOLUTION COGNITIVE ENGINE" -ForegroundColor Green
Write-Host "   [Skill Forge // Crucible Sandbox // 180 FPS HoloCore]" -ForegroundColor Cyan
Write-Host "==========================================================================" -ForegroundColor Yellow
Write-Host ""

# 2. Helper to refresh environment variables
function Update-Environment {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
}

# 3. Check for Python (only 3.11 / 3.12 are supported -- see README)
$SupportedPython = @("3.11", "3.12")
$PythonExe = $null
$PythonVersionArg = ""
$DetectedVersion = $null

# Read "major.minor" from an interpreter, or $null if it does not run.
function Get-PythonVersion([string]$Exe, [string[]]$PreArgs = @()) {
    try {
        $out = & $Exe @PreArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0) { return ($out | Select-Object -First 1).Trim() }
    } catch { }
    return $null
}

if (Get-Command "py" -ErrorAction SilentlyContinue) {
    foreach ($v in @("-3.12","-3.11")) {
        $ver = Get-PythonVersion "py" @($v)
        if ($ver -and ($SupportedPython -contains $ver)) {
            $PythonExe = "py"; $PythonVersionArg = $v; $DetectedVersion = $ver; break
        }
    }
    if (-not $PythonExe) {
        # py exists but no supported version among its aliases -- remember what
        # it defaults to so the error below can name the offending version.
        $DetectedVersion = Get-PythonVersion "py"
    }
}
if (-not $PythonExe -and (Get-Command "python" -ErrorAction SilentlyContinue)) {
    $ver = Get-PythonVersion "python"
    if ($ver -and ($SupportedPython -contains $ver)) {
        $PythonExe = "python"; $DetectedVersion = $ver
    } elseif ($ver -and -not $DetectedVersion) {
        $DetectedVersion = $ver
    }
}

# A too-new (or too-old) interpreter is the root cause of the
# "fatal error C1083: portaudio.h" build failure, so refuse to build a venv on
# one instead of silently falling through to it.
if (-not $PythonExe -and $DetectedVersion) {
    Write-Host ""
    Write-Host "Unsupported Python version detected: $DetectedVersion" -ForegroundColor Red
    Write-Host "Brahma Evo requires Python 3.11 or 3.12; newer versions have no" -ForegroundColor Red
    Write-Host "wheels for several bundled dependencies and will fail to build." -ForegroundColor Red
    Write-Host ""
    Write-Host "Install Python 3.12 from https://www.python.org/downloads/ and re-run" -ForegroundColor Yellow
    Write-Host "bootstrap.ps1." -ForegroundColor Yellow
    exit 1
}

if (-not $PythonExe) {
    Write-Host "Python not found. Downloading Python 3.11.8..." -ForegroundColor Yellow
    $PythonUrl = "https://www.python.org/ftp/python/3.11.8/python-3.11.8-amd64.exe"
    $PythonInstaller = "$env:TEMP\python_installer.exe"
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonInstaller

    Write-Host "Installing Python (Silent Mode)..." -ForegroundColor Yellow
    Start-Process -FilePath $PythonInstaller -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait

    Write-Host "Python installed successfully." -ForegroundColor Green
    Update-Environment
    $PythonExe = "python"
    $PythonVersionArg = ""
    $DetectedVersion = "3.11"
} else {
    Write-Host "Python $DetectedVersion detected ($PythonExe $PythonVersionArg)." -ForegroundColor Green
}

# 4. Check for Node.js
if (-not (Get-Command "node" -ErrorAction SilentlyContinue)) {
    Write-Host "Node.js not found. Downloading Node v20 LTS..." -ForegroundColor Yellow
    $NodeUrl = "https://nodejs.org/dist/v20.11.1/node-v20.11.1-x64.msi"
    $NodeInstaller = "$env:TEMP\node_installer.msi"
    Invoke-WebRequest -Uri $NodeUrl -OutFile $NodeInstaller
    
    Write-Host "Installing Node.js (Silent Mode)..." -ForegroundColor Yellow
    Start-Process -FilePath "msiexec.exe" -ArgumentList "/i `"$NodeInstaller`" /qn" -Wait
    
    Write-Host "Node.js installed successfully." -ForegroundColor Green
    Update-Environment
} else {
    Write-Host "Node.js is already installed: $(Get-Command node | Select-Object -ExpandProperty Source)" -ForegroundColor Green
}

# 5. Virtual Environment Setup
$VenvDir = Join-Path -Path $WorkingDir -ChildPath ".venv"
$VenvPython = Join-Path -Path $VenvDir -ChildPath "Scripts\python.exe"
$VenvPythonW = Join-Path -Path $VenvDir -ChildPath "Scripts\pythonw.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating Virtual Environment in .venv..." -ForegroundColor Cyan
    if (Test-Path $VenvDir) { Remove-Item -Recurse -Force $VenvDir }
    $VenvCreateArgs = @()
    if ($PythonVersionArg) { $VenvCreateArgs += $PythonVersionArg }
    $VenvCreateArgs += @("-m", "venv", ".venv")
    Start-Process -FilePath $PythonExe -ArgumentList $VenvCreateArgs -Wait -NoNewWindow
    Write-Host "Virtual Environment created." -ForegroundColor Green
} else {
    Write-Host "Virtual Environment already exists." -ForegroundColor Green
}

# 6. Install Dependencies
Write-Host "Updating pip and installing dependencies..." -ForegroundColor Cyan
Start-Process -FilePath $VenvPython -ArgumentList "-m pip install --upgrade pip setuptools wheel" -Wait -NoNewWindow
Start-Process -FilePath $VenvPython -ArgumentList "-m pip install -r requirements.txt" -Wait -NoNewWindow

Write-Host "Installing Playwright browsers..." -ForegroundColor Cyan
Start-Process -FilePath $VenvPython -ArgumentList "-m playwright install" -Wait -NoNewWindow

# 7. Launch App
Write-Host "Starting Brahma AI..." -ForegroundColor Green
if (Test-Path $VenvPythonW) {
    Start-Process -FilePath $VenvPythonW -ArgumentList "main.py --startup" -WorkingDirectory $WorkingDir
} else {
    Start-Process -FilePath $VenvPython -ArgumentList "main.py --startup" -WorkingDirectory $WorkingDir -WindowStyle Hidden
}

Write-Host "Bootstrap complete. You can close this window."
Start-Sleep -Seconds 3
