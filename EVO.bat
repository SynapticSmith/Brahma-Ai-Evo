@echo off
setlocal EnableExtensions

rem Launch Brahma Evo with a supported Python interpreter.
rem Preference order (same probe style as install_speech_deps.ps1):
rem   1. the repo virtualenv created by bootstrap.ps1 / start_brahma.bat
rem   2. py -3.12  3. py -3.11   4. py -3   5. python on PATH
rem README supports Python 3.11 or 3.12; the venv keeps us inside that range.
set "PY="

if exist "%~dp0.venv\Scripts\python.exe" set PY="%~dp0.venv\Scripts\python.exe"
if not defined PY ( py -3.12 --version >nul 2>&1 && set "PY=py -3.12" )
if not defined PY ( py -3.11 --version >nul 2>&1 && set "PY=py -3.11" )
if not defined PY ( py -3    --version >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python   --version >nul 2>&1 && set "PY=python" )

if not defined PY (
  echo ERROR: no Python found. Install Python 3.11 or 3.12, or run start_brahma.bat to bootstrap one.
  exit /b 1
)

rem Diagnostic/CI helper: print the chosen interpreter and exit without launching.
if defined BRAHMA_PRINT_PY (
  echo %PY%
  exit /b 0
)

%PY% "%~dp0main.py" %*
endlocal
