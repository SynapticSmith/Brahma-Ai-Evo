@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Launch Brahma Evo with a supported Python interpreter.
rem Python 3.11 is the primary supported runtime with dependencies installed.
set "PY="

rem Check .venv first only if it has the required runtime installed
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -c "import PyQt6" >nul 2>&1 && set PY="%~dp0.venv\Scripts\python.exe"
)
rem Fallback to Python 3.11 launcher (installed system runtime)
if not defined PY ( py -3.11 -c "import PyQt6" >nul 2>&1 && set "PY=py -3.11" )
if not defined PY ( py -3.11 --version >nul 2>&1 && set "PY=py -3.11" )
if not defined PY ( py -3.12 --version >nul 2>&1 && set "PY=py -3.12" )
if not defined PY ( if exist "%~dp0.venv\Scripts\python.exe" set PY="%~dp0.venv\Scripts\python.exe" )
if not defined PY ( py -3    --version >nul 2>&1 && set "PY=py -3" )
if not defined PY ( python   --version >nul 2>&1 && set "PY=python" )

if not defined PY (
  echo ERROR: no Python found. Install Python 3.11, or run .\scripts\setup_dev.ps1 to bootstrap one.
  exit /b 1
)

rem Diagnostic/CI helper: print the chosen interpreter and exit without launching.
if defined BRAHMA_PRINT_PY (
  echo %PY%
  exit /b 0
)

%PY% "%~dp0main.py" %*
endlocal
