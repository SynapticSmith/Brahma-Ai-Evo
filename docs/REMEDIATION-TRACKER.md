# Brahma AI Evo — Master Remediation Tracker

**Document Role:** Live Execution Ledger for Whole-System Remediation (`Batch R-1` through `Batch R-4`)  
**Source Authority:** [`docs/SYSTEM-ANALYSIS-MASTER-REPORT.md`](file:///C:/Users/Admin/Brahma-Ai-Evo/docs/SYSTEM-ANALYSIS-MASTER-REPORT.md) and [`docs/scan-reports/WP-01-agent-harness.md`](file:///C:/Users/Admin/Brahma-Ai-Evo/docs/scan-reports/WP-01-agent-harness.md) – [`WP-12-documentation-and-drift.md`](file:///C:/Users/Admin/Brahma-Ai-Evo/docs/scan-reports/WP-12-documentation-and-drift.md)  
**Started:** 2026-11-08  
**Completed:** 2026-11-08  
**Overall Status:** `COMPLETE`

---

## Progress Overview

| Wave | Batch & Domain Scope | Findings Covered | Status |
|---|---|---|---|
| **Wave 1** | **Batch R-1A:** Agentic Harness, Tool Dispatch Boundary (`I-01`) & SQLite Store Hardening | `FIND-004`, `FIND-005`, `FIND-010`, `FIND-011`, `FIND-018`, `FIND-036`, `FIND-041`, `FIND-042`, `FIND-043`, `FIND-044` | `[x] Complete` |
| **Wave 2** | **Batch R-1B:** Host RCE Elimination, Crucible Sandbox, Action Injections & DPAPI Secret Protection | `FIND-001`, `FIND-002`, `FIND-003`, `FIND-006`, `FIND-019`, `FIND-020`, `FIND-021`, `FIND-022`, `FIND-023`, `FIND-059`, `FIND-061` | `[x] Complete` |
| **Wave 3** | **Batch R-1C:** Dashboard UAC/LAN Hardening, Android Companion TLS/Path Confinement & Discord Bot | `FIND-007`, `FIND-008`, `FIND-009`, `FIND-028`, `FIND-032` | `[x] Complete` |
| **Wave 4** | **Batch R-2A:** Session Lifecycle Resumption, Egress Kill-Switches (`I-21`), Audio/Proactive Gating & Atomic Writes (`I-17`) | `FIND-024`, `FIND-025`, `FIND-026`, `FIND-027`, `FIND-033`, `FIND-034`, `FIND-035`, `FIND-055`, `FIND-056` | `[x] Complete` |
| **Wave 5** | **Batch R-2B & R-3:** UI Threading (`I-25`), Approval Queue, WebEngine Lazy Imports (`I-26`), Seam Wiring & Dead Code Pruning | `FIND-012`, `FIND-013`, `FIND-016`, `FIND-017`, `FIND-029`, `FIND-030`, `FIND-031`, `FIND-037`, `FIND-045`–`FIND-054`, `FIND-057`, `FIND-058`, `FIND-060`, `FIND-062`, `FIND-063` | `[x] Complete` |
| **Wave 6** | **Batch R-4:** PyInstaller Packaging, Frozen Asset/Subagent Paths, Test Coverage & Documentation Reconciliation | `FIND-014`, `FIND-015`, `FIND-038`, `FIND-039`, `FIND-040` | `[x] Complete` |

---

## Detailed Task Ledger

### Wave 1 — Batch R-1A: Agentic Harness, Tool Dispatch Boundary (`I-01`) & SQLite Store Hardening

- [x] **`FIND-004` — Path Traversal Boundary Enforcement (`agent/spec.py`, `actions/file_controller.py`):**
  - Add `"name"` to `PATH_ARG_KEYS` in `agent/spec.py:35-50`.
  - Enforce canonical path confinement in `actions/file_controller.py` so `name` cannot traverse outside the target base directory (`../`).
- [x] **`FIND-005` — Close `DynamicToolRegistry` & Voice MCP Permission Bypass (`core/tool_exec.py`, `core/dynamic_registry.py`, `core/mcp_manager.py`):**
  - Ensure tools registered in `DynamicToolRegistry` (including `features/*` and Voice MCP tools in `core/mcp_manager.py`) are wrapped/executed through `AgentRuntime.execute` so `I-01` holds across every execution door.
  - Keep `core/tool_exec.py` within ratchet ceilings ($\le 147$ lines, `0` `elif` branches).
- [x] **`FIND-010` — Fix `ToolIndex` In-Memory SQLite Thread Sharing (`core/tool_index.py:504`):**
  - Instantiate `_Fts5Backend` `:memory:` SQLite connection with `check_same_thread=False` and synchronize queries with a `threading.Lock`.
- [x] **`FIND-011` & `FIND-036` — Fix `workspace_store.py` Eager Import Crash & SQLite Connection Leaks (`workspace_store.py`, `agent/session.py`):**
  - Defer SQLite connection/WAL initialization in `workspace_store.py` until first method call (lazy `_StoreProxy` / lazy init) so importing `workspace_store` has zero import-time disk I/O (`I-26`), and recover gracefully from corrupt SQLite files.
  - Close every `sqlite3.Connection` cleanly on context exit (`with contextlib.closing(self._connect()) as conn:`) in both `workspace_store.py` and `agent/session.py`.
  - Resolve `_default_db_path()` in `agent/session.py` via `core.user_paths` without triggering `workspace_store` instantiation.
- [x] **`FIND-018` — Fix SSRF TOCTOU DNS Rebinding in `web_fetch` (`agent/web.py`):**
  - Pin validated IP addresses in `agent/web.py` custom HTTP/HTTPS connection handlers while preserving `Host` header and TLS SNI `server_hostname`.
- [x] **`FIND-041` — Fix Parallel Dispatch Scheduler Path Extraction (`agent/loop.py:544-550`):**
  - Inspect all canonical path keys in `PATH_ARG_KEYS` inside `AgentLoop._call_effect`, and treat relative parent traversals (`..`) in `glob_files`/`grep_files` patterns conservatively.
- [x] **`FIND-042` — Synchronize `MCPSession` Refresh & Calls (`agent/mcp.py`):**
  - Add a `threading.RLock` to `MCPSession` to prevent concurrent `refresh()` and `call()` races.
- [x] **`FIND-043` — Harden Windows Process Tree Termination (`agent/process.py`):**
  - Set `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` on the Windows Job Object and always invoke `taskkill /F /T` as a belt-and-suspenders fallback in `_kill_tree` on Windows.
- [x] **`FIND-044` & `WP-01-F06` — Fix `ToolSpec.format_output` Small-Cap Underflow & Registry Lock (`agent/spec.py`):**
  - Prevent negative `head` slice when `cap < 1200` in `format_output`, and guard `_REGISTRY` access with a `threading.RLock`.

---

### Wave 2 — Batch R-1B: Host RCE Elimination, Crucible Sandbox, Action Injections & DPAPI Secret Protection

- [x] **`FIND-061` — Remove Unrestricted `.py` `exec()` & `-m pip` CLI Hook in `main.py:3206-3226`:**
  - Remove the arbitrary script `exec()` and `-m pip` interception block in `main.py` so `BrahmaEvo.exe` cannot be used as a signed LOLBin interpreter.
- [x] **`FIND-001` — Harden `SkillCrucible` Against Host RCE (`core/skill_crucible.py`):**
  - Expand AST safety validation to block dangerous builtins (`__import__`, `getattr`, `open`, `compile`, `globals`, `locals`, dunder attribute access) and restricted imports (`ctypes`, `socket`, `http`, `urllib`, `pathlib`, `shutil`, `tempfile`), and run sandbox verification via `agent.process.run_captured` with scrubbed environment, isolated `-I` flags, and Job Object process-tree containment.
- [x] **`FIND-002` — Fix Python Script & `schtasks` XML Injection (`actions/reminder.py`):**
  - Replace raw string formatting in `.pyw` script generation with `repr()` / JSON-safe argument loading and escape XML fields with `xml.sax.saxutils.escape`, executing `schtasks` without `shell=True`.
- [x] **`FIND-003` — Fix PowerShell Window Title Injection (`actions/computer_control.py:245-249`):**
  - Replace interpolated PowerShell command string in `focus_window` with safe environment-variable parameter passing (`$env:BRAHMA_TARGET_TITLE`) and `shell=False`.
- [x] **`FIND-019` — Remove Arbitrary `exec()` in `actions/desktop.py` & Reclassify `desktop_control` (`core/desktop_tools.py`, `actions/desktop.py`):**
  - Remove raw `exec()` evaluation of arbitrary Python code in `actions/desktop.py` and mark `desktop_control` as requiring approval when performing destructive operations.
- [x] **`FIND-006`, `FIND-020`, `FIND-021`, `FIND-022`, `FIND-023` — DPAPI Secret Store & Plaintext Credential Remediation:**
  - `actions/instagram_mcp.py` & `actions/instagram_chat.py`: Use `core.secret_store` (`set_secret`/`load_secrets`) instead of raw `open("config/api_keys.json", "w")`, and move `ig_debug.log` to `get_user_data_dir() / "logs"`.
  - Migrate raw `api_keys.json` readers (`workspace_store.py`, `dashboard/server.py`, `discord_bot.py`, `actions/*`) to `core.secret_store`.
  - `core/ui_connectivity.py`: Store `local_api_key` and `custom_api_key` in `core.secret_store` rather than dumping plaintext into `app_settings.json`.
  - `core/ui_devices.py`, `actions/unlock_device.py`, `core/ui_discord.py`: Store Android `device_pins` and Discord `bot_token` via `core.secret_store` and use `atomic_write_json`.
  - `actions/google_workspace_mcp.py` & `actions/spotify_controller.py`: Protect `.email_key` and Spotify OAuth tokens via `core.secret_store`.
- [x] **`FIND-059` — Fail-Closed Release Signature Verification (`core/release_manager.py:29-31`):**
  - Reject empty/unconfigured `PINNED_RELEASE_PUBLIC_KEY` instead of comparing against `sha256(b"")`.

---

### Wave 3 — Batch R-1C: Dashboard UAC/LAN Hardening, Android Companion TLS/Path Confinement & Discord Bot

- [x] **`FIND-007` & `FIND-028` — Remediate Web Dashboard Security (`dashboard/server.py`, `dashboard/static/*.html`):**
  - Remove `_ensure_network_access()` elevated UAC (`ShellExecuteW("runas")`) firewall and network-category manipulation.
  - Default bind host to `127.0.0.1` (unless explicitly configured), add `CORSMiddleware` restricted to local origins, add `is_relative_to(bg_dir)` path confinement on `/web_background/{filename:path}`, add PIN brute-force rate limiting/lockout on `/login`, cap `MAX_UPLOAD_MB = 25` with filename sanitization and executable extension blocking, and remove `chrome://flags` insecure origin instructions from `login.html` / `app.html`.
- [x] **`FIND-008` & `FIND-009` — Harden Android Companion (`brahma-connect-android/...`):**
  - Set `android:usesCleartextTraffic="false"` in `AndroidManifest.xml`.
  - Update `BrahmaWebSocketClient.kt` to use `wss://` by default (with secure header-based token transport).
  - Add canonical path confinement (`canonicalFile.toPath().startsWith(...)`) to `handleFileRead`, `handleFileWrite`, and `handleFileDelete`, and restrict `handleOpenUrl` to `http`/`https` schemes in `DeviceCommandHandler.kt`.
- [x] **`FIND-032` & `REL-WP05-01` — Harden Discord Bot (`discord_bot.py`):**
  - Remove synchronous `pip install discord.py` subprocess execution on import (`I-26`).
  - Route unbound replies through `AgentRuntime` / `provider_router.guard` and load keys via `core.secret_store`.

---

### Wave 4 — Batch R-2A: Session Lifecycle Resumption, Egress Kill-Switches (`I-21`), Audio/Proactive Gating & Atomic Writes (`I-17`)

- [x] **`FIND-033` & `FIND-034` — Fix `SessionLifecycle` Host Attribute Scoping (`core/session_lifecycle.py`):**
  - Fix `_build_config` (`line 563`) to look up `getattr(self._host, "_resume_handle", None)`.
  - Fix `_pause_until` (`lines 88, 94-98, 105`) to read `_control_wake`, `_control_seq`, and `_pause_seen` from `self._host`.
- [x] **`FIND-024` & `FIND-025` — Enforce Provider Egress Kill-Switches (`I-21`):**
  - Check `provider_router.is_enabled("gemini")` / `provider_router.guard(...)` in `main.py:_update_memory_async`, `core/session_lifecycle.py:voice_backend`, `core/local_brain.py`, `core/llm_gateway.py:_chain`, `actions/screen_processor.py`, `core/shopping_agent.py`, and `actions/reverse_engineer.py`.
- [x] **`FIND-026` — Fix Browser MCP Subprocess Tree Termination (`actions/playwright_mcp_client.py`, `actions/rea_mcp_client.py`):**
  - Terminate whole process trees on Windows (`taskkill /F /T /PID`) on close/exit so `node.exe` and `chrome.exe` children do not leak.
- [x] **`FIND-027`, `FIND-035`, `FIND-056` — Fix Proactive Speech Gating, Clipboard Redaction & Audio TTS Serialization:**
  - Pass `proactive=True` in `main.py:_clipboard_monitor`, `core/attention_flow.py`, and `actions/attention_monitor.py`.
  - Redact secret-shaped patterns in `core/clipboard_sentry.py` / `main.py` before sending clipboard snippets to an LLM.
  - Serialize non-Gemini TTS playback in `core/audio_io.py` with a playback lock and make `winsound` playback in `core/speech_tts.py` cancellable (`SND_ASYNC` / `SND_PURGE`).
- [x] **`FIND-055` — Enforce Atomic JSON Writes (`I-17`) & Remove Import-Time Disk Mutation (`I-26`):**
  - Update `core/identity.py` to store user modifications in `get_user_data_dir() / "config" / "identity.json"` (seeding defaults from repo `config/identity.json` without calling `self.save()` at import time) via `atomic_write_json`.
  - Replace raw `open(..., "w")` with `atomic_write_json` in `core/learned_rules.py`, `core/boot_sentry.py`, `core/flowstate.py`, `sound_manager.py`, `core/device_location.py`, and `core/study_guard.py`, and upgrade `http://ip-api.com` to HTTPS.

---

### Wave 5 — Batch R-2B & R-3: UI Threading (`I-25`), Approval Queue, WebEngine Lazy Imports (`I-26`), Seam Wiring & Dead Code Pruning

- [x] **`FIND-012` & `FIND-058` — Fix Back-to-Back Approval Deadlock & Tray Visibility (`core/confirm.py`, `core/ui_overlays.py`):**
  - Serialize concurrent `ask_blocking_choice` calls with a FIFO queue / condition variable in `core/confirm.py` so back-to-back tool approvals never overwrite `_pending` or deadlock worker threads, and ensure the window restores/alerts when minimized to tray.
- [x] **`FIND-013` — Marshal Clipboard Sentry UI Updates to Qt Main Thread (`core/ui_shell_actions.py`):**
  - Dispatch `_on_clipboard_actionable` widget mutations onto the Qt main GUI thread via `run_on_ui` on `self._win`.
- [x] **`FIND-016` — Eliminate 1.5s PowerShell Subprocess Fork Bomb (`core/ui_sysmetrics.py`):**
  - Remove the recurring 1,500ms `powershell.exe` `_quiet_run` call in `_get_temp` and rely on `psutil` / lightweight telemetry.
- [x] **`FIND-017` — Fix Missing `np` and `cv2` Imports in `ScanningOverlay` (`core/ui_transients.py:124`):**
  - Add guarded lazy `import numpy as np` and `import cv2` in `ScanningOverlay._generate_layout`.
- [x] **`FIND-037` — Lazy `QtWebEngineWidgets` Imports & Safe `runJavaScript` Encoding (`core/radar_hud.py`, `core/space_cleaner_hud.py`, `core/globe_window.py`, `installer/install_wizard.py`):**
  - Defer `QtWebEngineWidgets` imports to function/method scope (`I-26` / `D46`), wrap dynamic strings in `json.dumps()` before `runJavaScript`, and use `core.qt_bootstrap.bootstrap()`.
- [x] **`FIND-051` — Stop Zombie Polling Timer in Hidden `SmartDevicesSection` (`core/ui_devices.py`, `core/ui_panels.py`):**
  - Stop `_poll_tmr` on `hideEvent` and restart on `showEvent` in `SmartDevicesSection`.
- [x] **`FIND-052`, `FIND-053`, `FIND-054`, `FIND-057`, `FIND-060` — UI & Action Polish:**
  - Fix `hasattr(self, "_on_chat_event")` $\rightarrow$ `"on_chat_event"` in `core/ui_runtime.py:156`.
  - Remove duplicate `subprocess.Popen` in `core/ui_connectivity.py` theme restart and persist via `_set_setting`.
  - Remove blocking `tkinter.simpledialog` in `actions/youtube_video.py`.
- [x] **`FIND-029`, `FIND-030`, `FIND-031`, `FIND-063` — Wire Disconnected Architectural Seams (`main.py`, `core/ui_wiring.py`, `core/ui_mute_send.py`, `core/ui_connectivity.py`, `core/ui_agent_panel.py`):**
  - Wire `brahma_connect.on_chat_message` in `core/ui_wiring.py` and add `brahma_connect.stop()`, `sensorium.stop()`, and `plugin_manager.dispatch("on_shutdown")` to `main()`'s `finally` block.
  - Wire `AgentBridge.publish_tools()` into startup and `AgentBridgeCard`, and wire `/skills` command and Settings Hub button to `SkillReviewDialog` (keeping `main.py <= 3,265` lines and `ui.py <= 827` lines).
- [x] **`FIND-045`–`FIND-050` & `FIND-062` — Deduplicate Skills & Tighten Lint Gates:**
  - Deduplicate `skills/` vs `.brahma/skills/` in `agent/skills.py` so identical bundled skills do not emit 12 shadow warnings per turn.

---

### Wave 6 — Batch R-4: PyInstaller Packaging, Frozen Asset/Subagent Paths, Test Coverage & Documentation Reconciliation

- [x] **`FIND-014`, `FIND-015`, `FIND-038` — Fix PyInstaller Specs & Frozen Path Resolution (`installer/*.spec`, `agent/subagent_library.py`, `core/user_paths.py`):**
  - Update `agent/subagent_library.py:156` and `core/user_paths.py` (`get_asset_path`) to check `sys._MEIPASS` and `Path(sys.executable).parent` in frozen builds.
  - Update `installer/BrahmaEvo.spec` to bundle `brahma_api`, `agent`, `uvicorn` dynamic protocols, `yaml`, and `fastembed`.
  - Replace hardcoded `D:\TiTech Prabha Solution\...` paths in `installer/BrahmaEcho.spec` with dynamic relative paths.
- [x] **`FIND-039` — Add Automated Unit Tests & Sandbox `accept_instructions.py` (`tests/test_dashboard.py`, `accept_instructions.py`):**
  - Sandbox `accept_instructions.py` so it writes to a temporary directory instead of mutating `%LOCALAPPDATA%\BrahmaAI\config\app_settings.json`.
  - Add `tests/test_dashboard.py` covering Dashboard PIN lockout, `/web_background` path traversal blocking, upload limits, and local binding.
- [x] **`FIND-040` — Reconcile Documentation Drift (`docs/ARCHITECTURE-SPEC.md`):**
  - Reconcile the internal contradictions in `docs/ARCHITECTURE-SPEC.md` (§5.2 / §5.3 / Appendix B vs §1 / §6.3 / §7.1 / §12) and record Appendix D.9 for `Batch R-1` through `Batch R-4`.
