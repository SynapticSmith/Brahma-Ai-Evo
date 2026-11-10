# Cookbook: Desktop Process Interception & Python Wrapping

## Objective
Identify local IPC channels (ports, pipes, COM) of a desktop app and generate a scriptable Python wrapper.

## Prerequisites
- Target desktop app running locally.
- PowerShell or Python with `requests`, `websockets`, or `pywin32`.

## Execution Recipe
1. **Find Process PID & Local Listening Ports**:
   ```powershell
   $proc = Get-Process "<AppName>"
   Get-NetTCPConnection -OwningProcess $proc.Id | Where-Object { $_.State -eq "Listen" }
   ```
2. **Inspect Named Pipes**:
   ```powershell
   [System.IO.Directory]::GetFiles("\\.\\pipe\\") | Select-String "<AppName>"
   ```
3. **Probe Endpoint with JSON-RPC / HTTP**:
   ```powershell
   Invoke-RestMethod -Uri "http://127.0.0.1:<port>/api" -Method Get
   ```
4. **Author Python Wrapper Class**:
   Wrap discovered endpoints into typed methods (`def perform_action(self, payload): ...`) using `urllib` or `requests`.
5. **Verification Gate**:
   *Gate: Python wrapper executes commands and retrieves state from the running app in <100ms.*
6. **Remediation**:
   If endpoints require auth tokens, locate session tokens in `%APPDATA%/<AppName>/Local State` or launch flags.
