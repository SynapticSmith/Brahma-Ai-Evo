# Cookbook: Windows Desktop GUI Inspection & Control

## Objective
Locate a target Windows application window, inspect its UI Automation tree, and invoke actions without blind coordinate clicking.

## Prerequisites
- Windows OS with Python `pywin32` or PowerShell UIA access.

## Execution Recipe
1. **Find Target Window Handle**:
   ```powershell
   Get-Process | Where-Object { $_.MainWindowTitle -match "<AppName>" } | Select-Object Id, MainWindowTitle, MainWindowHandle
   ```
2. **Bring Target Window to Foreground**:
   ```powershell
   [Microsoft.VisualBasic.Interaction]::AppActivate("<AppName>")
   ```
3. **Inspect UIA Control Tree via PowerShell**:
   ```powershell
   Add-Type -AssemblyName UIAutomationClient
   $root = [System.Windows.Automation.AutomationElement]::FromHandle($hwnd)
   $condition = [System.Windows.Automation.Condition]::TrueCondition
   $elements = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
   ```
4. **Trigger UIA Button or Input Pattern**:
   Invoke `InvokePattern` on the matching `AutomationId` or `Name`.
5. **Verification Gate**:
   *Gate: Element state or window text changes within 3 seconds of invocation.*
6. **Remediation**:
   If UIA tree is empty, bring window to foreground and dispatch synthetic keyboard accelerators (`Alt+<key>`).
