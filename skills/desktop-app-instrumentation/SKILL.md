---
name: desktop-app-instrumentation
description: Intercepting, inspecting, and instrumenting native desktop application IPC, local sockets, named pipes, and RPC protocols to call GUI apps programmatically from Python.
metadata:
  domain: coding
  tags: [reverse-engineering, ipc, sockets, windows, instrumentation, api-wrapper, coding]
triggers:
  - "desktop app instrumentation"
  - "intercept desktop app"
  - "call gui app from python"
  - "reverse engineer desktop app"
  - "instrument desktop process"
  - "local ipc wrapper"
---

# Desktop App Instrumentation Skill

## Overview
Drawn from `mimic`, this skill enables AI agents to turn closed, proprietary desktop applications into scriptable, high-speed Python libraries. Most modern desktop applications (Slack, Discord, Spotify, CAD, trading terminals, IDEs) communicate internally over local HTTP endpoints, WebSockets, named pipes, or COM objects. By intercepting these local IPC protocols, agents can bypass slow, brittle pixel-based computer-use and drive applications at native bus speeds with 100% reliability.

## Interception Vectors
1. **Localhost HTTP / REST APIs**: Inspect local port listeners (`netstat -ano | findstr LISTENING`) used by background helpers and electron engines.
2. **Local WebSockets**: Intercept local debugger protocols (Chrome DevTools Protocol `CDP` on `--remote-debugging-port`).
3. **Windows Named Pipes**: Enumerate and interact with named pipes (`\\.\pipe\...`) used by native Windows services.
4. **COM & Automation Interfaces**: Query registered OLE/COM interfaces (`win32com.client.Dispatch`).

## Safety & Ethics Invariants
1. **Local Process Scope Only**: Instrumentation is strictly limited to user-owned, locally running applications on the development workstation.
2. **Never Bypass Security Controls**: Do not disable authentication tokens, tamper with encryption certificates, or alter security telemetry.
3. **Graceful Connection Recovery**: Always implement reconnect handlers and heartbeat monitors in generated client wrappers.
