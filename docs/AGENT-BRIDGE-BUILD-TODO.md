# Agent & Orchestrator Bridge — Master Build TODO (Work Breakdown Structure)

**Purpose:** execution-ready work breakdown for building the agent-orchestrator bridge
designed in `docs/agent-orchestrator-bridge.md` (rev 3 + 2026-10-17 amendment).
That document is the **design source of truth**; this document is the **project-management
source of truth**: it freezes the architecture into exact contracts, then decomposes the
build into task hierarchy trees precise enough for **multiple agents to execute in
parallel without stepping on each other**.

- **Status of build:** ~3% — code does not exist yet (`core/agent_bridge/` absent, verified
  at `664ae4b`, 2026-10-17), but the decision layer is complete: `0K.1` and `0K.3` are DONE
  (`docs/AGENT-BRIDGE-ADR.md` ADR-001…ADR-017, accepted 2026-10-17). Next up: `0K.2`
  fixture capture + Wave 0 foundation. Everything below is greenfield on top of a
  **live-verified seam map** (§1.9).
- **Conflicts:** if this TODO and the design doc disagree, the design doc wins unless an
  ADR task (`0K.*`) supersedes it — record the supersession in `docs/AGENT-BRIDGE-ADR.md`.
- **Verification baseline:** 167 tests, ruff gate, `precommit_check.py` (real git hook),
  Windows CI — see §6 (Definition of Done).

---

## 0. Operating protocol for agents

### 0.1 Status lifecycle

```
[ ] unstarted → [~] in progress (must add "Owner:" line) → [x] done (must add "Evidence:" line)
                ↘ [!] blocked (must state blocker + who unblocks)     [-] deferred (must state ADR/task that resumes it)
```

**Rules (hard):**

1. Flip `[ ]`→`[~]` **before touching any file**; write `Owner: <agent-id>` under the card.
2. A task is `[x]` **only** with an `Evidence:` line containing the exact command run and
   its result (e.g. `Evidence: py -3.11 -m pytest tests/test_agent_discovery.py -q → 24 passed`).
3. Never edit files outside your track's **ownership matrix** (§4). Cross-track needs are
   posted as a blocked-note on your task, not fixed in place.
4. Contracts in §1.2–§1.8 are **frozen** after the `0F.*` wave merges. Changing a frozen
   contract requires a new ADR task (`0K.4+`) and a rename-impact note in every affected card.
5. Every agent runs the §6 gate on **every** claim of done, not just at phase end.
6. Discovery never spawns. Agent tools never enter `TOOL_DECLARATIONS` (`main.py:739`).
   One execution path only (the `ToolExecutor` seam). No second `MCPServerManager`.
   Agent output returns as **tool results**, never spliced into prompts. Secrets are
   **references** (env names / keyring ids), never values. Brahma targeting itself is
   refused. These global invariants are testable and owned by whoever's task touches them.

### 0.2 ID scheme

`<phase><track>.<n>[.<m>]` — phase digit 0–5, track letter (§2.0), task number, optional
subtask. `0D.3.2` = Phase 0, Discovery track, task 3, subtask 2. Phase 5 is a deferred
stub. IDs never get reused after completion.

### 0.3 Size tags

`[XS]` ≤ 0.5 d · `[S]` ≤ 1 d · `[M]` ≤ 2 d · `[L]` ≤ 4 d · `[XL]` > 4 d (split it).

---

## 1. Architecture extraction — the frozen target

Everything in §1 is a **contract**: multiple agents code against it without reading each
other's implementations. Verified line numbers are from HEAD `664ae4b` (2026-10-17).

### 1.1 Target module tree

```
core/agent_bridge/                 # NEW package — self-contained; no core file grows
    __init__.py                    # [F] facade: discover/connect/list_agents/call/status/shutdown (§1.6)
    adapter.py                     # [F] AgentAdapter ABC + Capability/ProbeResult/AgentCallResult (§1.2)
    errors.py                      # [F] error taxonomy (§1.4)
    constants.py                   # [F] enums, source prefixes, timeouts, spawn hygiene (§1.4)
    registry.py                    # [R] AgentRecord store + atomic persistence (§1.3, §1.8)
    detectors.py                   # [D] Layers A–G runner + DetectionContext DI seam (§1.5)
    bridge.py                      # [B] one dedicated loop/thread; sync facade (§1.5)
    governance.py                  # [G] confirm wrappers, sandbox, caps, self-target refusal (§1.7)
    health.py                      # [L] 3-stage probes, supervision, status(), orphan-safe shutdown
    publish.py                     # [P] registry publish/index/manual/sync/unregister + provenance (§1.6)
    artifacts.py                   # [P] durable agent-output store: save/read + manifest (ADR-015)
    transports/
        stdio_jsonrpc.py           # [X] persistent Popen JSON-RPC (generalizes playwright_mcp_client.py:79)
        mcp_client.py              # [X] MCP client transport extracted from core/mcp_manager.py (SDK handshake)
        http_openai.py             # [X] OpenAI-compatible chat/models probe (wraps llm_client/local_brain)
        http_agent.py              # [X] A2A agent-card probe + streamable-HTTP (Tier 3)
    adapters/
        ollama.py                  # [A] Tier 1 local model server :11434
        lm_studio.py               # [A] Tier 1 local model server :1234
        mcp_server.py              # [A] Tier 1 MCP (wraps transports/mcp_client.py — NOT MCPServerManager)
        generic_mcp.py             # [A] Tier 1 MCP servers found in vendor configs (Layer D)
        claude_code.py             # [A] Tier 1 CLI agent
        codex_cli.py               # [A] Tier 1 CLI agent
        gemini_cli.py              # [A] Tier 1 CLI agent
        orca.py                    # [O] Tier 2 orchestrator connector
        multica.py                 # [O] Tier 2 orchestrator connector
        paperclip.py               # [O] Tier 2 orchestrator connector
core/ui_agent_panel.py             # [U] NEW leaf module: Detected Local Agents list card (keeps ui_connectivity edits tiny)
plugins/local_agent_bridge.py      # [B] first-delivery shim: zero-core-edit boot wiring via plugin_manager.py:14/:46
tests/test_agent_discovery.py      # [V]
tests/test_agent_registry.py       # [V]
tests/test_agent_bridge_transports.py   # [V]
tests/test_agent_adapter_contract.py    # [V] one shared suite every adapter must pass
tests/test_agent_publishing.py     # [V]
tests/test_agent_artifacts.py      # [V] durable output store + read_agent_artifact (ADR-015)
tests/test_agent_governance.py     # [V]
tests/fixtures/agent_bridge/       # [V] golden --json captures (Appendix B)
accept_agent_bridge.py             # [V] manual acceptance harness (house style)
docs/AGENT-BRIDGE-ADR.md           # [K] decision record (created by 0K.1)
```

### 1.2 Frozen contract — `AgentAdapter` ABC and value types

```python
# core/agent_bridge/adapter.py — FROZEN after 0F.2 merges
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Dict, List, Optional

@dataclass(frozen=True)
class Capability:
    name: str                      # verb id, e.g. "run_task" | "create_issue" (sanitized at publish)
    description: str
    parameters: Dict[str, Any]     # JSON Schema, pre-normalization (normalize_parameters at publish)
    mutating: bool = False         # True → governance MUST gate (§1.7); discovery verbs are False

@dataclass
class ProbeResult:
    ok: bool
    stage: str                     # "liveness" | "protocol" | "capability"  (which stage failed)
    detail: str                    # ACTIONABLE sentence, never bare False (house rule: avatar probe)
    version: Optional[str] = None  # from --version / header, recorded on AgentRecord
    models: List[str] = field(default_factory=list)

@dataclass
class AgentCallResult:
    ok: bool
    output: str                    # returned to the model AS A TOOL RESULT only; size-capped
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None    # sanitized message; never raw secrets/stack traces
    timed_out: bool = False
    truncated: bool = False        # output was capped at MAX_AGENT_OUTPUT_BYTES for the model
    bytes_total: int = 0           # FULL output length — always persisted (ADR-015)
    artifact_ref: Optional[str] = None   # durable ref: "agent-out://<agent-id>/<call-id>"

class AgentAdapter(ABC):
    KIND: ClassVar[str] = "agent"          # or "orchestrator" for Tier 2
    TIER: ClassVar[int] = 1                # 1 | 2 | 3
    AGENT_ID: ClassVar[str] = ""           # stable slug; matches AgentRecord.id

    @classmethod
    @abstractmethod
    def detect(cls, ctx: "DetectionContext") -> Optional["AgentRecord"]:
        """PURE. Never raises, never spawns, never writes. Returns a record or None."""

    @abstractmethod
    async def probe(self) -> ProbeResult: ...

    @abstractmethod
    async def connect(self) -> None:
        """Raises ConnectError (transport), AuthRequired (→ status: degraded, 'needs login')."""

    @abstractmethod
    def verbs(self) -> List[Capability]: ...

    @abstractmethod
    async def call(self, verb: str, args: Dict[str, Any]) -> AgentCallResult:
        """Agent faults (non-zero exit, bad JSON, schema drift) come back as ok=False —
        NEVER as an exception. Timeouts enforced internally per §1.4 constants."""

    @abstractmethod
    async def close(self) -> None:
        """Idempotent. Must leave zero orphan child processes (kill the tree)."""
```

**Sync rule (§1.5):** the registry dispatches synchronously
(`DynamicToolRegistry.execute_sync`, `core/dynamic_registry.py:543`), so every adapter is
**callable from a worker thread** through `bridge.call()` → `run_coroutine_threadsafe` on
the bridge's own loop. Adapters themselves are async-only; only `bridge.py` is sync.

### 1.3 Frozen contract — `AgentRecord` (registry row)

```jsonc
{
  "id": "claude-code",                    // stable slug, _sanitize_name'd, unique key
  "display_name": "Claude Code",
  "kind": "cli_agent",                    // local_model | mcp_server | cli_agent | agent_daemon | peer | orchestrator
  "tier": 1,                              // 1 | 2 | 3  (missing on load → 1)
  "status": "found",                      // found | connecting | connected | degraded | needs_login | disabled | error
  "executable": "C:\\Users\\me\\.local\\bin\\claude.exe",   // Layer A/B/C evidence
  "endpoint": null,                       // "http://127.0.0.1:11434/v1" when Layer E/G hit
  "version": null,                        // filled at connect (probe.version)
  "transport": {
    "mode": "stdio",                      // stdio | mcp_stdio | http_openai | http_mcp | ws | remote_http
    "argv": ["claude", "--print", "--output-format", "json"],
    "cwd": null,                          // workspace sandbox path (§1.7); NEVER defaults to C:\
    "env": {}                             // env VAR NAMES only — values resolved at spawn, never persisted
  },
  "sources": ["path", "config"],          // which detection layers hit (evidence for UI tooltip)
  "capabilities": [],                     // filled on connect: Capability dicts
  "manual": null,                         // usage text → agent:<id> SkillManual (size-capped, sanitized)
  "tier3": { "allow_insecure": false, "verify_ssl": true, "base_url": null },
  "orchestrator": { "connector": null, "high_leverage_verbs": [] },   // orca|multica|paperclip
  "last_seen": 0.0,                       // unix ts stamped by every scan
  "enabled": true,                        // user kill switch per agent
  "user_edited": false                    // discovery NEVER overwrites a true row's protected fields
}
```

**Merge policy on re-scan (hard):** match by `id`; update `executable` / `endpoint` /
`sources` / `last_seen` / `version`; **never** overwrite `enabled`, `transport`
overrides, `user_edited`, or `manual` unless the scan was explicitly forced.
Tested at `0R.4`.

### 1.4 Frozen contract — enums, error taxonomy, constants

```python
# core/agent_bridge/constants.py  [F owns]
SOURCE_PREFIX_AGENT = "agent"                          # source=f"agent:{id}"
SOURCE_PREFIX_ORCH  = "agent:orchestrator"             # source=f"agent:orchestrator:{id}"  (Tier 2 provenance)
TOOL_NAME_TEMPLATE  = "agent_{id}_{verb}"              # sanitized via core/mcp_manager.py:75 _sanitize_name

# Timeouts (shapes from design §4.12 / mcp_manager.py:57)
DEFAULT_CALL_TIMEOUT_SECONDS   = 120.0                 # reuse the house constant, do not re-derive
CLI_ONE_SHOT_TIMEOUT           = (60.0, 120.0)         # connect ok, adaptive per verb
ORCHESTRATION_API_TIMEOUT      = (30.0, 60.0)
REMOTE_MCP_TIMEOUT_MAX         = 120.0
PROBE_TIMEOUT_SECONDS          = 5.0                   # detection-layer network probes
DISCOVERY_WARM_BUDGET_SECONDS  = 1.5                   # whole Layers A–G sweep, warm

# Output durability + in-context cap (ADR-015)
MAX_AGENT_OUTPUT_BYTES         = 256 * 1024            # ONLY the in-context slice is capped
ARTIFACTS_ROOT                 = "agent_artifacts"     # under %LOCALAPPDATA%\BrahmaAI\ — never the repo
ARTIFACT_REF_PREFIX            = "agent-out://"        # result.artifact_ref = f"{prefix}{agent_id}/{call_id}"

# Spawn hygiene (Windows; §5 constraint 4/5) — ONE helper, used by every transport
def spawn_kwargs() -> dict:
    # CREATE_NO_WINDOW on os.name=="nt"; text=True; encoding="utf-8"; errors="replace";
    # bufsize=0; .cmd/.bat routed through ["cmd", "/c", ...]; NEVER shell=True.
```

```python
# core/agent_bridge/errors.py  [F owns]
AgentBridgeError(Exception)                 # base
├── ConnectError                            # transport/protocol failure at connect
├── AuthRequired                            # non-interactive auth missing → status "needs_login"
├── CallTimeout                             # per-call budget exceeded (result.timed_out=True also allowed)
├── SchemaDrift                             # golden fixture mismatch → adapter forced to "degraded"
├── ForbiddenTarget                         # self-target/recursion refusal (Brahma or tagged peer)
├── FanoutBudgetExceeded                    # concurrency/spend cap hit
└── ConfirmationDenied                      # core/confirm.py gate returned deny (result: no-op, logged)
```

**Rule:** `detect()` and `status()`-shaped methods **never raise anything** (mirrors
`MCPServerManager.status()` UI-safety contract, `core/mcp_manager.py:897`). `call()`
converts agent faults into `AgentCallResult(ok=False)`; only bridge-internal bugs escape
as exceptions, and those are caught at the publish boundary and logged.

### 1.5 Threading & lifecycle model (single authoritative description)

```
main thread (Qt)                    bridge thread + own asyncio loop            discovery daemon thread
─────────────────                   ─────────────────────────────────           ──────────────────────
UI button "Scan again" ──────────►  (not involved — scan is pure) ──────────►  detectors.run_all(ctx)
                                                                               returns records, never raises
UI "Connect" / model tool call ─►  bridge.call(id, verb, args)
                                     run_coroutine_threadsafe(adapter.call(...),
                                                              bridge_loop) ─── blocks w/ timeout ──► adapter
registry executor (worker thread) ─► same path (DynamicToolRegistry.execute_sync
                                     → ToolExecutor → bridge.call)                    ▲
shutdown() ──────────────────────►  cancel tasks → adapter.close() (kill child tree) ──┘
```

- **One** dedicated daemon thread + asyncio loop for the whole bridge, created lazily on
  first `connect()`/`call()`; pattern copied from `MCPServerManager` (`core/mcp_manager.py:276-289`).
- **Never** `asyncio.run()` a session bound to another loop (the documented MCP deadlock).
- Discovery runs on its own daemon thread; UI consumes via Qt signal (§1.10).
- Boot wiring ships first as **plugin shim** (`plugins/local_agent_bridge.py` dispatching
  `on_startup` from `main.py:4061`); `main.py` proper gets exactly two edit points later
  (shutdown next to `MCPServerManager.stop_all()` `main.py:4158`, and the optional boot
  resync copied from the `main.py:3869-3887` pattern) — line-budget guarded (§6).

### 1.6 Frozen contract — publishing seam (`publish.py` [P owns])

For every connected agent, mirroring the MCP path exactly:

1. `DynamicToolRegistry.register_external_tool(name=_sanitize_name("agent", id, verb),
   description=f"[AGENT:{id}] {desc}", parameters=normalize_parameters(cap.parameters),
   executor=bridge.make_executor(id, verb), source=f"agent:{id}")`
   (`core/dynamic_registry.py:120`; sanitizer `core/mcp_manager.py:75`; schema `:105`).
2. Index row via `core/mcp_manager.py:969 _index_tool(...)`.
3. `SkillManual(skill_id=f"agent:{id}")` from the agent's manual/docs, published via the
   MCP manual seam; rendered only when an `agent:*` tool is advertised
   (`core/tool_index.py:283`, `:1363`).
4. `tool_bridge.sync_registry(registry)` (`core/tool_bridge.py:139`).
5. Disconnect mirror of `unregister_server_tools` (`core/mcp_manager.py:864`): unregister
   every tool of the source **and** `_drop_from_index` (`:954`) — no duplicates after restart.
6. **Dispatch provenance:** every executor records `(agent_id, verb, source, ok, ms)` once
   via the `AgentBridge` logger; Tier 2 additionally stamps `agent:orchestrator:<id>`.
7. **Advertisement policy:** `agent:*` tools are **unadvertised-by-default** (meta-tool
   reachable only); cold-start already tiers them last (`core/tool_index.py:1345-1361`) and
   the per-source cap (`:1206,:1247`) prevents monopoly — do not fight these, assert them.
8. **UI grouping:** `tool_bridge.status()` (`core/tool_bridge.py:497-532`) gains a third
   bucket: `kind: "agent"` with `agent:<id>` labels + state line from `AgentBridge.status()`.

### 1.7 Frozen contract — governance (`governance.py` [G owns])

| Guard | Rule | Implementation site |
|---|---|---|
| Confirmation gate | EVERY `mutating=True` verb → `core/confirm.py:82 request(...)` with per-adapter policy; deny = no-op + provenance log | `governance.gate(adapter, cap, run)` |
| Workspace sandbox | `transport.cwd` required for any shell/file-capable agent; default to a per-agent sandbox dir; refuse `C:\`/drive root | `governance.sandbox_cwd(record)` |
| No auto-spawn | discovery/detection never starts processes or services; connect is explicit (UI action or model verb) | invariant + test in `0V`/`2V` |
| Untrusted output | agent stdout = tool-result data only; never into system prompt or SkillManual; the in-context slice is capped + sanitized, but the **full output is always persisted** to the artifact store with an `artifact_ref` (ADR-015 — minimal ephemerality) | `publish.py` boundary + `artifacts.py` |
| Secrets | `transport.env` stores **names only**; values resolved at spawn from `api_keys.json`/env; registry JSON never holds tokens; Paperclip JWT re-fetched on 401/403, never cached | adapters + `0R.2` persistence test |
| Self-target / recursion | refuse to publish any adapter whose detected identity is Brahma (`ForbiddenTarget`); peers tagged so no verb re-opens a bridge | `governance.check_target(record)` |
| Fan-out budget | per-adapter `max_concurrent` + `max_spend_usd` — **user-configurable** via `local_agent_budget_defaults` / `local_agent_budget_overrides` settings (ADR-016); `assign*`/`run_*` default `dry_run=True`; every fleet-spawning verb confirm-gated | `governance.Budget` object per adapter |
| Transport hygiene | stdio/loopback default; remote requires HTTPS with `verify_ssl=True`; `allow_insecure` only via explicit user toggle + UI "Remote" badge | `0R` record fields + `1X.3` |

### 1.8 Frozen config schema (settings keys — `0F.5` owns the defaults edit)

```jsonc
// %LOCALAPPDATA%\BrahmaAI\config\app_settings.json  (defaults authored in
// core/ui_startup.py:66 _default_app_settings(); persisted read-modify-write,
// ATOMICALLY via core/user_paths.atomic_write_json — P0-2 house standard)
{
  "local_agents": [],                       // AgentRecord[] (§1.3)
  "local_agents_seeded": false,             // one-time seed guard (pattern: core/mcp_manager.py:343-369)
  "local_agent_bridge_enabled": false,      // master kill switch — ships DISABLED, fully opt-in (ADR-014)
  "local_agent_connect_on_boot": false,     // safe classes only (HTTP/MCP) when true — ADR-005
  "local_agent_scan": {                     // ALL layers default false — fully opt-in (ADR-014)
    "port_sweep": false, "process_scan": false, "vendor_configs": false,
    "known_dirs": false, "orchestrators": false   // Layer G toggle
  },
  "local_agent_confirm_policy": "per_agent",    // per_agent (decided) — no "off" value ships (ADR-003)
  "local_agent_budget_defaults":  { "max_concurrent": 2, "max_spend_usd": 5.00 },  // ADR-016
  "local_agent_budget_overrides": {},           // {"<agent-id>": {max_concurrent, max_spend_usd}}
  "local_agent_artifact_retention_days": 0      // 0 = keep outputs forever (ADR-015)
}
```

### 1.9 Verified reuse seams (live at `664ae4b` — reuse, never re-implement)

| Seam | Location | Used by track |
|---|---|---|
| tool registration / removal | `core/dynamic_registry.py:120` / `:166`; `execute_sync` `:543` | P |
| single bridged execution path | `core/tool_exec.py:69 execute_via_bridge` / `:73 execute_sync` | P, B |
| index row add/drop | `core/mcp_manager.py:969 _index_tool` / `:954 _drop_from_index` | P |
| index resync | `core/tool_bridge.py:139 sync_registry` / `:180 sync_all` | P |
| budgeted payload + clamp | `core/tool_bridge.py:204`; `core/tool_index.py:1315-1316` | P (assert only) |
| meta-tools | `core/tool_bridge.py:296 is_meta_tool` / `:300 execute_meta` | P (assert only) |
| MCP transports | `core/mcp_manager.py:946 _stdio_connect` / `:644 _http_connectors` / `:929 _transport_for` | X |
| MCP instruction ingestion | `core/mcp_manager.py:670 _ingest_instructions` | A (mcp), P |
| MCP status shape | `core/mcp_manager.py:897 status` | L (mirror shape) |
| name/schema normalization | `core/mcp_manager.py:75 _sanitize_name` / `:105 normalize_parameters` | P |
| timeouts | `core/mcp_manager.py:57 DEFAULT_CALL_TIMEOUT_SECONDS` | all |
| stdio spawn reference | `actions/playwright_mcp_client.py:79` (Popen; add CREATE_NO_WINDOW — plan §5.4) | X |
| HTTP model probe / chat | `llm_client.py:162 poll_opencode_models` / `:80 _openai_chat_completion` | X, A |
| Ollama probe | `core/local_brain.py:50 is_available` / `:63 list_installed_models` | A |
| SSE stream | `core/local_brain.py:159 generate_chat_stream` | X |
| port check | `main.py:120 _is_port_in_use` | D |
| registry App Paths | `actions/browser_control.py:85-86` | D |
| `shutil.which` / process scan | `actions/open_app.py:186-188` / `:72` | D |
| confirm gate | `core/confirm.py:68 bind` / `:82 request` / `:115 resolve` | G |
| settings | `memory/config_manager.py:24/:35/:49`; **atomic write** `core/user_paths.atomic_write_json` | R, F |
| plugin seam (zero core edits) | `plugin_manager.py:14 load_plugins` / `:46 dispatch`; wired `main.py:4061` | B |
| boot resync pattern | `main.py:3869 start_servers` → `:3887 sync_all` | B |
| shutdown anchor | `main.py:4158 stop_all` (bridge shutdown goes next to it) | L |
| Qt worker/signal pattern | `core/ui_connectivity.py:244 _ModelDiscoveryBridge`; button `:2338` | U |
| UI status grouping | `core/tool_bridge.py:497-532` (add `kind: "agent"`) | P |
| settings defaults | `core/ui_startup.py:66 _default_app_settings` | F |

### 1.10 Data-flow contracts (who hands what to whom)

```
detectors.run_all(ctx)  ──list[AgentRecord]──►  registry.upsert (merge policy)
registry                ──AgentRecord──►        adapters.<x>.connect/call   (record supplies argv/endpoint/env names)
bridge.make_executor(id, verb) ──closure──►     publish.register (executor → DynamicToolRegistry)
model: search_tools ─► call_tool("agent_<id>_<verb>") ─► ToolExecutor ─► bridge.call ─► adapter.call
adapter.call ──AgentCallResult──► publish boundary (size cap, sanitize) ──str──► model (tool result)
health.probe ──ProbeResult──► registry (status/version) ──dict──► UI panel + tool_bridge.status()
governance.gate ──runs or denies──► adapter.call (only mutating verbs route through)
```

---

## 2. Work breakdown — hierarchy trees

### 2.0 Track legend (threads of work / ownership units)

| Track | Name | Owns (write rights) | Runs in waves |
|---|---|---|---|
| **K** | Decisions / ADRs | `docs/AGENT-BRIDGE-ADR.md` | 0 (serial, first) |
| **F** | Foundation & contracts | `core/agent_bridge/{__init__,adapter,errors,constants}.py`, defaults block in `core/ui_startup.py` | 0 |
| **R** | Registry & persistence | `core/agent_bridge/registry.py` | 1+ |
| **D** | Discovery (Layers A–G) | `core/agent_bridge/detectors.py` (+ private `_layers_*.py` helpers if needed) | 1+ |
| **B** | Bridge runtime | `core/agent_bridge/bridge.py`, `plugins/local_agent_bridge.py`, the 2 guarded `main.py` edit points | 2+ |
| **X** | Transports | `core/agent_bridge/transports/*` | 1 (spike), 2 |
| **A** | Tier-1 adapters | `core/agent_bridge/adapters/{ollama,lm_studio,mcp_server,generic_mcp,claude_code,codex_cli,gemini_cli}.py` | 2–3 |
| **O** | Tier-2 connectors | `core/agent_bridge/adapters/{orca,multica,paperclip}.py` | 3 |
| **P** | Publishing | `core/agent_bridge/publish.py`; the single allowed edit in `core/tool_bridge.py` (status grouping) | 2+ |
| **G** | Governance | `core/agent_bridge/governance.py`, `tests/test_agent_governance.py` | 2+ |
| **L** | Lifecycle / health | `core/agent_bridge/health.py`, shutdown anchor in `main.py` | 4 |
| **U** | UI | `core/ui_agent_panel.py`, minimal wiring edits in `core/ui_connectivity.py` | 2 (list), 4 (full) |
| **V** | Verification | `tests/test_agent_*.py`, `tests/fixtures/agent_bridge/**`, `accept_agent_bridge.py` | all |

**Conflict rule:** one track, one file. `main.py` may be edited only by **B** (boot/shutdown
wiring, ≤ 20 lines total) and **L** (shutdown anchor, ≤ 10 lines). `core/tool_bridge.py`
may be edited only by **P** (status grouping block, ≤ 20 lines). Everything else is
create-only inside `core/agent_bridge/`, `core/ui_agent_panel.py`, `tests/`, `plugins/`.

---

### 2.1 PHASE 0 — Decisions, contracts, registry, discovery (read-only capability)

```
PHASE 0
├── K  Decisions & ADRs
│   ├── 0K.1  [x] DONE 2026-10-17 — ADR bundle #1 (docs/AGENT-BRIDGE-ADR.md ADR-001…ADR-017):
│   │         plugin-first delivery; peers deferred; per-agent confirm; generic run_task;
│   │         auto-connect safe-classes OFF; import-and-wrap extraction; fully opt-in scan;
│   │         user-configurable budgets; durable referencable output; vendor catalog
│   ├── 0K.2  [S]  Capture golden fixtures from this machine (orca/multica/paperclip/claude/codex/
│   │         gemini: --version + one real --json sample each → tests/fixtures/agent_bridge/; absent
│   │         tools recorded as "absent" with recapture TODO — Layer G targets verified present here)
│   └── 0K.3  [x] DONE 2026-10-17 — extraction strategy recorded as ADR-006 (import-and-wrap;
│                   public surface defined; importability spike executes inside 1X.2)
├── F  Foundation & contracts (BLOCKING — interface freeze at end)
│   ├── 0F.1  [XS] Package skeleton: dirs + __init__.py facade stubs (NotImplementedError bodies,
│   │         exact signatures of §1.6), py.typed marker
│   ├── 0F.2  [S]  adapter.py: AgentAdapter ABC + Capability/ProbeResult/AgentCallResult (§1.2 verbatim)
│   ├── 0F.3  [XS] errors.py taxonomy (§1.4)
│   ├── 0F.4  [XS] constants.py: enums, prefixes, timeouts, spawn_kwargs() (§1.4)
│   └── 0F.5  [XS] config schema per §1.8 (incl. budget_defaults/overrides + artifact
│            retention — ADR-015/016; ALL defaults false — ADR-014) in core/ui_startup.py:66
│            block; master kill-switch helper; local_agents_seeded guard constant
├── R  Registry & persistence
│   ├── 0R.1  [S]  AgentRecord dataclass + to_dict/from_dict + load-time migration
│   │         (tier missing → 1; kind inferred from class; unknown fields preserved)
│   ├── 0R.2  [S]  Persistence: read-modify-write by id, ATOMIC via core/user_paths.atomic_write_json;
│   │         merge policy (§1.3) — never clobber enabled/transport/user_edited on scan
│   ├── 0R.3  [S]  Registry API: upsert/get/list(tier,kind,enabled_only)/set_enabled/forget/
│   │         mark_seen/status(); RLock; status() returns plain dicts (UI-safe contract)
│   └── 0R.4  [S]  tests/test_agent_registry.py: round-trip; merge preservation; seeded-guard fires
│             once; atomic-write spy; concurrent upsert smoke
├── D  Discovery layers (all pure; DI-injectable; never raise)
│   ├── 0D.1  [M]  detectors.py runner: DetectionContext dataclass (injectables: which, env, home,
│   │         appdata roam/local, registry_rows, port_open, processes, read_text, now),
│   │         run_all(ctx) union/dedupe-by-id/evidence-stamp, per-layer time budget, never-raise wrapper
│   ├── 0D.2  [S]  Layer A — PATH + prefixes (%APPDATA%\npm, ~\.local\bin, ~\.bun\bin, ~\.cargo\bin,
│   │         %LOCALAPPDATA%\Programs\*); candidate table claude/codex/gemini/aider/goose/qwen-code/
│   │         copilot/cursor-agent/opencode/ollama/lmstudio/node/npx (+ .cmd/.exe variants) —
│   │         vendor-agnostic catalog, extension recipe = ADR-017
│   ├── 0D.3  [S]  Layer B — App Paths registry (browser_control.py:85-86 pattern) + Uninstall sweep
│   │         (HKLM/HKCU × WOW6432Node), DisplayName/InstallLocation pattern table, TTL cache
│   ├── 0D.4  [XS] Layer C — bounded known-dir probes (Ollama, LM Studio, Cursor, VS Code list)
│   ├── 0D.5  [M]  Layer D — vendor configs, tolerant parse (bad JSON ⇒ silent skip):
│   │         claude_desktop_config.json, ~/.claude.json, ~/.codex/config.toml (tomllib),
│   │         ~/.gemini/settings.json, cursor mcp.json, VS Code mcp.json;
│   │         MCP-server dedupe vs custom_mcp_servers (core/mcp_manager.py:327) by hash(command,args)/url
│   ├── 0D.6  [S]  Layer E — port sweep via main.py:120 _is_port_in_use over the fixed table
│   │         (11434/1234/4096/3000/8000/8080) + protocol probe on hit + psutil process scan
│   ├── 0D.7  [XS] Layer F — env/credential PRESENCE only (ANTHROPIC_/OPENAI_/GEMINI_/CODEX_API_KEY,
│   │         ~/.claude ~/.codex ~/.gemini dirs); "installed & authenticated" vs "needs login"
│   ├── 0D.8  [S]  Layer G — orchestrator detection per §1.9 of design (orca/multica/paperclip:
│   │         exe candidates, roots, daemon.pid/.port, :3100 health probe); stamp tier=2,
│   │         kind=orchestrator; id scheme orchestrator:<name>
│   └── 0D.10 [M]  tests/test_agent_discovery.py: one test per layer (fake which/registry/ports/
│             processes/tmp vendor configs: valid|missing|corrupt|CRLF); union+dedupe+evidence;
│             never-raises assertion; warm-budget smoke (<1.5 s with all fakes)
└── V0 Verification scaffolding
    ├── 0V.1  [S]  tests/conftest fixtures: fake DetectionContext builder, tmp settings dir,
    │         fake adapter (echo), fake confirm gate, fixture-dir helpers
    └── 0V.2  [XS] tests/fixtures/agent_bridge/ layout + README (capture recipe per Appendix B)
```

**Phase 0 acceptance (mirrors design §6):** on this machine (Claude Code + Ollama + all
three orchestrators present), `discover()` returns each with correct `sources` evidence;
with everything faked-empty it returns `[]`; never raises; < 1.5 s warm. NOTE: because
every setting ships false (ADR-014), the shipped-default assertion is `discover() == []`;
the positive acceptance run uses the 0V.1 fixture helper that force-enables the scan
layers — and must prove no spawn occurred in either state.

---

### 2.2 PHASE 1 — Connect safe classes (HTTP + MCP), publish, first UI

```
PHASE 1
├── B  Bridge runtime
│   ├── 1B.1  [M]  bridge.py: dedicated daemon thread + own asyncio loop (MCPServerManager pattern
│   │         core/mcp_manager.py:276-289); sync facade connect/call/status/shutdown via
│   │         run_coroutine_threadsafe; per-call timeout enforcement; lazy start; NEVER asyncio.run
│   │         across loops; make_executor(id, verb) closure factory for publish
│   └── 1B.2  [S]  plugins/local_agent_bridge.py shim: on_startup → background discover+connect
│         (safe classes only, gated by local_agent_bridge_enabled + connect_on_boot);
│         zero main.py edits; verify plugin_manager.py:14/:46 dispatch path
├── X  Transports
│   ├── 1X.1  [S]  transports/http_openai.py: thin wrapper over llm_client._openai_chat_completion
│   │         (:80 — auth header only when key present) + poll_opencode_models (:162) +
│   │         local_brain.generate_chat_stream (:159); EXPLICIT short timeouts everywhere
│   ├── 1X.2  [M]  transports/mcp_client.py per ADR-006: stdio/streamable-HTTP/SSE via official SDK,
│   │         surface = connect_stdio(config)/connect_http(url,headers)/session.initialize/
│   │         list_tools()/call_tool(); reuses mcp_manager helpers by import where legal;
│   │         NO supervision, NO retry-on-mutate, NO writes to custom_mcp_servers
│   └── 1X.3    —  (reserved) http_agent.py lands in Phase 2/5 with Tier 3
├── A  Tier-1 adapters (safe classes)
│   ├── 1A.1  [S]  adapters/ollama.py: detect (port 11434 + exe + dir + /api/tags), probe,
│   │         verbs = run_task(prompt,cwd) [mutating=False default; shell verbs later] + list_models;
│   │         connect = none (HTTP stateless)
│   ├── 1A.2  [S]  adapters/lm_studio.py: same shape, port 1234, /v1/models
│   ├── 1A.3  [M]  adapters/mcp_server.py: wraps transports/mcp_client.py; tools/list → Capabilities
│   │         (mutating heuristic: name/description pattern table, default True when uncertain);
│   │         instruction ingestion via mcp_manager.py:670 pattern → manual
│   └── 1A.4  [S]  adapters/generic_mcp.py: any Layer-D-discovered MCP server; provenance tag
│         "vendor-config"; dedupe key shared with 0D.5
├── P  Publishing
│   ├── 1P.1  [M]  publish.py: register (§1.6 steps 1-4), unregister (step 5), manual publish,
│   │         MAX_AGENT_OUTPUT_BYTES sanitize boundary, tool-name collision check against registry
│   │         duplicates (agent_<id>_ prefix + registry duplicate probe)
│   ├── 1P.2  [XS] dispatch provenance logger (step 6) — one wrapper, reused by every adapter
│   ├── 1P.3  [XS] core/tool_bridge.py:497-532 — add kind:"agent" bucket + state line from
│   │         AgentBridge.status() (the ONLY allowed tool_bridge edit; ≤20 lines)
│   ├── 1P.4  [M]  artifacts.py (ADR-015): durable per-call output store under
│   │         %LOCALAPPDATA%\BrahmaAI\agent_artifacts\<agent-id>\ — full output persisted
│   │         BEFORE result truncation; manifest.jsonl index (call_id, verb, ts, bytes,
│   │         sha256); agent-out://<agent-id>/<call-id> ref format; retention_days honored
│   │         (0 = keep forever); never stores inside the repo; wire into publish boundary
│   └── 1P.5  [XS] pinned `read_agent_artifact(ref)` tool (source "agent_artifacts",
│         always_reachable=true like recall_memory at tool_bridge.py:191/216) — returns a
│         persisted output by ref; registered/unregistered with the adapter's publish
│         lifecycle; paged read honors MAX_AGENT_OUTPUT_BYTES per slice
├── G  Governance (first slice)
│   └── 1G.1  [M]  governance.py: gate() wrapper (per-adapter policy table), sandbox_cwd(),
│         untrusted-output boundary helpers, no-spawn invariant documented + asserted in test;
│         wired into publish boundary so ALL adapters inherit it before any CLI agent exists
├── U  UI (first slice)
│   ├── 1U.1  [S]  core/ui_agent_panel.py: Detected Local Agents LIST card (icon, display_name,
│   │         kind, status pill, sources tooltip, version; rows: Enable/Connect/Disable/Forget);
│   │         precedent = "Tools & MCP Access" grouping card
│   ├── 1U.2  [S]  _AgentScanBridge worker (Qt thread+signal clone of _ModelDiscoveryBridge
│   │         ui_connectivity.py:244; finished(list, error) signal); "Scan again" button
│   └── 1U.3  [XS] wiring edit in core/ui_connectivity.py (import + instantiate only);
│         "Connect all (safe classes)" action; master kill-switch surfaced
└── V1 Verification
    ├── 1V.1  [S]  tests/test_agent_bridge_transports.py: http_openai against fake HTTP server;
    │         mcp_client against fake SDK session; timeout honored; teardown clean
    ├── 1V.2  [M]  tests/test_agent_publishing.py: agent tools appear in build_session_payload
    │         WITHIN BUDGET; group under kind=="agent"; removed on disconnect (no duplicates
    │         after reconnect); output cap enforced; provenance line emitted
    ├── 1V.3  [S]  accept_agent_bridge.py harness #1: scan → list populated; connect Ollama
    │         (or fake) → models shown; connect MCP → tools reachable via search_tools→call_tool;
    │         Live payload ≤ budget; denied confirm performs nothing
    └── 1V.4  [S]  tests/test_agent_artifacts.py (ADR-015): full output persisted even when
          truncated; ref round-trip via read_agent_artifact; sha256 manifest; retention=0
          never deletes; store never lands in the repo tree; no-spawn invariant holds
```

**Phase 1 acceptance:** discovered Ollama answers a routed prompt; a discovered MCP
server's tools answer through `search_tools`→`call_tool`; the Live handshake still
succeeds (payload ≤ budget).

---

### 2.3 PHASE 2 — CLI agents + Tier-2 orchestrator connectors

```
PHASE 2
├── X  Transports
│   └── 2X.1  [M]  transports/stdio_jsonrpc.py: persistent Popen (spawn_kwargs() hygiene —
│         CREATE_NO_WINDOW, utf-8, errors=replace, bufsize=0), reader thread with id→Event
│         correlation, per-call timeout, idempotent close(), process-TREE kill
│         (CREATE_NEW_PROCESS_GROUP / taskkill /T equivalent); template: playwright_mcp_client.py:79
├── A  CLI agent adapters (one-shot spawn-per-call default)
│   ├── 2A.1  [M]  adapters/claude_code.py: `claude --print "<p>" --output-format json`;
│   │         --version probe → record + flag table; parse .result/.structured_output;
│   │         TTY caveats (no stream-json persistent mode), CRLF/blank-line normalization
│   │         for <v2.1.208; AuthRequired when non-interactive auth missing;
│   │         EVERY call confirm-gated (shell-capable) + sandbox cwd
│   ├── 2A.2  [M]  adapters/codex_cli.py: `codex exec --json` JSONL parse
│   │         (thread.started…turn.completed/error); --output-schema optional; exit-code +
│   │         turn.failed → ok=False; resume/fork DEFERRED ([-] note)
│   └── 2A.3  [S]  adapters/gemini_cli.py: `gemini "<p>" --output-format json` → .response;
│         headless auth failure → status "needs_login" (degraded, NOT error)
├── O  Tier-2 connectors (high-leverage verbs ONLY — never mirror sub-agent fleets)
│   ├── 2O.1  [M]  adapters/orca.py: verbs assign_goal, list_worktrees, open_worktree,
│   │         list_automations, run_automation(mutating), read_status, list_artifacts, cancel;
│   │         ORCA_CLI_COMMAND / orca-dev precedence; --json probe → degraded on drift;
│   │         contract-pinned to fixtures from 0K.2
│   ├── 2O.2  [L]  adapters/multica.py: WRITES via `multica <cmd> --json` (issue create/assign/
│   │         update-status/comment, autopilot run), READS via daemon HTTP (port file
│   │         .multica/daemon.port, /health); steering = issue comment; "needs start" status
│   │         when daemon down (start only via explicit Connect action)
│   └── 2O.3  [L]  adapters/paperclip.py: HTTP /api/* + Authorization: Bearer $PAPERCLIP_API_KEY
│         (short-lived run JWT — re-fetch on 401/403, NEVER persist); read verbs first
│         (list_tasks/get_task/read_org_chart/read_budgets/list_approvals/read_audit);
│         mutating (create_task/assign_task/update_status/add_comment/run_heartbeat) all
│         confirm-gated; run_heartbeat NEVER auto-pulses; env contract vars per design §4.11.3
├── G  Governance (orchestrator slice)
│   ├── 2G.1  [S]  self-target refusal (Brahma identity check) + peer tagging → ForbiddenTarget;
│   │         publish-time assertion; test: adapter pointed at Brahma refuses to publish
│   └── 2G.2  [M]  fan-out budget: per-adapter Budget(max_concurrent, max_spend_usd) — values
│         READ FROM SETTINGS (local_agent_budget_defaults overlaid by per-agent overrides,
│         ADR-016), not hardcoded; assign*/run_* default dry_run=True; per-verb confirm
│         table for EVERY mutating orchestrator verb; provenance agent:orchestrator:<id>;
│         dedupe on (adapter_id,title) for repeated create_task/assign_task
└── V2 Verification
    ├── 2V.1  [M]  golden-fixture parse tests per CLI/connector (0K.2 captures):
    │         mismatch → SchemaDrift → status "degraded", NEVER mis-parsed data;
    │         fake orchestrator echo-binary for the spawn path (real subprocess, tmp script)
    └── 2V.2  [L]  tests/test_agent_adapter_contract.py: ONE shared contract suite every adapter
          (real + fake) runs: detect shape & purity (no spawn/write), probe never raises,
          connect/verbs/call/close shape, per-call timeout honored, close() leaves no orphan
          child, mutating verbs are gated, env carries names only, output size-capped
```

**Phase 2 acceptance:** "ask Claude Code to summarise this file" works end-to-end through
the model; a denied confirmation performs nothing; a missing-auth agent reports
"needs login"; a golden-fixture mismatch degrades the adapter instead of mis-parsing.

---

### 2.4 PHASE 3 — Supervision, lifecycle, full UI

```
PHASE 3
├── L  Lifecycle / health
│   ├── 3L.1  [M]  health.py three-stage probe: liveness (path/TCP) → protocol (MCP init /
│   │         /v1/models / --version) → capability (≥1 tool or model); failed stage recorded
│   │         in status; PROBE_TIMEOUT_SECONDS bounds every stage
│   ├── 3L.2  [M]  supervision loop for PERSISTENT transports only (mirror mcp_manager.py:548
│   │         _supervise + P1-9 circuit breaker: N consecutive failures → parked "failed");
│   │         one-shot spawns are NEVER auto-restarted (non-idempotent verbs — double-create risk)
│   ├── 3L.3  [S]  status() snapshot: plain dicts, UI-safe, shaped like MCPServerManager.status()
│   │         (core/mcp_manager.py:897) so UI/tool_bridge consume both identically
│   └── 3L.4  [S]  shutdown: PID registry for every spawned child; tree-kill; wired NEXT TO
│         MCPServerManager.stop_all() (main.py:4158) — the one guarded main.py edit;
│         plugin-shim path also calls bridge.shutdown() on app exit
├── U  UI (full)
│   ├── 3U.1  [S]  enable/disable/forget wired to registry API; user_edited protection surfaced
│   │         ("discovery will not overwrite your settings" affordance); status pill refresh
│   │         from AgentBridge.status() poll (2 s tick pattern from boot resync)
│   ├── 3U.2  [XS] orchestrator rows: Tier badge, verb summary tooltip, "Remote" badge ready
│   │         for Tier 3 (fields exist from 0R.1); needs-login affordance (per design §4.8)
│   └── 3U.3  [S]  Local Agents settings block: scan-layer opt-in toggles (ADR-014 — the
│         master switch does NOT imply scanning), budget editor (defaults + per-agent
│         max_concurrent/max_spend_usd overrides, ADR-016), artifact store affordance
│         (per-agent output_ref list + "read via read_agent_artifact", ADR-015)
└── V3 Verification
    └── 3V.1  [M]  lifecycle tests: kill a persistent agent → status degraded + UI model updated;
          restart recovers tool availability; shutdown leaves ZERO orphan processes
          (psutil assertion in test); supervision never restarts a one-shot (assertion)
```

**Phase 3 acceptance:** killing a persistent agent flips status to `degraded` and the UI
reflects it; restart recovers tools; shutdown leaves zero orphans.

---

### 2.5 PHASE 4 — Observability & polish

```
PHASE 4
├── 4P.1  [S]  per-agent manuals: ingest --help / MCP instructions → SkillManual(agent:<id>);
│         render ONLY when an agent tool is advertised (core/tool_index.py:1363);
│         sanitize + cap (untrusted-output rule applies to manuals too)
├── 4A.2  [M]  vendor pack #2 (ADR-017): aider / goose / qwen-code — catalog entries for
│         0D.2 detection + generic one-shot adapters via 1A.3-style prompt→argv template;
│         bespoke module ONLY where parsing demands it; fixtures via Appendix B recipe
├── 4L.1  [XS] logging: one logger "AgentBridge" (convention: core/mcp_manager.py:43);
│         startup log line via _startup_log (main.py:126 vicinity); provenance logs from 1P.2
│         surfaced in a daily-rotating adapter audit file under %LOCALAPPDATA%\BrahmaAI\logs\
├── 4X.1  [S]  token-budget tuning: per-agent advertise_budget override; deprioritize
│         rarely-used agents (usage counter in registry); assert per-source cap still holds
├── 4U.1  [S]  telemetry card polish: agent group in tool_bridge.status() with live state;
│         scan timing + layer-evidence breakdown in UI tooltip
└── 4V.1  [S]  docs: flip design doc status section (phases landed), VERSION log entry,
        CODE-REVIEW-TODO rows, this TODO's status board; acceptance harness #2 (full matrix)
```

---

### 2.6 PHASE 5 — Deferred stubs (do not schedule)

```
PHASE 5  [-] explicitly deferred per design §8 Q3 + §4.12
├── 5A.1 [-] Tier 3 remote runtimes: transports/http_agent.py (A2A agent-card),
│         remote_runtimes settings list, HTTPS/verify_ssl enforcement, "Remote" badge live
├── 5P.1 [-] peer agents: brahma_connect ServiceBrowser + protocol extension (recursion
│         guard from 2G.1 MUST be proven first — hard dependency)
└── 5D.1 [-] mDNS/Zeroconf discovery expansion
```

---

## 3. Dependency graph, waves, critical path

### 3.1 Wave plan (what may run in parallel)

```
WAVE 0 (serial)     0K.1 ✓ → 0K.2 → 0K.3 ✓ → 0F.1..0F.5   ══► INTERFACE FREEZE (contracts §1.2-§1.8 locked)
                     │
WAVE 1 (5 parallel) ├── 0R.1→0R.2→0R.3→0R.4          (registry thread)
                    ├── 0D.1→0D.2..0D.8→0D.10         (discovery thread)
                    ├── 1X.2 spike against ADR-006    (transport thread — may start pre-freeze on ADR only)
                    ├── 0V.1 + 0V.2                   (test scaffolding thread)
                    └── 1U.1 (panel against FROZEN AgentRecord dict shape + fake data)   (UI thread)
                     │
WAVE 2 (5 parallel) ├── 1B.1→1B.2                     (bridge thread)
                    ├── 1X.1 + 1X.2 completion        (transports)
                    ├── 1A.1..1A.4                    (safe adapters — needs 1X)
                    ├── 1P.1→1P.2→1P.4→1P.5→1P.3 (+1G.1 gate)   (publishing+artifacts+governance)
                    └── 1U.2→1U.3 + 1V.1..1V.4        (UI wiring + phase-1 verification)
                     │
WAVE 3 (4 parallel) ├── 2X.1                          (stdio transport)
                    ├── 2A.1..2A.3                    (CLI adapters — needs 2X.1)
                    ├── 2O.1..2O.3                    (orchestrators — needs 2G.2 policy merged FIRST
                    │                                   or code against governance API + enable in test)
                    └── 2G.1→2G.2 + 2V.1..2V.2        (governance slice + contract suite)
                     │
WAVE 4 (3 parallel) ├── 3L.1..3L.4                    (lifecycle)
                    ├── 3U.1→3U.2→3U.3                (full UI incl. settings/budget/artifacts)
                    └── 3V.1                          (lifecycle verification — needs 3L)
                     │
WAVE 5 (serial)     4P.1 → 4A.2 → 4L.1 → 4X.1 → 4U.1 → 4V.1 → FULL GATE → docs flip
```

### 3.2 Critical path

```
0K.1 ✓ → 0K.3 ✓ → 0F.2 (ABC freeze) → 0R.1 → 0R.3 → 1B.1 → 1P.1 → 1P.4 → 1A.3 → 2V.2 (contract suite green)
                                                                            ↘ 1V.3 (accept #1)
```

Everything not on this path is schedulable immediately after its own dependencies. The
two long poles: **0F.2** (contracts — everything imports it) and **2V.2** (the shared
contract suite — the gate every adapter must pass).

### 3.3 Cross-track dependency edges (exhaustive)

| Task | Depends on | Blocks |
|---|---|---|
| 0K.1 | — | DONE — recorded as ADR-001…017 in docs/AGENT-BRIDGE-ADR.md |
| 0K.2 | — | 2V.1, 2O.* (fixture pinning) |
| 0K.3 | — | DONE — recorded as ADR-006; 1X.2 spike executes it |
| 0F.* | 0K.1 | everything (imports) |
| 0R.* | 0F.2 | 0D.1 (records), 1B, 1P, 1U, 3U |
| 0D.1 | 0F.2, 0R.1 | 0D.2–0D.8, 0D.10 |
| 0D.5 | 0D.1 | 1A.4 |
| 0D.8 | 0D.1 | 2O.* (detection feeds connectors) |
| 0V.1 | 0F.* | all test tasks |
| 1B.1 | 0R.3, 0F.4 | 1P.1, 1A.*, 2A.*, 2O.* |
| 1X.1 | 0F.4 | 1A.1, 1A.2 |
| 1X.2 | 0K.3, 0F.4 | 1A.3, 1A.4 |
| 1G.1 | 0K.1, 0F.3 | 1P.1 (wired at publish boundary) |
| 1P.1 | 1B.1, 1G.1, 0R.3 | 1P.4, 1V.2, 1V.3, 2A.*, 2O.* |
| 1P.4 | 1P.1 | 1P.5, 1V.4, 2A.*, 2O.* (every call persists full output) |
| 1P.5 | 1P.4 | 3U.3 (artifact affordance), 1V.4 |
| 1U.* | 0R.3, 0D.10 | 3U.* |
| 2X.1 | 0F.4 | 2A.* |
| 2G.2 | 1G.1, 0F.5 (settings-backed budget) | 2O.* (mutating verbs) |
| 2V.2 | 0V.1, 1P.1 | phase-2 acceptance, phase-3 start (contract suite is the phase gate) |
| 3L.4 | 1B.1 | shutdown DoD |
| 3U.3 | 0F.5, 1P.5, 2G.2 | 4U.1 |
| 4A.2 | 2A.1..2A.3 (adapter patterns proven), 0D.2 | 4V.1 |
| 3V.1 | 3L.*, 2V.2 | phase-3 acceptance |

---

## 4. Multi-agent assignment matrix

### 4.1 Role bundles (suggested agent staffing)

| Agent role | Takes | Must not take |
|---|---|---|
| **Architect agent** | 0K.*, 0F.* (owns the frozen contracts; on call for contract-change ADRs) | implementation tracks |
| **Discovery agent** | 0D.* + its tests | registry internals, transports |
| **Registry agent** | 0R.* | detectors, publishing |
| **Transport agent** | 1X.*, 2X.*, 5A.1 transport | adapters, publishing |
| **Adapter agent (Tier 1)** | 1A.*, 2A.* | transports (calls their API only), governance internals |
| **Orchestrator agent** | 2O.* | anything else (highest domain-knowledge cost; one agent per connector is fine) |
| **Platform agent** | 1B.*, P track (incl. 1P.4 artifacts + 1P.5 retrieval tool), L track, main.py edits | UI, adapters |
| **Governance agent** | 1G.*, 2G.* | publishing internals (ships the API publish wires in) |
| **UI agent** | 1U.*, 3U.*, 4U.*, `core/ui_agent_panel.py` (incl. 3U.3 scan-toggles/budget/artifact affordances) | bridge/registry internals (consumes dicts only) |
| **QA agent** | 0V.*, 1V.*, 2V.*, 3V.*, 4V.1, accept harnesses, gate runs | feature code (files bugs against owners) |

### 4.2 Handoff artifacts (the only cross-track interfaces)

1. **Frozen §1.2–§1.8 contracts** — code-level interface.
2. **`AgentRecord` dict shape** (§1.3) — UI codes against this dict, never against
   `registry.py` classes directly (`registry.status()` returns plain dicts).
3. **`bridge.call(agent_id, verb, args) -> dict`** — the single entry every executor/UI
   action uses; adapters are invisible to callers.
4. **Golden fixtures** (`0K.2`) — the parse contract for orchestrator/CLI adapters.
5. **This WBS's status board** (§7) — the coordination surface: claim before working.

### 4.3 Merge order (avoid mid-air collisions)

`0F.*` (freeze) → `0R.*` → `0D.*` → `1B.1` → `1G.1` → `1P.1` → adapters/transport merges →
`2V.2` suite → `3L.*` → UI → polish. Each merge must leave `pytest` + `ruff` + precommit
quick green (§6). Contract changes post-freeze require the ADR loop (§0.1 rule 4).

---

## 5. Risk register → owning tasks

| # | Risk (from design §5 + review findings) | Mitigation lives in |
|---|---|---|
| R1 | Gemini 128-cap / payload bloat | 1P.1 (unadvertised-by-default), 1V.2 (budget assertion), 4X.1 |
| R2 | GUI-thin PATH misses installs | 0D.2 (prefix merge), 0D.10 test |
| R3 | Hung HTTP stalls tool calls | 0F.4 (timeouts), 1X.1, 2V.2 (timeout assertion) |
| R4 | Console window flash / focus steal | 0F.4 spawn_kwargs(), 2X.1, 2V.2 assertion |
| R5 | Subprocess encoding (OEM codepage) | 0F.4 spawn_kwargs(), contract suite |
| R6 | Headless/TTY crashes, version drift | 2A.* (--version pinning, one-shot default, needs_login) |
| R7 | No PTY — TUI agents out of scope | scope note in every CLI adapter docstring |
| R8 | Orphan processes | 2X.1 tree-kill, 3L.4 shutdown, 3V.1 psutil assertion |
| R9 | Tool-name collisions | 1P.1 duplicate probe + `_sanitize_name` prefix |
| R10 | Duplicate MCP servers (user + vendor) | 0D.5 dedupe hash, 1A.4 |
| R11 | Orchestrator JSON drift (TOP RISK) | 0K.2 fixtures, 2V.1 pin tests, SchemaDrift→degraded |
| R12 | Tier-2/3 latency, JWT expiry | 2O.3 re-auth on 401/403, ORCHESTRATION_API_TIMEOUT |
| R13 | Hierarchy opacity / tool explosion | §1.6 step 7 policy + high-leverage-verb-only rule in 2O.* |
| R14 | Token cost of advertised tools | per-source cap (existing), 4X.1 budgets |
| R15 | Monolith regrowth | file-ownership matrix (§4), main.py ≤ 20-line budget (§6), plugin-first |
| R16 | Uninstall-sweep slowness | 0D.3 TTL cache + bounds |
| R17 | Delegation loops (self-target) | 2G.1 + 5P.1 hard dependency |
| R18 | Unbounded fan-out / spend | 2G.2 Budget + dry_run default + per-verb gate |
| R19 | Secrets in registry JSON | §1.7 secrets row, 0R.2 test, precommit credential blocker |
| R20 | Agent output prompt injection | §1.7 untrusted-output boundary, 1V.2 cap test |
| R21 | Artifact store disk growth (durable outputs — ADR-015) | 1P.4 retention_days setting (default 0 = keep forever is an explicit owner choice); 4U.1 surfaces total bytes per agent so the user can prune |

---

## 6. Definition of Done (global gate)

A phase is done when **all** its tasks are `[x]` with evidence **and**:

```powershell
py -3.11 -m py_compile <every touched .py>                       # per-task evidence
py -3.11 -m pytest -q                                            # full suite green (baseline 167 + new)
ruff check .                                                     # curated ruleset: F401 F841 F811 F541 F821
py -3.11 scripts/precommit_check.py --quick                      # SAFE TO COMMIT (hook runs this);
PRECOMMIT_FULL=1 py -3.11 scripts/precommit_check.py             # full: + 7→8 acceptance harnesses
```

**Structural guards (assert in 4V.1 / CI):**

- `main.py` stays ≤ 3903 lines (3883 baseline + 20) and `ui.py` stays ≤ 692 — the bridge
  is a package, not a monolith regrowth.
- `core/agent_bridge/` has **no import of** `ui_*` modules and **no Qt imports** — UI
  depends on the bridge, never the reverse (testable via AST scan).
- Zero secrets in any committable file (precommit enforces; fixtures must be scrubbed).
- `local_agent_bridge_enabled=false` **or any `local_agent_scan.*` layer false** makes the
  corresponding facade entry a safe no-op; shipped defaults are ALL false (ADR-014) —
  kill-switch test asserts both the master switch and per-layer no-ops.
- Every `AgentCallResult` from a successful call carries a resolvable `artifact_ref`;
  the full (untruncated) output is readable via `read_agent_artifact` (ADR-015); the
  artifact store never writes inside the repo tree.

**Phase acceptance harnesses** (`accept_agent_bridge.py`, house style `accept_util.py`):
harness #1 at Phase 1 (§2.2), harness #2 at Phase 4 (full matrix incl. orchestrators on
dry_run).

---

## 7. Status board (master checklist — update in lockstep with the cards)

| ID | Task | Track | Size | Status |
|---|---|---|---|---|
| 0K.1 | ADR bundle #1 (open questions) | K | XS | [x] ✓ 2026-10-17 — ADR-001…017 |
| 0K.2 | Golden fixture capture (this machine) | K | S | [ ] |
| 0K.3 | ADR: mcp_client extraction strategy | K | S | [x] ✓ 2026-10-17 — ADR-006 |
| 0F.1 | Package skeleton + facade stubs | F | XS | [ ] |
| 0F.2 | adapter.py ABC + value types | F | S | [ ] |
| 0F.3 | errors.py | F | XS | [ ] |
| 0F.4 | constants.py + spawn_kwargs | F | XS | [ ] |
| 0F.5 | config schema + defaults | F | XS | [ ] |
| 0R.1 | AgentRecord + migration | R | S | [ ] |
| 0R.2 | Atomic persistence + merge policy | R | S | [ ] |
| 0R.3 | Registry API | R | S | [ ] |
| 0R.4 | Registry tests | R | S | [ ] |
| 0D.1 | Detector runner + DetectionContext | D | M | [ ] |
| 0D.2 | Layer A (PATH + prefixes) | D | S | [ ] |
| 0D.3 | Layer B (registry/uninstall) | D | S | [ ] |
| 0D.4 | Layer C (known dirs) | D | XS | [ ] |
| 0D.5 | Layer D (vendor configs) | D | M | [ ] |
| 0D.6 | Layer E (ports + processes) | D | S | [ ] |
| 0D.7 | Layer F (env presence) | D | XS | [ ] |
| 0D.8 | Layer G (orchestrators) | D | S | [ ] |
| 0D.10 | Discovery tests | D | M | [ ] |
| 0V.1 | Test scaffolding fixtures | V | S | [ ] |
| 0V.2 | Fixture layout + capture README | V | XS | [ ] |
| 1B.1 | Bridge loop/thread + sync facade | B | M | [ ] |
| 1B.2 | Plugin shim wiring | B | S | [ ] |
| 1X.1 | http_openai transport | X | S | [ ] |
| 1X.2 | mcp_client extraction | X | M | [ ] |
| 1A.1 | ollama adapter | A | S | [ ] |
| 1A.2 | lm_studio adapter | A | S | [ ] |
| 1A.3 | mcp_server adapter | A | M | [ ] |
| 1A.4 | generic_mcp adapter | A | S | [ ] |
| 1P.1 | publish.py | P | M | [ ] |
| 1P.2 | dispatch provenance | P | XS | [ ] |
| 1P.3 | tool_bridge status grouping | P | XS | [ ] |
| 1P.4 | artifacts.py durable output store | P | M | [ ] |
| 1P.5 | read_agent_artifact pinned tool | P | XS | [ ] |
| 1G.1 | governance core (gate/sandbox/boundary) | G | M | [ ] |
| 1U.1 | ui_agent_panel list card | U | S | [ ] |
| 1U.2 | _AgentScanBridge worker | U | S | [ ] |
| 1U.3 | ui_connectivity wiring | U | XS | [ ] |
| 1V.1 | transport tests | V | S | [ ] |
| 1V.2 | publishing tests | V | M | [ ] |
| 1V.3 | acceptance harness #1 | V | S | [ ] |
| 1V.4 | artifact store tests (ADR-015) | V | S | [ ] |
| 2X.1 | stdio_jsonrpc transport | X | M | [ ] |
| 2A.1 | claude_code adapter | A | M | [ ] |
| 2A.2 | codex_cli adapter | A | M | [ ] |
| 2A.3 | gemini_cli adapter | A | S | [ ] |
| 2O.1 | orca connector | O | M | [ ] |
| 2O.2 | multica connector | O | L | [ ] |
| 2O.3 | paperclip connector | O | L | [ ] |
| 2G.1 | self-target refusal | G | S | [ ] |
| 2G.2 | fan-out budget + per-verb gating | G | M | [ ] |
| 2V.1 | golden-fixture parse tests | V | M | [ ] |
| 2V.2 | shared adapter contract suite | V | L | [ ] |
| 3L.1 | three-stage health probe | L | M | [ ] |
| 3L.2 | supervision (persistent only) | L | M | [ ] |
| 3L.3 | status() snapshot | L | S | [ ] |
| 3L.4 | shutdown + orphan kill + main.py anchor | L | S | [ ] |
| 3U.1 | enable/disable/forget wiring | U | S | [ ] |
| 3U.2 | badges + needs-login affordance | U | XS | [ ] |
| 3U.3 | scan-toggles/budget/artifact settings block | U | S | [ ] |
| 3V.1 | lifecycle tests | V | M | [ ] |
| 4P.1 | per-agent manuals | P | S | [ ] |
| 4L.1 | logging + audit surface | L | XS | [ ] |
| 4X.1 | token-budget tuning | X | S | [ ] |
| 4A.2 | vendor pack #2 (aider/goose/qwen-code) | A | M | [ ] |
| 4U.1 | telemetry polish | U | S | [ ] |
| 4V.1 | docs flip + harness #2 | V | S | [ ] |
| 5A.1 | Tier 3 remote (deferred) | — | — | [-] |
| 5P.1 | peer agents (deferred) | — | — | [-] |
| 5D.1 | mDNS (deferred) | — | — | [-] |

---

## Appendix A — CLI invocation cheat-sheet (per adapter, from design Appendix B)

| Agent | Detect | One-shot invocation | Machine output |
|---|---|---|---|
| Claude Code | `claude` on PATH / `~/.claude` / `~\.local\bin` | `claude --print "<p>"` | `--output-format json` → `.result` / `.structured_output` |
| Codex CLI | `codex` / `~/.codex/config.toml` | `codex exec --json "<p>"` (`exec -` = stdin) | JSONL events; `-o <file>`; `--output-schema` |
| Gemini CLI | `gemini` / `~/.gemini` | `gemini "<p>" --output-format json` | `.response`; `stream-json` for JSONL |
| Ollama | `:11434` / `ollama` / `%LOCALAPPDATA%\Programs\Ollama` | HTTP `/v1/chat/completions` | JSON / SSE |
| LM Studio | `:1234` / `%LOCALAPPDATA%\Programs\LM Studio` | HTTP `/v1/chat/completions` | JSON / SSE |
| Orca (ADE) | `orca`/`orca.exe` or `%LOCALAPPDATA%\Programs\orca\resources\bin\orca.exe`; `%USERPROFILE%\orca\workspaces` | `orca --json <subcmd>` | JSON; respect `ORCA_CLI_COMMAND`, orca-dev/orca-ide precedence |
| Multica | `%USERPROFILE%\.multica\bin\multica.exe` or PATH; `.multica\config.json`, `daemon.pid/.port` | `multica <cmd> --json` (writes) / daemon HTTP (reads) | JSON; cmds: auth, daemon, workspace, issue*, autopilot, config |
| Paperclip | `%USERPROFILE%\.paperclip` and/or `%USERPROFILE%\paperclip`; server `:3100` | `paperclipai` (npx) or repo `cli/`; HTTP `/api/*` + `Bearer $PAPERCLIP_API_KEY` | JSON; env: `PAPERCLIP_AGENT_ID/COMPANY_ID/API_URL/RUN_ID/TASK_ID/WAKE_REASON/...` |
| OpenCode | `:4096` / `opencode` | HTTP `/v1/chat/completions` | JSON |
| Any MCP server | vendor configs / `npx` | MCP `initialize` + `tools/call` | JSON-RPC |

## Appendix B — golden fixture conventions (`0V.2` owns layout)

```
tests/fixtures/agent_bridge/
├── README.md                      # capture recipe + scrub checklist (no tokens, no usernames)
├── claude_print.json              # real `claude --print "ping" --output-format json` output
├── codex_exec.jsonl               # real `codex exec --json "ping"` event stream
├── gemini_headless.json
├── versions.json                  # {"claude": "2.1.x", "codex": "...", ...} captured via --version
├── orca/ {version.json, worktrees.json, automations.json, status.json}
├── multica/ {version.json, daemon_status.json, issue_get.json, issue_list.json}
├── paperclip/ {health.json, task_list.json, task_get.json, org_chart.json}
└── vendor_configs/ {claude_desktop.valid.json, claude_desktop.corrupt.json,
                     codex_config.valid.toml, gemini_settings.valid.json, *.crlf.json}
```

**Scrub rule:** replace usernames/machine names with `TESTUSER` placeholders; fixtures
are committable and precommit blocks real credentials.

## Appendix C — per-task evidence cheat-sheet

| Task family | Evidence command |
|---|---|
| any new module | `py -3.11 -m py_compile core/agent_bridge/<file>.py` |
| unit task | `py -3.11 -m pytest tests/test_agent_<topic>.py -q` |
| lint | `ruff check .` |
| publishing budget | `py -3.11 -m pytest tests/test_agent_publishing.py -q -k budget` |
| no-orphan | `py -3.11 -m pytest tests/test_agent_adapter_contract.py -q -k orphan` |
| line-budget guard | `(Get-Content main.py).Count` / `(Get-Content ui.py).Count` |
| phase gate | `PRECOMMIT_FULL=1 py -3.11 scripts/precommit_check.py` |

---

*WBS revision 2 (2026-10-17), authored against design doc rev 3 + amendments 1-2 and
verified seams at commit `664ae4b`. Rev 2 folds in ADR-001…ADR-017 (fully opt-in scan
defaults, user-configurable budgets, durable referencable output, vendor-agnostic CLI
catalog; 0K.1/0K.3 closed). Update §7 status board and card status lines in lockstep.*
