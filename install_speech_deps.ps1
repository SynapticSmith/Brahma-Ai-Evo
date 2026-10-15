# install_speech_deps.ps1
# Optional installers for Brahma Evo's local speech engines.
#
# Windows blocks .ps1 by default. Either run it from a PowerShell window after
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# (process-scoped; it does NOT change your machine settings) or invoke it as
#   powershell -ExecutionPolicy Bypass -File .\install_speech_deps.ps1
#
# Nothing here is required. The defaults (Google STT + Edge TTS) already work and
# this script is not run automatically. Install only the pieces you actually
# want to switch on in Settings > Speech Engines.
#
#   .\install_speech_deps.ps1                     # everything (recommended start)
#   .\install_speech_deps.ps1 -Component stt      # faster-whisper only
#   .\install_speech_deps.ps1 -Component tts      # piper + kokoro only
#   .\install_speech_deps.ps1 -Component sapi     # pyttsx3 only
#   .\install_speech_deps.ps1 -Component whisper   # faster-whisper + pre-download the model
#
# Local voices land in %LOCALAPPDATA%\BrahmaAI\voices and are auto-discovered,
# so no path needs pasting into the settings card.

param(
    [ValidateSet("all", "stt", "tts", "sapi", "whisper")]
    [string]$Component = "all",
    [string]$WhisperModel = "base.en"
)

$ErrorActionPreference = "Continue"

# Resolve the interpreter the app actually uses, so we never install into a
# different Python than the one that will import these packages.
# Resolve the interpreter the app actually uses, so we never install into a
# different Python than the one that will import these packages. The executable
# and any launcher flags are kept separate: `& "py -3.11"` treats the whole
# string as one command name and fails.
$pythonExe = $null
$pythonArgs = @()

# Prefer the app's virtualenv when present: bootstrap.ps1 creates .venv and the
# app runs from it, so it is the only interpreter whose site-packages the app
# actually sees. A bare `python` on PATH would install somewhere it never looks.
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $pythonExe = $venvPython
    $pythonArgs = @()
}
if (-not $pythonExe) {
    foreach ($candidate in @(@{Exe="py"; Args=@("-3.11")}, @{Exe="py"; Args=@("-3")}, @{Exe="python"; Args=@()})) {
        if (Get-Command $candidate.Exe -ErrorAction SilentlyContinue) {
            $pythonExe = $candidate.Exe
            $pythonArgs = $candidate.Args
            break
        }
    }
}
if (-not $pythonExe) {
    Write-Host "No Python interpreter found on PATH." -ForegroundColor Red
    exit 1
}
$pythonDesc = "$pythonExe $($pythonArgs -join ' ')".Trim()
Write-Host "Using interpreter: $pythonDesc" -ForegroundColor Cyan

function Invoke-Python {
    param([string[]]$Rest)
    & $pythonExe @pythonArgs @Rest
}

function Invoke-Pip {
    param([string[]]$Packages, [string]$What)
    Write-Host "`n==> $What" -ForegroundColor Cyan
    Invoke-Python @("-m", "pip", "install", "--upgrade", "pip") | Out-Null
    Invoke-Python (@("-m", "pip", "install") + $Packages)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    install reported a problem (exit $LASTEXITCODE); continuing." -ForegroundColor Yellow
    }
}

$voices = Join-Path $env:LOCALAPPDATA "BrahmaAI\voices"

if ($Component -in @("all", "stt", "whisper")) {
    Invoke-Pip -Packages @("faster-whisper") -What "faster-whisper (offline speech to text)"
}

if ($Component -in @("all", "tts")) {
    Invoke-Pip -Packages @("piper-tts", "onnxruntime", "kokoro-onnx") -What "Piper TTS + Kokoro runtime"
}

if ($Component -in @("all", "sapi")) {
    Invoke-Pip -Packages @("pyttsx3") -What "pyttsx3 (Windows SAPI voices)"
}

# --- pre-download the whisper model so the first utterance is not a slow one -- #
if ($Component -in @("all", "whisper")) {
    Write-Host "`n==> Pre-downloading faster-whisper model '$WhisperModel'" -ForegroundColor Cyan
    Write-Host "    (first run downloads ~75-150 MB; later runs are fully offline)" -ForegroundColor DarkGray
    Invoke-Python @("-c", "from faster_whisper import WhisperModel; WhisperModel('$WhisperModel', device='cpu', compute_type='int8'); print('model ready')")
}

Write-Host "`n==> Creating voice directory: $voices" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path (Join-Path $voices "piper") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $voices "kokoro") | Out-Null

if ($Component -in @("all", "tts")) {
    Write-Host @"

Manual step for local neural voices (the packages alone do not include them):
  Piper : download a voice .onnx + its .json from
          https://github.com/rhasspy/piper/releases
          and place BOTH files in  $voices\piper
  Kokoro: place the model .onnx + voices.bin in  $voices\kokoro

Until a voice file is present the card will show the engine as
"not installed" and Brahma will fall back to the next available engine,
so nothing breaks while you decide.
"@ -ForegroundColor DarkGray
}

Write-Host "`nDone. Open Brahma > Settings > Speech Engines to select the engines." -ForegroundColor Green
Write-Host "Reminder: a live Gemini session still uses the unified native voice." -ForegroundColor DarkGray