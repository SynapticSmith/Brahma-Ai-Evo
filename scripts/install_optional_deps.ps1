<#
.SYNOPSIS
    Installs every optional dependency and model ONE-391 added to Brahma Evo.

.DESCRIPTION
    Everything this script installs is OPTIONAL. Brahma Evo runs without all of
    it, on the defaults it shipped with. The script is grouped by feature so
    you can install only what you actually want:

      speech      Offline STT/TTS engines. Defaults are untouched: STT stays
                  on Google's cloud recogniser and TTS on Edge TTS until you
                  pick an engine in Settings -> Speech Engines.
      osc         python-osc, for the external VMC avatar (VSeeFace).
      avatar-l2d  The Live2D "Ren" model plus its licence text.
      avatar-vrm  The VRM "cryptovoxels" model plus its licence record.
      vrm-runtime Vendors three.js + @pixiv/three-vrm into assets/avatar_vrm.
      all         Everything above.

    Models are installed to %LOCALAPPDATA%\BrahmaAI\avatar\ - the same place
    the app reads them from, so the repo stays free of binaries.

.NOTES
    Execution policy: PowerShell blocks unsigned scripts by default. Prefer the
    process-scoped workaround, which changes nothing on the machine:

        powershell -ExecutionPolicy Bypass -File scripts\install_optional_deps.ps1

    Live2D's Cubism *runtime* is NOT installed here and cannot be. It comes from
    https://www.live2d.com/en/sdk/download/web/ after you accept their EULA,
    and dropping the two files into assets\avatar_live2d\vendor\ is all that
    is needed. Everything else about Live2D is installed by this script.
#>
[CmdletBinding()]
param(
    [ValidateSet('speech', 'osc', 'avatar-l2d', 'avatar-vrm', 'vrm-runtime', 'all')]
    [string]$Group = 'all',

    # Re-download even if already present.
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

# Resolve a working Python and return it as an ARGV ARRAY, so the launcher and
# its flags stay separate elements.
#
# Two traps this function exists to avoid, both of which install into the wrong
# interpreter while looking completely fine:
#
#  1. `py -3.11` is a LAUNCHER plus a flag, not a versioned executable.
#     `& "py -3.11" -m pip` treats the whole string as one command name and
#     fails; `& py -3.11 -m pip` works. Quoting the launcher is the bug.
#
#  2. A bare `python` on PATH is NOT necessarily the interpreter the app runs.
#     Here EVO.bat launches `py -3.11 main.py` while `python` is 3.14 and is
#     also py's default - so resolving `python` first would silently install
#     every dependency somewhere the app never looks.
#
# So the target version is read from the launcher the app itself uses, and an
# exact match is required.
function Get-TargetPythonVersion {
    # Guarded: $PSScriptRoot is empty when this file is dot-sourced rather than
    # run, and a Join-Path against $null throws and drowns the real message.
    if ($PSScriptRoot) {
        # Join-Path takes exactly two positional arguments (-Path, -ChildPath).
        # Passing three binds the extra one as a positional parameter and throws
        # a baffling "cannot find a positional parameter" - which the parser
        # cannot catch, so it only shows up on a real run.
        $bat = Join-Path (Join-Path $PSScriptRoot '..') 'EVO.bat'
        if (Test-Path $bat) {
            $m = Select-String -Path $bat -Pattern 'py\s+-(\d+\.\d+)' -AllMatches |
                 Select-Object -First 1
            if ($m -and $m.Matches.Count -gt 0) { return $m.Matches[0].Groups[1].Value }
        }
    }
    return '3.11'
}

function Resolve-Python {
    # The app's own bootstrap creates .venv and runs from it, so when it exists
    # it IS the interpreter the app imports from - prefer it over any PATH
    # lookup. A .venv\Scripts\python.exe is a real argv[0] with no launcher
    # flags, so it slots straight into the ARGV-array contract below.
    if ($PSScriptRoot) {
        $venvPython = Join-Path (Split-Path -Parent $PSScriptRoot) '.venv\Scripts\python.exe'
        if (Test-Path $venvPython) {
            Write-Host "[info] using the app virtualenv: $venvPython"
            return @($venvPython)
        }
    }

    $want = Get-TargetPythonVersion
    Write-Host "[info] app runs on Python $want (read from EVO.bat)"

    $candidates = @(
        @{ Exe = 'py';      Args = @("-$want") },   # exactly what EVO.bat uses
        @{ Exe = "python$want"; Args = @() },
        @{ Exe = 'python';  Args = @() }
    )

    $fallback = $null
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $argv = @($c.Exe) + $c.Args
        $ver = & $c.Exe @($c.Args + @('-c', "import sys;print('%d.%d'%sys.version_info[:2])")) 2>$null
        if ($ver -notmatch '^(\d+\.\d+)$') { continue }
        if ($ver -eq $want) { return $argv }
        if (-not $fallback) { $fallback = @($argv, $ver) }
    }

    if ($fallback) {
        throw ("Found Python $($fallback[1]) but the app needs $want. " +
               "Install Python $want, or run this script with that interpreter " +
               "on PATH. Installing into $($fallback[1]) would put every " +
               "dependency somewhere Brahma Evo never looks.")
    }
    throw "No suitable Python interpreter found. Install Python $want and re-run."
}

# @(...) is load-bearing: a function returning a single-element array is
# unrolled to a bare string, and $PyArgv[0] would then be the first CHARACTER
# (e.g. 'C:') - invoking 'C' as a command. Force the array shape here.
$script:PyArgv = @(Resolve-Python)
$Repo = Split-Path -Parent $PSScriptRoot
Write-Host "[info] repository  : $Repo"
Write-Host "[info] interpreter : $($script:PyArgv -join ' ')"

function Step($msg) { Write-Host "[....] $msg" }
function Ok($msg)   { Write-Host "[ ok ] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[warn] $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "[FAIL] $msg" -ForegroundColor Red; throw $msg }

function Invoke-Py([string[]]$PyArgs) {
    # Splat the interpreter and its own flags as separate elements. Guarded,
    # because `$a[1..($a.Count-1)]` on a ONE-element array is `1..0`, which
    # slices backwards and hands back element 0 as a spurious extra argument -
    # so a bare `python` resolution would invoke "p".
    #
    # Out-Host matters just as much: a PowerShell function returns EVERYTHING
    # written to its output stream, so letting the child's stdout through would
    # make the return value an array of log lines plus the exit code, and every
    # caller comparing it to 0 would fail for no reason.
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

function Run-Py($file, $argv, $label) {
    $path = Join-Path $Repo $file
    if (-not (Test-Path $path)) { Die "missing $file" }
    Step "$label"
    Push-Location $Repo
    try {
        $rc = Invoke-Py (@($path) + @($argv))
        if ($rc -ne 0) { Die "$label failed (exit $rc)" }
    } finally { Pop-Location }
    Ok "$label"
}

# --------------------------------------------------------------------------
# speech: offline STT/TTS. Optional; cloud defaults are untouched.
# --------------------------------------------------------------------------
function Install-Speech {
    Install-Pip 'faster-whisper' 'faster-whisper (offline STT)'
    Install-Pip 'piper-tts'       'piper-tts (offline TTS)'
    Install-Pip 'kokoro-onnx'     'kokoro-onnx (offline TTS)'

    Step 'pre-downloading the faster-whisper base.en model'
    $rc = Invoke-Py @('-c', "from faster_whisper import WhisperModel; WhisperModel('base.en', device='cpu', compute_type='int8'); print('base.en ready')")
    if ($rc -ne 0) { Warn 'model download failed; it will retry on first use' }
    else { Ok 'base.en ready' }

    Warn 'Piper needs a voice .onnx + .onnx.json; Kokoro needs .onnx + voices.bin.'
    Warn 'Settings -> Speech Engines shows availability and has a voice-file field.'
}

# --------------------------------------------------------------------------
# osc: the external VMC avatar transport.
# --------------------------------------------------------------------------
function Install-Osc {
    Install-Pip 'python-osc' 'python-osc (VMC / OSC)'
    Warn 'Start VSeeFace (or any VMC host) on 127.0.0.1:39539 to see it work.'
}

# --------------------------------------------------------------------------
# avatar models. Installed to %LOCALAPPDATA%, never into the repo.
# --------------------------------------------------------------------------
function Install-Live2DModel {
    Run-Py 'fetch_live2d_model.py' ($(if ($Force) { @('--force') } else { @() })) `
           'Live2D model (Ren) + licence text'
    Warn 'The Cubism runtime is NOT installed by this script - see the header.'
}

function Install-VrmModel {
    $argv = @()
    if ($Force) { $argv += '--force' }
    Run-Py 'fetch_vrm_model.py' $argv 'VRM model (cryptovoxels) + licence record'
}

function Install-VrmRuntime {
    Step 'resolving the three.js / @pixiv/three-vrm packages'
    Push-Location $Repo
    try {
        $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
        if (-not $npm) { $npm = Get-Command npm -ErrorAction SilentlyContinue }
        if (-not $npm) { Die 'npm not found. Install Node.js, or run: py -3.11 vendor_vrm_runtime.py --source <path>' }
        cmd.exe /c "npm install --no-audit --no-fund --loglevel=error three@0.186.1 @pixiv/three-vrm@3.5.5"
        if ($LASTEXITCODE -ne 0) { Die 'npm install failed' }
    } finally { Pop-Location }
    Run-Py 'vendor_vrm_runtime.py' @() 'vendoring the VRM runtime into assets/avatar_vrm/vendor'
    Ok 'VRM is now fully self-contained: no network needed at render time'
}

# --------------------------------------------------------------------------
$groups = if ($Group -eq 'all') { @('speech', 'osc', 'avatar-l2d', 'avatar-vrm', 'vrm-runtime') } else { @($Group) }

Write-Host ""
Write-Host "Installing group(s): $($groups -join ', ')" -ForegroundColor Cyan
Write-Host "Everything here is OPTIONAL - the app runs without all of it." -ForegroundColor DarkGray
Write-Host ""

foreach ($g in $groups) {
    switch ($g) {
        'speech'      { Install-Speech }
        'osc'         { Install-Osc }
        'avatar-l2d'  { Install-Live2DModel }
        'avatar-vrm'  { Install-VrmModel }
        'vrm-runtime' { Install-VrmRuntime }
    }
}

Write-Host ""
Write-Host "=" * 74 -ForegroundColor Cyan
Write-Host "Done. Nothing was committed and no default was changed." -ForegroundColor Green
Write-Host ""
Write-Host "Models went to: $env:LOCALAPPDATA\BrahmaAI\avatar\"
Write-Host "Next: Settings -> Avatar, tick the master switch and pick a backend."
Write-Host "Run 'py -3.11 scripts\precommit_check.py' before committing anything."
Write-Host "=" * 74 -ForegroundColor Cyan