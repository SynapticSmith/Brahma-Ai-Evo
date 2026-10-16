# Local Agent Bridge — Codebase Analysis & Design Document

**Status:** analysis only — no code has been modified
**Scope of this document:** enable Brahma Evo to **discover every AI agent installed on the
machine** and **open a communication channel to each one**, so agent capabilities become
first-class tools inside the assistant.
**Primary integration seams:** `core/dynamic_registry.py`, `core/tool_bridge.py`,
`core/mcp_manager.py`, `core/provider_router.py`, `plugin_manager.py`, `main.py` boot path,
`ui.py` settings.

---

## 1. Objective

Deliver a new subsystem (working name **Local Agent Bridge**) that:

1. **Finds** all local AI agents/tools present on the Windows machine — coding CLIs
   (Claude Code, Codex, Gemini CLI, …), local model servers (Ollama, LM Studio, vLLM,
   OpenCode), MCP-speaking processes, and agent daemons — by scanning PATH, registry,
   install dirs, vendor config files, running processes, and localhost ports.
2. **Connects** to each found agent over the right transport for that agent class:
   stdio (spawned subprocess / JSON-RPC / MCP), HTTP (OpenAI-compatible, MCP
   streamable-HTTP/SSE), or WebSocket.
3. **Publishes** each connected agent's abilities as tools the existing tool index can
   advertise, without ever exceeding Gemini Live's 128-declaration ceiling.

Everything below is grounded in what the codebase *already has* (with `file:line`
references) and what is *missing*.

---

## 2. What exists today — reusable assets

### 2.1 Architecture context (one paragraph)

Brahma Evo is a PyQt6 desktop assistant orchestrated by `BrahmaLive` in `main.py` (class at
`main.py:2162`, main loop `main.py:5914`, boot `main.py:6069`). The Gemini Live model
discovers capabilities through a **budgeted tool surface**: `core/tool_index.py` indexes
every tool (native + dynamic + MCP), `core/tool_bridge.py` decides which ≤24 declarations
the model sees per session (`tool_bridge.prime` `core/tool_bridge.py:285`,
`build_session_payload` `:205`), and `core/dynamic_registry.py` is the universal execution
backend for anything not hardcoded in the giant `_execute_tool` elif-chain
(`main.py:4895`, ~50 native branches; unknown names fall through to
`DynamicToolRegistry.execute_sync` at `main.py:5451-5456`).

**Consequence:** a new "agent" capability does **not** need a new `elif` branch, a new
Gemini declaration, or any change to the Live handshake. Registering it as an external
tool is enough for it to become searchable, speakable, and executable.

### 2.2 Reusable building blocks

| Capability | Where | Notes |
|---|---|---|
| **Generic stdio JSON-RPC 2.0 client** | `actions/playwright_mcp_client.py:29` (`PlaywrightMCPClient`) | Long-lived `subprocess.Popen` (`:80`), MCP `initialize` handshake (`:104-129`), reader thread with id→event correlation (`:135-162`), `_send_request` (`:179`), `call_tool` (`:216`), `close` (`:424`). **This is the reference implementation for talking to any CLI agent over stdio.** |
| **One-shot stdio JSON-RPC variant** | `actions/spotify_controller.py:358-399` | Spawns `node`, writes `initialize`/`notifications/initialized`/`tools/call` lines, tears down. Good pattern for cheap/short agents. |
| **MCP client manager (production-grade)** | `core/mcp_manager.py` (`MCPServerManager` `:276`) | `load_server_configs` `:380`, `start_servers` `:420`, supervise/reconnect loop `_supervise` `:542`, transports via the official `mcp` SDK — stdio (`:578-592`), streamable-HTTP (`:625`), SSE (`:628`), `_transport_for` `:864`, status readback `:832`, call timeout `DEFAULT_CALL_TIMEOUT_SECONDS=120` `:57`, tool publication into the registry (`:754,805,822`). |
| **Tool publication seam** | `core/dynamic_registry.py:121` `register_external_tool(name, description, parameters, executor, *, version, active, source)` + `unregister_external_tool` `:163` | Durable overlay under `%LOCALAPPDATA%\BrahmaAI\skills\external\`. Execution dispatch `execute_sync` `:544`. |
| **Tool discovery/budget for Gemini** | `core/tool_bridge.py` — `configure` `:65`, `sync_registry` `:140`, `sync_all` `:181`, meta-tools `search_tools` / `call_tool` / `get_skill_manual` (`:297-301`) | Keeps the advertised set small and constant; everything else is reachable *through* a tool. |
| **HTTP local-endpoint discovery** | `llm_client.py:162` `poll_opencode_models(url)` — GET `{url}/models`, tolerant of `{data:[]}`/`{models:[]}`/bare list, never raises | Already wired to a **"Discover Local Agents" button** at `ui.py:13101` with worker thread `_ModelDiscoveryBridge` (`ui.py:11007`). Today it only probes *one* URL for models. |
| **Local runtime health/model probe** | `core/local_brain.py:53` `is_available()` (GET `http://localhost:11434/api/tags`), `:63` `list_installed_models()`, `:68` `pull_model_async` | Ollama-specific. |
| **OpenAI-compatible chat transport** | `llm_client.py:80` `_openai_chat_completion` (`requests.post`, **auth header only when a key exists** — `:67`), `core/local_brain.py:159` SSE streaming | Covers Ollama / LM Studio / vLLM / OpenCode / custom. |
| **Provider registry + kill switches** | `core/provider_router.py:39-68` `PROVIDERS` list, `guard()` `:180`, `chain()` `:157` | Adding a vendor/provider = one dict entry; enable/disable is already persisted as `provider_enabled_<id>`. |
| **Installed-program discovery patterns** | `actions/browser_control.py:83-104` — Windows **registry `App Paths`** across `HKLM` + `HKCU`; `actions/open_app.py:92,128` — `shutil.which` + hardcoded `%ProgramFiles%` / `%LOCALAPPDATA%` candidates; `actions/dev_agent.py:273-296` — VS Code candidate list | The only existing "find a program that is installed" code. Directly copyable. |
| **Localhost port check** | `main.py:138` `_is_port_in_use(port, host="127.0.0.1")` | Single-port today; trivially generalizable into a multi-port sweep. |
| **Process enumeration** | `actions/open_app.py:67-81` uses `psutil.process_iter` | Can detect already-running agents. |
| **Peer-agent protocol (network agents)** | `brahma_connect/gateway/*` + `brahma_connect/agents/PROTOCOL.md` | JSON envelope (`hello, pair_request, authenticate, capabilities, execute, result, …`), device registry, pairing codes, capability checks. The closest existing analogue to a real agent protocol — but oriented at phones/PCs, not coding CLIs. |
| **Background boot + index resync pattern** | `main.py:6086` (`MCPServerManager.start_servers()` in a thread) + `main.py:6091-6111` (poll `status()` and `tool_bridge.sync_all` every 2 s for 30 s) | Exactly the pattern to copy for async agent discovery at startup. |
| **Hook-based plugin seam** | `plugin_manager.py:14` `load_plugins`, `:46` `dispatch(hook, ...)`; wired `main.py:6237` (`on_startup` at `:6279`) and `main.py:2836` (`on_text_command`) | `plugins/` is currently **empty** — an ideal zero-touch place to stage the bridge as a plugin first. |
| **Dangerous-action confirmation gate** | `core/confirm.py:82` `request(key, title, detail, run)` / `:115` `resolve` | Must wrap any agent that can run shell commands. Used by native tools, e.g. `main.py:5395`. |
| **Sandbox for agent-produced code** | `core/skill_crucible.py` — AST bans (`:26`), dep install, subprocess test harness (`:178-290`) | Relevant if discovered agents write skills. |
| **Settings layer** | `memory/config_manager.py` (`load_settings` `:24`, `set_setting` `:50`) over `%LOCALAPPDATA%\BrahmaAI\config\app_settings.json`; defaults authored at `ui.py:1605-1670` | Where agent-scan results and per-agent toggles persist. |
| **Test conventions** | `tests/test_mcp_server_config.py` (config-shape tests with tmp files), `tests/test_tool_bridge.py`, `tests/test_tool_index.py` | Fake/session-free style; matches how a discovery unit should be tested. |

### 2.3 The "Discover Local Agents" button (existing, underpowered)

`ui.py:13101` already presents agent discovery to the user, but it only:
1. takes the single `local_ai_url` field (fallback `OPENCODE_DEFAULT_URL = http://localhost:4096/v1`, `llm_client.py:22`),
2. runs `UnifiedAIClient.poll_opencode_models(url)` on a worker (`ui.py:11021`),
3. fills a model combo box and auto-adopts the URL if the field was blank (`ui.py:13123-13134`).

**The plumbing (worker thread → Qt signal → panel) is there; the discovery breadth is not.**
This is the natural UI entry point to extend.

---

## 3. Gap analysis — what does not exist

Verified absent by repo-wide search (excluding `.venv`, `__pycache__`, `assets`):

1. **No invocation or PATH lookup of any AI coding CLI.** Zero matches for spawning or
   locating `claude`, `codex`, `gemini`, `aider`, `copilot`, `cursor-agent`, `windsurf`,
   `cline`. No `shutil.which("claude")`-style probes anywhere. (`claude_code` in the repo is
   an in-process rebrand — `actions/claude_code_bridge.py:40` →
   `actions/brahma_dev_agent.py`; it never launches a CLI.)
2. **No reading of other vendors' config files.** No `%APPDATA%\Claude\claude_desktop_config.json`,
   no `~/.claude/`, `~/.codex/`, `~/.gemini/`, `~/.cursor/mcp.json`, no
   `%APPDATA%\Code\User\mcp.json`. `mcpServers` is read only from Brahma's *own*
   `app_settings.json` (`core/mcp_manager.py:324-337`).
3. **No installed-software enumeration.** No Windows Uninstall-key sweep, no
   `Get-AppxPackage`/`winget`/`npm -g` queries, no `%ProgramFiles%` or `%LOCALAPPDATA%\Programs`
   walk. Registry is used only for Opera (`actions/browser_control.py:83-104`), autostart
   (`ui.py:9325`), and Desktop path (`ui.py:14893`).
4. **No PATH/PATHEXT inspection or manipulation at all.**
5. **No localhost service scanner.** Only Brahma's own ports 8000 / 8765 are checked
   (`main.py:138,6139,6184`). No probe for LM Studio `:1234`, Ollama `:11434`,
   OpenCode `:4096` (beyond the single `poll_opencode_models` URL).
6. **No LM Studio-specific client** — only the generic OpenAI-compatible URL setting.
7. **No named pipes / Unix-socket IPC.**
8. **No supervisor or auto-start for agent CLIs** — `MCPServerManager._supervise`
   (`core/mcp_manager.py:542`) supervises *configured MCP servers* only.
9. **No agent-to-agent protocol (A2A), agent cards, or "spawn subagent" runtime.**
   `agent/` is a single in-process plan→execute loop (`agent/executor.py:478`).
10. **No PTY/interactive-TTY capture** for rich TUI agent CLIs.
11. **No UI listing "detected local agents"** — only manual MCP entry
    (`ui.py:12609-12722`) and the single-URL model probe.
12. **No tests for discovery or real external-process communication** — all MCP/gateway
    tests use fakes or tmp files.

---

## 4. Proposed design

### 4.1 New module layout (proposed — not yet created)

```
core/agent_bridge/
    __init__.py          # public facade: discover(), connect(), list_agents(), call()
    detectors.py         # Layer A/B/C/D/E discovery (see 4.2), pure & side-effect-free
    registry.py          # AgentRecord store, persisted to app_settings.json
    transports/
        stdio_jsonrpc.py # generalization of playwright_mcp_client.py (persistent Popen)
        mcp_stdio.py     # thin adapter over mcp.client.stdio (reuse mcp_manager internals)
        http_openai.py   # reuse llm_client._openai_chat_completion / local_brain
        http_agent.py    # A2A / agent-card / streamable-HTTP probe
    adapters/
        claude_code.py   # `claude -p` / stream-json one-shot + MCP tool surface
        codex_cli.py     # `codex exec` one-shot + ~/.codex MCP servers
        gemini_cli.py    # `gemini -p` one-shot + ~/.gemini settings
        ollama.py        # wraps core/local_brain.py
        lm_studio.py     # :1234 OpenAI-compatible + /api/v1/... probe
        generic_mcp.py   # any MCP server discovered from vendor configs
tests/test_agent_discovery.py
tests/test_agent_bridge_transports.py
```

Design rules:
- **Discovery must be read-only and cheap** (target < 1.5 s warm, run on a background
  thread), returning data, never raising.
- **Connection must be lazy**: record an agent as `found` first; only spawn/probe when
  the user (or the model) actually uses it, or when the user hits "Connect".
- **Every transport is synchronous-callable from a worker** because
  `_execute_tool` dispatches blocking work through `loop.run_in_executor`
  (`main.py` tool path) and MCP has its own dedicated loop
  (`core/mcp_manager.py:13-21` design note). Follow that exact convention.

### 4.2 Discovery — the five detection layers

`detectors.py` runs these layers, unions the results, de-dupes by agent id, and
timestamps each hit with its `evidence` (which layer found it, exact path/URL/port).

**Layer A — PATH / executable resolution (fast, catches npm & native installs)**

Probe candidate command names with `shutil.which` (pattern: `actions/open_app.py:128`,
`actions/browser_control.py:120-122,140,154`):

| Agent | Candidate names |
|---|---|
| Claude Code | `claude`, `claude.cmd`, `claude.exe` |
| OpenAI Codex | `codex`, `codex.cmd`, `codex.exe` |
| Gemini CLI | `gemini`, `gemini.cmd` |
| Aider | `aider` |
| GitHub Copilot CLI | `copilot` |
| Cursor Agent | `cursor-agent` |
| OpenCode | `opencode`, `opencode.exe` |
| Ollama | `ollama`, `ollama.exe` |
| LM Studio | `lmstudio` |
| Node/MCP hosts | `node`, `npx` (needed to run MCP servers) |

Probe against `os.environ["PATH"]` **plus** known npm/native prefixes that are often
missing from a GUI app's PATH (important: Brahma may be launched from a shortcut and
inherit a thinner PATH than a terminal):

- `%APPDATA%\npm`
- `%LOCALAPPDATA%\Programs\*`
- `%USERPROFILE%\.local\bin` (Claude Code native installer)
- `%USERPROFILE%\.bun\bin`
- `%USERPROFILE%\.cargo\bin`

> Reusable precedent: candidate-path lists + `shutil.which` in
> `actions/browser_control.py:117-162` and `actions/open_app.py:84-150`.

**Layer B — Windows registry & uninstall keys (catches GUI installers)**

Reuse the exact hive/key walk from `actions/browser_control.py:83-104`
(`HKLM`/`HKCU` × `SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\<name>.exe`).

Additionally open (read-only):
- `SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*`
- `SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*`
- `SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*` under **HKCU**

and match `DisplayName`/`InstallLocation` against an agent pattern table
(`Ollama`, `LM Studio`, `Claude`, `Cursor`, `Windsurf`, `Visual Studio Code`, …).

**Layer C — known install directories (fast filesystem probes, no full walk)**

Probe a *bounded* candidate list only — never a recursive Program Files crawl:
`%LOCALAPPDATA%\Programs\Ollama`, `%LOCALAPPDATA%\Programs\LM Studio`,
`%LOCALAPPDATA%\Programs\Cursor`, `%ProgramFiles%\Ollama`, … plus the existing
VS Code list in `actions/dev_agent.py:273-296`.

**Layer D — vendor config files (finds agents + their MCP servers + auth state)**

Parse, if present (tolerant: missing file or bad JSON ⇒ skip silently, record nothing):

| File | Yields |
|---|---|
| `%APPDATA%\Claude\claude_desktop_config.json` | Claude Desktop `mcpServers` (spawnable) |
| `%USERPROFILE%\.claude\settings.json`, `.claude.json` | Claude Code presence/config |
| `%USERPROFILE%\.codex\config.toml` | Codex CLI + its `mcp_servers` |
| `%USERPROFILE%\.gemini\settings.json` | Gemini CLI + MCP config |
| `%USERPROFILE%\.cursor\mcp.json`, `%APPDATA%\Cursor\User\mcp.json` | Cursor MCP servers |
| `%APPDATA%\Code\User\mcp.json` | VS Code MCP servers |
| `%APPDATA%\Ollama\...`, LM Studio app data | runtime hints |

This reuses the config-shape philosophy already in
`core/mcp_manager.py:324-337` (which already understands the de-facto
`{"mcpServers": {...}}` shape — it just currently reads it only from Brahma's own file).

**Layer E — live runtime probes (finds agents already running)**

1. **Port sweep** of a fixed candidate table using `main.py:138` `_is_port_in_use`:

   | Port | Service |
   |---|---|
   | 11434 | Ollama (then `GET /api/tags` — reuse `core/local_brain.py:53`) |
   | 1234 | LM Studio (then `GET /v1/models` via `poll_opencode_models`, `llm_client.py:162`) |
   | 4096 | OpenCode (already the default in `llm_client.py:22`) |
   | 4000/8000/3000 | assorted local agent UIs (probe `/v1/models` and `/mcp`, ignore failures) |

2. **Process scan** with `psutil.process_iter(['name','exe','cmdline'])` (pattern:
   `actions/open_app.py:67-81`) matching the agent command-name table — this catches
   agents running with a non-default port or a portable install.

Each hit produces an `AgentRecord`:

```jsonc
{
  "id": "claude-code",              // stable slug
  "display_name": "Claude Code",
  "kind": "cli_stdio",              // cli_stdio | mcp_stdio | http_openai | http_mcp | peer_ws
  "status": "found",                // found | connected | degraded | disabled
  "executable": "C:\\Users\\…\\claude.cmd",   // if Layer A/B/C
  "endpoint": null,                 // if Layer E
  "transport": { "mode": "stdio", "args": ["-p"], "env": {} },
  "sources": ["path", "config"],    // which layers hit (evidence)
  "capabilities": [],               // filled after connect (tools/models)
  "last_seen": 1770000000.0,
  "enabled": true
}
```

Persisted under a **new settings key** `local_agents` in
`%LOCALAPPDATA%\BrahmaAI\config\app_settings.json` via
`memory/config_manager.py:50 set_setting` — matching how `custom_mcp_servers`
(`ui.py:12765`) and provider flags are already stored. Discovery results are merged,
never blindly overwritten (keep `enabled` and user edits).

### 4.3 Connection — one transport per agent class

| Class | Transport | Reuse | Notes |
|---|---|---|---|
| **MCP-speaking CLI / server** | stdio spawn + MCP handshake | `core/mcp_manager.py:578-592` (`stdio_client`, `StdioServerParameters`) | **Preferred path**: publish tools straight into `DynamicToolRegistry` exactly as MCP servers do today (`core/mcp_manager.py:754,805,822`). Discovery of agents therefore becomes "auto-configured MCP servers". |
| **Generic stdio JSON-RPC agent** | persistent `Popen` + reader thread | `actions/playwright_mcp_client.py:29-251` | Generalize into `transports/stdio_jsonrpc.py`: same id-correlation, timeouts, `close()`. |
| **One-shot CLI agents** (Claude Code, Codex, Gemini CLI) | spawn per request, non-interactive mode | `actions/spotify_controller.py:358-399` (spawn/write/teardown) | e.g. `claude -p "<prompt>" --output-format json`, `codex exec "<prompt>"`, `gemini -p "<prompt>"`. Wrap as a tool executor with timeout + workspace cwd. *Exact flags must be verified against the installed version at implementation time.* |
| **Local model server** | HTTP OpenAI-compatible | `llm_client.py:80` `_openai_chat_completion` (no `Authorization` header when key empty — critical for Ollama/LM Studio), `core/local_brain.py:159` for streaming | Probe with `poll_opencode_models` (`llm_client.py:162`). |
| **Ollama native API** | HTTP | `core/local_brain.py:53-88` | `/api/tags`, `/api/pull`. |
| **Remote/MCP HTTP agent** | streamable-HTTP / SSE | `core/mcp_manager.py:625,628` + `_transport_for` `:864` | Config shape already supports `{"name": {"url": …, "headers": …}}`. |
| **Peer agent on LAN** | WebSocket + `brahma_connect` envelope | `brahma_connect/gateway/*`, `agents/PROTOCOL.md` | Only if scope includes other machines/phones; advertise-only Zeroconf today (`brahma_connect/gateway/discovery.py:20-58`) — browsing would be new work. |

**Safety:** any agent whose transport can execute shell/ file writes must go through
`core/confirm.py:82 request(...)` on first use per session (precedent `main.py:5395`),
and be constrained to a workspace directory the way `brahma_dev_agent` sandboxes
(`actions/brahma_dev_agent.py:28` `NativeTools`).

### 4.4 Publishing agents to the model (the zero-handshake-change path)

Do **not** add native declarations. Follow the MCP playbook:

1. On successful connect, call
   `DynamicToolRegistry.register_external_tool(...)` (`core/dynamic_registry.py:121`)
   for each agent capability, with `source=f"agent:{agent_id}"`.
2. Call `tool_bridge.sync_registry(registry)` (`core/tool_bridge.py:140`) — same as the
   MCP resync at `main.py:6091-6111` — so the index picks them up.
3. The budgeted payload (`build_session_payload`, `core/tool_bridge.py:205`) and the
   meta-tools `search_tools` / `call_tool` (`:297`) mean the model reaches any agent
   capability without a new declaration, honoring `GEMINI_MAX_TOOLS = 128`
   (`core/tool_index.py:87`) and `tool_index_budget` (default 24, `ui.py:1623`).
4. Skill manuals: MCP servers already ingest instructions as `mcp:<server>` manuals
   (`core/mcp_manager.py:646-665,921`) — mirror this as `agent:<id>` so the model can
   learn *how* to drive each agent.

Recommended tool names (illustrative):
`list_local_agents`, `connect_local_agent`, `agent_capabilities`, `call_local_agent`.

Two of these can be registered as external tools rather than native `elif` branches —
keeping `main.py` untouched is possible in the minimal variant.

### 4.5 Where to wire it in (integration points)

| Step | Location | What happens |
|---|---|---|
| Boot (background) | alongside `main.py:6086` (`MCPServerManager.start_servers()` thread) | start `AgentBridge.discover_async()`; then the existing resync loop pattern (`main.py:6091-6111`) picks up published tools |
| Tool index build | after `DynamicToolRegistry.initialize()` `main.py:6126` | registry already seeded; bridge registers on connect |
| Tool dispatch | none required in the minimal variant | falls through to `DynamicToolRegistry.execute_sync` (`main.py:5451`) |
| Optional native tools | `_execute_tool` `main.py:4895` elif-chain (50 branches) | only if first-class `list_local_agents` verbs are wanted |
| Shutdown | with `MCPServerManager.stop_all()` `main.py:6375` | `AgentBridge.shutdown()` — kill spawned CLIs |
| Plugin staging | `plugin_manager.py` + `main.py:6237/6279` (`on_startup`) | the whole bridge can ship first as `plugins/local_agent_bridge.py` with **zero** core edits |
| UI | extend the "Discover Local Agents" card `ui.py:13043-13145` (button `:13101`, worker `:11007`) into a "Detected Local Agents" list; MCP card precedent at `ui.py:12609-12722` | per-agent enable/disable/status |
| Settings | `memory/config_manager.py` key `local_agents`; defaults in `ui.py:1605-1670` | persistence |
| Providers (optional) | `core/provider_router.py:39-68` | add `{"id": "claude-code", "kind": "openai", ...}` **only** if agents should participate in the LLM fallback `chain()`; capability-style agents do **not** belong here |

### 4.6 Suggested phases

**Phase 0 — pure discovery (read-only, ship behind a setting)**
`detectors.py` Layers A–E, `AgentRecord` persistence, unit tests with monkeypatched
`shutil.which` / registry / filesystem / ports. No spawns, no network writes.

**Phase 1 — connect the safe classes**
Ollama / LM Studio / OpenCode (HTTP) via existing `llm_client` +
`poll_opencode_models`; MCP servers from Layer D vendor configs via
`MCPServerManager`. Publish tools + resync. Extend the existing button into a real
agent list.

**Phase 2 — CLI agents**
`stdio_jsonrpc` transport + per-agent adapters (`claude -p`, `codex exec`,
`gemini -p`) behind `core/confirm.py`, workspace sandbox, timeouts, one-shot lifecycle.

**Phase 3 — supervision & lifecycle**
Adopt `MCPServerManager._supervise` (`core/mcp_manager.py:542`) semantics: restart,
status readback, `tool_bridge` status surface (`core/tool_bridge.py:444,518` already
reads `MCPServerManager.status()`).

**Phase 4 (optional) — peer agents**
mDNS *browsing* (today Zeroconf is advertise-only) and/or `brahma_connect` protocol
extension for LAN agents.

---

## 5. Constraints & risks

1. **Gemini Live's 128-declaration ceiling is a hard protocol limit**
   (`core/tool_index.py:87`, documented in `docs/ONE-391-tool-index.md`: the `setup`
   frame cannot be resent mid-session). Agent tools *must* go through the index +
   meta-tools budget, never into `TOOL_DECLARATIONS` (`main.py:752`).
2. **Thinner PATH under GUI launch.** A shortcut-launched process often lacks
   `%APPDATA%\npm` and `%USERPROFILE%\.local\bin`. Layer A must merge explicit
   prefixes, not trust `PATH` alone. (`scripts/install_optional_deps.ps1:44-60`
   documents this exact trap for the interpreter.)
3. **Blocking HTTP today.** `requests.post` with no/loose timeouts
   (`or_client.py:133`, `llm_client.py:103`, `timeout=None`) means a hung local agent can
   stall a tool call. Bridge calls need explicit short timeouts + the
   `DEFAULT_CALL_TIMEOUT_SECONDS`-style guard (`core/mcp_manager.py:57`).
4. **Security.** Discovered CLI agents execute shell code. Mandatory: `core/confirm.py`
   gate, workspace sandbox, never auto-spawn on discovery, never store agent output in
   prompts without hygiene scanning (cf. the prompt-injection hygiene machinery on
   timeline/observation text). Do not read or execute anything from vendor config
   files beyond structured fields.
5. **Ownership of processes.** Spawned CLIs must be tracked and killed on shutdown next
   to `MCPServerManager.stop_all()` (`main.py:6375`) to avoid orphan `node`/`claude`
   children (the bug class `actions/spotify_controller.py` handles with `finally`).
6. **Version drift.** CLI flags (`-p`, `exec`, `--output-format`) change between
   releases; adapters should version-check (`claude --version`) and degrade to
   `status: degraded` rather than erroring.
7. **No PTY support exists.** Rich TUI agents cannot be driven interactively; stick to
   non-interactive/print modes.
8. **Monolith risk.** `main.py` (6.4k lines) and `ui.py` (~17k) are already large;
   keep the bridge a self-contained package and prefer the plugin seam
   (`plugin_manager.py`) for first delivery.

---

## 6. Test plan

Follow the house style in `tests/` (fakes + tmp paths, no real processes):

- `tests/test_agent_discovery.py` — one test per layer: fake `shutil.which`, fake
  registry read (in-memory hive stub), tmp config files (valid/missing/corrupt JSON),
  fake port probe, fake `psutil.process_iter`. Assert union + de-dupe + evidence, and
  that discovery never raises.
- `tests/test_agent_registry.py` — persistence round-trip through
  `config_manager.set_setting`/`get_setting`, merge semantics preserving `enabled`.
- `tests/test_agent_bridge_transports.py` — `stdio_jsonrpc` against a tiny fake Python
  echo-server script (the pattern `tests/test_mcp_server_config.py` already uses for
  config, extended to a real subprocess), asserting id correlation + timeout + clean
  teardown.
- `tests/test_tool_bridge.py` additions — agent-sourced entries appear in
  `build_session_payload` within budget and group under `kind == "agent"` the way MCP
  entries group today.
- Manual acceptance script, mirroring `accept_providers.py` / `accept_391.py`: start the
  app, press "Discover Local Agents", confirm detected list, connect one HTTP agent and
  one CLI agent, ask the assistant to use them, confirm the Live handshake still succeeds.

---

## 7. Quick reference — the exact seams

```
boot / background discovery  → main.py:6086  (copy the MCP start + resync pattern)
tool registration            → core/dynamic_registry.py:121  register_external_tool
tool unregistration          → core/dynamic_registry.py:163  unregister_external_tool
index resync                 → core/tool_bridge.py:140 sync_registry / :181 sync_all
budgeted session payload     → core/tool_bridge.py:205 build_session_payload
meta-tool reachability       → core/tool_bridge.py:297 is_meta_tool / :301 execute_meta
MCP transport + supervision  → core/mcp_manager.py:276, :420, :542, :864
stdio JSON-RPC reference     → actions/playwright_mcp_client.py:29-251
HTTP model probe             → llm_client.py:162 poll_opencode_models
Ollama probe                 → core/local_brain.py:53, :63
port check                   → main.py:138 _is_port_in_use
registry App Paths pattern   → actions/browser_control.py:83-104
which/candidate-path pattern → actions/open_app.py:84-150
process scan pattern         → actions/open_app.py:67-81 (psutil)
provider registry (LLM only) → core/provider_router.py:39-68, guard :180
plugin hook (zero core edit) → plugin_manager.py:14/:46, main.py:6237, main.py:2836
confirmation gate            → core/confirm.py:82
settings persistence         → memory/config_manager.py:24/:50  (key: local_agents)
UI entry point               → ui.py:13101 button, ui.py:11007 worker bridge
shutdown                     → main.py:6375 (next to MCPServerManager.stop_all)
```

*End of analysis document. No code was modified.*
