# install_memory_deps.ps1
# Root launcher for installing Mem0 and Letta memory systems in Brahma Evo.
param(
    [ValidateSet('all', 'mem0', 'letta')]
    [string]$System = 'all',

    [switch]$Force
)

$targetScript = Join-Path $PSScriptRoot "scripts\install_memory_deps.ps1"
if (-not (Test-Path $targetScript)) {
    Write-Host "Error: cannot find scripts\install_memory_deps.ps1" -ForegroundColor Red
    exit 1
}

$params = @{ System = $System }
if ($Force) { $params['Force'] = $true }

& powershell -ExecutionPolicy Bypass -File $targetScript @params
exit $LASTEXITCODE
