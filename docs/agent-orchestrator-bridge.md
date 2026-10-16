# Agent & Orchestrator Bridge — Codebase Analysis & Design Document

**Status:** analysis + design only — no code has been modified
**Revision:** 3 (broadens revision 2 from a *local-agent* bridge into a **three-tier**
bridge. Tier 1 = local agents (all of revision 2). Tier 2 = **agent orchestrators** —
programs (Orca, Multica, Paperclip) that themselves run teams of agents. Tier 3 = **remote
agents/runtimes** on other machines. Adds §1.4, §2.7, §4.11-§4.13, and Appendix updates.)
**History:** rev 1 = first pass; rev 2 = added the concurrency model, concrete adapter
protocol details, a corrected security section, a detection matrix, and per-phase
acceptance criteria.
**Goal:** enable Brahma Evo to act as a **top-level ("god") orchestrator**: receive an
instruction from the user, **discover every AI agent *and every agent orchestrator***
installed on or reachable from this machine, open a channel to each, delegate the work
downward** (to individual agents *and* to orchestrators that run fleets of agents,
locally or remotely), and **report back when the task is done**. Discovered capability
becomes first-class tools inside the assistant.

**Primary integration seams:** `core/dynamic_registry.py`, `core/tool_bridge.py`,
`core/tool_index.py`, `core/mcp_manager.py`, `core/provider_router.py`,
`plugin_manager.py`, `main.py` boot path, `ui.py`/`core/ui_connectivity.py` settings.

**⚠️ Refactor note (read first):** all `file:line` references below were captured against
the **current (pre-refactor)** tree. A separate code-review pass (see
`docs/CODE-REVIEW-TODO.md`) moves many of these symbols into new modules
(`core/tool_exec.py`, `core/ui_connectivity.py`, `core/ui_startup.py`, `core/audio_io.py`,
`core/session_lifecycle.py`, `core/provider_failover.py`, `core/reply_flow.py`,
`core/qt_bootstrap.py`, `core/user_paths.py`). **Trust the symbol names, not the line
numbers.** §2.7 is the old→new mapping table.

---

## 0. Executive summary

Brahma Evo already has every *mechanism* needed to host external agents; it is simply not
pointed at locally-installed AI tools, or at the orchestrators that run them.

- It has a **budgeted, search-backed tool surface** (`core/tool_index.py` +
  `core/tool_bridge.py`) that lets thousands of tools be reachable by the Gemini Live
  model through **three discovery meta-tools**, so new agent capabilities require **no
  change to the Live handshake** and cannot break the 128-declaration ceiling.
- It has a **universal execution backend** (`core/dynamic_registry.py`) that accepts any
  in-process callable as a tool — the same path MCP servers already use.
- It has a **production MCP client** (`core/mcp_manager.py`) with stdio / streamable-HTTP /
  SSE transports, a supervision loop, name sanitization, JSON-Schema→Gemini normalization,
  and automatic ingestion of a server's own usage docs into the prompt.
- It has a **generic stdio JSON-RPC client** (`actions/playwright_mcp_client.py`) that is
  already the template for driving an external CLI process.
- It has an **HTTP local-model probe** (`llm_client.py poll_opencode_models`) wired to a
  **"Discover Local Agents" button** (`ui.py:13101`) — correct plumbing, single-URL breadth.

What is **missing** is the discovery layer (finding `claude` / `codex` / `gemini` / Ollama /
LM Studio / MCP servers as they exist on *this* machine) and per-agent adapters. This
document specifies both, and shows where each piece plugs into the existing seams.

**Revision 3 widens the target.** Brahma's role is not only to *call* agents but to *be*
the top-level orchestrator over entire fleets. Two extra tiers join the local-agent tier:

- **Tier 2 — Agent orchestrators.** Programs that already run teams of agents and expose a
  control surface: **Orca** (an ADE: worktrees, terminals, automations, agent handoff),
  **Multica** ("agents that show up on the board" — a self-hostable workspace whose local
  **daemon** spawns any of ~26 agent CLIs against issues), and **Paperclip** ("the company"
  for a fleet — org charts, budgets, governance, heartbeats, a `/api` control plane).
  Brahma delegates a *goal* to one of these and it fans the work out to its own agents.
- **Tier 3 — Remote agents/runtimes.** The same agents/orchestrators living on another
  machine (a Multica *runtime*, a Paperclip cloud/sandbox adapter, a remote MCP server).
  The bridge must reach them over the network, not just over `localhost`/stdio.

All three tiers publish into the **same** tool surface (§4.5), so the model sees one
uniform, budgeted catalog regardless of where the capability physically lives.

---

## 1. Objective, scope, and definitions

### 1.1 In scope

1. **Discover** local AI agents/tools on the Windows machine via six detection layers
   (§4.3 A–F) **and local orchestrator programs/services** via a seventh (Layer G, §4.13).
2. **Connect** to each over the transport appropriate to its class (§4.4, §4.11).
3. **Publish** each connected agent's abilities as index-backed tools (never as new native
   declarations) — §4.5.
4. **Manage** their lifecycle (health, restart, shutdown, enable/disable) — §4.6.
5. **Govern** their use (confirmation gate, workspace sandbox, no auto-spawn) — §4.7.
6. **(Tier 2/3) Delegate to orchestrators and remote runtimes** — treat an orchestrator as a
   single high-leverage "agent" whose capabilities are *assign a goal / file an issue / run
   an automation / read status*, and a remote runtime as a discovered execution target —
   §4.11, §4.12.

### 1.2 Non-goals (explicitly deferred)

- Interactive/TUI driving of agents that only expose a rich terminal UI (no PTY support
  exists in this repo — see §5.7).
- Cloud **LLM** provider APIs (that is the existing `core/provider_router.py` /
  `llm_client.py` concern). *Cloud agent runtimes are now in scope* (§4.12).
- Re-implementing an orchestrator's own internals (org charts, budgets, boards). We **wrap**
  them via their CLI/API; we never fork them.
- Agent-authored skills (that is `core/skill_forge.py`).

### 1.3 Agent taxonomy (drives the transport choice)

| Tier | Class | Examples | Primary transport |
|---|---|---|---|
| 1 | **Local model server** | Ollama, LM Studio, vLLM, LocalAI, OpenCode | HTTP (OpenAI-compatible `/v1/models`, `/v1/chat/completions`) |
| 1 | **MCP server** (process or remote) | any `mcpServers` entry from a vendor config | MCP stdio / streamable-HTTP / SSE |
| 1 | **CLI coding agent** | Claude Code, Codex CLI, Gemini CLI, Aider | spawned subprocess in non-interactive mode (one-shot or stream-json) |
| 1 | **Generic agent daemon** | custom JSON-RPC / A2A / agent-card services | stdio JSON-RPC or HTTP agent protocol |
| 1 | **Peer device/agent** | another Brahma instance, phones | `brahma_connect` WebSocket envelope |
| 2 | **Agent orchestrator** | Orca (ADE), Multica, Paperclip | its control surface: `orca`/`multica`/`paperclipai` CLI + HTTP control-plane API |
| 3 | **Remote agent / runtime** | a Multica runtime on a cloud box, a Paperclip cloud/sandbox adapter, a remote MCP server, a remote Brahma | HTTP(S)/WebSocket control plane + remote MCP (TLS) |

### 1.4 The top-level ("god") orchestration model (revision 3)

```
                       user instruction
                              │
                              ▼
                   ┌───────────────────────┐
                   │        Brahma         │   ← top-level orchestrator
                   │  (Gemini Live + tool   │      plan · decompose · route · verify
                   │   index + registry)    │
                   └───────────┬───────────┘
              delegates to     │     all published as tools, one catalog
        ┌──────────────┬───────┴────────┬──────────────────┐
        ▼              ▼                ▼                  ▼
   Tier-1 agent   Tier-2 orchestrator  Tier-3 remote   (native / MCP tools)
   (claude/ollama)│  Orca · Multica ·   agent/runtime
        ▲         │  Paperclip          (cloud Multica
        │         ▼                      runtime, Paperclip
        │    their own teams              cloud adapter)
        └── same execution path: DynamicToolRegistry → tool index → call_tool
```

Rationale: Brahma already has the decomposition loop (`agent/executor.py`), the retrieval
budget, and the universal executor. Adding tiers 2–3 is **another set of adapters behind the
same seam** — not a new orchestration engine. Brahma remains the single point that (a)
receives the instruction, (b) chooses *agent vs orchestrator*, (c) bounds cost/governance,
and (d) reports completion. This is intentionally **not** a peer federation: it is a strict
hierarchy with Brahma at the top, matching the existing supervisor pattern.

---

## 2. Codebase analysis

### 2.1 The integration model: why agents need no handshake change

Brahma Evo orchestrates Gemini Live from `BrahmaLive` (`main.py:2162`, loop `main.py:5914`,
boot `main.py:6069`). The model's tool surface is **not** the full catalog: it is a
**retrieved, budgeted subset**.

**The chain, end to end:**

1. `core/tool_index.py` is a search engine over every tool. It indexes name variants,
   parameter names, domain, keywords, and description (`ToolEntry.search_text`,
   `core/tool_index.py:253`). It fuses FTS5/ngram TF-IDF with optional MiniLM embeddings
   via Reciprocal Rank Fusion.
2. Gemini Live **cannot swap tools mid-session** and caps at **128 declarations**
   (`GEMINI_MAX_TOOLS = 128`, `core/tool_index.py:88`; the reason is documented in
   `docs/ONE-391-tool-index.md`). So the advertised set is small and constant:
   `DEFAULT_ADVERTISED_BUDGET = 24` (`:92`) + pinned `undo`/`recall_memory` (`:96`) + the
   three meta-tools `search_tools` / `call_tool` / `get_skill_manual`
   (`META_TOOLS`, `core/tool_index.py:106`).
3. `core/tool_bridge.py` builds the per-session payload
   (`build_session_payload`, `core/tool_bridge.py:205`; budget clamps at
   `core/tool_index.py:1315-1316`) and executes the meta-tools
   (`is_meta_tool` `:297`, `execute_meta` `:301`).
4. `call_tool` routes a discovered-but-unadvertised tool back through
   `BrahmaLive._execute_tool_via_bridge` (`main.py:4290`) → `_execute_tool_sync`
   (`main.py:4300`) → `_execute_tool` (`main.py:4895`). The comment at `main.py:4293-4296`
   is explicit: *"There is deliberately no second execution path."*
5. In `_execute_tool`, unknown names fall through to
   `DynamicToolRegistry.execute_sync` (`main.py:5451-5456`).
6. `core/dynamic_registry.py:121 register_external_tool(...)` is the **durable overlay**
   (survives `initialize()`, which clears `_skills` — see the design note at
   `core/mcp_manager.py:22-24`).

**Consequence for this project:** a discovered agent becomes `N` external tools with
`source="agent:<id>"`. They are indexed, retrievable by `search_tools`, invocable through
`call_tool`, and executed through the registry — **with zero edits to `main.py`'s guard
chain, zero new declarations, and no risk to the Live handshake.** This is exactly how
custom MCP servers are onboarded today (`core/mcp_manager.py:754-767`).

**Cold-start placement (a refinement worth stating).** At connect time (no user intent),
`tool_index._cold_start_order` (`core/tool_index.py:1345-1361`) advertises in strict tiers:
native/skill first, then `mcp:*`, then everything else. `agent:*` sources land in the
**last tier**, so a machine with ten agents will **not** crowd out core built-ins on the
initial payload. At query time, retrieval applies a **per-source cap**
(`max_per_source = max(2, (k+1)//2)`, `core/tool_index.py:1206,1247`) so one agent cannot
monopolize the advertised slots. Both behaviors are desirable defaults for this feature.

### 2.2 Reusable assets (detailed)

| Asset | Exact location | What it gives us | Reuse for |
|---|---|---|---|
| Tool publication | `core/dynamic_registry.py:121` `register_external_tool(name, description, parameters, executor, *, version, active, source)`; `:163` unregister; `:200` initialize | Durable external-tool overlay; executor is any callable taking `**kwargs` | Publish every agent tool |
| Index registration helpers | `core/mcp_manager.py:762 _index_tool(...)`; `_drop_from_index({...})` (`:810`) | Adds/removes `ToolEntry` rows for a source | Agent tools, agent skill manuals |
| Manuals | `core/tool_index.py:283 SkillManual`; `_publish_manual` (`core/mcp_manager.py:733`); `render_skill_context` (`core/tool_index.py:1363`) | A per-group usage guide injected **only** on turns where a tool of that group is advertised | Ingest per-agent usage docs as `agent:<id>` manuals |
| Name sanitization | `core/mcp_manager.py:75 _sanitize_name(*parts)` | Provider-safe tool names, collapses illegal chars/separators | `agent_<id>_<tool>` naming |
| Schema normalization | `core/mcp_manager.py:105 normalize_parameters(schema)` | JSON Schema → Gemini `FunctionDeclaration` subset (drops `anyOf`/`$ref`, upper-cases types) | Agent tool schemas |
| MCP transports | `core/mcp_manager.py:578-592` stdio, `:625` streamable-HTTP, `:628` SSE; `:864 _transport_for` | Ready-made clients via the official `mcp` SDK | Every MCP-class agent, incl. those discovered from vendor configs |
| Supervision | `core/mcp_manager.py:542 _supervise`; `:832 status`; `:420 start_servers`; `:495 stop_all` | Reconnect loop + UI-safe status snapshot + lifecycle | Agent lifecycle management |
| Manual ingestion | `core/mcp_manager.py:643 _ingest_instructions` | Reads `initialize.instructions`, `prompts/list`+`get`, `resources/list` | Automatic per-agent docs (and a template for CLI agents) |
| Generic stdio JSON-RPC | `actions/playwright_mcp_client.py:29-251` | Long-lived `Popen` (`:80`), MCP handshake (`:104-129`), reader thread with id correlation (`:135-162`), `_send_request` (`:179`), `call_tool` (`:216`), `close` (`:424`) | CLI agent drivers |
| One-shot stdio | `actions/spotify_controller.py:358-399` | Spawn → write JSON-RPC lines → read → tear down in `finally` | Cheap/short-lived agents |
| HTTP model probe | `llm_client.py:162 poll_opencode_models(url)` | Tolerant `/v1/models` parse; never raises | HTTP-agent capability probe |
| Local runtime probe | `core/local_brain.py:53 is_available`, `:63 list_installed_models`, `:68 pull_model_async` | Ollama `/api/tags`, `/api/pull` | Ollama adapter |
| OpenAI-compatible chat | `llm_client.py:80 _openai_chat_completion` (`:67` — auth header only when a key exists), `core/local_brain.py:159 generate_chat_stream` (SSE) | Streaming + non-streaming local chat | HTTP-agent invocation |
| Process enumeration | `actions/open_app.py:67-81` (`psutil.process_iter`) | Detect already-running agents | Discovery Layer E |
| Registry App Paths | `actions/browser_control.py:83-104` (HKLM+HKCU, `...\App Paths\<x>.exe`) | Find installed executables via registry | Discovery Layer B |
| `which` + candidate paths | `actions/open_app.py:92,128`; `actions/browser_control.py:117-162`; `actions/dev_agent.py:273-296` | Locate executables | Discovery Layer A/C |
| Port check | `main.py:138 _is_port_in_use(port, host)` (`connect_ex`, 0.25 s timeout) | Detect live services | Discovery Layer E |
| Confirmation gate | `core/confirm.py:82 request(key,title,detail,run)`, `:115 resolve`; bound to HUD via `bind()` | Parks irreversible actions behind an on-screen confirm | Any agent that runs shell/files |
| Provider registry | `core/provider_router.py:39-68 PROVIDERS`, `:180 guard`, `:126 provider_spec`, `:141 _configured` | Declarative provider list + kill switches | If agents should join LLM fallback |
| Settings | `memory/config_manager.py:24 load_settings`, `:35 save_settings`, `:50 set_setting` | JSON persistence under `%LOCALAPPDATA%\BrahmaAI\config` | Persist `local_agents` |
| Plugin seam | `plugin_manager.py:14 load_plugins`, `:46 dispatch`; wired `main.py:6237` (+`on_startup` `:6279`, `on_text_command` `:2836`) | Hook-based, `plugins/` currently **empty** | Ship the bridge with zero core edits first |
| Boot + resync pattern | `main.py:6086` (start MCP in a thread) + `main.py:6091-6111` (poll `status()`, `tool_bridge.sync_all` every 2 s for 30 s) | Background boot with progressive index refresh | Copy verbatim for agent discovery |
| Status → UI grouping | `core/tool_bridge.py:490-541` groups index entries by source: `mcp:*` → kind `"mcp"`, else `"default"` | Extend to recognize `agent:*` as its own group | "Tools & MCP Access" card |

### 2.3 The existing "Discover Local Agents" path (to be extended, not replaced)

- Button: `ui.py:13101` (`🤖 Discover Local Agents`), worker `_ModelDiscoveryBridge`
  (`ui.py:11007`), signal `finished = pyqtSignal(list, str, str)` (models, url, error),
  `discover(url)` spawns a daemon thread and calls
  `UnifiedAIClient.poll_opencode_models(url)` (`ui.py:11021`).
- Default URL: `OPENCODE_DEFAULT_URL = "http://localhost:4096/v1"` (`llm_client.py:22`).
- On success the panel fills a model combo and auto-adopts the URL if blank
  (`ui.py:13123-13134`).

This is the correct UI entry point. The refinement is to make it discover **many** agents,
not one URL's models, and to render a **list** rather than a combo box — while keeping the
exact worker/signal pattern.

### 2.4 Concurrency & lifecycle model (must be respected)

This is the single most error-prone area, so the bridge must copy the existing conventions:

- **The Live session runs on one asyncio loop** created by `asyncio.run(brahma_evo.run())`
  in the runner thread (`main.py:6360`). `BrahmaLive._loop` is that loop.
- **Tool calls can arrive from arbitrary threads.** Sync tools hand work back with
  `asyncio.run_coroutine_threadsafe(self._execute_tool(fc), self._loop)` and block
  (`main.py:4311-4313`).
- **Blocking work is fanned out to daemon threads**, e.g. `threading.Thread(target=...,
  daemon=True)` throughout `_on_text_command` (`main.py:2931,3017,3464,...`).
- **MCP uses one dedicated loop + thread** with a *synchronous* executor that hops onto it
  (`core/mcp_manager.py:13-21` design note; `_call_sync` `:1010-1021`). The explicit
  warning: calling `asyncio.run` on an MCP session from another thread deadlocks or raises
  *"attached to a different loop"*.

**Design rule for the bridge:** give `AgentBridge` **one dedicated background event loop +
thread** (like `MCPServerManager`) and expose **synchronous** `connect()/call()/status()`
entry points that use `run_coroutine_threadsafe`. Never `asyncio.run` a session bound to
another loop. Discovery is pure/blocking and runs in its own daemon thread.

### 2.5 Persistence & settings semantics (subtlety to respect)

`config_manager.save_settings` does a **shallow** `current.update(data)`
(`memory/config_manager.py:37-38`) — a value that is a list/ dict is **replaced wholesale**,
not merged. Therefore the bridge must **read-modify-write** the `local_agents` key itself
(load full list, merge by `id`, save), rather than relying on `set_setting` to merge entries.

Precedent for a one-time seed + migration guard: `core/mcp_manager.py:339-369`
(`_seed_from_template`, flag `custom_mcp_servers_migrated`). The bridge should use the same
pattern (`local_agents_seeded`).

### 2.6 Extension seams, ranked by blast radius

1. **Plugin** (`plugins/*.py` via `plugin_manager.py`) — zero core edits; gets
   `on_brahma_created(brahma)`, `on_startup(brahma=...)`, `on_text_command(text, source)`.
   Best for a first delivery and for the discovery + UI wiring.
2. **External tools** (`DynamicToolRegistry.register_external_tool`) — the runtime
   surface the model actually calls. No `main.py` change.
3. **Feature/skill files** (`features/*` with `FEATURE_METADATA`/`manifest.json`) — for a
   declarative adapter that needs no per-install code; loaded by
   `DynamicToolRegistry.initialize` (`core/dynamic_registry.py:200`).
4. **Native tool branch** (`main.py:_execute_tool` elif-chain, ~50 branches) — only if
   first-class verbs like `list_local_agents` are wanted in every session regardless of
   retrieval. Avoid unless necessary.

### 2.7 Post-refactor module mapping (from `docs/CODE-REVIEW-TODO.md`)

**IMPORTANT:** all `file:line` references in this document were taken from the current
(pre-refactor) tree. The refactor moves many symbols into dedicated modules. **Trust the
symbol names, not the line numbers.** Use this table to locate post-refactor code.

| Document reference (pre-refactor) | Post-refactor location (target) | Notes |
|---|---|---|
| `main.py:_execute_tool`, `_execute_tool_via_bridge`, `_execute_tool_sync` | `core/tool_exec.py::ToolExecutor` | `main.py` keeps thin `self._tool_exec` delegations; validate with `py -3.11 -m py_compile` after review. |
| `BrahmaLive` in `main.py` (audio I/O paths) | `core/audio_io.py::AudioIO` | Loop/IG paths moved out of `main.py:2162` vicinity. |
| Reply/IG/email/reply flow | `core/reply_flow.py::ReplyFlow` | Keeps reply branching outside the Live loop. |
| Session state machine / lifecycle | `core/session_lifecycle.py::SessionLifecycle` | Decouples lifecycle from execution. |
| Provider failover, loopback logic | `core/provider_failover.py::ProviderFailover` | Routes to chain/guard paths. |
| `ui.py` MCP card, connectivity/settings pane, `_ModelDiscoveryBridge`, `SystemConnectivityPage`, `SmartDevicesSection` | `core/ui_connectivity.py` | Connectivity UI consolidated. |
| `ui.py` `_default_app_settings` | `core/ui_startup.py` | Startup defaults extracted. |
| Qt bootstrap/import-time side effects | `core/qt_bootstrap.bootstrap()` | Applied in tests/conftest.py and at app boot. |
| Atomic settings writes | `core/user_paths.atomic_write_json` + `memory/config_manager.py` | `save_settings` uses atomic writes (P0-2). |
| `MainWindow` (~97 methods, ~1945 lines) | split across 16 mixins | `BrahmaUI` (~952 lines) over 12 mixins. |

*Guardrail:* the commit/tag `checkpoint/pre-p0-review-fixes` (`262616428b54afec35b912912b6dbf305e951b7e`) is the reference before pushing refactor changes. Verify `py -3.11 -m py_compile`, `py -3.11 -m pytest -q`, and relevant `accept_*.py` after the review run.

---

## 3. Gap analysis (what does not exist)

Verified by repo-wide search (excluding `.venv`, `__pycache__`, `assets`):

1. **No invocation or lookup of any AI coding CLI.** Zero matches for spawning/locating
   `claude`, `codex`, `gemini`, `aider`, `copilot`, `cursor-agent`, `windsurf`, `cline`.
   The in-repo `claude_code` is an in-process rebrand
   (`actions/claude_code_bridge.py:40` → `actions/brahma_dev_agent.py`); it never launches
   a binary. No `shutil.which("claude")`-style probe exists.
2. **No reading of other vendors' config files.** No
   `%APPDATA%\Claude\claude_desktop_config.json`, no `~/.claude/`, `~/.codex/`,
   `~/.gemini/`, `~/.cursor/mcp.json`, no `%APPDATA%\Code\User\mcp.json`. `mcpServers` is
   read only from Brahma's *own* `app_settings.json` (`core/mcp_manager.py:324-337`).
3. **No installed-software enumeration.** No Uninstall-key sweep, no
   `winget`/`npm -g`/`Get-Package`/Appx, no `%ProgramFiles%` or `%LOCALAPPDATA%\Programs`
   walk. Registry is used only for Opera (`actions/browser_control.py:83-104`), autostart
   (`ui.py:9325`), Desktop path (`ui.py:14893`).
4. **No PATH/PATHEXT inspection or manipulation.**
5. **No localhost service scanner.** Only Brahma's own ports 8000/8765 are checked
   (`main.py:138,6139,6184`). Nothing probes LM Studio `:1234` or sweeps a port table.
6. **No LM Studio-specific client.** Only the generic OpenAI-compatible URL.
7. **No named-pipe / Unix-socket IPC.**
8. **No auto-start/watchdog for agent CLIs.** `MCPServerManager._supervise`
   (`core/mcp_manager.py:542`) only supervises *configured* MCP servers.
9. **No agent-to-agent protocol (A2A), agent cards, or subagent runtime.** `agent/` is one
   in-process plan→execute loop (`agent/executor.py:478`).
10. **No PTY/interactive-TTY capture.** No `pty`, no raw-mode handling——which matters
    because several target CLIs crash without a TTY (see §5.6).
11. **No UI listing detected agents.** Only manual MCP entry (`ui.py:12609-12722`) and the
    single-URL model probe.
12. **No tests for discovery or real external-process communication.** All MCP/gateway
    tests use fakes or tmp files.
13. **No prompt-injection/hygiene scanner in this codebase.** (The hygiene tooling seen in
    tool listings belongs to the separate Synapse MCP daemon, not to this repo.) The bridge
    must therefore treat agent output as **untrusted by construction** (see §4.7).

---

## 4. Design

### 4.1 Proposed module layout (new; not yet created)

```
core/agent_bridge/
    __init__.py          # facade: discover(), connect(), list_agents(), call(), shutdown()
    detectors.py         # Layers A-F; pure, side-effect-free, never raises
    registry.py          # AgentRecord store + persistence (read-modify-write)
    bridge.py            # dedicated event loop/thread; sync facade over transports
    transports/
        stdio_jsonrpc.py # generalization of playwright_mcp_client.py
        mcp_adapter.py   # thin reuse of core/mcp_manager's stdio/HTTP/SSE helpers
        http_openai.py   # reuse llm_client._openai_chat_completion / local_brain
        http_agent.py    # A2A agent-card probe / streamable-HTTP
    adapters/
        claude_code.py
        codex_cli.py
        gemini_cli.py
        ollama.py
        lm_studio.py
        generic_mcp.py   # any MCP server found in vendor configs
tests/
    test_agent_discovery.py
    test_agent_registry.py
    test_agent_bridge_transports.py
```

Design rules:
- **Discovery is read-only and cheap** — target < 1.5 s warm, on a daemon thread; returns
  data, never raises, never spawns.
- **Connection is lazy** — record `found` first; only spawn/probe when the user hits
  "Connect" or the model actually uses the agent.
- **All transports are synchronously callable** from a worker thread (mirror
  `MCPServerManager._call_sync`), because that is how the registry dispatches
  (`DynamicToolRegistry.execute_sync`).
- **Failure isolation** — a broken agent logs and is skipped; it never blocks startup
  (same contract as `MCPServerManager`, `core/mcp_manager.py:25-26`).

### 4.2 Data model

```jsonc
// AgentRecord — one detected agent
{
  "id": "claude-code",                 // stable slug (sanitized)
  "display_name": "Claude Code",
  "kind": "cli_agent",                 // local_model | mcp_server | cli_agent | agent_daemon | peer
  "status": "found",                   // found | connecting | connected | degraded | disabled | error
  "executable": "C:\\Users\\me\\.local\\bin\\claude.exe",   // when Layer A/B/C hit
  "endpoint": null,                    // "http://127.0.0.1:11434" when Layer E hit
  "version": "2.1.208",                // from `--version` at connect time (optional)
  "transport": {
    "mode": "stdio",                   // stdio | mcp_stdio | http_openai | http_mcp | ws
    "argv": ["claude", "--print", "--output-format", "json"],
    "cwd": null,                       // workspace sandbox if set
    "env": {}                          // names only; secrets resolved at spawn
  },
  "sources": ["path", "config"],       // which layers hit (evidence for the UI)
  "capabilities": [                    // filled on connect
    { "name": "run_task", "description": "…", "parameters": {…} }
  ],
  "manual": "…usage text…",            // optional, becomes agent:<id> SkillManual
  "last_seen": 1770000000.0,
  "enabled": true,                     // user kill switch
  "user_edited": false                 // protect from discovery overwrite
}
```

Merge policy on re-discovery: match by `id`; update `executable`/`endpoint`/`sources`/
`last_seen`; **never** overwrite `enabled`, `transport` overrides, or `user_edited` rows.

### 4.3 Discovery — six layers

`detectors.py` runs the layers, unions results, de-dupes by `id`, and stamps `sources` with
the exact evidence (path / registry key / config file / port / pid).

**Layer A — PATH + known prefixes (catches npm & native installs)**

`shutil.which` over candidate names, probed against `PATH` **plus** prefixes that a
GUI-launched process often lacks:

| Prefix | Why |
|---|---|
| `%APPDATA%\npm` | global npm CLIs (`claude`, `codex`, `gemini`, `aider`, `copilot`) |
| `%LOCALAPPDATA%\Programs\*` | per-user GUI installs |
| `%USERPROFILE%\.local\bin` | Claude Code native installer |
| `%USERPROFILE%\.bun\bin` | bun-installed agents |
| `%USERPROFILE%\.cargo\bin` | cargo-installed agents |

Candidate names per agent: `claude`/`claude.cmd`/`claude.exe`, `codex*`, `gemini*`,
`aider*`, `copilot*`, `cursor-agent*`, `opencode*`, `ollama*`, `lmstudio*`, plus `node`/`npx`
(needed to run MCP servers).

**Layer B — Registry (catches GUI installers)**

Reuse the exact hive/key walk at `actions/browser_control.py:83-104`. Add a read-only scan
of Uninstall keys:
`HKLM`/`HKCU` × `SOFTWARE\…\Uninstall\*` (and the `WOW6432Node` mirror), matching
`DisplayName`/`InstallLocation` against an agent pattern table (Ollama, LM Studio, Claude,
Cursor, Windsurf, VS Code, …).

**Layer C — Known install directories (bounded probes, never a recursive crawl)**

`%LOCALAPPDATA%\Programs\Ollama`, `…\LM Studio`, `…\Cursor`, `%ProgramFiles%\Ollama`, plus
the existing VS Code candidate list (`actions/dev_agent.py:273-296`).

**Layer D — Vendor config files (finds agents + their MCP servers + auth presence)**

Parse tolerantly (missing file / bad JSON ⇒ skip silently, record nothing):

| File | Yields |
|---|---|
| `%APPDATA%\Claude\claude_desktop_config.json` | Claude Desktop `mcpServers` (spawnable) |
| `~/.claude.json`, `~/.claude/settings.json` | Claude Code presence + `mcpServers` scope |
| `~/.codex/config.toml` | Codex CLI + its MCP servers (parse with stdlib `tomllib`, Python ≥3.11 per `bootstrap.ps1:41`) |
| `~/.gemini/settings.json` | Gemini CLI + MCP config |
| `~/.cursor/mcp.json`, `%APPDATA%\Cursor\User\mcp.json` | Cursor MCP servers |
| `%APPDATA%\Code\User\mcp.json` | VS Code MCP servers |

Dedupe discovered MCP servers against Brahma's own `custom_mcp_servers` /
`mcpServers` (`core/mcp_manager.py:380`) by a hash of `(command, args)` or `url` so an
agent is not configured twice.

**Layer E — Live runtime probes (finds agents already running)**

1. **Port sweep** using `main.py:138 _is_port_in_use` over a fixed table, then a protocol
   probe on hit:

   | Port | Service | Probe |
   |---|---|---|
   | 11434 | Ollama | `GET /api/tags` (`core/local_brain.py:53`) |
   | 1234 | LM Studio | `GET /v1/models` (`poll_opencode_models`) |
   | 4096 | OpenCode | `GET /v1/models` (already the default, `llm_client.py:22`) |
   | 3000/8000/8080 | assorted agent UIs | best-effort `GET /v1/models` and `/mcp`; ignore failures |

2. **Process scan** with `psutil.process_iter(['name','exe','cmdline'])` (pattern
   `actions/open_app.py:67-81`) matching the agent command table — catches portable
   installs and non-default ports.

**Layer F — Environment & auth presence (cheap hints, no secrets read)**

Detect presence (not values) of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`CODEX_API_KEY`, `GOOGLE_GENAI_USE_VERTEXAI`, and existing credential dirs
(`~/.claude`, `~/.codex`, `~/.gemini`). This lets the UI show "installed & authenticated"
vs "installed, needs login" and lets adapters choose one-shot vs interactive auth paths.

**Layer G — Local orchestrator programs & services (§4.13)**

Detect agent orchestrators installed locally: **Orca** (ADE), **Multica**, **Paperclip**.
See §4.13 for executable paths, data/config roots, control surfaces, and daemon/heartbeat
probing.

**Detection matrix (latency / confidence / false-positive risk):**

| Layer | Cost | Confidence | Catches | Misses |
|---|---|---|---|---|
| A PATH | ~ms | high | npm/native CLIs | GUI-only installs, non-PATH |
| B Registry | ~10–30 ms | high | installed software | portable/npm |
| C dirs | ~ms | medium | standard dirs | custom locations |
| D configs | ~ms | high | agents with state + MCP servers | never-run agents |
| E runtime | up to ~N×0.25 s | high (live) | running services | not-running agents |
| F env | ~ms | low | auth presence | values |

### 4.4 Connection — transports and concrete adapters

#### 4.4.1 MCP servers (preferred path)

Discovered MCP servers are handed to the existing MCP machinery. Two options:
- **Register into the same `MCPServerManager`** by writing them into
  `custom_mcp_servers` (`core/mcp_manager.py:380` validates; `:578-592` connects), so tools,
  manuals, supervision and status all come for free.
- Or run a **sibling manager** with identical mechanics to avoid mixing user-authored and
  discovered servers.

Command: stdio (`{"command","args","env"}`) or remote (`{"url","headers"}`); transport
inferred by `core/mcp_manager.py:864 _transport_for`. Handshake uses the official SDK; the
hand-rolled variant in `actions/playwright_mcp_client.py:104-129` uses
`protocolVersion: "2024-11-05"` — keep the SDK path to stay current.

#### 4.4.2 CLI coding agents (spawned, non-interactive)

Each adapter spawns the binary in a **print/non-interactive** mode, captures machine-readable
output, and returns the final text/JSON. Concrete, currently-documented invocation forms:

**Claude Code** (v2.1.x):
```
claude --print "<prompt>" --output-format json          # one-shot, final JSON
claude --print --output-format stream-json --verbose --include-partial-messages
claude --input-format stream-json --output-format stream-json   # bidirectional
```
- `-p` is shorthand for `--print`. `--output-format` ∈ `text|json|stream-json`.
- Structured output is available via the Agent SDK `output_format: {type:"json_schema", schema}`.
- **Windows/headless caveats to encode in the adapter:**
  - Without a TTY, older builds crashed in Ink raw mode
    (`--output-format stream-json --input-format stream-json` with no TTY → exit 1).
    Prefer `--print` one-shot unless a persistent session is needed.
  - `stream-json` stdin line handling: blank/whitespace-only lines and CRLF were fixed in
    **v2.1.208**; on older builds the host must strip empty lines and normalise to LF.
- Adapter shape: one-shot `--print --output-format json` per tool call; parse `.result`
  (or `.structured_output`). Wrap in `core/confirm.py` and a workspace `cwd`.

**Codex CLI** (`codex exec`, or `codex e`):
```
codex exec --json "<prompt>"                 # JSONL events on stdout
codex exec - --json                          # read prompt from stdin (pipe a live prompt)
codex exec -o final.txt "<prompt>"           # also write final message to a file
codex exec --output-schema schema.json "<prompt>"   # structured output
CODEX_API_KEY=… codex exec --json "<prompt>" # per-run key
```
- JSONL event types: `thread.started`, `turn.started`, `turn.completed`, `turn.failed`,
  `item.*`, `error`. Item types include agent messages, reasoning, command executions, file
  changes, MCP tool calls, web searches, plan updates.
- `resume` / `fork <SESSION> --worktree` support multi-turn and isolated git worktrees.
  `--ignore-user-config` avoids inheriting the user's config when needed.

**Gemini CLI** (headless):
```
gemini "<prompt>" --output-format json         # { "response", "stats", "error"? }
gemini "<prompt>" --output-format stream-json  # JSONL events
```
- Headless triggers when stdin is non-TTY **or** a positional prompt is given without the
  interactive flag.
- Auth: uses cached credentials if present; otherwise requires `GEMINI_API_KEY` (or Vertex
  env vars). **In non-interactive mode with no auth it exits with an error** — the adapter
  must surface this as `status: degraded` ("installed, needs login"), not a hard failure.

All three emit a `--version`/version string that the adapter should read once to record
`AgentRecord.version` and choose flag sets (guarding against drift; see §5.5).

#### 4.4.3 Local model servers (HTTP, OpenAI-compatible)

- Reuse `llm_client._openai_chat_completion` (`llm_client.py:80`) — note it **only** sends
  `Authorization` when a key is non-empty (`:67`), which is exactly what Ollama/LM Studio
  need.
- Streaming via `core/local_brain.py:159 generate_chat_stream` (SSE `data:` frames until
  `[DONE]`).
- Capability probe: `poll_opencode_models` (`llm_client.py:162`) → model list; then optionally
  a 1-token completion to confirm the model answers.
- Ollama extras: `core/local_brain.py:53,63,68` (`/api/tags`, `/api/pull`).

#### 4.4.4 Generic agent daemons

- **A2A / agent-card**: `GET /.well-known/agent.json` (or `/agent-card`), then a JSON-RPC
  `message/send` POST. New code in `transports/http_agent.py`; validate the response is a
  well-formed task result before treating the endpoint as an agent.
- **MCP over HTTP**: reuse `core/mcp_manager.py:625/628`.

#### 4.4.5 Generic stdio JSON-RPC daemons

Generalize `actions/playwright_mcp_client.py` into `transports/stdio_jsonrpc.py`:
persistent `Popen` with `stdin/stdout` pipes, a reader thread correlating responses by id to
`threading.Event`s, request/notification writers, per-call timeout, and an idempotent
`close()`. This covers any agent that speaks line-delimited JSON-RPC.

#### 4.4.6 One-shot vs persistent: selection rule

| Need | Mode |
|---|---|
| Independent, single-shot tasks ("run this prompt, give me the result") | spawn-per-call (`--print`/`exec`/headless) |
| Multi-turn dialogue with an agent | `stream-json` bidirectional (Claude) / `resume` (Codex) |
| Tool-call style, low latency, many calls | persistent stdio JSON-RPC / MCP session |

#### 4.4.7 Peer agents (optional, Phase 5)

`brahma_connect` already defines a JSON envelope and lifecycle
(`hello → pair_request → authenticate → capabilities → execute → result`;
`brahma_connect/agents/PROTOCOL.md`), with a device registry and pairing codes. It is
advertise-only Zeroconf today (`brahma_connect/gateway/discovery.py:20-58`) — discovering
*other* agents on the LAN would add a `ServiceBrowser`. Defer to Tier 3 remote coverage.

### 4.5 Publishing agents to the model

For each connected agent capability, mirror the MCP publication path exactly:

1. `DynamicToolRegistry.register_external_tool(name=agent_<id>_<tool>,
   description="[AGENT:<id>] …", parameters=normalize_parameters(schema),
   executor=bridge.make_executor(...), source=f"agent:{id}")`
   (`core/dynamic_registry.py:121`; naming via `core/mcp_manager.py:75`; schema via `:105`).
2. Add the index row (`core/mcp_manager.py:762 _index_tool`).
3. Publish a `SkillManual(skill_id=f"agent:{id}")` from the agent's own docs/`--help`/MCP
   instructions (`core/mcp_manager.py:643` is the template; `core/tool_index.py:1363`
   renders it only when an agent tool is advertised).
4. `tool_bridge.sync_registry(registry)` (`core/tool_bridge.py:140`) — same as the MCP
   resync loop (`main.py:6091-6111`).
5. On disconnect, mirror `unregister_server_tools` (`core/mcp_manager.py:798-811`):
   unregister every tool for the source and drop its index rows
   (`_drop_from_index`), or a restarted agent leaves duplicates.

**UI grouping refinement:** `tool_bridge.status()` currently buckets `mcp:*` as kind
`"mcp"` and everything else as `"default"` (`core/tool_bridge.py:490-541`). Extend the
bucket logic so `agent:*` becomes its own `kind: "agent"` group with an `agent:<id>` label
and a state line sourced from `AgentBridge.status()` — the same way MCP server state is
read at `core/tool_bridge.py:516-535`.

### 4.6 Health, lifecycle, and supervision

Adopt the `MCPServerManager` contract:

- **Three-stage health probe:** (1) liveness (path exists / TCP connect), (2) protocol
  (MCP `initialize` / HTTP `/v1/models` / `--version` runs), (3) capability (≥1 tool or
  ≥1 model). Record which stage failed in `status`.
- **Supervision loop** mirroring `core/mcp_manager.py:542 _supervise` — reconnect with
  backoff for persistent transports; do **not** auto-restart one-shot spawns.
- **UI-safe status snapshot** — return plain dicts (the MCP `status()` at `:832` is
  explicitly documented as safe to call from the UI thread).
- **Shutdown** — kill every spawned child next to `MCPServerManager.stop_all()`
  (`main.py:6375`). Track PIDs and use a job object or `taskkill /T`-equivalent to avoid
  orphan `node`/`claude` children (the class `actions/spotify_controller.py:391` handles
  with `finally`).

### 4.7 Security & governance

1. **Confirmation gate.** Any agent able to run shell commands or write files is wrapped in
   `core/confirm.py:82 request(...)` (`main.py:5395` is the precedent). The gate is bound to
   the HUD via `confirm.bind()`; with no interface bound it **refuses** rather than
   proceeding (`core/confirm.py` headless branch) — correct default.
2. **Workspace sandbox.** Constrain `cwd` the way `actions/brahma_dev_agent.py:28`
   (`NativeTools`) sandboxes file operations. Never let an agent default to `C:\`.
3. **No auto-spawn.** Discovery never starts anything. Connecting is explicit.
4. **Untrusted output.** This repo has **no** prompt-injection scanner (§3.13) and no
   hygiene module; agent stdout is third-party text. Rules: never splice raw agent output
   into the system prompt or a `SkillManual` without sanitizing/limiting it; prefer
   returning output as a **tool result** to the model (which treats it as tool data) rather
   than as instructions; cap size.
5. **Secret handling.** Read API keys only from the existing
   `%LOCALAPPDATA%\BrahmaAI\config\api_keys.json` (`or_client.py:22`) / environment;
   pass env **names** in `AgentRecord.transport.env`, resolve at spawn; never persist
   secret values into the settings JSON or the doc's examples.
6. **Executable trust / allow-list.** Maintain an allow-list of known binaries + their
   expected version signature; surface unknown-but-detected agents as `status: found`
   requiring explicit user enabling.
7. **Transport hygiene.** Prefer stdio/loopback; for remote MCP/HTTP require TLS or
   explicit `allow_insecure`. (Note: `brahma_connect` and the dashboard currently use
   plaintext on LAN — `SECURITY.md:52-58` — do not inherit that for cloud-routed agents;
   for Tier 3, use `https` and/or mTLS, with a `verify_ssl` toggle defaulting to `true`.)

### 4.8 UI design (extend, don't rebuild)

Turn the "Discover Local Agents" card (`ui.py:13043-13145`) into a two-part page:

- **Detected Local Agents** list: icon, `display_name`, `kind`, `status` pill
  (`found` / `connected` / `needs login` / `disabled`), `sources` tooltip (evidence),
  version, and per-row actions **Enable / Connect / Disable / Forget**.
- **Actions:** "Scan again" (re-run detectors), "Connect all (safe classes)" (HTTP/MCP
  only), and for CLI agents an explicit "Allow to run commands" checkbox that arms the
  confirmation gate for that agent.
- Reuse `_ModelDiscoveryBridge` (`ui.py:11007`) verbatim as the worker-thread + Qt-signal
  pattern; add an `_AgentScanBridge` with an analogous `finished(list, error)` signal.
- Precedent for a grouped list card: the "Tools & MCP Access" page
  (`ui.py:11627-11770`) and the "Custom MCP Servers" card (`ui.py:12609-12722`).

### 4.9 Observability

- One module logger (`logging.getLogger("AgentBridge")`) following the existing convention
  (`core/mcp_manager.py:43`).
- Expose a `status()` dict shaped like `MCPServerManager.status()` so it can be surfaced in
  the same UI and (optionally) via `tool_bridge.status()` (`core/tool_bridge.py:440-461`
  already embeds `mcp_servers`).
- Startup log line via the existing `_startup_log` helper (`main.py:145`).

### 4.10 Config schema + migration

New key in `%LOCALAPPDATA%\BrahmaAI\config\app_settings.json`:

```jsonc
{
  "local_agents": [ /* AgentRecord[], see 4.2 */ ],
  "local_agents_seeded": true,        // migration guard (pattern: core/mcp_manager.py:339)
  "local_agent_bridge_enabled": true, // master kill switch (pattern: provider_enabled_*)
  "local_agent_scan": {
    "port_sweep": true,
    "process_scan": true,
    "vendor_configs": true,
    "known_dirs": true
  }
}
```

Defaults authored in the settings schema at `ui.py:1605-1670`. Persist with
read-modify-write (§2.5). A master kill switch mirrors the provider flags
(`core/provider_router.py:95 is_enabled`).

---

## 5. Constraints & risks (expanded)

1. **Gemini Live ceiling is a hard protocol limit** (`GEMINI_MAX_TOOLS = 128`,
   `core/tool_index.py:88`; `docs/ONE-391-tool-index.md`). Agent tools must go through the
   index + meta-tools budget — never into `TOOL_DECLARATIONS` (`main.py:752`).
2. **Thinner PATH under GUI launch.** A shortcut-launched process often lacks
   `%APPDATA%\npm` and `~/.local/bin`. Layer A must merge explicit prefixes. (The same trap
   is documented for the interpreter in `scripts/install_optional_deps.ps1:44-60`.)
3. **Blocking HTTP today.** `requests.post` calls have loose/`None` timeouts
   (`or_client.py:133`, `llm_client.py:103`, `core/local_brain.py` `urlopen(..., timeout=None)`).
   A hung local agent could stall a tool call. Bridge calls need explicit short timeouts,
   shaped like `DEFAULT_CALL_TIMEOUT_SECONDS` (`core/mcp_manager.py:57`).
4. **Windows console window flash.** No `creationflags`/`CREATE_NO_WINDOW`/`startupinfo`
   appears anywhere in the repo. A GUI app spawning a console process will **flash a console
   window** (and can steal focus). The bridge must set `subprocess.CREATE_NO_WINDOW` on
   Windows and route `.cmd`/`.bat` through `cmd /c`. (The existing spawn at
   `actions/playwright_mcp_client.py:80` uses `shell=(platform.system()=="Windows")` — a new
   bridge should use the explicit, windowless form instead.)
5. **Subprocess I/O encoding.** Follow the house pattern: `text=True`,
   `encoding="utf-8"`, `errors="replace"`, `bufsize=0`
   (`actions/playwright_mcp_client.py:80-88`; console reconfig `main.py:47-48`). Never trust
   the OEM codepage for agent output.
6. **Headless/TTY crashes.** Some agent CLIs fail without a TTY
   (Claude Code Ink raw-mode crash) and some version-hop on `stream-json` blank-line/CRLF
   handling (fixed v2.1.208). Prefer one-shot print mode; normalise input lines to LF; treat
   auth errors in non-interactive mode as "needs login", not a crash.
7. **No PTY support.** Rich TUI-only agents are out of scope until a PTY layer is added.
8. **Orphan processes.** Track every spawned child and kill the tree on shutdown next to
   `MCPServerManager.stop_all()` (`main.py:6375`).
9. **Name collisions.** Two agents may expose the same tool name; `_sanitize_name`
   (`core/mcp_manager.py:75`) plus an `agent_<id>_` prefix and a registry duplicate check
   are required.
10. **Duplicate MCP servers.** A server may be both user-configured and vendor-config
    discovered; dedupe by command/url hash (§4.3 Layer D).
11. **Version drift.** CLI flags change between releases; read `--version`, keep an
    adapter capability table, degrade gracefully (§4.4.2).
12. **Orchestrator assumptions.** Orca, Multica and Paperclip evolve their JSON shapes. Wrap
    them behind small connector classes (one per orchestrator) with schema guards and
    "degraded" status on unknown fields, rather than spreading direct CLI parsing.
13. **Tier 3 remote latency/auth.** Network failures, TLS, and short-lived run tokens (e.g.
    Paperclip `PAPERCLIP_API_KEY` is a **short-lived run JWT**) — timeouts must be network-safe,
    connections must retry with backoff, and reconnects must re-auth when tokens expire.
14. **Hierarchy semantics.** Delegating a goal to a Tier 2 orchestrator means the work
    becomes **opaque below it** to the top-level model; the bridge should publish only
    high-leverage verbs (`assign_goal`, `create_issue`, `run_automation`, `read_status`,
    `cancel`) and **not** mirror its entire sub-agent fleet as tools (prevents tool explosion).
15. **Context/token cost.** Every advertised tool costs tokens; the per-source retrieval cap
    (`core/tool_index.py:1206,1247`) and cold-start tiers (`:1345-1361`) already bound this,
    but a machine with many agents can still inflate the index. Consider a per-agent
    `advertise_budget` and deprioritizing agents the user rarely uses.
13. **Monolith risk.** `main.py` (6.4k lines) and `ui.py` (~17k) are already large; keep the
    bridge a self-contained package and prefer the plugin seam (`plugin_manager.py`) for
    first delivery so no core file grows.
14. **Uninstall-sweep performance.** Reading the full Uninstall hive is fast but the
    `WOW6432Node` mirror and per-key `QueryValueEx` can add up; bound it and cache results
    with a TTL.

---

## 6. Phased plan with acceptance criteria

**Phase 0 — pure discovery (read-only, default-off flag)**
Deliver `detectors.py` Layers A–F + `AgentRecord` persistence + tests. No spawns, no
network writes.
*Accept:* on a machine with Claude Code + Ollama installed, `discover()` returns both with
correct `sources` evidence; with nothing installed it returns `[]`; it never raises; it
runs in < 1.5 s warm.

**Phase 1 — connect safe classes (HTTP + MCP)**
Ollama / LM Studio / OpenCode via `llm_client` + `poll_opencode_models`; MCP servers from
Layer D via `MCPServerManager`. Publish tools + resync. Extend the existing button into a
real agent list.
*Accept:* a discovered Ollama shows its models and answers a routed prompt; a discovered
MCP server's tools answer through `search_tools`→`call_tool`; the Live handshake still
succeeds (payload ≤ budget).

**Phase 2 — CLI agents + orchestrator connectors**
`stdio_jsonrpc` transport + `claude_code` / `codex_cli` / `gemini_cli` adapters; add
**Orchestrator connectors** for Orca (CLI), Multica (CLI + daemon HTTP), Paperclip (HTTP `/api`
with short-lived run JWT) behind `core/confirm.py`, workspace sandbox, timeouts, one-shot
lifecycle. For Tier 2, publish **only high-leverage verbs** (§5 constraint 14).
*Accept:* "ask Claude Code to summarise this file" works end-to-end through the model; a
denied confirmation performs nothing; a missing-auth agent reports "needs login".

**Phase 3 — supervision & lifecycle**
Reconnect/health loop; status readback; UI grouping (`kind: "agent"`); enable/disable/forget.
*Accept:* killing a persistent agent flips its status to `degraded` and the UI reflects it;
restart recovers tool availability; shutdown leaves zero orphan processes.

**Phase 4 — observability & polish**
Startup log, telemetry surface, per-agent manuals, token-budget tuning. Include UI grouping
`kind: "agent"` and orchestrator state in `tool_bridge.status()`.

**Phase 5 (optional) — peer agents & Tier 3 remote**
mDNS browsing + `brahma_connect` protocol extension; also cover remote Tier 3 runtimes (§4.12).

---

## 7. Test plan (expanded)

Follow the house style (fakes + tmp paths, no real processes — cf.
`tests/test_mcp_server_config.py`):

- **Discovery** — one test per layer with fake `shutil.which`, an in-memory registry stub,
  tmp vendor-config files (valid / missing / corrupt JSON / CRLF), a fake port probe, and a
  fake `psutil.process_iter`. Assert union, de-dupe by id, and `sources` evidence, and that
  no layer raises.
- **Registry/persistence** — round-trip through `config_manager`; assert read-modify-write
  preserves `enabled` and `user_edited` across a re-scan; assert the `local_agents_seeded`
  guard fires once.
- **Transports** — `stdio_jsonrpc` against a tiny fake Python echo-server script (real
  subprocess; extends the tmp-file pattern to a real child), asserting id correlation,
  per-call timeout, and clean teardown (no orphan process).
- **Publishing** — agent-sourced entries appear in `build_session_payload` within budget,
  group under `kind == "agent"`, and are removed on disconnect (mirror
  `tests/test_tool_bridge.py`, `tests/test_tool_index.py` MCP-source assertions).
- **Security** — an agent tool that runs a command does not execute without confirmation
  (fake `confirm`), and output is never spliced into the system prompt.
- **Manual acceptance script** — mirror `accept_providers.py` / `accept_391.py`: launch,
  press "Discover Local Agents", verify the list, connect one HTTP and one CLI agent, ask the
  assistant to use them, confirm the Live handshake still succeeds.

---

## 8. Open questions / decisions needed

1. **Where do discovered MCP servers live** — merged into `custom_mcp_servers` (shared
   supervision) or a parallel manager (isolation)? (Recommend: parallel, to avoid surprising
   the user's hand-authored config.)
2. **First delivery form** — plugin (`plugins/local_agent_bridge.py`, zero core edits) vs
   core package from day one? (Recommend: plugin first.)
3. **Scope of "agents"** — local model servers + MCP + coding CLIs, or also LAN peer agents
   (`brahma_connect`)? (Recommend: defer peers.)
4. **Confirmation policy** — per-agent arm, or global "local agents may run commands"?
   (Recommend: per-agent.)
5. **Capability model for CLI agents** — one generic `run_task(prompt, cwd)` tool per CLI,
   or introspect each CLI's sub-capabilities? (Recommend: start with generic `run_task`.)
6. **Auto-connect on startup** — for safe classes only (HTTP/MCP), or never?

---

## 9. Appendix A — exact seams (quick reference)

```
boot / background discovery  → main.py:6086  (copy the MCP start + resync pattern)
tool registration            → core/dynamic_registry.py:121  register_external_tool
tool unregistration          → core/dynamic_registry.py:163  unregister_external_tool
index row add/drop           → core/mcp_manager.py:762 _index_tool / :810 _drop_from_index
index resync                 → core/tool_bridge.py:140 sync_registry / :181 sync_all
budgeted session payload     → core/tool_bridge.py:205 build_session_payload
budget clamp / tiers         → core/tool_index.py:1315-1316, :1345-1361
per-source retrieval cap     → core/tool_index.py:1206, :1247
meta-tool reachability       → core/tool_bridge.py:297 is_meta_tool / :301 execute_meta
bridged execution path       → main.py:4290 _execute_tool_via_bridge / :4300 _execute_tool_sync
registry fall-through        → main.py:5451-5456
MCP transports + supervision → core/mcp_manager.py:276, :420, :542, :578-592, :625, :628, :864
MCP instruction ingestion    → core/mcp_manager.py:643 _ingest_instructions
MCP status snapshot          → core/mcp_manager.py:832 status
name/schema normalization    → core/mcp_manager.py:75 _sanitize_name / :105 normalize_parameters
stdio JSON-RPC reference     → actions/playwright_mcp_client.py:29-251
HTTP model probe             → llm_client.py:162 poll_opencode_models
OpenAI-compatible chat       → llm_client.py:80 _openai_chat_completion (auth :67)
SSE stream                   → core/local_brain.py:159 generate_chat_stream
Ollama probe                 → core/local_brain.py:53, :63
port check                   → main.py:138 _is_port_in_use
registry App Paths pattern   → actions/browser_control.py:83-104
which/candidate-path pattern → actions/open_app.py:84-150
process scan pattern         → actions/open_app.py:67-81 (psutil)
provider registry (LLM only) → core/provider_router.py:39-68, guard :180, _configured :141
plugin hook (zero core edit) → plugin_manager.py:14/:46, main.py:6237, main.py:2836
confirmation gate            → core/confirm.py:82 request / :115 resolve / bind()
settings persistence         → memory/config_manager.py:24/:35/:50  (key: local_agents)
settings shallow-merge caveat→ memory/config_manager.py:37-38
UI entry point + worker      → ui.py:13101 button, ui.py:11007 _ModelDiscoveryBridge
UI status grouping           → core/tool_bridge.py:490-541 (add kind "agent")
shutdown                     → main.py:6375 (next to MCPServerManager.stop_all)
```

## 10. Appendix B — adapter command cheat-sheet

| Agent | Detect | One-shot invocation | Machine output |
|---|---|---|---|
| Claude Code | `claude` on PATH / `~/.claude` / `.local\bin` | `claude --print "<p>"` | `--output-format json` → `.result` / `.structured_output`; `stream-json` for live |
| Codex CLI | `codex` / `~/.codex/config.toml` | `codex exec "<p>"` (or `codex exec -` with stdin) | `--json` (JSONL) or `-o <file>`; `--output-schema` for structured |
| Gemini CLI | `gemini` / `~/.gemini` | `gemini "<p>"` | `--output-format json` → `.response`; `stream-json` for JSONL |
| Ollama | `:11434` / `ollama` / `%LOCALAPPDATA%\Programs\Ollama` | HTTP `/v1/chat/completions` | JSON / SSE |
| LM Studio | `:1234` / `%LOCALAPPDATA%\Programs\LM Studio` | HTTP `/v1/chat/completions` | JSON / SSE |
| Orca (ADE) | `orca`/`orca.exe` on PATH or `%LOCALAPPDATA%\Programs\orca\resources\bin\orca.exe`; `%USERPROFILE%\orca\workspaces` if present | `orca --json <subcmd> [...]` | JSON; multi-subcommand surface (worktrees, terminals, automations, handoff). See `C:\Users\Admin\.agents\skills\orca-cli\SKILL.md` |
| Multica | `.multica\bin\multica.exe` or `multica` on PATH; `.multica\config.json`, `.multica\daemon.pid/.port` | `multica <cmd> --json [...]` | JSON output; key cmds: auth, daemon (start/stop/status/logs), workspace, issue (list/get/create/update/assign/comment/status), autopilot, config. HTTP API via local daemon. See `C:\Users\Admin\multica\CLI_AND_DAEMON.md` |
| Paperclip | `%USERPROFILE%\.paperclip` and/or `%USERPROFILE%\paperclip`; server `:3100` | `paperclipai` (npx) or `paperclip` CLI under repo; HTTP `/api/*` with `Authorization: Bearer $PAPERCLIP_API_KEY` (short-lived run JWT) | JSON; env vars: `PAPERCLIP_AGENT_ID/COMPANY_ID/API_URL/RUN_ID/TASK_ID/WAKE_REASON/...`. See `C:\Users\Admin\paperclip\skills\paperclip\SKILL.md`, `paperclip\DESIGN.md` |
| OpenCode | `:4096` / `opencode` | HTTP `/v1/chat/completions` | JSON |
| Any MCP server | vendor configs / `npx` | MCP `initialize`+`tools/call` | JSON-RPC |

## 11. Appendix C — glossary

- **AgentRecord** — one detected/connected agent's persisted descriptor (§4.2).
- **Layer A–F** — the six discovery techniques (§4.3).
- **Capability** — one tool/verb an agent exposes after connect (§4.5).
- **SkillManual / `agent:<id>`** — the usage guide injected only when an agent tool is
  advertised (`core/tool_index.py:283`, `core/tool_bridge.py` rendering).
- **Cold-start tier** — the deterministic pre-query advertisement order
  (`core/tool_index.py:1345-1361`).
- **Meta-tools** — `search_tools` / `call_tool` / `get_skill_manual`, the reachability
  mechanism for the unadvertised catalog (`core/tool_index.py:103-106`).

---

## 4.11 Orchestrator adapters (Orca / Multica / Paperclip)

**Target:** Tier 2 — wrap, don't reimplement. Publish only high-leverage verbs; keep
sub-agent fleets opaque to the top-level model (§5 constraint 14).

### 4.11.1 Orca (ADE)

**Detection (Layer G):** `orca`/`orca.exe` on PATH, or
`%LOCALAPPDATA%\Programs\orca\resources\bin\orca.exe`; respect `ORCA_CLI_COMMAND`,
`orca-dev` (if `ORCA_DEV_REPO_ROOT` set), `orca-ide` precedence (see Orca skill notes).
Presence also hinted by `%USERPROFILE%\orca\workspaces` and `~/.orca` (read-only).

**Transport:** `orca` CLI with `--json` subcommands (per its skill). Use the `stdio_jsonrpc`-style
spawn/line-reader for JSON output, or the one-shot spawn-per-call for immutable commands.
Avoid PTY. Copy `actions/playwright_mcp_client.py` spawn hygiene: `CREATE_NO_WINDOW`,
`encoding=utf-8`, `errors=replace`.

**High-leverage verbs to publish (initial set):**
- `assign_goal (goal, worktree_context, priority)` — handoff to an Orca worktree/agent.
- `list_worktrees ()`, `open_worktree (id|path)`.
- `list_automations ()`, `run_automation (id, args)`.
- `read_status (since)`, `list_artifacts (worktree_id)`.
- `cancel (task_id)`.

**Status:** degraded if `orca --version` fails or non-JSON output on a probe.

### 4.11.2 Multica

**Detection (Layer G):** `%USERPROFILE%\.multica\bin\multica.exe` or `multica` on PATH; read
`%USERPROFILE%\.multica\config.json` (tolerant, no secrets). Check daemon: `.multica\daemon.pid`,
`.multica\daemon.port` (files in `%USERPROFILE%\.multica\` or config dir). Also profile
`desktop-api.multica.ai`. Live probe: `GET` to `http://127.0.0.1:<port>/health` or
`multica daemon status --json`.

**Transports:** (a) `multica` CLI with `--json` for machine-readable ops (auth/daemon/workspace/issue/
autopilot/config); (b) daemon HTTP API for reactive reads (issues, execution history, runs,
token usage). Prefer CLI for writes (explicit flags, self-documenting) with timeouts.

**High-leverage verbs:**
- `create_issue (title, description, project_id, workspace_id)` and `assign_issue (id, agent_id)`.
- `update_issue_status (id, status)` (with `--wait`/polling via daemon HTTP).
- `add_comment (id, text)`, `read_issue (id)` (includes comments w/ threads, execution history).
- `list_agents (runtime_id)`, `list_workspaces ()`, `list_autopilots ()`, `run_autopilot (id)`.
- `read_status (issue_id|autopilot_run_id)`, `cancel (issue_id)`.

**Token/steering:** use `multica issue comment` to steer while running (Steering feature). Respect
`--json` output format, exit codes, request timeout, stall detection from `CLI_AND_DAEMON.md`.

**Status:** if daemon not running and CLI lacks connectivity, mark `needs start` (`multica daemon start --json` allowed only via explicit Connect action).

### 4.11.3 Paperclip

**Detection (Layer G):** `%USERPROFILE%\.paperclip` (installed/runtime), and/or `%USERPROFILE%\paperclip`
(source checkout with `cli/` and `paperclip.bat`). Check server on `http://localhost:3100` (default)
or `PAPERCLIP_API_URL`. Read settings/instances under `.paperclip/` tolerantly. Probe:
`GET http://localhost:3100/api/health` (or root) returning JSON.

**Transport:** HTTP `/api/*` authenticated with `Authorization: Bearer $PAPERCLIP_API_KEY`
(short-lived run JWT). For CLI wrappers, prefer `paperclipai` (npx) or repo `cli/` with
environment populated per the Paperclip env contract:
`PAPERCLIP_AGENT_ID`, `PAPERCLIP_COMPANY_ID`, `PAPERCLIP_API_URL`, `PAPERCLIP_RUN_ID`,
`PAPERCLIP_TASK_ID`, `PAPERCLIP_WAKE_REASON`, `PAPERCLIP_WAKE_COMMENT_ID`, `PAPERCLIP_APPROVAL_ID`,
`PAPERCLIP_APPROVAL_STATUS`, `PAPERCLIP_LINKED_ISSUE_IDS`.

**High-leverage verbs (task/heartbeat/governance surface):**
- `list_tasks ()` / `get_task (id)` / `assign_task (id, agent_id)`.
- `create_task (title, description, ... )` (map to Paperclip "issue/task" concept).
- `update_status (id, status)` and `add_comment (id, text)` to surface blockers/reports.
- `read_org_chart ()`, `read_budgets ()` (read-only by default).
- `list_approvals ()`, `read_audit ()` (governance; `allow_insecure` required only if remote).
- `run_heartbeat (reason)` — operator-only confirmation (destructive/workside effect semantics)
  wrapped in `core/confirm.py`.

**Notes:** Paperclip is a control plane, not a single worker. Treat writes as governance-sensitive;
prefer read-only verbs first, and gate mutating verbs (budgets/approvals/heartbeats) behind
the confirmation gate with a detailed description. Heartbeats are central ("If it can receive
a heartbeat, it's hired") — do **not** auto-pulse.

## 4.12 Remote tier (Tier 3)

**Scope:** agents/orchestrators on other machines reachable over the network (Multica runtimes on a
cloud box, Paperclip cloud/sandbox adapters, remote MCP servers, remote Brahma).

**Constraints:**
- Prefer **HTTPS** with `verify_ssl = true`. `allow_insecure=false` by default.
- Timeouts/net-safe backoff (connection/read) shaped like `DEFAULT_CALL_TIMEOUT_SECONDS` (120s)
  but per-class: CLI one-shot 60–120s, orchestration API 30–60s, remote MCP 120s max.
- Token freshness: short-lived JWTs (Paperclip) must be re-fetched on 401/403; do **not** cache
  long-lived secrets in the bridge registry JSON.
- Tier 3 endpoints **not** on 127.0.0.1 are treated as "remote"; UI shows a "Remote" badge and
  warns on `allow_insecure`.

**Discovery (future, Layer G expansion):**
- Multica: `runtimes` advertised via daemon or a pre-configured `remote_runtimes` list in settings
  (manual add first; auto-mDNS later, optional Phase 5).
- Paperclip: `PAPERCLIP_API_URL` points to remote host.
- MCP: streamable-HTTP/SSE to remote host (reuse `core/mcp_manager.py:625/628`).
- Peer Brahma: `brahma_connect` LAN (Phase 5).

**Publishing:** same as Tier 1/2 — `source=f"agent:{id}"` but stamped `tier=3`, `transport=remote_http`.
Tool names unchanged. Do **not** duplicate tiers.

## 4.13 Layer G — local orchestrator detection

Run after A–F; union results:

| Orchestrator | Executable | Roots/config | Live probe | Notes |
|---|---|---|---|---|
| Orca (ADE) | `orca`/`orca.exe`, `%LOCALAPPDATA%\Programs\orca\resources\bin\orca.exe` | `%USERPROFILE%\orca\`, `%USERPROFILE%\orca\workspaces`, skill at `.agents\skills\orca-cli\SKILL.md` | `orca --json version` (tolerant) | respect `ORCA_CLI_COMMAND`, `orca-dev`/`orca-ide`; ignore bare `orca` on Linux context only — Windows OK; on Unix be careful (GNOME screen reader) — Windows: no such name collision risk here. |
| Multica | `multica`/`multica.exe`, `%USERPROFILE%\.multica\bin\multica.exe` | `%USERPROFILE%\.multica\`, `.multica\config.json`, `.multica\daemon.pid/.port`, profile `desktop-api.multica.ai`, repo `C:\Users\Admin\multica` | `multica --version` or `GET http://127.0.0.1:<port>/health` if daemon port file exists | parse port from file; treat `--json` output carefully (version may be plain text). |
| Paperclip | `paperclipai` (npx) or CLI from `%USERPROFILE%\paperclip\cli\` or `%USERPROFILE%\.paperclip\cli\` | `%USERPROFILE%\.paperclip\`, `.paperclip\instances`, `.paperclip\adapter-plugins`, `.paperclip\runtime`, `.paperclip\adapter-settings.json`, repo `C:\Users\Admin\paperclip` | `GET http://localhost:3100/api/health` or `GET http://localhost:3100/` | if repo exists but not installed, surface "found (source)" but require explicit Connect (install/run via repo `cli/`). |

De-dupe: match by id `orchestrator:orca|multica|paperclip` (or hash of (name, exe, endpoint)).
Never overwrite `enabled/user_edited`. Stamp `tier=2`, `kind=orchestrator`.

## 4.14 Data model additions (AgentRecord)

Extend `AgentRecord` with fields (backward-compatible, defaults):

```jsonc
{
  "tier": 1,                // 1|2|3
  "kind": "agent",          // "agent" | "orchestrator" | "peer"
  "tier3": { "allow_insecure": false, "verify_ssl": true, "base_url": null },
  "orchestrator": { "connector": "orca|multica|paperclip", "high_leverage_verbs": ["..."] },
  "endpoint": "http://127.0.0.1:11434/v1"  // or CLI exe+args
}
```

Migration: on load, if `tier` missing, default 1; if `kind` missing, infer from class.

*End of analysis document (revision 3). No code was modified.*
