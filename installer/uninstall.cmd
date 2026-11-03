@echo off
setlocal enabledelayedexpansion
title Brahma Evo Uninstaller
echo ===============================================================
echo                Brahma Evo Certified Uninstaller
echo ===============================================================
echo.

:: 1. Terminate any running Brahma instances
echo [1/5] Terminating running Brahma Evo processes...
taskkill /F /IM BrahmaEvo.exe /T >nul 2>&1
taskkill /F /IM BrahmaEvo_Setup.exe >nul 2>&1
timeout /t 1 /nobreak >nul

:: 2. Identify target install directory
set "TARGET_DIR=%~dp0"
if "%TARGET_DIR:~-1%"=="\" set "TARGET_DIR=%TARGET_DIR:~0,-1%"

:: 3. Remove Desktop and Start Menu shortcuts
echo [2/5] Removing application shortcuts...
del /f /q "%USERPROFILE%\Desktop\Brahma Evo.lnk" >nul 2>&1
del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Brahma Evo.lnk" >nul 2>&1
del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Uninstall Brahma Evo.lnk" >nul 2>&1
del /f /q "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Brahma Evo.lnk" >nul 2>&1

:: 4. Remove Windows Registry uninstall entry
echo [3/5] Unregistering from Windows Installed Apps...
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\BrahmaEvo" /f >nul 2>&1

:: 5. Prompt for user configurations
echo.
if "%~1"=="/S" goto SilentMode
if "%~1"=="-s" goto SilentMode

choice /C YN /M "Do you also want to remove personal data, cache, and models (%LOCALAPPDATA%\BrahmaAI)?"
if errorlevel 2 goto SkipUserData
if errorlevel 1 goto RemoveUserData

:SilentMode
:RemoveUserData
echo [4/5] Removing user cache and configurations...
rd /s /q "%LOCALAPPDATA%\BrahmaAI" >nul 2>&1
goto CleanFiles

:SkipUserData
echo [4/5] Keeping user cache for future installs.

:CleanFiles
echo [5/5] Removing application files...
echo.
echo ===============================================================
echo         Brahma Evo was successfully uninstalled!
echo ===============================================================
echo.

:: Detach cmd to delete target directory after script exits
start "" /B cmd.exe /c "timeout /t 2 /nobreak >nul & rd /s /q \"%TARGET_DIR%\""

if "%~1"=="/S" exit /b 0
if "%~1"=="-s" exit /b 0

timeout /t 3 >nul
exit /b 0
