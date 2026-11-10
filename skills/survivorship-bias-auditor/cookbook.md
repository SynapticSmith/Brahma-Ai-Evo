# Cookbook: Survivorship Bias Telemetry Audit & Silent Failure Detection

## Objective
Detect silent failure modes that bypass application logging, and implement external watchdog supervision.

## Prerequisites
- Application process logs and OS telemetry.

## Execution Recipe
1. **Inspect OS Event Logs for Hard Crashes**:
   Query Windows Event Log for unhandled application crashes (`Event ID 1000` / `1001`):
   ```powershell
   Get-WinEvent -FilterHashtable @{LogName='Application'; Level=1,2} -MaxEvents 20 | Select-Object TimeCreated, Message
   ```
2. **Audit Funnel Dropouts**:
   Compare inbound request counter with completed response counter:
   ```powershell
   $started = (Select-String -Path "app.log" -Pattern "TURN_STARTED").Count
   $completed = (Select-String -Path "app.log" -Pattern "TURN_COMPLETED").Count
   $silent_drops = $started - $completed
   Write-Host "Silent unlogged drops: $silent_drops"
   ```
3. **Instrument External Watchdog Probe**:
   Ensure an out-of-process monitor polls process health every 5 seconds.
4. **Verification Gate**:
   *Gate: `silent_drops == 0` or all non-completions accounted for with explicit exit codes.*
5. **Remediation**:
   If silent drops exist, register an unhandled exception handler (`sys.excepthook` / `atexit`).
