# Creates a "Brahma Evo" desktop shortcut for the CURRENT user and machine.
# All paths are resolved from this script's location, so nothing is tied to a
# specific checkout, drive, or username.
$repo = Split-Path -Parent $PSScriptRoot
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'Brahma Evo.lnk'

$pythonw = Join-Path $repo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = Join-Path $repo '.venv\Scripts\python.exe' }
$mainPy = Join-Path $repo 'main.py'
$icon = Join-Path $repo 'assets\Brahma_Lite_Logo.ico'

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = $pythonw
$Shortcut.Arguments = '"{0}"' -f $mainPy
$Shortcut.WorkingDirectory = $repo
$Shortcut.WindowStyle = 7
$Shortcut.Description = 'Launch Brahma Evo'
if (Test-Path $icon) { $Shortcut.IconLocation = "$icon,0" }
$Shortcut.Save()
