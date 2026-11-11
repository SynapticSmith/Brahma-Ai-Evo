<#
.SYNOPSIS
    Installs external memory systems (Mem0 and Letta) for Brahma Evo.

.DESCRIPTION
    Brahma Evo supports three memory backends:
      1. Local (built-in SQLite / JSON semantic store, offline, zero-setup)
      2. Mem0 (hybrid vector search + graph memory, cloud API or local Qdrant)
      3. Letta / MemGPT (tiered archival and persona memory, local/remote REST server)

    This script installs the necessary Python packages and dependencies:
      - Mem0: mem0ai + qdrant-client + local vector dependencies
      - Letta: letta-client (official SDK) + Letta server environment

    Usage:
      powershell -ExecutionPolicy Bypass -File scripts\install_memory_deps.ps1
      powershell -ExecutionPolicy Bypass -File scripts\install_memory_deps.ps1 -System mem0
      powershell -ExecutionPolicy Bypass -File scripts\install_memory_deps.ps1 -System letta
#>
[CmdletBinding()]
param(
    [ValidateSet('all', 'mem0', 'letta')]
    [string]$System = 'all',

    [switch]$Force
)

$ErrorActionPreference = 'Stop'

function Get-TargetPythonVersion {
    if ($PSScriptRoot) {
        $bat = Join-Path (Split-Path -Parent $PSScriptRoot) 'EVO.bat'
        if (Test-Path $bat) {
            $prev = $env:BRAHMA_PRINT_PY
            try {
                $env:BRAHMA_PRINT_PY = '1'
                $chosen = & cmd.exe /c "`"$bat`"" 2>$null
                if ($chosen) {
                    $cmdStr = ($chosen | Select-Object -Last 1).Trim()
                    if ($cmdStr -match '^"([^"]+)"$') {
                        $exe = $Matches[1]
                        if (Test-Path $exe) { return @($exe) }
                    } elseif ($cmdStr -match '^py\s+-(\d+\.\d+)$') {
                        $arg = "-$($Matches[1])"
                        if (Get-Command 'py' -ErrorAction SilentlyContinue) { return @('py', $arg) }
                    } elseif ($cmdStr -and (Get-Command $cmdStr -ErrorAction SilentlyContinue)) {
                        return @($cmdStr)
                    }
                }
            } finally {
                $env:BRAHMA_PRINT_PY = $prev
            }
        }
    }

    if ($PSScriptRoot) {
        $venvPython = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'
        if (Test-Path $venvPython) { return @($venvPython) }
    }

    if (Get-Command 'py' -ErrorAction SilentlyContinue) { return @('py', '-3.11') }
    if (Get-Command 'python' -ErrorAction SilentlyContinue) { return @('python') }
    throw "No Python interpreter found on PATH or via EVO.bat."
}

$script:PyArgv = @(Get-TargetPythonVersion)
$Repo = Split-Path -Parent $PSScriptRoot
Write-Host "[info] repository  : $Repo"
Write-Host "[info] interpreter : $($script:PyArgv -join ' ')"

function Step($msg) { Write-Host "[....] $msg" }
function Ok($msg)   { Write-Host "[ ok ] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[warn] $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "[FAIL] $msg" -ForegroundColor Red; throw $msg }

function Invoke-Py([string[]]$PyArgs) {
    $launcher = $script:PyArgv[0]
    $launcherArgs = @()
    if ($script:PyArgv.Count -gt 1) {
        $launcherArgs = $script:PyArgv[1..($script:PyArgv.Count - 1)]
    }
    & $launcher @launcherArgs @PyArgs | Out-Host
    return $LASTEXITCODE
}

function Install-Pip($spec, $label) {
    Step "pip install $spec  ($label)"
    $rc = Invoke-Py @('-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', $spec)
    if ($rc -ne 0) { Die "pip install failed for $label ($spec)" }
    Ok "$label installed"
}

function Install-Mem0 {
    Write-Host "`n=== Mem0 (Hybrid Vector & Graph Memory) ===" -ForegroundColor Cyan
    Install-Pip 'mem0ai' 'mem0ai (Core SDK)'
    Install-Pip 'qdrant-client' 'qdrant-client (Local Vector Storage)'
    Install-Pip 'fastembed' 'fastembed (Local ONNX Vector Embeddings)'

    Step "verifying Mem0 import & connector status..."
    $rc = Invoke-Py @('-c', "from memory.connectors.mem0 import Mem0MemoryConnector; c = Mem0MemoryConnector(); stat = c.status(); print('Mem0 connector ready: available =', stat.get('available'), 'mode =', stat.get('mode')); assert stat.get('ok') is True")
    if ($rc -ne 0) {
        Warn "Mem0 installed but connector verification failed."
    } else {
        Ok "Mem0 connector verified (offline vector mode operational)"
    }

    Write-Host "Usage:" -ForegroundColor DarkGray
    Write-Host "  - Local mode : default, stores vector facts locally via Qdrant and FastEmbed (zero-cloud)." -ForegroundColor DarkGray
    Write-Host "  - Cloud mode : set MEM0_API_KEY (m0-...) or save it in Settings -> Connectivity -> Memory." -ForegroundColor DarkGray
}

function Install-Letta {
    Write-Host "`n=== Letta / MemGPT (Tiered Archival Memory) ===" -ForegroundColor Cyan
    Install-Pip 'letta-client' 'letta-client (Official Python SDK)'

    Step "verifying Letta connector..."
    $rc = Invoke-Py @('-c', "from memory.connectors.letta import LettaMemoryConnector; c = LettaMemoryConnector(); print('Letta connector ready: backend =', c.backend_name)")
    if ($rc -ne 0) {
        Warn "Letta connector verification failed."
    } else {
        Ok "Letta connector verified"
    }

    # Check Windows LongPathsEnabled for full server wheel
    $reg = Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" -Name "LongPathsEnabled" -ErrorAction SilentlyContinue
    $longPaths = if ($reg) { $reg.LongPathsEnabled } else { 0 }

    if ($longPaths -eq 0) {
        Warn "Windows 260-character MAX_PATH is currently enabled (LongPathsEnabled=0)."
        Warn "To run the standalone 'letta' CLI server directly on Windows, enable Long Paths in an Administrator PowerShell:"
        Warn "  Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name 'LongPathsEnabled' -Value 1"
        Warn "Alternatively, run the Letta server using Docker (recommended by Letta):"
        Warn "  docker run -d --name letta -p 8283:8283 letta/letta:latest"
        Warn "Or connect to Letta Cloud: https://app.letta.com"
    } else {
        Step "Windows Long Paths enabled. Attempting pip install of full 'letta' CLI package..."
        $rc = Invoke-Py @('-m', 'pip', 'install', '--disable-pip-version-check', 'letta')
        if ($rc -eq 0) {
            Ok "letta CLI installed successfully"
        } else {
            Warn "letta CLI install returned exit code $rc; use Docker or letta-client."
        }
    }

    Write-Host "Usage:" -ForegroundColor DarkGray
    Write-Host "  - Server URL : default http://localhost:8283 (configured in Settings -> Memory)" -ForegroundColor DarkGray
    Write-Host "  - Archival   : stores and queries tiered memory blocks via REST API" -ForegroundColor DarkGray
}

if ($System -in @('all', 'mem0')) {
    Install-Mem0
}

if ($System -in @('all', 'letta')) {
    Install-Letta
}

Write-Host "`n" + ("=" * 74) -ForegroundColor Cyan
Write-Host "Done. External memory systems installed and ready." -ForegroundColor Green
Write-Host "Configure backends in Settings -> Connectivity -> Memory System & Connectors."
Write-Host ("=" * 74) -ForegroundColor Cyan
