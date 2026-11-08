# WP-03: App Entry, Session Lifecycle, Text Turn Controller & Provider Chain — Deep Scan & Analysis Report

- **Work Package:** WP-03
- **Baseline Commit:** `6439b2efde3c36b1d34b21e4cc5e93baff31b2b5` (`6439b2e`)
- **Files Scanned:** 14 files (9,801 lines of code)
- **Overall Subsystem Health:** Needs Hardening (Seam extraction regressions, egress kill-switch gaps, and SQLite connection leaks identified)

---

## 1. Subsystem Architecture & Data-Flow Summary

The **WP-03** subsystem forms the operational backbone and execution core of Brahma AI Evo. It encompasses application boot sentry, process runtime configuration, Gemini Live session management, the GUI text turn pipeline, background autonomous agent task delegation, local LLM orchestration, provider failover/routing, and the SQLite workspace persistence layer.

### 1.1 Architecture Topology & Seam Boundaries

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                            PyQt6 Main Thread (ui.py)                             │
│       User Input / Mode Toggles / Connect Buttons / Confirmation Overlays        │
└───────────────┬───────────────────────────────┬──────────────────────────────────┘
                │ Qt Signals / Slots            │ Text Commands / Slash Handlers
                ▼                               ▼
┌───────────────────────────────┐   ┌──────────────────────────────────────────────┐
│  core/session_lifecycle.py    │   │        core/text_turn_controller.py          │
│   (SessionLifecycle Seam)     │   │         (TextTurnController Seam)            │
│  - Gemini Live WebSocket      │   │  - Plane-A Pre-LLM Routing Checks            │
│  - Audio Loop Orchestration   │   │  - Local Brain / Custom Endpoint Turns       │
│  - On Call Standby Wake Word  │   │  - Fallback Multi-Provider Reply Ladder      │
│  - Resumption & 1008 Pinning  │   │  - Session Seeding & Replay (/sessions)      │
└───────────────┬───────────────┘   └──────────────────────┬───────────────────────┘
                │                                          │
                │ Shared BrahmaLive Host State             │ Spawns AgentLoop / Tools
                ▼                                          ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                               main.py (BrahmaLive)                               │
│        Composition Root / Background Daemon Manager / Global Tool Registry       │
└───────┬───────────────────────────────┬──────────────────────────────────┬───────┘
        │                               │                                  │
        ▼                               ▼                                  ▼
┌───────────────────────┐   ┌───────────────────────┐   ┌──────────────────────────┐
│ core/provider_router  │   │   core/local_brain    │   │    workspace_store.py    │
│ core/provider_failover│   │   llm_client.py       │   │  - workspace_store.db    │
│ core/llm_gateway.py   │   │   or_client.py        │   │  - History, Memories     │
│ (Provider & Egress)   │   │ (Local / Remote LLMs) │   │  - Active State Keys     │
└───────────────────────┘   └───────────────────────┘   └──────────────────────────┘
```

### 1.2 Data-Flow & Lifecycle

1. **Boot Sequence:**
   - [core/qt_bootstrap.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/qt_bootstrap.py#L179-L185): Configures WebEngine Chromium flags, high-DPI awareness, OpenGL context sharing, and auto-detects/re-executes under a valid Python 3.11/3.12 environment if started on an incompatible interpreter.
   - [core/boot_sentry.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/boot_sentry.py#L21-L76): Detects `FATAL_CRASH.log` from failed auto-patches and performs automatic rollback before initializing GUI.
   - [main.py](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py#L2791-L3048): Initializes the tool index, registers desktop `ToolSpec`s into `AgentRuntime`, starts background services (custom MCP servers, mobile dashboard, Brahma Connect gateway, loopback `brahma_api`, and sensorium), and launches the Qt event loop (`ui.root.mainloop()`).

2. **Voice Door Execution (`gemini_live` vs `local_loop`):**
   - [core/session_lifecycle.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L572-L787): The asyncio worker thread maintains a bidirectional WebSocket to Gemini Live (`LIVE_MODEL`). `SessionLifecycle` orchestrates PortAudio capture/playback via `core.audio_io`. In On Call standby mode, it stops the live socket and spawns `_wakeword_loop` to listen for local wake words ("Brahma Evo").
   - Tool calls from Gemini Live route through `core.tool_exec.ToolExecutor` into `AgentRuntime.execute`.

3. **GUI Text Turn Pipeline:**
   - [core/text_turn_controller.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L253-L1159): User input from the HUD is checked against Plane-A routing authorities (exact phrases, aliases, and owned states). Non-syntax requests evaluate active provider preferences (`Gemini` vs `OpenRouter` vs `Local` vs `Custom`).
   - If local/custom, it executes an `AgentLoop` via `finish_tool_turn`, supporting tool calls, streaming token assembly, and session history persistence. If fallback is needed, `fallback_reply` walks the enabled links of `core.provider_router`.

4. **Background Agent Delegation:**
   - [core/agent_delegation.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/agent_delegation.py#L39-L215): Background coordinator enabling voice turns to delegate multi-turn autonomous coding/analysis tasks without blocking the WebSocket audio stream. Results notify via HUD and spoken completion notices.

5. **Storage & Egress Boundaries:**
   - [workspace_store.py](file:///C:/Users/Admin/Brahma-Ai-Evo/workspace_store.py): SQLite database (`workspace_store.sqlite3`) in WAL mode storing conversations, messages, extracted user memories, and session state.
   - [core/provider_router.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/provider_router.py), [core/llm_gateway.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/llm_gateway.py), [llm_client.py](file:///C:/Users/Admin/Brahma-Ai-Evo/llm_client.py), and [or_client.py](file:///C:/Users/Admin/Brahma-Ai-Evo/or_client.py): Govern LLM dispatch, kill switches, token streaming, and fallback ordering.

---

## 2. File-by-File Inventory & Health Matrix

| File | Lines | Primary Symbols / Classes | Status | Risk Level | Notes |
|---|---:|---|---|---|---|
| [main.py](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py) | 3,236 | `BrahmaLive`, `TOOL_DECLARATIONS`, `main`, `_update_memory_async`, `_gemini_text_reply` | Active | **High** | Core composition root. Within ratchet ceiling (3,236 $\le$ 3,265). Memory extraction bypasses kill switch. |
| [core/session_lifecycle.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py) | 1,013 | `SessionLifecycle`, `run`, `_serve_live_session`, `_build_config`, `_pause_until` | Active | **High** | Extracted from `main.py`. Carries object scoping regressions: `_resume_handle` and `_control_wake` lookups on `self` instead of `self._host`. |
| [core/text_turn_controller.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py) | 1,791 | `TextTurnController`, `on_text_command`, `finish_tool_turn`, `fallback_reply`, `local_brain_reply` | Active | **Medium** | Extracted from `main.py`. Coordinates Plane-A checks, `AgentLoop` execution, and fallback ladder. |
| [core/agent_delegation.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/agent_delegation.py) | 366 | `TaskDelegationManager`, `DelegatedTask`, `delegate_agent_task`, `register_delegation_tools` | Active | **Low** | Robust recursion prevention. Top-level spec registration at import violates `I-26`. |
| [core/provider_router.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/provider_router.py) | 208 | `guard`, `chain`, `is_enabled`, `set_enabled`, `PROVIDERS`, `ProviderDisabled` | Active | **Low** | Clean registry and kill switch logic. |
| [core/provider_failover.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/provider_failover.py) | 43 | `ProviderFailover`, `prefer_openrouter`, `throttle_to_openrouter`, `reset` | Active | **Low** | Pure timestamp-based failover tracking. Well isolated and tested. |
| [core/llm_gateway.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/llm_gateway.py) | 378 | `generate_content`, `AuxResponse`, `GatewayError`, `_call_gemini`, `_chat_link` | Active | **Medium** | Chokepoint for auxiliary calls. Fails to explicitly call `guard(pid)` in request loop; relies on `_chain()`. |
| [core/local_brain.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/local_brain.py) | 787 | `LocalBrain`, `chat_complete`, `generate_chat_stream`, `resolve_tools`, `active_profile` | Active | **Medium** | Hardware-aware timeout profiles (VRAM vs CPU). Missing kill-switch check (`provider_router.guard`). |
| [llm_client.py](file:///C:/Users/Admin/Brahma-Ai-Evo/llm_client.py) | 557 | `UnifiedAIClient`, `chat_as`, `chat_as_completion`, `_route_chat`, `_openai_chat_completion` | Active | **Low** | Validates kill switch before routing. Direct model probe does not check guard. |
| [or_client.py](file:///C:/Users/Admin/Brahma-Ai-Evo/or_client.py) | 425 | `OpenRouterClient`, `chat`, `chat_json`, `vision`, `_call_with_fallback` | Active | **Low** | Checks `provider_router.guard("openrouter")` in `_call_with_fallback`. |
| [workspace_store.py](file:///C:/Users/Admin/Brahma-Ai-Evo/workspace_store.py) | 546 | `WorkspaceStore`, `create_conversation`, `append_message`, `search_memories`, `_summarize_session_async` | Active | **High** | SQLite connection leak on every query (missing `conn.close()`); raw plaintext read of `api_keys.json` crashes under DPAPI. |
| [core/boot_sentry.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/boot_sentry.py) | 76 | `check_and_recover_on_boot`, `run` | Active | **Low** | Startup crash recovery sentry. Non-atomic file write on rollback history. |
| [core/qt_bootstrap.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/qt_bootstrap.py) | 185 | `bootstrap`, `ensure_supported_environment`, `configure_qt_environment`, `configure_dpi_awareness` | Active | **Low** | Clean, idempotent bootstrap routines. Relaunch logic adheres to Decision D45. |
| [core/user_paths.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/user_paths.py) | 190 | `get_user_data_dir`, `atomic_write_json`, `load_trusted_workspaces`, `is_workspace_trusted` | Active | **Low** | Implements Invariant `I-17` (`atomic_write_json`) and ADR-020 (`trusted_workspaces.json`). |

---

## 3. Invariant & Specific Audit Question Verdicts

### 3.1 Architectural Invariant Verification Table

| Invariant ID | Verdict | Evidence (`file:line` & symbol) | Explanation |
|---|---|---|---|
| **I-01** | `PASS` | [core/tool_exec.py:94-105](file:///C:/Users/Admin/Brahma-Ai-Evo/core/tool_exec.py#L94-L105) (`ToolExecutor.execute`) | All 84 registered desktop tool specs and harness tools route through `agent_runtime.execute`. |
| **I-02** | `PASS` | [core/desktop_tools.py:12-40](file:///C:/Users/Admin/Brahma-Ai-Evo/core/desktop_tools.py#L12-L40) (`register`) | Scoped gap closed across ADR-019 Waves 1–3: all 59 declarations + aliases are `ToolSpec` adapters under `AgentRuntime`. |
| **I-03** | `PASS` | [core/text_turn_controller.py:1457-1473](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L1457-L1473) (`finish_tool_turn`) | Loop runs via `AgentLoop` sharing active `runtime`. Policy `deny` cannot be bypassed. |
| **I-04** | `PASS` | [core/text_turn_controller.py:1457](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L1457) | Handled by `AgentRuntime.execute`. |
| **I-05** | `PASS` | [main.py:2880](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py#L2880), [core/confirm.py:100](file:///C:/Users/Admin/Brahma-Ai-Evo/core/confirm.py#L100) | `gui_approver` configured; `confirm.py` asserts calling thread is not Qt GUI main thread. |
| **I-06** | `PASS` | [core/text_turn_controller.py:1529-1535](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L1529-L1535) (`agent_event`) | Policy reload failures are logged as `policy_reload(ok=False)` with old policy retained. |
| **I-07** | `PASS` | [core/tool_exec.py:110-118](file:///C:/Users/Admin/Brahma-Ai-Evo/core/tool_exec.py#L110-L118) (`ToolExecutor.execute`) | Meta-tools (`search_tools`, `call_tool`, `list_tools`, `get_skill_manual`, `load_skill`) checked and executed via `tool_bridge`. |
| **I-08** | `PASS` | [core/agent_delegation.py:128-139](file:///C:/Users/Admin/Brahma-Ai-Evo/core/agent_delegation.py#L128-L139) (`TaskDelegationManager._run_task`) | Filters out delegation and subagent tools (`delegate_agent_task`, `run_subagent`, etc.) from child loop to prevent recursion. |
| **I-09** | `PASS` | [core/text_turn_controller.py:1460-1473](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L1460-L1473) (`finish_tool_turn`) | Delegates turn execution to `AgentLoop` which guarantees 1:1 `tool` results matching `tool_calls`. |
| **I-10** | `PASS` | [core/local_brain.py:127-141](file:///C:/Users/Admin/Brahma-Ai-Evo/core/local_brain.py#L127-L141) (`convert_schema_to_lowercase`) | Recursively normalizes all uppercase schema types to OpenAI lowercase types. |
| **I-11** | `PASS` | [core/text_turn_controller.py:1444-1445](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L1444-L1445) (`finish_tool_turn`) | Managed by `agent.context.ConversationContext`. |
| **I-12** | `PASS` | [core/agent_delegation.py:154-164](file:///C:/Users/Admin/Brahma-Ai-Evo/core/agent_delegation.py#L154-L164) | Harness tools invoked by child loop use `agent.process.run_captured`. |
| **I-13** | `PASS` | [core/agent_delegation.py:124](file:///C:/Users/Admin/Brahma-Ai-Evo/core/agent_delegation.py#L124) | Shares parent runtime and sandbox constraints. |
| **I-14** | `PASS` | [core/agent_delegation.py:124](file:///C:/Users/Admin/Brahma-Ai-Evo/core/agent_delegation.py#L124) | Managed by `agent.sandbox`. |
| **I-15** | `PASS` | [agent/__init__.py](file:///C:/Users/Admin/Brahma-Ai-Evo/agent/__init__.py) | CI `harness-pythons` gate passes; all imports from WP-03 files into `agent/` are outward consumers. |
| **I-16** | `PASS` | [core/text_turn_controller.py:1449-1455](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L1449-L1455) (`finish_tool_turn`) | Replay appends only `pending = bridge.messages_after(...)` to prevent turn duplication. |
| **I-17** | `PARTIAL` | [core/user_paths.py:24-58](file:///C:/Users/Admin/Brahma-Ai-Evo/core/user_paths.py#L24-L58) vs [core/boot_sentry.py:57](file:///C:/Users/Admin/Brahma-Ai-Evo/core/boot_sentry.py#L57) | `atomic_write_json` exists and is used in `user_paths.py`, but `boot_sentry.py` writes `patch_history.json` with raw in-place `open(..., "w")`. |
| **I-18** | `PASS` | [main.py:2130, 2803](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py#L2130) | `WorkspaceMCP` (stdlib) used for harness/text; `MCPServerManager` (SDK) used for voice. |
| **I-19** | `PASS` | [core/session_lifecycle.py:534-555](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L534-L555) (`_build_config`) | Calls `tool_bridge.build_session_payload` to budget setup declarations within Gemini limit. |
| **I-20** | `PASS` | [core/session_lifecycle.py:520-571](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L520-L571) | `LiveConnectConfig` is configured strictly once per connection attempt; no mid-session mutation. |
| **I-21** | `FAIL` | [main.py:733](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py#L733), [core/session_lifecycle.py:671](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L671), [core/local_brain.py:440](file:///C:/Users/Admin/Brahma-Ai-Evo/core/local_brain.py#L440) | Multiple outbound model calls bypass `provider_router.guard` (background memory extraction, Gemini Live connect, `LocalBrain` direct requests). |
| **I-22** | `PASS` | [llm_client.py:166-170](file:///C:/Users/Admin/Brahma-Ai-Evo/llm_client.py#L166-L170) (`_openai_chat_completion`) | Usage dictionary extracted from provider response payload and attached to message. |
| **I-23** | `PASS` | [core/agent_delegation.py:63-65](file:///C:/Users/Admin/Brahma-Ai-Evo/core/agent_delegation.py#L63-L65) (`cancel_task`) | Cancellation sets `task.cancel_event`; worker thread is never forcefully terminated. |
| **I-24** | `PASS` | [core/qt_bootstrap.py:63](file:///C:/Users/Admin/Brahma-Ai-Evo/core/qt_bootstrap.py#L63) | Subprocess calls in `qt_bootstrap` are isolated to startup interpreter probes. |
| **I-25** | `PASS` | [core/text_turn_controller.py:1586-1593](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L1586-L1593), [main.py:3063](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py#L3063) | HUD updates emit queued Qt signals (`_win._task_workspace_sig.emit`). |
| **I-26** | `PARTIAL` | [core/agent_delegation.py:365](file:///C:/Users/Admin/Brahma-Ai-Evo/core/agent_delegation.py#L365) (`register_delegation_tools()`) | `agent_delegation.py` mutates the global `agent.spec` registry as an import side effect. |
| **I-27** | `PASS` | Review-time rule; followed. | Scans conducted without file rewrites. |
| **I-28** | `PASS` | [core/tool_exec.py](file:///C:/Users/Admin/Brahma-Ai-Evo/core/tool_exec.py) (147 lines) | 0 `elif` branches in `core/tool_exec.py`; ratchet ceiling respected. |

---

### 3.2 Specific Audit Question Verdicts

#### Q1: Egress & Kill-Switch Completeness (`I-21`)
> **Question:** Do *all* outbound model calls in `main.py`, `llm_client.py`, `or_client.py`, `core/local_brain.py`, and `core/llm_gateway.py` check `provider_router.guard` before opening a network connection?

- **Verdict:** `FAIL`
- **Findings:**
  1. `main.py:733-736` (`_update_memory_async`): Calls `_get_api_key()` and `extract_memory(user_text, brahma_text, api_key)` on a background daemon thread for every text utterance exceeding 4 characters. It never checks `provider_router.is_enabled("gemini")` or `provider_router.guard("gemini")`. Even if Gemini is disabled in Settings > AI Providers, this background thread still initiates network requests to Google APIs.
  2. `core/session_lifecycle.py:670-692, 879-890` (`SessionLifecycle.run`, `voice_backend`): When `voice_backend()` resolves, it checks whether `default_ai_provider` is set to `"local"`, `"custom"`, or `"openrouter"`. If the user disables Gemini via `provider_router` (`provider_enabled_gemini = False`), but `default_ai_provider` remains `"Google Gemini"` (or `"Gemini"`), `voice_backend()` still returns `"gemini_live"`! `SessionLifecycle.run()` then opens a WebSocket to Gemini Live without checking `provider_router.guard("gemini")`.
  3. `core/local_brain.py:363-380, 440, 674, 751`: Contains zero references to `provider_router`. Any direct caller of `local_brain.chat_complete()` or `local_brain.generate_chat_stream()` bypasses the `local` provider kill switch.
  4. `core/llm_gateway.py:108-110, 352-362`: `generate_content()` relies on `_chain()` to exclude disabled links, but if `provider_router.chain()` raises an unhandled exception, `_chain()` falls back to `["gemini", "openrouter", "local", "custom"]`, which bypasses kill switches entirely. Additionally, `_call_gemini` does not call `guard("gemini")`.
  5. `workspace_store.py:252-267` (`_summarize_session_async`): Dispatches an unmonitored background thread invoking Gemini through `llm_gateway.generate_content` without checking whether Gemini or cloud egress is enabled.

#### Q2: Duplicate Reply & Voice/Text Race Conditions
> **Question:** Commit `26daa83` fixed two duplicate-reply paths. Audit `core/text_turn_controller.py`, `core/session_lifecycle.py`, and `main.py` for any remaining paths where a tool result or fallback triggers two assistant responses or overlapping speech.

- **Verdict:** `PARTIAL / DEFICIENCIES FOUND`
- **Findings:**
  1. **Unserialized Non-Gemini TTS (`core/audio_io.py:426-436`):** When the voice backend is not `gemini_live` (e.g. local voice, offline mode, or fallback replies), every call to `speak(text)` spawns an uncoordinated thread calling `speech_tts.speak(text, use_sink=False)`. Rapid back-to-back notifications (e.g., action announcements followed by task completion or clipboard comments) execute multiple TTS engines concurrently on the audio device, resulting in garbled overlapping speech and racing `self._host.set_speaking()` flags.
  2. **Clipboard Monitor Ungated Speech (`main.py:3160-3162`):** `_clipboard_monitor` calls `brahma_evo.speak(reply)` without `proactive=True`. This completely bypasses the proactive speech toggle (`identity.is_proactive()`), causing unsolicited voice commentary whenever text is copied, potentially talking over an ongoing assistant turn.
  3. **Throttled Failover Speech Echo (`core/text_turn_controller.py:1766` vs `core/audio_io.py:382-410`):** When Gemini Live throttles and fails over to OpenRouter (`_prefer_openrouter() == True`), `fallback_reply` obtains an answer from OpenRouter and calls `host.speak(reply, proactive=True)`. However, if the Gemini Live WebSocket connection is still open (`session and _loop and backend == "gemini_live"`), `AudioIO.speak` injects that text into the Gemini Live socket as `System Alert / Context: {reply}`, causing Gemini Live to re-speak the OpenRouter response over the WebSocket!

#### Q3: `BrahmaLive` Thread & Asyncio Loop Safety
> **Question:** `main.py` runs an `asyncio` event loop on a worker thread, spawns daemon threads (clipboard monitor, attention monitor, proactive loop, IG daemon, dashboard, gateway, `brahma_api`), and communicates with the Qt main thread. Audit for deadlocks, unawaited coroutines, or unsynchronized shared state on `self`.

- **Verdict:** `PARTIAL / DEFICIENCIES FOUND`
- **Findings:**
  1. **Unsynchronized Session & Loop Pointers:** `self._host.session` and `self._host._loop` are cleared (`None`) on error or teardown in `SessionLifecycle.run()`, while worker threads in `_idle_speech_loop` ([core/session_lifecycle.py:408](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L408)), `on_text_command` ([core/text_turn_controller.py:1143](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L1143)), and `AudioIO.speak` ([core/audio_io.py:382](file:///C:/Users/Admin/Brahma-Ai-Evo/core/audio_io.py#L382)) read both pointers without synchronization. A disconnect occurring between checking `if host.session and host._loop:` and calling `asyncio.run_coroutine_threadsafe(..., host._loop)` causes `run_coroutine_threadsafe` to fail with `AttributeError` on `NoneType`.
  2. **`execute_sync` Event Loop Re-entrancy Hazard:** [core/tool_exec.py:56-66](file:///C:/Users/Admin/Brahma-Ai-Evo/core/tool_exec.py#L56-L66) (`ToolExecutor.execute_sync`): If `execute_sync` is invoked on the same thread as `self._host._loop`, calling `future.result(timeout=TOOL_EXEC_TIMEOUT_SECONDS)` blocks the event loop thread waiting for `self.execute(fc)` to complete, causing an unrecoverable 300-second deadlock. While `tool_bridge.py` uses `asyncio.to_thread` to mitigate this for `call_tool`, any other synchronous caller on the loop thread will deadlock.

#### Q4: Gemini Live Reconnect & `1008` State
> **Question:** In `core/session_lifecycle.py`, verify what happens during rapid disconnects, `GoAway` frames, `1008` policy errors, and manual mute/unmute toggles. Are audio streams, tasks, and MCP sessions cleaned up without leaking sockets or threads?

- **Verdict:** `FAIL`
- **Findings:**
  1. **Resume Handle Scoping Bug ([core/session_lifecycle.py:563](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L563)):** `_build_config()` sets `session_resumption=types.SessionResumptionConfig(handle=getattr(self, '_resume_handle', None))`. Because `_resume_handle` is stored on `self._host` by `core.audio_io` (line 195), looking it up on `self` (`SessionLifecycle`) always evaluates to `None`. Consequently, Gemini Live session resumption never activates, and every reconnect starts a fresh session.
  2. **Broken Early Wake in Backoff Pause ([core/session_lifecycle.py:88, 94](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L88)):** `_pause_until` reads `getattr(self, "_control_wake", None)` and `getattr(self, "_control_seq", 0)`. Because both attributes are on `self._host`, `event` is always `None`. The loop always sleeps the full exponential backoff delay (up to 30 seconds), ignoring user clicks on "Connect" or mode changes during backoff.
  3. **Handshake Cancellation Gap During Rapid Toggles:** If the user clicks "Disconnect" while `connect_cm.__aenter__()` is awaiting connection ([core/session_lifecycle.py:691](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L691)), `_session_task` is still `None`. `disconnect_live()` has no task to cancel. The connection handshake continues in the background until timeout (12s) before being cleaned up by `__aexit__`.
  4. **Custom MCP Sessions Persist Across Drop:** `MCPServerManager` subprocesses remain active in the background across Gemini Live disconnects and reconnects.

#### Q5: `workspace_store.py` Concurrency & SQLite Locking
> **Question:** Multiple threads read/write `workspace_store.sqlite3` (GUI thread, `AgentLoop` session store, memory, tasks). Check connection handling, WAL mode, timeouts, and transaction boundaries.

- **Verdict:** `FAIL`
- **Findings:**
  1. **SQLite Connection Leaks Across All Methods:** Every method in `WorkspaceStore` opens a connection with `with self._lock, self._connect() as conn:`. In Python `sqlite3`, the `with conn:` context manager commits/rolls back transactions but **does not close the connection**. None of the connections are closed via `conn.close()`. Unclosed connections hold Windows file locks and descriptors, resulting in `sqlite3.OperationalError: database is locked` during concurrent writes from `agent.session.AgentSession` or worker threads.
  2. **Independent In-Memory Locks:** `WorkspaceStore._lock` (`threading.RLock`) and `AgentSession._lock` (`threading.Lock`) are separate locks in separate objects. They do not synchronize between each other, relying entirely on SQLite file-level concurrency.
  3. **Direct Read of `api_keys.json` (`workspace_store.py:252-255`):** `_summarize_session_async` reads `api_keys.json` directly via `json.load()` instead of `core.secret_store.load_api_keys()`. Under DPAPI encryption (Security Phase 0), this raises `json.decoder.JSONDecodeError` and fails.
  4. **Nested Connections in `append_message` (`workspace_store.py:380-395`):** If a conversation row does not exist, `append_message` calls `create_conversation` while `conn` has an open `SELECT` transaction, opening a second and third connection within the same thread.

---

## 4. Detailed Findings

### [WP-03-F01] Gemini Live Resumption Handle Looked Up on Wrong Object (`self` vs `self._host`) Breaking Session Reconnection
- **Severity:** `High`
- **Category:** `Correctness/Bug`
- **Location:** [core/session_lifecycle.py:563](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L563) (`SessionLifecycle._build_config`), [core/session_lifecycle.py:762-763](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L762-L763) (`SessionLifecycle.run`), [core/audio_io.py:192-195](file:///C:/Users/Admin/Brahma-Ai-Evo/core/audio_io.py#L192-L195) (`AudioIO._receive_audio`)
- **Evidence & Mechanism:**
  When `SessionLifecycle` was extracted from `main.py`, method references were refactored to delegate to `self._host`. However, line 563 of `_build_config()` still reads:
  ```python
  session_resumption=types.SessionResumptionConfig(handle=getattr(self, '_resume_handle', None))
  ```
  In [core/audio_io.py:195](file:///C:/Users/Admin/Brahma-Ai-Evo/core/audio_io.py#L195), when a session resumption token arrives from Gemini Live, it is stored on the host:
  ```python
  self._host._resume_handle = _sru.new_handle
  ```
  Similarly, [core/session_lifecycle.py:763](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L763) clears it on `1008` error via `self._host._resume_handle = None`.
  Because `getattr(self, '_resume_handle', None)` looks up the attribute on `SessionLifecycle` (which never sets `_resume_handle`), it evaluates to `None` on every reconnection attempt.
- **Impact:** Live session resumption after network hiccups, `GoAway` frames, or transient disconnects is completely disabled. Every reconnection forces a full cold start and loses conversational context on Gemini Live.
- **Recommended Fix:** Change `getattr(self, '_resume_handle', None)` to `getattr(self._host, '_resume_handle', None)`. Add a unit test verifying that `_build_config` inspects `host._resume_handle`.

---

### [WP-03-F02] Reconnect Backoff Early Wake Event & Sequence Looked Up on Wrong Object, Defeating Instant Reconnect on User Action
- **Severity:** `High`
- **Category:** `Correctness/Bug`
- **Location:** [core/session_lifecycle.py:88, 94-98, 105](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L88) (`SessionLifecycle._pause_until`)
- **Evidence & Mechanism:**
  [core/session_lifecycle.py:80-106](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L80-L106) implements `_pause_until(seconds)` to sleep during exponential backoff while allowing early wakeups when the user toggles modes or clicks "Connect". Lines 88 and 94 read:
  ```python
  event = getattr(self, "_control_wake", None)
  ...
  with self._host._control_lock:
      seen = getattr(self, "_control_seq", 0)
  if seen != getattr(self, "_pause_seen", 0):
  ```
  In [core/session_lifecycle.py:617, 836](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L617), `_control_wake` and `_control_seq` are assigned and incremented on `self._host`:
  ```python
  self._host._control_wake = asyncio.Event()
  ...
  self._host._control_seq = getattr(self._host, "_control_seq", 0) + 1
  ```
  Because `_pause_until` inspects `self` instead of `self._host`, `event` is always `None`, and `seen` is always `0`.
- **Impact:** `_pause_until` fails to detect early control wakeups. Whenever the app enters reconnect backoff, user interactions (clicking "Connect" or switching between Always On and On Call) are ignored until the backoff timer expires (up to 30 seconds).
- **Recommended Fix:** Update `_pause_until` to read `getattr(self._host, "_control_wake", None)`, `getattr(self._host, "_control_seq", 0)`, and `getattr(self._host, "_pause_seen", 0)`.

---

### [WP-03-F03] Egress Kill-Switch Bypasses in `main.py`, `core/session_lifecycle.py`, and `core/local_brain.py` (`I-21` Violation)
- **Severity:** `High`
- **Category:** `Security` / `Architecture/Invariant`
- **Location:** [main.py:733-736](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py#L733-L736) (`_update_memory_async`), [core/session_lifecycle.py:670-692, 879-890](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L670-L692) (`SessionLifecycle.run`), [core/local_brain.py:363-380, 440, 674, 751](file:///C:/Users/Admin/Brahma-Ai-Evo/core/local_brain.py#L363-L380) (`LocalBrain`)
- **Evidence & Mechanism:**
  Invariant `I-21` mandates that *every provider egress is gated by its kill switch before the call, not after a failure*.
  1. In `main.py:733-736`, `_update_memory_async` checks `memory_extraction_enabled`, then retrieves `api_key = _get_api_key()` and directly calls `extract_memory(user_text, brahma_text, api_key)` on a background thread. It never invokes `provider_router.is_enabled("gemini")` or `provider_router.guard("gemini")`.
  2. In `core/session_lifecycle.py:879-890`, `voice_backend()` checks `default_ai_provider`, but does not consult `provider_router.is_enabled("gemini")`. If a user disables Gemini via Settings > AI Providers (`provider_enabled_gemini = False`), `voice_backend()` still returns `"gemini_live"` if `default_ai_provider` is `"Google Gemini"`, and lines 671–692 instantiate `genai.Client` and establish a live WebSocket session.
  3. In `core/local_brain.py`, methods `chat_complete`, `generate_chat_stream`, `_post_json`, and `_fetch_models` contain no kill-switch checks.
- **Impact:** Disabling providers in Settings does not prevent background threads or voice loops from communicating with external or local model endpoints.
- **Recommended Fix:**
  - Wrap `_update_memory_async` in `if not provider_router.is_enabled("gemini"): return`.
  - In `core/session_lifecycle.py:voice_backend()`, check `if not provider_router.is_enabled("gemini"): return "local_loop"`.
  - Add `provider_router.guard("local")` inside `LocalBrain.chat_complete` and `generate_chat_stream`.

---

### [WP-03-F04] `workspace_store.py` SQLite Connection Leaks & File Handle Contention Under Multi-Thread Access
- **Severity:** `High`
- **Category:** `Concurrency/Threading` / `Performance/Resource`
- **Location:** [workspace_store.py:84-90, 127-138, 149-158, 177-224, 309-320, 347-366, 380-415, 455-467, 473-477, 527-531](file:///C:/Users/Admin/Brahma-Ai-Evo/workspace_store.py#L84-L90) (`WorkspaceStore`)
- **Evidence & Mechanism:**
  In `workspace_store.py:84-90`, `_connect()` returns a fresh connection:
  ```python
  def _connect(self) -> sqlite3.Connection:
      conn = sqlite3.connect(self._db_path, timeout=10, check_same_thread=False)
      conn.row_factory = sqlite3.Row
      conn.execute("PRAGMA journal_mode=WAL;")
      conn.execute("PRAGMA synchronous=NORMAL;")
      return conn
  ```
  Every method uses `with self._lock, self._connect() as conn:`. In Python `sqlite3`, context-manager exit commits or rolls back transactions, but **never closes the connection**. The open `sqlite3.Connection` objects remain alive until Python's garbage collector collects them. On Windows, these unclosed connections retain file locks on `workspace_store.sqlite3` and its `-wal` / `-shm` sidecars. When `agent.session.AgentSession` or other worker threads attempt concurrent writes, SQLite raises `sqlite3.OperationalError: database is locked`.
- **Impact:** Database lock contention, sporadic transaction failures during multi-turn agent runs, and file handle leaks on Windows.
- **Recommended Fix:** Provide a helper context manager in `WorkspaceStore`:
  ```python
  @contextlib.contextmanager
  def _connection(self):
      conn = self._connect()
      try:
          with conn:
              yield conn
      finally:
          conn.close()
  ```
  Replace all `with self._connect() as conn:` with `with self._connection() as conn:`. Apply the same fix to `agent/session.py`.

---

### [WP-03-F05] Plaintext `api_keys.json` Read in `workspace_store.py` Crashes Under DPAPI Encryption
- **Severity:** `Medium`
- **Category:** `Security` / `Correctness/Bug`
- **Location:** [workspace_store.py:252-255](file:///C:/Users/Admin/Brahma-Ai-Evo/workspace_store.py#L252-L255) (`WorkspaceStore._summarize_session_async._do_summary`)
- **Evidence & Mechanism:**
  In `workspace_store.py:252-255`:
  ```python
  key_path = get_user_data_dir() / "config" / "api_keys.json"
  with open(key_path, "r", encoding="utf-8") as f:
      api_key = json.load(f)["gemini_api_key"]
  ```
  Security Phase 0 (Q0-B) established DPAPI encryption at rest for `api_keys.json` via `core.secret_store`. When DPAPI encryption is enabled, `api_keys.json` contains encrypted ciphertext, not plaintext JSON. `json.load(f)` raises `json.decoder.JSONDecodeError`, breaking session summarization.
- **Impact:** Startup session rollover summarization crashes silently with `JSONDecodeError` on any installation with DPAPI secret storage enabled.
- **Recommended Fix:** Replace raw file loading with:
  ```python
  from core.secret_store import load_api_keys
  keys = load_api_keys()
  api_key = keys.get("gemini_api_key", "")
  ```

---

### [WP-03-F06] Clipboard Monitor Fires Unprompted Spoken Output Without Proactive Speech Gating
- **Severity:** `Medium`
- **Category:** `Correctness/Bug`
- **Location:** [main.py:3160-3162](file:///C:/Users/Admin/Brahma-Ai-Evo/main.py#L3160-L3162) (`runner._clipboard_monitor`)
- **Evidence & Mechanism:**
  In `main.py:3160-3162`:
  ```python
  reply = _clipboard_gemini_reply(text[:1000])
  ui.write_log(f"Brahma Evo (Clipboard): {reply}")
  brahma_evo.speak(reply)
  ```
  Notice that `brahma_evo.speak(reply)` is invoked with default arguments (`proactive=False`).
  In [core/audio_io.py:366-372](file:///C:/Users/Admin/Brahma-Ai-Evo/core/audio_io.py#L366-L372), `speak()` only evaluates `identity.is_proactive()` when `proactive=True`. Because the clipboard monitor passes `proactive=False`, unsolicited speech bypasses the user toggle.
- **Impact:** The assistant speaks spontaneous commentary whenever the user copies text, even when "Proactive Spoken Interjections" is turned off in Settings.
- **Recommended Fix:** Pass `proactive=True`:
  ```python
  brahma_evo.speak(reply, proactive=True)
  ```

---

### [WP-03-F07] Lack of Outbound Audio Serialization in `core/audio_io.py` Allows Concurrent Overlapping Speech on Non-Gemini TTS
- **Severity:** `Medium`
- **Category:** `Concurrency/Threading`
- **Location:** [core/audio_io.py:426-436](file:///C:/Users/Admin/Brahma-Ai-Evo/core/audio_io.py#L426-L436) (`AudioIO.speak`)
- **Evidence & Mechanism:**
  When `backend != "gemini_live"` (or Gemini Live is offline), `AudioIO.speak` dispatches TTS via:
  ```python
  def _speak_thread():
      try:
          self._host.set_speaking(True)
          from core import speech_tts
          speech_tts.speak(text, use_sink=False)
      except Exception as exc: ...
      finally:
          self._host.set_speaking(False)
  threading.Thread(target=_speak_thread, daemon=True).start()
  ```
  There is no serialization lock or FIFO playback queue. If two calls to `speak()` arrive in rapid succession (e.g. action progress followed by completion, or background monitor alerts), both threads concurrently invoke the audio device through `speech_tts.speak`. Both voices play at the same time, producing unintelligible audio, and the earlier thread's `finally` unsets `_is_speaking` prematurely while the second thread is still speaking.
- **Impact:** Garbled, overlapping speech on local/offline TTS; premature unsetting of the speaking indicator.
- **Recommended Fix:** Implement a dedicated speech queue (`queue.Queue`) and a single consumer worker thread in `core.audio_io`, serializing playback requests when using modular TTS.

---

### [WP-03-F08] Unsynchronized Multi-Threaded State Access on `self.session` and `self._loop` in `BrahmaLive`
- **Severity:** `Medium`
- **Category:** `Concurrency/Threading`
- **Location:** [core/session_lifecycle.py:408-415](file:///C:/Users/Admin/Brahma-Ai-Evo/core/session_lifecycle.py#L408-L415) (`_idle_speech_loop`), [core/text_turn_controller.py:1143-1158](file:///C:/Users/Admin/Brahma-Ai-Evo/core/text_turn_controller.py#L1143-L1158) (`on_text_command`), [core/audio_io.py:382-409](file:///C:/Users/Admin/Brahma-Ai-Evo/core/audio_io.py#L382-L409) (`speak`)
- **Evidence & Mechanism:**
  `self._host.session` and `self._host._loop` are assigned when a Gemini Live session connects and reset to `None` on disconnect or error. Concurrently, worker threads (`_idle_speech_loop`, GUI text turn thread, clipboard monitor) read `host.session` and `host._loop` without acquiring a lock. A disconnect occurring between checking `if host.session and host._loop:` and calling `asyncio.run_coroutine_threadsafe(..., host._loop)` passes `None` as the loop, triggering an unhandled `AttributeError` or `TypeError`.
- **Impact:** Intermittent unhandled exceptions in background threads when network drops coincide with user turns or idle prompts.
- **Recommended Fix:** Guard reads/writes of `session` and `_loop` under `self._control_lock`, or capture local references atomically:
  ```python
  with self._control_lock:
      loop = self._loop
      sess = self.session
  if loop and sess and loop.is_running():
      asyncio.run_coroutine_threadsafe(..., loop)
  ```

---

### [WP-03-F09] Non-Atomic File Writes in `core/boot_sentry.py` Violate Invariant `I-17`
- **Severity:** `Low`
- **Category:** `Architecture/Invariant`
- **Location:** [core/boot_sentry.py:57-58](file:///C:/Users/Admin/Brahma-Ai-Evo/core/boot_sentry.py#L57-L58) (`check_and_recover_on_boot`)
- **Evidence & Mechanism:**
  When performing an automatic rollback of a crashed patch, `core/boot_sentry.py` writes the updated history back to disk:
  ```python
  with open(PATCH_HISTORY_FILE, "w", encoding="utf-8") as f:
      json.dump(history, f, indent=4)
  ```
  Invariant `I-17` requires that *writes are atomic where user data is persisted (`core/user_paths.atomic_write_json`)*. Writing with `open(..., "w")` truncates the file in place. A power loss or sudden crash during recovery can corrupt `patch_history.json`.
- **Impact:** Potential corruption of patch history on sudden system failure.
- **Recommended Fix:** Replace `open` with `core.user_paths.atomic_write_json(PATCH_HISTORY_FILE, history)`.

---

### [WP-03-F10] Module-Level Import-Time Spec Registration in `core/agent_delegation.py` Violates Invariant `I-26`
- **Severity:** `Low`
- **Category:** `Architecture/Invariant`
- **Location:** [core/agent_delegation.py:365](file:///C:/Users/Admin/Brahma-Ai-Evo/core/agent_delegation.py#L365) (`register_delegation_tools()`)
- **Evidence & Mechanism:**
  At the bottom of `core/agent_delegation.py`:
  ```python
  register_delegation_tools()
  ```
  This immediately mutates the global `agent.spec` tool registry upon module import. Invariant `I-26` states that *importing a UI/core module must not start a thread, open a socket, or read user data*. While registering in-memory tool specs is not an external I/O action, executing stateful registration at module scope introduces ordering dependencies and prevents clean test isolation.
- **Impact:** Global tool registry state mutation at import time.
- **Recommended Fix:** Remove top-level `register_delegation_tools()`. Call it explicitly in `desktop_tools.register()` or `main()`.

---

### [WP-03-F11] Redundant O(N) In-Memory Linear Scan on Entire Memory Table in `workspace_store.py:search_memories`
- **Severity:** `Informational`
- **Category:** `Performance/Resource`
- **Location:** [workspace_store.py:473-500](file:///C:/Users/Admin/Brahma-Ai-Evo/workspace_store.py#L473-L500) (`WorkspaceStore.search_memories`)
- **Evidence & Mechanism:**
  On every user text turn, `_memory_context_for_request` calls `search_memories(query, limit=5)`. Line 475 queries all rows:
  ```python
  rows = conn.execute("SELECT id, content, created_at, source_conversation FROM memories ORDER BY created_at DESC").fetchall()
  ```
  The method fetches the entire table into Python memory, tokenizes every memory row in Python, and calculates word overlap.
- **Impact:** As the memory table grows over weeks of usage, every single user command incurs an O(N) memory read and CPU tokenization cycle.
- **Recommended Fix:** Utilize SQLite FTS5 virtual tables or a `LIKE ?` token pre-filter in SQL to narrow candidate rows before scoring.

---

## 5. Cross-Package Seams & Handoff Notes for Orchestrator

1. **Handoff to WP-01 (Agent Harness):**
   - Check `agent/session.py:127-145`: It duplicates the exact same unclosed connection bug discovered in `workspace_store.py` (`_connect` without `close()`).
2. **Handoff to WP-02 (Tool Surface & Retrieval):**
   - In `core/text_turn_controller.py:350-390`, `DynamicToolRegistry.execute_sync` runs dynamic features without passing through `AgentRuntime.execute`. WP-02 should verify whether dynamic features in `features/` adhere to permission policies.
3. **Handoff to WP-04 (Security & Secrets):**
   - `workspace_store.py:252-255` reads `api_keys.json` directly via `json.load()` without DPAPI handling. WP-04 must catalogue this alongside `dashboard/server.py:83-89` as DPAPI bypasses.
4. **Handoff to WP-08 (Audio/Speech Pipeline):**
   - `core/audio_io.py:426-436` spawns uncoordinated threads calling `speech_tts.speak`, allowing concurrent overlapping audio output when using non-Gemini TTS. WP-08 should review `core/speech_tts.py` and `core/audio_io.py` for audio device locking.

---

## 6. Prioritized Action Queue for This Subsystem

### 1. Immediate (P0/P1)
- **Fix Scoping Regressions in `SessionLifecycle` ([WP-03-F01], [WP-03-F02]):**
  - Update `core/session_lifecycle.py:563` to `handle=getattr(self._host, '_resume_handle', None)`.
  - Update `core/session_lifecycle.py:88, 94` to inspect `_control_wake`, `_control_seq`, and `_pause_seen` on `self._host`.
- **Close SQLite Connection Leaks in `workspace_store.py` ([WP-03-F04]):**
  - Implement a connection context manager ensuring `conn.close()` is called on every query exit.
- **Enforce DPAPI in `workspace_store.py` ([WP-03-F05]):**
  - Replace raw `json.load` in `_summarize_session_async` with `core.secret_store.load_api_keys()`.

### 2. Near-Term (P2)
- **Enforce Egress Kill Switches (`I-21`) ([WP-03-F03]):**
  - Gate `_update_memory_async` in `main.py` on `provider_router.is_enabled("gemini")`.
  - Gate `voice_backend()` in `core/session_lifecycle.py` on `provider_router.is_enabled("gemini")`.
  - Add `provider_router.guard("local")` inside `core/local_brain.py`.
- **Serialize Modular TTS Output ([WP-03-F07]):**
  - Add an audio playback queue in `core.audio_io` so non-Gemini speech requests play sequentially without garbled overlap.
- **Gate Clipboard Monitor Spoken Output ([WP-03-F06]):**
  - Pass `proactive=True` in `main.py:3162` to respect user proactive speech preferences.

### 3. Cleanup / Refactor (P3)
- **Atomic Rollback Logging ([WP-03-F09]):**
  - Use `core.user_paths.atomic_write_json` in `core/boot_sentry.py:57`.
- **Remove Import-Time Side Effect ([WP-03-F10]):**
  - Defer `register_delegation_tools()` in `core/agent_delegation.py` to startup.
- **Optimize Memory Search ([WP-03-F11]):**
  - Migrate `workspace_store.py:search_memories` to FTS5 or SQL token indexing.
