---
name: windows-gui-automation
description: Native Windows GUI desktop automation protocol leveraging pywin32, UI Automation, window handles, coordinate clicks, and accessibility tree inspection.
metadata:
  domain: architecture
  tags: [windows, gui, automation, uia, win32, accessibility, desktop]
triggers:
  - "windows gui automation"
  - "automate windows desktop"
  - "click desktop element"
  - "inspect window handle"
  - "desktop ui automation"
  - "interact with windows app"
---

# Windows GUI Automation Skill

## Overview
Drawn from `Windows-Use`, this skill enables AI agents to inspect, manipulate, and automate Windows desktop applications via native OS handles (`HWND`), Microsoft UI Automation (UIA) accessibility trees, and synthetic input events. When an application lacks CLI or API endpoints, GUI automation provides a reliable bridge to read control states, trigger buttons, fill text fields, and capture window screenshots.

## Safety & Stability Rules
1. **Never Click Blind Coordinates**: Always resolve UI elements through UIA control IDs, AutomationIDs, or localized names before falling back to raw pixel coordinates.
2. **Foreground Focus Verification**: Ensure the target window handle (`GetForegroundWindow`) matches the target process before dispatching keystrokes.
3. **Graceful Timeout & Retry**: Desktop animations and loading states require bounded exponential backoff (e.g. wait up to 5 seconds for control emergence).
4. **No Destructive Keystrokes in System Dialogs**: Prohibit sending `Enter` or `Space` into OS elevation (UAC) or administrative confirmation dialogs.

## Execution Layers
1. **Process & Window Discovery**: Enumerate running processes and top-level windows (`EnumWindows`, `FindWindowEx`) to locate the application `HWND`.
2. **UIA Tree Inspection**: Query child controls using `UIAutomationClient` for elements with role `Button`, `Edit`, `ListItem`, or `Document`.
3. **Element Action**: Invoke `InvokePattern.Invoke()` or `ValuePattern.SetValue()` to interact deterministically without moving the physical cursor.
4. **Visual Verification**: Capture the window rect via `PrintWindow` or GDI bitblt to verify post-action state.
