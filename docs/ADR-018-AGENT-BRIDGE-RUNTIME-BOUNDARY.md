# ADR-018 — Agent Bridge Runtime Boundary: A Tool Source, Never a Runtime

**Status:** Accepted (ratified 2026-11-02) · **Gates:** Q1 item 1, O-4 re-base, and Q2 Bridge v2 · **Supersedes:** ADR-006 (custom MCP extraction), ADR-011 (ungated confirm bypass), and conflicts C-1–C-7 in `docs/ARCHITECTURE-SPEC.md` §8.3  
**Related:** `docs/ARCHITECTURE-SPEC.md` §8.1–§8.4, §11.2 (O-4), §12 Q1.1, §12 Q2; `docs/AGENT-BRIDGE-ADR.md` (ADR-001–ADR-017); `agent/runtime.py` (`AgentRuntime.execute`); `agent/spec.py` (`ToolSpec`, `ToolContext`); `agent/subagent.py` (`run_subagent`, `run_subagent_batch`); `agent/mcp.py` (`WorkspaceMCP`)

---

## 1. Context

The initial architectural design for the Agent & Orchestrator Bridge (`docs/agent-orchestrator-bridge.md` and ADR-001 through ADR-017) was accepted on 2026-10-17. It envisioned Brahma as a top-level orchestrator discovering local model servers, MCP servers, and CLI coding agents across the operating system.

However, that design predated the implementation of the unified agentic harness (`agent/`). The legacy bridge specification assumed:
1. Spawning and executing bridged tools through `DynamicToolRegistry` and direct calls to `core/confirm.py`, completely bypassing `AgentRuntime.execute` (Conflict C-1).
2. Extracting and embedding a third bespoke MCP client transport beside `core/mcp_manager.py` and `agent/mcp.py` (Conflict C-2).
3. Spawning CLI agents outside `run_subagent`, creating a second autonomous loop and delegation hierarchy (Conflict C-3).
4. Unbounded tool registration of $N \text{ agents} \times M \text{ verbs}$, immediately causing provider context blowups and tool selection confusion (Conflict C-5).
5. Persisting cleartext endpoint tokens and secrets directly inside registry files (Conflict C-6).

In the current architecture, `AgentRuntime.execute` is the single, non-bypassable permission, audit, and execution boundary for all desktop assistant actions, harness tools, subagents, and MCP servers across voice, GUI, and CLI modes. Permitting external agents or bridged tools to bypass `AgentRuntime` would open a severe privilege escalation vector, violating core invariant `I-01` and Security Phase 0 principles.

---

## 2. Decision: Seven Re-basing Axioms (O-4)

To resolve conflicts C-1 through C-7 and align the bridge with the ratified architecture, we adopt the following binding decisions:

### Axiom 1: The Bridge is a Tool Source, Never a Runtime (O-4, C-1)
The bridge discovers external agents, probes their capabilities, validates golden fixtures, and publishes tool specifications. It **never** executes tools directly, never runs an independent execution loop, and never bypasses `AgentRuntime.execute`.

### Axiom 2: Every Bridged Verb is a Canonical `ToolSpec` (C-1)
Every capability exposed by an external agent registers as an explicit, lowercase `ToolSpec`. Its `tool_class` maps to the actual operational risk:
- `"exec"`: for local CLI agents, subprocesses, and commands.
- `"mcp"`: for bridged MCP server tools.
- `"network"`: for HTTP/REST model server endpoints.
- `"read"`: for read-only inspection/status probes.

Calls to these tools flow strictly through `AgentRuntime.execute(spec, args, context)`, inheriting policy evaluation, workspace containment, session-level approvals, and audit logging.

### Axiom 3: Superseding ADR-006 — No Third MCP Client Transport (C-2)
ADR-006 proposed extracting an MCP client into a separate transport package. This is superseded:
- Harness-discovered and workspace-local agents use the pure standard-library client `agent/mcp.py` (`WorkspaceMCP`).
- Voice-only GUI agents use `core/mcp_manager.py`.
- No third MCP client is permitted. If unification is required later, it must occur under those two layers without breaking harness purity.

### Axiom 4: Superseding ADR-011 — Policy Decides, Not Ad-Hoc Confirm (C-1)
ADR-011 proposed routing mutating agent actions through `core/confirm.py` popups. This is superseded:
- The single authority for execution approval is `agent/permissions.py` via `AgentRuntime.execute`.
- `core/confirm.py` serves exclusively as a UI display sink when `AgentRuntime` requests user confirmation. It does not contain independent policy logic.

### Axiom 5: CLI Agents Execute via `run_subagent` / `run_command` (C-3)
External CLI agents (e.g. Claude Code, Codex, Aider) do not construct separate background task executors or thread workers. An external CLI run is either:
1. A single bounded execution tool call executing via `agent.process.run_captured` with process tree isolation and sandbox limits; or
2. A decomposed child run managed by `run_subagent` or `run_subagent_batch`, subject to strict turn budgets (clamped to 12), workspace confinement, and parent cancel events.

### Axiom 6: Bounded Surface and Deferred Tool Retrieval (C-5)
To prevent provider context exhaustion:
- An adapter registers at most **one** pinned tool by default (e.g. `agent_<name>_run_task`).
- Granular subordinate verbs are unadvertised by default and discovered dynamically at runtime using `search_tools` and `call_tool`.
- Pinned additions adhere to ADR-021 session-scoped extra budgets with oldest-first eviction.

### Axiom 7: Secret References, Never Stored Secrets (C-6)
The bridge registry (`core/agent_bridge/registry.py`) stores `AgentRecord` metadata. It must never store plaintext secrets, API keys, or long-lived authentication tokens. Credentials must reference:
- Environment variable names; or
- Keys managed by `core.secret_store` (Windows DPAPI user scope).

---

## 3. Architecture Overview

```
                      ┌──────────────────────────────────────┐
                      │ UI / Voice / CLI / Model Prompter    │
                      └──────────────────┬───────────────────┘
                                         │
                                         ▼
                      ┌──────────────────────────────────────┐
                      │         AgentRuntime.execute         │
                      │  (Single Permission & Policy Gate)   │
                      └──────┬────────────────────────┬──────┘
                             │                        │
         ┌───────────────────┴───────┐   ┌────────────┴──────────────────────┐
         ▼                           ▼   ▼                                   ▼
┌──────────────────┐       ┌────────────────────────┐             ┌─────────────────────┐
│ Native Desktop   │       │ Workspace MCP          │             │ Agent Bridge        │
│ ToolSpecs        │       │ (agent/mcp.py)         │             │ (core/agent_bridge) │
│ (core/desktop_   │       └────────────────────────┘             │                     │
│  tools.py)       │                                              │ • Detectors         │
└──────────────────┘                                              │ • Registry          │
                                                                  │ • Adapters          │
                                                                  │ • Artifact Store    │
                                                                  └──────────┬──────────┘
                                                                             │
                                                                             ▼
                                                                  ┌─────────────────────┐
                                                                  │ Canonical ToolSpecs │
                                                                  │ tool_class="exec"   │
                                                                  │ tool_class="mcp"    │
                                                                  └─────────────────────┘
```

---

## 4. Consequences

### Positive
- **Architectural Integrity:** Eliminates the risk of dual-runtime divergence and un-gated execution bypasses.
- **Security Parity:** External agents inherit all Phase 0 security controls (sandbox doctor behavioral probe, workspace trust verification, Windows DPAPI user-scope secrets).
- **Maintainability:** Bridge implementation in Q2 (`core/agent_bridge/`) is purely additive; it registers `ToolSpec` instances without mutating `main.py` line ceilings or adding `elif` dispatch branches.

### Negative / Trade-offs
- Adapters cannot execute custom interactive prompt loops directly in Brahma's main process; they must communicate via structured tool arguments and results or conform to `run_subagent` protocols.
- Verbs cannot all be statically advertised simultaneously to the model, requiring dependency on ADR-021 dynamic tool search.
