@echo off
setlocal
rem Ensure Python Scripts directory is on PATH for this session
set "SCRIPTS_DIR=%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\LocalCache\local-packages\Python312\Scripts"
if exist "%SCRIPTS_DIR%" set "PATH=%SCRIPTS_DIR%;%PATH%"

where letta >nul 2>&1
if errorlevel 1 (
  echo Error: letta executable not found.
  echo Expected at: %SCRIPTS_DIR%\letta.exe
  pause
  exit /b 1
)

rem If no arguments were given, start local App Server listener (offline, zero-cloud)
if "%~1"=="" (
  echo Starting local Letta App Server on 127.0.0.1:8283 ^(offline mode^)...
  letta server --listen ws://127.0.0.1:8283
) else (
  echo Starting Letta Server with arguments: %*
  letta server %*
)
