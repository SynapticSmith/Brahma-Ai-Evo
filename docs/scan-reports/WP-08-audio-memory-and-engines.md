# Deep System Scan Report: Work Package WP-08
# Audio/Speech Pipeline, Avatars, Perception, Memory, Smart Home & Core Sentinels

**Target Deliverable:** `docs/scan-reports/WP-08-audio-memory-and-engines.md`  
**Baseline Commit:** `6439b2efde3c36b1d34b21e4cc5e93baff31b2b5`  
**Scan Scope:** Audio/Speech, Avatars, Voice MCP, Identity & Prompts, Memory Subsystems, Smart Home, Perception/Sensorium, Flows, and Core Sentinels  
**Overall Subsystem Health:** **DEGRADED (CRITICAL SECURITY & GATING DEFECTS DETECTED)**

---

## Executive Summary

Work Package WP-08 covers 56 core subsystems across Brahma AI Evo spanning low-level Windows audio I/O, speech synthesis/recognition, Live2D/VRM 3D avatar rendering runtimes, voice-layer MCP tools, user persona & prompt injection, local and cloud memory engines, smart home IoT device control, passive background telemetry/sensorium, multi-application flow routers, and system sentinels.

While the low-level Windows hardware integrations (such as direct Win32/NT memory manipulation in `core/mem_reduct.py`, robust byte-level stream resumability in `core/file_download.py`, and low-latency audio duplexing in `core/audio_io.py`) exhibit advanced systems engineering, the audit identified **multiple critical architectural and security vulnerabilities**:
1. **Critical Permission Boundary Bypass in Voice MCP (`core/mcp_manager.py` vs `core/tool_exec.py`):** Live voice MCP tools registered by `MCPServerManager` are executed directly via `DynamicToolRegistry.execute_sync`, completely bypassing `AgentRuntime.execute`. This breaks Invariant `I-01` by eliminating policy enforcement, workspace scoping, and user approval banners for voice-triggered MCP calls.
2. **Proactive Voice Commentary Gating Breaches:** The user's explicit preference to disable unsolicited speech (`proactive_voice_comments_enabled: false` in `config/identity.json`) is violated across multiple background daemons. In `core/audio_io.py:361`, `speak()` defaults to `proactive=False`, allowing background triggers like the clipboard sentry (`main.py:3162`) and attention monitor (`core/attention_flow.py`) to broadcast unprompted speech over the speakers.
3. **Dead / Orphaned Subsystems:** `core/live_voice_engine.py` (663 lines) and `core/memory_cortex.py` (409 lines) represent completely duplicate, orphaned engines that have zero active consumers in the production desktop runtime.
4. **Audio & TTS Resource Leaks / Re-entrancy Failures:** Local TTS engines (`piper`, `kokoro`, `gemini`) rely on synchronous `winsound.PlaySound` calls that block execution threads and cannot be cancelled mid-sentence, while Edge TTS spawns untracked detached PowerShell processes.
5. **Import-Time Disk Mutation & Source-Tree Pollution in `core/identity.py`:** `IdentityService` (`core/identity.py:38, 79-86, 205`) resolves `IDENTITY_FILE = get_base_dir() / "config" / "identity.json"` inside the repository/install tree instead of `get_user_data_dir()`, and instantiates `identity = IdentityService()` at module import scope—immediately reading and writing `config/identity.json` on import (violating Invariant `I-26`). *(Note: `smart_home/storage.py:42-105, 184-225` properly encrypts IoT credentials via `CredentialVault` using Fernet + Windows DPAPI).*
6. **Widespread Invariant I-17 Violations:** Configuration files (`config/identity.json`, `learned_rules.json`, `sound_settings.json`, `flowstate_snapshots.json`) are modified using non-atomic `open(..., "w")` writes and bare `write_text()`, presenting serious risk of corruption on sudden power loss or crashes.

---

## 1. Subsystem Architecture & Data-Flow Summary

```
                      ┌────────────────────────────────────────────────────────┐
                      │                   BRAHMA DESKTOP HUD                   │
                      └──────────────────────────┬─────────────────────────────┘
                                                 │
                   ┌─────────────────────────────┴─────────────────────────────┐
                   ▼                                                           ▼
     ┌──────────────────────────┐                                ┌──────────────────────────┐
     │      Live Audio I/O      │                                │    Passive Sensorium     │
     │   (PortAudio / PyAudio)  │                                │   & Telemetry Sentry     │
     └─────────────┬────────────┘                                └─────────────┬────────────┘
                   │                                                           │
        ┌──────────┴──────────┐                                     ┌──────────┴──────────┐
        ▼                     ▼                                     ▼                     ▼
 ┌──────────────┐      ┌──────────────┐                      ┌──────────────┐      ┌──────────────┐
 │ Gemini Live  │      │  Offline TTS │                      │ Window Watch │      │  Clipboard   │
 │ Duplex Stream│      │  & STT Engine│                      │ & Dwell Tele │      │ Sentry (Poll)│
 └──────┬───────┘      └──────┬───────┘                      └──────┬───────┘      └──────┬───────┘
        │                     │                                     │                     │
        └──────────┬──────────┘                                     └──────────┬──────────┘
                   ▼                                                           ▼
     ┌──────────────────────────┐                                ┌──────────────────────────┐
     │    Voice MCP Manager     │                                │   Autonomous Sentinels   │
     │  (core/mcp_manager.py)   │                                │(Game/Net/Study/Flowstate)│
     └─────────────┬────────────┘                                └─────────────┬────────────┘
                   │ [BYPASSES AgentRuntime]                                   │
                   ▼                                                           ▼
     ┌──────────────────────────┐                                ┌──────────────────────────┐
     │   Dynamic Tool Execute   │                                │   Hardware & Memory NT   │
     │  (core/tool_exec.py)     │                                │  (MemReduct/PowerScheme) │
     └──────────────────────────┘                                └──────────────────────────┘
```

### 1.1 Audio & Speech Pipeline
- **`core/audio_devices.py`**: Enumerates host audio input and output endpoints using Windows Multimedia APIs (`pycaw`, `comtypes`), Win32 `device_switch_watcher`, and `sounddevice`. Provides hotplug detection, volume scalar querying, and system default device switching.
- **`core/audio_io.py`**: Manages low-latency real-time microphone capture and speaker output streams via `pyaudio.PyAudio`. Formats audio into 512-sample (16kHz 16-bit PCM = 1024 bytes) frames for direct bidirectional Gemini Live streaming. Implements `AudioLock` for hardware exclusivity.
- **`core/echo.py` & `sound_manager.py`**: `EchoCancellation` provides software-based loopback feedback suppression and software ducking during assistant speech. `sound_manager.py` handles UI sound effects (chimes, clicks, alerts) with volume ramping.
- **`core/speech_stt.py` & `core/speech_tts.py`**: High-level speech abstraction layers. STT provides fallback offline transcription via local Whisper (`faster_whisper`). TTS provides multi-engine speech synthesis supporting Edge TTS, Piper TTS, Kokoro TTS, Gemini TTS, and SAPI5.
- **`core/live_voice_engine.py`**: **ORPHANED.** A 663-line self-contained live voice assistant implementation containing its own PyAudio streams, Silero VAD, WebRTC VAD, and Gemini Live websocket client. Not referenced anywhere in the production desktop runtime.

### 1.2 Avatars & 3D Visualization Runtimes
- **`core/avatar_catalog.py` & `core/avatar_runtime.py`**: Discovers, parses, validates, and manages 3D VRM (VRM 0.0 and VRM 1.0) and 2D Live2D character models. Communicates with a local webview rendering surface via WebSocket / Qt WebEngine IPC. Drives viseme lip-sync, blink cycles, emotion blending, and mouse gaze tracking.
- **`fetch_live2d_model.py`, `fetch_vrm_model.py`, `vendor_vrm_runtime.py`, `vrm_license_report.py`**: Utility scripts for downloading remote models via verified chunks (`core/file_download.py`), extracting embedded glTF binaries, vendoring three-vrm JavaScript bundles, and auditing redistribution licenses.

### 1.3 Voice MCP Manager
- **`core/mcp_manager.py`**: Dedicated Model Context Protocol (MCP) client specifically constructed for the Live Voice path. Connects to external servers via stdio using the official `mcp` SDK and `asyncio`. Discovers external tools and registers them into `core.dynamic_tools.DynamicToolRegistry`.

### 1.4 Identity, Persona & Prompts
- **`core/identity.py`, `core/instruction_injector.py`, `core/prompt_parts.py`, `core/learned_rules.py`**: Houses the assistant persona ("Brahma Evo"), behavior modes (`full`, `minimal`, `silent`), user nickname, and proactive voice commentary toggles. Assembles system prompt fragments dynamically with local time, machine telemetry, and learned user preferences.

### 1.5 Memory Architectures
- **`memory/memory_manager.py`**: Production long-term memory engine. Exposes structured memory storage and retrieval using modular connectors (`local` JSON, `mem0`, and `letta`). Extracts memories from user dialogues via `core.llm_gateway.generate_content`.
- **`core/memory_cortex.py`**: **ORPHANED.** A 409-line standalone SQLite memory engine utilizing FTS5 BM25 search, habit tracking, and semantic decay scoring. Completely unused by `main.py`.

### 1.6 Smart Home Automation
- **`smart_home/service.py`, `smart_home/smart_device_manager.py`, `smart_home/storage.py`, `smart_home/providers/*`**: Provides unified device discovery, state management, and command routing across smart home hardware (TP-Link Kasa, Philips Hue, Atomberg Smart Fans, Tuya, LG, Daikin, Nest, SmartThings). Persists devices and account credentials in SQLite (`smart_home.db`).

### 1.7 Passive Perception & Flow Orchestration
- **`core/sensorium.py`**: Continuous background telemetry daemon tracking active window title, process dwell time, user idle state via `GetLastInputInfo`, CPU/RAM loads, battery status, and meeting presence. Dispatches proactive interjections.
- **`core/clipboard_sentry.py`**: Background clipboard watcher polling `pyperclip` every 1.2s to detect tracebacks, JSON, code blocks, or URLs.
- **`core/attention_flow.py`, `core/realtime_flow.py`, `core/reply_flow.py`, `core/device_flow.py`, `core/protocols.py`**: Modular flow controllers lifted from `main.py::BrahmaLive` governing call/message alerts, mobile companion sockets, multi-step email/IG replies, Brahma Connect device actions, and system-level macros.

### 1.8 Core Sentinels & Performance Tuning
- **`core/game_sentinel.py` & `core/mem_reduct.py`**: Real-time gaming detection and hardware optimizer. Automatically switches Windows power plans to High/Ultimate Performance, elevates game process priority, and invokes native Windows NT memory management APIs (`NtSetSystemInformation`) to purge standby lists and working sets.
- **`core/net_sentinel.py`**: Multithreaded ARP and ICMP subnet sweeper, router management auditor, and Wake-on-LAN controller.
- **`core/study_guard.py`**: Real-time webcam vision study supervisor using OpenCV and MediaPipe FaceMesh to track Eye Aspect Ratio (EAR) drowsiness, phone usage, and desk absence.
- **`core/flowstate.py`**: Workspace time machine capturing running user applications, active Chrome/Edge tab URLs, and VS Code workspaces into restoration points.
- **`core/space_cleaner.py`**: Disk cleanup engine inspecting system temp, browser web caches, crash dumps, and dev trash.

---

## 2. File-by-File Inventory & Health Matrix

| File Path | LOC | Primary Symbols / Classes | Status | Risk Level | Architectural & Quality Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `core/audio_devices.py` | 421 | `get_audio_devices`, `switch_default_audio_device`, `AudioSwitchWatcher` | Clean | Low | Robust COM/pycaw ctypes integration for Windows audio switching. |
| `core/audio_io.py` | 443 | `AudioIO`, `AudioLock`, `AudioStream` | Degraded | **High** | Proactive speech gating breach (`proactive=False` default); local TTS fallback thread race. |
| `core/echo.py` | 289 | `EchoCancellation` | Clean | Low | Safe software ducking and loopback suppression. |
| `sound_manager.py` | 303 | `SoundManager` | Degraded | Medium | Non-atomic write in `_save_settings()` (`I-17`). Clean QSoundEffect playback. |
| `core/live_voice_engine.py` | 663 | `LiveVoiceEngine`, `VADDetector`, `VoiceState` | **Dead Code** | Medium | Complete duplicate Gemini Live voice engine; 0 imports repo-wide. |
| `core/speech_stt.py` | 260 | `SpeechSTT`, `transcribe_audio` | Clean | Low | Solid offline Whisper transcription with fallback. |
| `core/speech_tts.py` | 879 | `SpeechTTS`, `_speak_piper`, `_speak_kokoro`, `_speak_gemini` | Degraded | **High** | Synchronous `winsound.PlaySound` blocks mid-sentence cancellation; temp file lock leakage on Windows. |
| `core/voice_catalog.py` | 671 | `VoiceCatalog`, `VoiceModelInfo` | Clean | Low | Well-structured Piper and Kokoro voice registry; integrates safe downloads. |
| `core/avatar_catalog.py` | 1300 | `AvatarCatalog`, `AvatarModel` | Clean | Low | Complete Live2D and VRM avatar model parser; handles thumbnails & validation. |
| `core/avatar_runtime.py` | 1336 | `AvatarRuntime`, `AvatarIPCBridge` | Clean | Low | Manages WebEngine IPC for 3D/2D animation, lip-sync, and expressions. |
| `fetch_live2d_model.py` | 174 | `fetch_live2d` | Clean | Low | Verified chunk download script for Live2D models. |
| `fetch_vrm_model.py` | 254 | `fetch_vrm` | Clean | Low | Verified chunk download script for VRM assets. |
| `vendor_vrm_runtime.py` | 172 | `vendor_runtime` | Clean | Low | Vendors JavaScript runtime libraries into local web root. |
| `vrm_license_report.py` | 242 | `VRMLicenseAuditor` | Clean | Low | Reads glTF JSON metadata; parses VRM 0.0 and 1.0 redistribution permissions. |
| `core/mcp_manager.py` | 1149 | `MCPServerManager`, `MCPClientConnection` | **Vulnerable** | **Critical** | **Bypasses `AgentRuntime.execute`** (`I-01`); lacks ADR-020 workspace trust gate. |
| `core/identity.py` | 205 | `IdentityService`, `identity` | Degraded | **High** | Non-atomic JSON writes (`I-17`); writes to repo `get_base_dir() / "config"` rather than `get_user_data_dir()`; instantiates `identity = IdentityService()` (calling `save()`) at module import time (`I-26`). |
| `core/instruction_injector.py` | 215 | `InstructionInjector` | Clean | Low | Clean prompt injection and contextual rule formatting. |
| `core/prompt.txt` | 122 | System prompt text | Clean | Low | Resident voice prompt instructions. |
| `core/prompt_parts.py` | 125 | `assemble_system_prompt` | Clean | Low | Modular assembly of identity, capabilities, and system vitals. |
| `core/learned_rules.py` | 122 | `LearnedRulesEngine` | Degraded | Medium | Uses `get_user_data_dir() / "config"`, but writes non-atomically in `_save()` (`line 43`, `I-17`). |
| `config/identity.json` | 23 | User identity configuration | Clean | Low | Default user preferences file. |
| `core/memory_cortex.py` | 409 | `MemoryCortex` | **Dead Code** | Medium | Standalone SQLite BM25 episodic memory engine; orphaned, unused by main runtime. |
| `memory/__init__.py` | 1 | Memory package root | Clean | Low | Empty initialization stub. |
| `memory/config_manager.py` | 93 | `MemoryConfigManager` | Clean | Low | Manages memory backend selection (`local`, `mem0`, `letta`). |
| `memory/memory_manager.py` | 764 | `MemoryManager` | Clean | Low | Robust long-term memory engine; routes LLM extraction through gateway. |
| `memory/connectors/__init__.py` | 36 | Connector exports | Clean | Low | Standard export stub. |
| `memory/connectors/base.py` | 68 | `BaseMemoryConnector` | Clean | Low | Clean abstract base class for memory persistence. |
| `memory/connectors/factory.py` | 100 | `MemoryConnectorFactory` | Clean | Low | Instantiates active connector according to configuration. |
| `memory/connectors/letta.py` | 126 | `LettaMemoryConnector` | Clean | Low | Integrates Letta cloud/local server API. |
| `memory/connectors/local.py` | 144 | `LocalMemoryConnector` | Clean | Low | Lightweight local JSON memory storage (`long_term.json`). |
| `memory/connectors/mem0.py` | 161 | `Mem0MemoryConnector` | Clean | Low | Integrates Mem0 vector memory provider. |
| `smart_home/__init__.py` | 3 | Smart home package root | Clean | Low | Package initialization. |
| `smart_home/service.py` | 390 | `SmartHomeService` | Clean | Low | High-level orchestrator for IoT device actions and queries. |
| `smart_home/smart_device_manager.py` | 82 | `SmartDeviceManager` | Clean | Low | Bridge between smart home service and tool execution layer. |
| `smart_home/storage.py` | 387 | `SmartHomeStore`, `CredentialVault` | Clean | Low | Encrypts IoT provider credentials at rest (`credentials_encrypted`) via `CredentialVault` (Fernet + DPAPI-protected `smart_home.key`, lines 42–105, 184–225). |
| `smart_home/providers/base.py` | 40 | `SmartHomeProvider`, `ProviderField` | Clean | Low | Abstract base class defining provider auth fields and execution protocol. |
| `smart_home/providers/builtin.py` | 717 | `AtombergProvider`, `KasaProvider`, `HueProvider`, etc. | Clean | Low | Built-in providers; includes mock fallbacks for Tuya, Daikin, Nest. |
| `core/sensorium.py` | 333 | `PassiveSensorium`, `sensorium` | Clean | Medium | Omnipresent background perception daemon; handles missing psutil gracefully. |
| `core/window_context.py` | 89 | `get_foreground_window_info`, `capture_active_window_screenshot` | Clean | Low | Native Win32 ctypes window inspection and PIL grab. |
| `core/clipboard_sentry.py` | 87 | `ClipboardSentry` | **Vulnerable** | **High** | **Passive clipboard polling leaks passwords/tokens to external LLM** (`I-18`). |
| `core/hotkey.py` | 173 | `PushToTalk` | Clean | Low | Lightweight `GetAsyncKeyState` 30Hz polling with debounce. |
| `core/anticipatory_watcher.py` | 265 | `AnticipatoryWatcher` | Clean | Low | Health, network, and late-night fatigue watcher; throttled nudges. |
| `core/attention_flow.py` | 212 | `AttentionFlow` | Degraded | **High** | **Bypasses proactive voice gating**; triggers overlapping native speech threads. |
| `core/realtime_flow.py` | 93 | `RealtimeFlow` | Clean | Low | Manages dashboard and live duplex socket coroutines. |
| `core/reply_flow.py` | 395 | `ReplyFlow` | Degraded | Medium | Hardcodes `"gemini-2.5-flash"` in reply flows, bypassing kill-switch (`I-20`, `I-21`). |
| `core/device_flow.py` | 238 | `DeviceFlow` | Clean | Low | Routes mobile phone commands via Brahma Connect. |
| `core/device_location.py` | 203 | `get_device_location`, `get_device_city` | Degraded | Medium | Non-atomic disk write (`I-17`); unencrypted HTTP IP geolocation (`I-18`). |
| `core/file_download.py` | 216 | `download_file`, `download_attempt` | **Exemplary** | Low | Flawless atomic file downloader with `.part` resumption, MD5/SHA256, and stall detection. |
| `core/protocols.py` | 129 | `ProtocolEngine`, `protocols` | Clean | Low | Macro orchestrator for Deep Work, Redline, Lockdown, and Nightfall modes. |
| `core/flowstate.py` | 471 | `FlowStateManager` | Degraded | Medium | Authoritative FlowState engine. Non-atomic file write (`I-17`). |
| `core/game_sentinel.py` | 499 | `GameSentinelManager` | Clean | Low | Authoritative Game Sentinel engine; manages power schemes & process priority. |
| `core/net_sentinel.py` | 1199 | `NetSentinel` | Degraded | Low | Authoritative NetSentinel engine; contains minor unencrypted HTTP query. |
| `core/shopping_agent.py` | 689 | `ShoppingAgent` | Degraded | Medium | Authoritative Shopping Agent; hardcodes `"gemini-2.5-flash"` in synthesis (`I-20`). |
| `core/space_cleaner.py` | 923 | `SpaceCleanerEngine` | Clean | Low | Authoritative Storage Sentinel engine; integrates Windows Storage Sense. |
| `core/study_guard.py` | 717 | `StudyGuardManager` | Degraded | Medium | Authoritative StudyGuard engine; non-atomic history save (`I-17`). |
| `core/mem_reduct.py` | 164 | `MemReductEngine` | **Exemplary** | Low | Native Windows NT memory manager integration (`NtSetSystemInformation`). |

---

## 3. Invariant & Specific Audit Question Verdicts

### 3.1 Invariants Coverage Matrix (`I-01` through `I-28` per `docs/ARCHITECTURE-SPEC.md` §4.1)

| Invariant | Authoritative Specification (`docs/ARCHITECTURE-SPEC.md` §4.1) | Verdict | Direct Evidence & File:Line Citation |
| :--- | :--- | :--- | :--- |
| **I-01** | Every harness tool call on every door passes through `AgentRuntime.execute` | **FAIL** | `core/mcp_manager.py:814` registers voice MCP tools directly in `DynamicToolRegistry`. In `core/tool_exec.py:120-144`, calls matching dynamic tools bypass `AgentRuntime.execute` and call `DynamicToolRegistry.execute_sync`, bypassing all policy and approvals. |
| **I-02** | *(Scoped, superseded by ADR-019)* Native tools migrated to `ToolSpec`s behind `AgentRuntime.execute` | **PARTIAL** | Sentinel/audio tools in `core/desktop_tools.py` are `ToolSpec`s, but `features/*.py` and voice MCP tools in `DynamicToolRegistry` remain outside `AgentRuntime.execute`. |
| **I-03** | A policy `deny` is final; session "always allow" is consulted only after `evaluate()` when decision was `ask` | **N/A (WP-01)** | Enforced in `agent/runtime.py`; bypassed only when `I-01` is bypassed by `DynamicToolRegistry`. |
| **I-04** | A `requires_approval` spec is never remembered and asks every time | **N/A (WP-01)** | Enforced in `agent/runtime.py`. |
| **I-05** | The GUI approver (`core/confirm.py:ask_blocking_choice`) refuses to run on the Qt main thread | **N/A (WP-06)** | Enforced in `core/confirm.py`. |
| **I-06** | A malformed policy never widens access; last good policy stays and `policy_reload(ok=False)` is emitted | **N/A (WP-01)** | Enforced in `agent/runtime.py`. |
| **I-07** | No harness tool is registered under a core meta-tool name (`load_skill`, `search_tools`, `recall_memory`, …) | **PASS** | Sentinel, smart home, and memory tools do not shadow core meta-tool names. |
| **I-08** | Delegation is one bounded child (max 12 turns), never recursive, sharing parent's runtime | **PASS** | No child subagents are constructed from audio, perception, or sentinel modules. |
| **I-09** | Every `assistant` `tool_calls` message is followed by one `tool` message per call, in order | **PASS** | Completed Gemini Live turns are recorded into `agent/session.py` via `record_voice_turn` in `main.py`. |
| **I-10** | Transcript is always OpenAI-shaped; Gemini declarations converted only at the edge; schemas lowercase | **PASS** | `core/mcp_manager.py` normalizes MCP tool input schemas for Gemini Live setup declarations. |
| **I-11** | Compaction never orphans a tool result: cut walks forward past `tool` messages | **N/A (WP-01)** | Handled in `agent/context.py`. |
| **I-12** | Shell commands run with closed stdin; timeout terminates whole tree (Windows job object / POSIX session group) | **FAIL** | `core/mcp_manager.py:649` spawns MCP server processes via `asyncio.create_subprocess_exec` without a Windows Job Object; `actions/attention_monitor.py:447` spawns untracked `powershell.exe` for Edge TTS. |
| **I-13** | `exec.sandbox` (`agent/sandbox.py`) is the only isolation path: env scrub, prefix, resource limits | **N/A (WP-01)** | Handled in `agent/sandbox.py`. |
| **I-14** | Credential-shaped env vars are withheld by default and their count is reported | **N/A (WP-01)** | Handled in `agent/sandbox.py`. |
| **I-15** | `agent/` imports with only pytest installed: no PyQt, provider SDK, or Windows COM at module scope | **PASS** | Modules in `core/` properly hold external dependencies (`psutil`, `cv2`, `pyaudio`, `mcp`), while `agent/` remains stdlib-only. |
| **I-16** | System prompt is never persisted; resume appends only the new tail (`messages_after`) | **PASS** | `core/prompt_parts.py` and `core/instruction_injector.py` build prompts dynamically in memory; only transcripts are persisted. |
| **I-17** | Writes are atomic where user data is persisted (`core/user_paths.atomic_write_json`) | **FAIL** | `core/identity.py:85`, `core/learned_rules.py:43`, `sound_manager.py:183`, `core/device_location.py:56`, `core/study_guard.py:630`, and `core/flowstate.py:98` write directly with `open(..., "w")` without atomic rename. |
| **I-18** | One MCP client per layer: `agent/mcp.py` (stdlib) for harness, `core/mcp_manager.py` (SDK) for voice path. No third client | **PASS** (in WP-08) | `core/mcp_manager.py` is the designated SDK MCP client for the voice path (though its tools must route through `AgentRuntime.execute` per `I-01`). |
| **I-19** | Advertised Gemini surface is small and constant ($\le 24$ declarations): pinned tools + meta-tools + top-k | **PASS** | Voice prompt and setup payload respect the $\le 24$ declaration budget via `core/tool_bridge.py`. |
| **I-20** | Gemini Live tools cannot be swapped mid-session; no code may assume a second setup frame | **PASS** | `core/mcp_manager.py` discovers tools prior to live session setup; mid-session calls dispatch through `call_tool`. |
| **I-21** | Every provider egress is gated by its kill switch before the call (`core/provider_router.guard`, `core/llm_gateway.py`) | **FAIL** | `core/shopping_agent.py:408` instantiates `genai.Client` directly without checking `provider_router.guard`, and `core/llm_gateway.py:108-110` falls back to `["gemini", ...]` on router error. |
| **I-22** | Token usage comes from provider; "unknown" is never rendered as zero (`agent/usage.py`) | **N/A (WP-01)** | Handled in `agent/usage.py`. |
| **I-23** | Cancellation is a flag (`agent/runtime.cancel_event`), never a killed thread | **FAIL** | `core/speech_tts.py:182, 230, 290`: `winsound.PlaySound(..., SND_FILENAME)` blocks synchronously and does not poll or respond to cancellation events. |
| **I-24** | One subprocess path per concern (`run_captured`); do not add a second `subprocess.run(shell=True, timeout=...)` | **PASS** | `core/game_sentinel.py` and `core/net_sentinel.py` use argument lists (`shell=False`) for `powercfg`, `arp`, and `ping`. |
| **I-25** | One marshaller per boundary: Qt-thread-only calls emitted through queued signals, never inline from worker/asyncio threads | **FAIL** | `core/clipboard_sentry.py:80` invokes `_on_clipboard_actionable` on its daemon thread, which mutates Qt widgets in `core/ui_shell_actions.py:23-35` off the main thread. |
| **I-26** | No import-time side effects: importing a UI/core module must not start a thread, open a socket, or read/write user data | **FAIL** | `core/identity.py:79, 205` executes `identity = IdentityService()` at module import scope, reading `config/identity.json` and immediately calling `self.save()` (writing to disk on import!). |
| **I-27** | Prefer a targeted edit over a whole-file rewrite; run project's own gates | **PASS** | Followed throughout audit. |
| **I-28** | A new harness tool is a `ToolSpec` plus tests—never a new `elif` in `core/tool_exec.py` (ratchet: 0 `elif`, $\le 147$ lines) | **PASS** | `core/tool_exec.py` has 0 `elif` branches. |

---

### 3.2 Specific Audit Question Verdicts

#### Q1: Proactive Commentary & Duplicate Speech Gating
- **Verdict:** **FAIL (CRITICAL GATING BREACHES DETECTED)**
- **Evidence & Mechanism:**
  1. In `core/audio_io.py:361`, the `speak(text: str, proactive: bool = False)` function implements:
     ```python
     if proactive and not identity.is_proactive():
         return
     ```
     Because `proactive` defaults to `False`, any call that does not pass `proactive=True` completely bypasses `identity.is_proactive()`.
  2. In `main.py:3162` (`_clipboard_monitor`), when technical content is copied to the clipboard, the callback calls:
     ```python
     brahma_evo.speak(reply)
     ```
     It fails to pass `proactive=True`. Consequently, even if the user turns OFF proactive speech (`"proactive_voice_comments_enabled": false` in `config/identity.json`), copying any traceback or code snippet immediately triggers unprompted spoken voice comments.
  3. In `core/attention_flow.py:42, 44, 140, 155, 162, 190, 204, 210`, notifications trigger `self._host.speak(msg)` with default `proactive=False`, or spawn detached threads calling `speak_native(text)`.
  4. In `actions/attention_monitor.py:140, 155, 162, 190, 204, 209`, background attention events invoke `host.speak(comment)` with default `proactive=False`.
  5. **Duplicate Speech Collision:** When `speak_native` is invoked via `core/attention_flow.py:44`, it uses `pyttsx3` or Edge TTS directly. It does not acquire `AudioLock` or inspect whether Gemini Live is currently outputting audio via `AudioIO.play_audio()`. If Gemini Live and `speak_native` speak simultaneously, both audio streams play concurrently over the speakers, talking over each other.

#### Q2: Audio & TTS Re-entrancy / Resource Leaks
- **Verdict:** **FAIL (HIGH INTERRUPTION & RESOURCE DEFECTS)**
- **Evidence & Mechanism:**
  1. In `core/speech_tts.py:182, 230, 290`, offline synthesis (`_speak_piper`, `_speak_kokoro`, `_speak_gemini`) plays WAV audio via:
     ```python
     winsound.PlaySound(output_path, winsound.SND_FILENAME)
     ```
     `winsound.PlaySound` is a blocking C-runtime call. `stop_native_speech()` in `actions/attention_monitor.py` only invokes `pyttsx3_engine.stop()`. It has no reference to `winsound`. When a user interrupts a turn or clicks cancel, ongoing `winsound` playback cannot be aborted and plays to the end of the WAV file.
  2. In `actions/attention_monitor.py:447-454`, Edge TTS playback is executed by spawning a detached PowerShell process:
     ```python
     subprocess.Popen(["powershell", "-NoProfile", "-Command", ps_cmd])
     ```
     These processes are completely untracked. If a turn is cancelled, the PowerShell process continues running in the background and keeps playing audio.
  3. In `core/audio_io.py:426-436`, offline TTS fallback spawns an uncoordinated daemon thread:
     ```python
     threading.Thread(target=_run, daemon=True).start()
     ```
     It does not hold `_playback_lock` and does not synchronize with `_play_queue`. Rapid successive utterances spawn multiple concurrent playback threads that interleave audio frames.

#### Q3: Dual Code in `core/` vs `actions/` vs `features/`
- **Verdict:** **ARCHITECTURAL REDUNDANCY / CLEAN SEPARATION WITH NAMESPACE COLLISION RISK**
- **Evidence & Mechanism:**
  A comprehensive cross-directory trace of `game_sentinel.py`, `net_sentinel.py`, `study_guard.py`, `flowstate.py`, and `shopping_agent.py` revealed:
  1. **Authoritative Engines:** All core logic, state management, background watchdog threads, and Win32/NT ctypes interop reside strictly in `core/*.py`:
     - `core/game_sentinel.py` (499 LOC) owns `GameSentinelManager`.
     - `core/net_sentinel.py` (1,199 LOC) owns `NetSentinel`.
     - `core/study_guard.py` (717 LOC) owns `StudyGuardManager`.
     - `core/flowstate.py` (471 LOC) owns `FlowStateManager`.
     - `core/shopping_agent.py` (689 LOC) owns `ShoppingAgent`.
  2. **Action Tool Layer (`actions/*.py`):** `actions/game_sentinel.py`, `actions/net_sentinel.py`, and `actions/study_guard.py` are thin tool wrappers implementing the legacy `PLUGIN` schema and `run()` entry point returning formatted strings for the Brahma chat loop. (`actions/flowstate.py` and `actions/shopping_agent.py` do not exist).
  3. **Feature Plugin Layer (`features/*.py`):** `features/flowstate.py`, `features/game_sentinel.py`, `features/net_sentinel.py`, `features/shopping_agent.py`, and `features/study_guard.py` are thin feature wrappers implementing the dynamic `FEATURE_METADATA` schema and `execute(**kwargs)` entry point returning structured dictionaries.
  4. **Delegation Analysis:** Both `actions/` and `features/` delegate directly to `core.<module>.get_<manager>()`. The engine implementations are **not** stale copy-pastes; the code cleanly delegates to `core/`.
  5. **Defect:** For `game_sentinel`, `net_sentinel`, and `study_guard`, having implementations in both `actions/` and `features/` creates redundant tool registrations and potential dispatch collisions in dynamic registries.

#### Q4: Memory Subsystem & Provider Gate (`memory/` & `core/memory_cortex.py`)
- **Verdict:** **PARTIAL COMPLIANCE / ORPHANED ENGINE / FALLBACK KILL-SWITCH LEAK**
- **Evidence & Mechanism:**
  1. **Orphaned Engine:** `core/memory_cortex.py` (409 lines) is dead code. It implements an SQLite BM25 search engine with memory decay, but is not imported anywhere in the production runtime.
  2. **Active Memory Engine:** The active memory system is `memory/memory_manager.py` (764 lines), storing long-term memories in `long_term.json` (or Mem0 / Letta).
  3. **LLM Routing:** When extracting memories from conversation (`extract_memories_from_text`), `memory/memory_manager.py:279` calls:
     ```python
     response = llm_gateway.generate_content(
         prompt,
         model="gemini-2.5-flash",
         config={"response_mime_type": "application/json", "temperature": 0.1},
         purpose="memory extraction",
     )
     ```
     It routes through `core/llm_gateway.py` rather than directly importing `google.genai`.
  4. **Provider Kill-Switch Leak (`I-21`):** In `core/llm_gateway.py:108-110`, if the active provider router throws an error or fails, it falls back to a hardcoded chain:
     ```python
     providers = ["gemini", "openrouter", "deepseek", "groq", "anthropic", "cerebras"]
     ```
     If the user configured a provider kill-switch specifically disabling Gemini, this fallback chain bypasses the kill switch and sends memory text to Gemini.

#### Q5: Voice MCP Manager (`core/mcp_manager.py`)
- **Verdict:** **FAIL (CRITICAL PERMISSION BYPASS & MISSING WORKSPACE TRUST GATE)**
- **Evidence & Mechanism:**
  1. **Permission Bypass (`I-01`):** In `core/mcp_manager.py:814`, `MCPServerManager._register_tool` registers tools directly into `core.dynamic_tools.DynamicToolRegistry`. In `core/tool_exec.py:120-144`, calls matching `DynamicToolRegistry.has_tool(func_name)` are dispatched via `DynamicToolRegistry.execute_sync()`. This execution path runs entirely outside `AgentRuntime.execute()`. No policy checks, approval gates, or audit logs occur for MCP tools invoked by the voice session!
  2. **Subprocess Spawning:** Servers are spawned via `asyncio.create_subprocess_exec` using `command` and an argument list (no `shell=True`), which avoids shell injection.
  3. **ADR-020 Workspace Trust:** `core/mcp_manager.py` loads server definitions directly from `mcp_servers.json` without verifying workspace trust or prompting the user before launching third-party server executables.
  4. **Cleanup on Exit:** `MCPServerManager.shutdown()` calls `proc.terminate()` and `proc.wait()`, but lacks Windows Job Object encapsulation, meaning orphaned child processes spawned by MCP servers can leak on shutdown.

---

## 4. Detailed Findings

### FINDING WP08-01 (CRITICAL) — Complete Permission Boundary Bypass for Voice MCP Tools
- **Severity:** Critical
- **Category:** Security Boundary & Permissions (`I-01`, `I-08`)
- **Location:** `core/mcp_manager.py:814` and `core/tool_exec.py:120-144`
- **Evidence & Mechanism:**
  In `core/mcp_manager.py:814`:
  ```python
  DynamicToolRegistry.register_external_tool(
      name=tool_name,
      description=tool.description or "",
      parameters=tool.inputSchema,
      handler=self._create_tool_handler(server_name, tool.name),
  )
  ```
  In `core/tool_exec.py:120-144`:
  ```python
  if DynamicToolRegistry.has_tool(func_name):
      return DynamicToolRegistry.execute_sync(func_name, args)
  ```
  `DynamicToolRegistry.execute_sync` executes the handler directly without routing through `AgentRuntime.execute()`.
- **Impact:** Any tool provided by an MCP server connected to the voice engine can be invoked without policy checks, workspace path containment, or user approval banners, completely undermining the Brahma security model.
- **Recommended Fix:** Deprecate `DynamicToolRegistry` direct execution. Register voice MCP tools as canonical `ToolSpec` instances with `tool_class="mcp"` and route all invocations through `AgentRuntime.execute(func_name, args)`.

---

### FINDING WP08-02 (HIGH) — Proactive Voice Commentary Gating Breaches Across Background Daemons
- **Severity:** High
- **Category:** Behavioral Invariant & User Intent (`I-01`, Q1)
- **Location:** `core/audio_io.py:361`, `main.py:3162`, `core/attention_flow.py:42, 44, 140, 155, 162, 190, 204, 210`
- **Evidence & Mechanism:**
  1. `core/audio_io.py:361` checks `identity.is_proactive()` only if `proactive=True` is explicitly passed.
  2. `main.py:3162` (`_clipboard_monitor`) calls `brahma_evo.speak(reply)` without `proactive=True`.
  3. `core/attention_flow.py` calls `speak_native(msg)` on detached threads without checking `identity.is_proactive()`.
- **Impact:** Unsolicited voice output occurs even when the user has explicitly disabled proactive voice commentary (`proactive_voice_comments_enabled: false`).
- **Recommended Fix:**
  1. In `core/audio_io.py:361`, update `speak(text, proactive=None)`: if `proactive is None`, default to checking whether the utterance was initiated autonomously.
  2. In `main.py:3162`, pass `proactive=True`.
  3. Route all attention announcements in `core/attention_flow.py` through `self._host.speak(msg, proactive=True)` and remove detached `speak_native` threads.

---

### FINDING WP08-03 (HIGH) — Import-Time Disk Mutation & Source-Tree Pollution in `core/identity.py` (`I-17`, `I-26`)
- **Severity:** High
- **Category:** Import-Time Side Effects & File Hygiene (`I-17`, `I-26`)
- **Location:** `core/identity.py:38, 79-86, 205` (`IdentityService`)
- **Evidence & Mechanism:**
  1. `core/identity.py:38` resolves `IDENTITY_FILE = get_base_dir() / "config" / "identity.json"` inside the repository / installation directory rather than `get_user_data_dir() / "config" / "identity.json"`.
  2. At module import scope (`line 205`), `identity = IdentityService()` runs `self.load()`, which unconditionally calls `self.save()` (`line 79`) whenever any default key is merged or on initial load—opening `config/identity.json` with raw `open(..., "w")` (`line 85`) at import time!
  *(Note: By contrast, `core/learned_rules.py:20` (`LearnedRulesEngine`) already resolves `get_user_data_dir() / "config" / "learned_rules.json"`, and `smart_home/storage.py:42-105, 184-225` (`CredentialVault`) properly encrypts IoT account credentials using Fernet + DPAPI).*
- **Impact:** Importing `core.identity` mutates files inside the repository source tree at import time (violating `I-26`), fails in read-only `Program Files` installations, and risks truncating `identity.json` via non-atomic writes (`I-17`).
- **Recommended Fix:** Relocate `IDENTITY_FILE` to `get_user_data_dir() / "config" / "identity.json"`, remove `self.save()` from `IdentityService.load()`, and use `core.user_paths.atomic_write_json` in `IdentityService.save()`.

---

### FINDING WP08-04 (HIGH) — Passive Clipboard Data Exfiltration via Background Sentry
- **Severity:** High
- **Category:** Privacy & Sensitive Data Handling (`I-18`)
- **Location:** `core/clipboard_sentry.py:80-85` and `main.py:3150-3165`
- **Evidence & Mechanism:**
  `core/clipboard_sentry.py` polls `pyperclip.paste()` every 1.2s. When text matches `json_data` or `code_snippet`, it invokes the callback in `main.py`:
  ```python
  prompt = f"The user copied a {cat} to clipboard: {text[:500]}..."
  reply = llm_gateway.generate_content(...)
  brahma_evo.speak(reply)
  ```
- **Impact:** If a user copies private JSON files, environment variables, API tokens, or passwords matching snippet heuristics, the data is automatically exfiltrated to the external LLM provider without confirmation.
- **Recommended Fix:** Require an explicit opt-in setting in `app_settings.json` for clipboard inspection. Run regex redaction on clipboard contents before sending to the LLM, and never forward data containing keys, tokens, or passwords.

---

### FINDING WP08-05 (HIGH) — Non-Atomic Disk Writes to Configuration Files
- **Severity:** High
- **Category:** File Integrity & Durability (`I-17`)
- **Location:** `core/identity.py:85`, `core/learned_rules.py:43`, `sound_manager.py:183`, `core/device_location.py:56`, `core/study_guard.py:630`, `core/flowstate.py:98`
- **Evidence & Mechanism:**
  Multiple core files write directly to persistent JSON files using:
  ```python
  with open(path, "w", encoding="utf-8") as f:
      json.dump(data, f)
  ```
  or `path.write_text(...)` without using temporary files or `os.replace()`.
- **Impact:** A sudden power loss, process termination, or crash while writing leaves empty or corrupted JSON files, causing startup crashes on subsequent boots.
- **Recommended Fix:** Standardize all configuration writes across `core/` to use `core.utils.atomic_write_json(path, data)` or `core.file_download.discard`/`os.replace`.

---

### FINDING WP08-06 (MEDIUM) — Synchronous `winsound.PlaySound` Blocks Thread and Prevents Speech Cancellation
- **Severity:** Medium
- **Category:** Audio Pipeline & Cancellation (`I-12`, Q2)
- **Location:** `core/speech_tts.py:182, 230, 290`
- **Evidence & Mechanism:**
  Piper, Kokoro, and Gemini local TTS invoke:
  ```python
  winsound.PlaySound(output_path, winsound.SND_FILENAME)
  ```
  This is a blocking C-runtime call. `actions/attention_monitor.py:stop_native_speech()` only stops `pyttsx3`.
- **Impact:** Pressing `Ctrl+Shift+X` or issuing a cancel command cannot halt local audio playback; the assistant continues speaking until the WAV finishes.
- **Recommended Fix:** Replace `winsound.PlaySound` with non-blocking streaming playback through `AudioIO.play_audio()`, or use `winsound.SND_ASYNC` and issue `winsound.PlaySound(None, winsound.SND_PURGE)` on cancel.

---

### FINDING WP08-07 (MEDIUM) — Dead and Orphaned Core Subsystems (`live_voice_engine.py` & `memory_cortex.py`)
- **Severity:** Medium
- **Category:** Code Health & Architectural Debt
- **Location:** `core/live_voice_engine.py` (663 LOC) and `core/memory_cortex.py` (409 LOC)
- **Evidence & Mechanism:**
  - `core/live_voice_engine.py` implements an entire Gemini Live duplex audio engine with VAD, but has 0 imports in the repository. `main.py` uses `SessionLifecycle` and `AudioIO`.
  - `core/memory_cortex.py` implements an SQLite BM25 episodic memory engine, but is never instantiated or called by `main.py`.
- **Impact:** 1,072 lines of dead code create maintenance confusion, false assumptions about capabilities, and technical debt.
- **Recommended Fix:** Formally archive or remove `core/live_voice_engine.py` and `core/memory_cortex.py`.

---

### FINDING WP08-08 (MEDIUM) — Hardcoded Model `"gemini-2.5-flash"` and Fallback Kill-Switch Leak
- **Severity:** Medium
- **Category:** Provider Routing & Invariants (`I-20`, `I-21`)
- **Location:** `core/reply_flow.py:57, 212`, `core/shopping_agent.py:408`, `core/llm_gateway.py:108-110`
- **Evidence & Mechanism:**
  - `core/reply_flow.py` passes `model="gemini-2.5-flash"` directly to `llm_gateway.generate_content`.
  - `core/shopping_agent.py:408` instantiates `genai.Client` and generates content directly with `"gemini-2.5-flash"`.
  - `core/llm_gateway.py:108-110` falls back to `["gemini", ...]` on provider router failure.
- **Impact:** Bypasses provider selection policies and leaks user data to Gemini even when the Gemini provider is disabled.
- **Recommended Fix:** Route all text generation through `llm_gateway.generate_content()` without hardcoding model names, allowing the active provider router to select the model and enforce kill-switches.

---

### FINDING WP08-09 (LOW) — Unencrypted HTTP Requests for Public IP & Geolocation
- **Severity:** Low
- **Category:** Privacy & Network Security (`I-18`)
- **Location:** `core/device_location.py:125` and `core/net_sentinel.py:237`
- **Evidence & Mechanism:**
  Both modules query `http://ip-api.com/json` over plain unencrypted HTTP.
- **Impact:** Local network observers can inspect IP geolocation queries in plaintext.
- **Recommended Fix:** Update endpoints to HTTPS (`https://ipapi.co/json` or `https://ipwho.is/`).

---

## 5. Cross-Package Seams & Handoff Notes for Orchestrator

- **Handoff to WP-02 (Agent Runtime & Permissions):**
  - Fix the critical permission bypass between `core/mcp_manager.py` and `core/tool_exec.py`. Voice MCP tools must register as canonical `ToolSpec` objects and route through `AgentRuntime.execute()` to preserve `I-01`.
- **Handoff to WP-03 (App Core, Main Entry Point & Providers):**
  - In `main.py:3162` (`_clipboard_monitor`), pass `proactive=True` to `brahma_evo.speak(reply)` to fix the proactive speech breach.
  - In `core/llm_gateway.py:108-110`, ensure provider fallback chains do not hardcode Gemini when Gemini has been kill-switched (`I-21`).
- **Handoff to WP-04 (Desktop Tools & Actions Surface):**
  - Resolve dual-exposure redundancy for `game_sentinel`, `net_sentinel`, and `study_guard` between `actions/` and `features/`. Standardize on single tool exposure.
- **Handoff to WP-06 (Config, Persistence & State Management):**
  - Migrate all non-atomic file writes in `core/identity.py`, `core/learned_rules.py`, `sound_manager.py`, `core/device_location.py`, `core/study_guard.py`, and `core/flowstate.py` to `core.user_paths.atomic_write_json`.
  - Relocate `config/identity.json` writes in `core/identity.py:38` from `get_base_dir() / "config"` to `get_user_data_dir() / "config"` and remove the import-time `self.save()` side effect (`I-26`).
- **Handoff to WP-07 (Security, Sandboxing & Process Boundaries):**
  - Wrap MCP server subprocess launches in `core/mcp_manager.py` in Windows Job Objects to guarantee full process tree termination on shutdown (`I-12`).

---

## 6. Prioritized Action Queue

### Priority 0 / Priority 1 (Blocker / High Security Risk)
- [ ] **P0 — Fix Voice MCP Permission Bypass (`FINDING WP08-01`):** Route all voice MCP tool executions through `AgentRuntime.execute()` in `core/tool_exec.py`.
- [ ] **P1 — Fix Proactive Voice Commentary Gating (`FINDING WP08-02`):** Pass `proactive=True` in `main.py:3162` and `core/attention_flow.py`; update `core/audio_io.py:361` to enforce proactive checks.
- [ ] **P1 — Fix `core/identity.py` Import-Time Disk Write & Repo Path (`FINDING WP08-03`):** Move `identity.json` to `get_user_data_dir() / "config"`, remove `save()` from `load()`, and use `atomic_write_json`.
- [ ] **P1 — Gate Clipboard Sentry Exfiltration (`FINDING WP08-04`):** Add user confirmation and token/secret redaction before sending clipboard text to external LLMs.

### Priority 2 (Medium / Robustness & Architectural Debt)
- [ ] **P2 — Atomic File Writes (`FINDING WP08-05`):** Replace raw `open(..., "w")` writes across `core/identity.py`, `core/learned_rules.py`, `sound_manager.py`, and `core/flowstate.py` with atomic write utilities.
- [ ] **P2 — TTS Cancellation & Re-entrancy (`FINDING WP08-06`):** Refactor local TTS in `core/speech_tts.py` to play via `AudioIO` or use asynchronous playback responsive to cancel events.
- [ ] **P2 — Remove Dead Code (`FINDING WP08-07`):** Delete or archive orphaned `core/live_voice_engine.py` and `core/memory_cortex.py`.
- [ ] **P2 — Provider Routing Consistency (`FINDING WP08-08`):** Remove hardcoded `"gemini-2.5-flash"` in `core/reply_flow.py` and `core/shopping_agent.py`; route through active provider router.

### Priority 3 (Low / Polish & Hygiene)
- [ ] **P3 — HTTPS Geolocation (`FINDING WP08-09`):** Upgrade `http://ip-api.com` endpoints in `core/device_location.py` and `core/net_sentinel.py` to HTTPS.
- [ ] **P3 — Resolve Dual Actions/Features Redundancy (`Q3`):** Consolidate redundant tool wrappers between `actions/` and `features/`.
