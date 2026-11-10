# Cookbook: Headless Browser Automation

## Objective
Automate web interactions, DOM extraction, and visual screenshot verification.

## Prerequisites
- playwright or selenium installed in environment

## Execution Recipe
1. **Pre-flight Check**:
   Verify browser driver availability.
2. **Execute Headless Task**:
   Launch automation script targeting URL with explicit selectors and wait states:
   ```powershell
   python -c "print('Navigating to target and awaiting DOM content')"
   ```
3. **Extract Content / Capture State**:
   Save HTML snapshot to `cache/browser_dump.html` and screenshot to `.assets/`.
4. **Verification Gate**:
   Verify output file size > 500 bytes and screenshot PNG signature exists.
5. **Remediation**:
   If selector timeout occurs, increase explicit wait timeout from 5s to 15s.
