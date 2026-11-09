# Brahma Swarm / Federation — Investigation & Findings

**Status:** Investigation record. No implementation. Every claim below was checked
against the tree at `main` @ `b7a7cc9` (working tree clean apart from this document).
**Date:** 2026-11-09
**Question:** if Brahma is installed on three machines, can they talk to each other
and form a swarm — and what would it take to make that true?

---

## 0. Verdict

**There is no swarm in the repository today, and no code path that lets one Brahma
call another Brahma as a peer.** What exists is:

1. A **device gateway** (`brahma_connect`) for companion devices — phones, tablets,
   the Android app — with pairing, a WebSocket protocol, and a deliberately tiny
   command allowlist. It is not a peer-agent surface.
2. A **client** for remote agents (`core/agent_bridge/adapters/remote.py`) that
   speaks an A2A-shaped HTTP contract (`/.well-known/agent.json`, `POST /tasks`,
   `GET /tasks/{id}`, `POST /tasks/{id}/cancel`) — but **no Brahma process serves
   those routes**. The client exists; the server half does not.
3. An **OpenAI-compatible HTTP API** (`brahma_api`) that already turns
   `POST /v1/chat/completions` into a full `AgentLoop` run under a permission
   profile. This is the only existing way one process can make another Brahma
   *do agentic work*, and it is the cheapest transport to build a swarm on.
4. A **tool-publication path** (`AgentBridge.publish_tools`) that turns an external
   agent's verbs into ordinary `ToolSpec`s behind `AgentRuntime.execute`, which is
   exactly how a peer must appear to the harness.

So: three machines can be wired together **today**, but only as a client/server pair
using `brahma_api` with hand-configured tokens and `allow_remote_bind=true`; that is
a remote-work API, not a swarm. A real swarm needs five things that do not exist:
peer discovery, a peer task server, cross-node identity, LAN transport security, and
a scheduler that reasons about node capabilities. This document specifies each.

**Recommendation in one line:** build the swarm as a thin **federation layer** on
top of the existing `AgentRuntime` + `AgentBridge` seams — API transport first,
A2A task server second, mDNS/discovery third — and never add a second execution
runtime. See §13 for the phased plan.

---

## 1. What "swarm" means here

The word hides four very different systems. They should be kept distinct because
they have different security and failure properties:

| Level | Behaviour | What it buys | Brahma today |
| --- | --- | --- | --- |
| **L0 — Shared brain** | One Brahma process, many UIs/voices | Convenience | Exists (`brahma_connect` device gateway, `brahma_api`) |
| **L1 — Remote work API** | Node A sends a prompt; node B runs an `AgentLoop` and returns text/artifacts | Offload, use B's GPU/workspace | Mostly exists (`brahma_api`), unsafe as configured for LAN |
| **L2 — Peer task federation** | Nodes advertise capabilities; a coordinator dispatches typed tasks with deadlines, cancellation, and artifacts; each node enforces its own policy | Real "swarm" for 3 machines | Missing (client half only) |
| **L3 — Distributed memory / state** | Shared knowledge, replicated sessions, consensus | Global context, resilience | Not attempted, and should not be attempted first |

The three-machine target in the request is **L2**, with **L3 explicitly out of scope
for the first version**. L3 is where distributed systems projects die: last-writer
wins on a SQLite transcript is not a design, and a "shared brain" that silently
merges two divergent tool histories is a security incident waiting to happen.

**Non-goal:** making three machines look like one machine to the model. The model
should see a coordinator that can call named peers, not a transparent single
address space. Transparency hides where data went and which policy applied.

---

## 2. Current state — inventory

### 2.1 `brahma_connect` — companion-device gateway

Files: `brahma_connect/gateway/{server,websocket,protocol,command_router,capability_manager,device_manager,pairing,discovery}.py`,
`core/gateway_auth.py`, `brahma_connect/service.py`.

What is real:

- FastAPI app with HTTP control routes and a `/ws` WebSocket endpoint
  (`brahma_connect/gateway/server.py:360-651`).
- JSON envelope contract: `type`, `request_id`, `timestamp`, `payload`
  (`brahma_connect/gateway/protocol.py`), message types `hello`, `pair_request`,
  `pair_approved`, `authenticate`, `device_online`, `device_offline`,
  `capabilities`, `execute`, `result`, `event`, `error`, `ping`, `pong`,
  `file_transfer`, `screen_capture`, `chat_message`.
- Persistent device registry (`device_manager.py`), expiring pairing offers
  (`pairing.py`), capability tracking (`capability_manager.py`), request/response
  routing to devices (`command_router.py`).
- SAS pairing with a 6-digit code, 120 s TTL, three-failure lockout, ±60 s
  timestamp skew, nonce replay cache (`core/gateway_auth.py`).
- HTTP control routes now require a paired device (`_require_device`,
  `server.py:363-388`); only `/health` is public.
- Advertise-only mDNS via Zeroconf, service type `_BRAHMA._tcp.local.`
  (`discovery.py`); **no `ServiceBrowser`**, so it cannot find anyone.
- Defaults are safe: `host="127.0.0.1"`, `port=8765`, `enabled=False`,
  `advertise=False` (`server.py:35-43`). Note: `docs/SECURITY-ARCHITECTURE-ROADMAP.md`
  WS-8 still describes `host="0.0.0.0", enabled=True` — that is stale; code wins.

Why it is **not** a swarm transport:

- The only executable verb set is
  `ALLOWED_GATEWAY_COMMANDS` (`core/gateway_auth.py`): `app.open`, `device.ping`,
  `system.status`, `device.battery`, `media.play/pause/next/prev`, `camera.snap`,
  `notification.send`, `status`. There is no "run a task", "read a file",
  "run a command", or "answer a prompt" verb.
- The security model is "a human paired their phone". Peers are the same trust
  class as a phone in this model, and that is the wrong default for a machine
  that can execute agent tools.
- `brahma_connect` is also wired into the GUI as a device feature
  (`main.py:2948-2987`, `core/device_flow.py`), so extending it with peer verbs
  means changing a user-facing product surface.

**Verdict:** reuse its pairing/identity *concepts*, not its command surface.

### 2.2 `core/gateway_auth.py` — what is reusable

- `GatewayAuthManager` already provides SAS pairing, constant-time secret compare,
  lockout, and nonce replay protection. A peer-pairing flow can call the same
  primitives or a sibling class rather than inventing a second pairing UX.
- Its allowlist is the correct shape for a peer: *explicit verbs only, deny by
  default*. The mistake would be to widen it in place; a peer should get its own
  allowlist, not add `agent.run` to the phone's list.

### 2.3 `brahma_api` — the existing "call another Brahma" lane

Files: `brahma_api/{server,config,models,auth,runner,translate}.py`,
`config/brahma_api.json`.

What is real:

- `POST /v1/chat/completions` resolves a `Principal` (bearer token → record),
  resolves a model/profile/workspace, then `AgentRunner` constructs the **only**
  `AgentRuntime` and `AgentLoop` for that request
  (`brahma_api/runner.py:1-6`, `runner.py:159-216`).
- Non-streaming and SSE streaming both supported.
- Profiles: `chat` (no tools), `reader`, `worker`, `operator`, each mapped to a
  `PermissionPolicy` (`brahma_api/models.py:162-216`). A token's profile caps what
  the remote caller may ever do; a `reader` key cannot become a writer by naming
  a different model.
- Token registry stores only `sha256(token)`; scopes are
  `chat | models | diagnostics | approve | admin` (`brahma_api/auth.py:31`).
- Limits: RPM, global/per-token concurrency, max turns, max body, wall clock,
  approval timeout (`config/brahma_api.json`).
- Runs are recorded (`/v1/brahma/runs/{id}`), sessions are listed
  (`/v1/brahma/sessions`), audit is on by default.
- Live config: `enabled: true`, `host: 127.0.0.1`, `port: 8010`,
  `allow_remote_bind: false`, `auth_required: true`,
  `preselect_profile: "reader"`, `max_turns: 12`.

The two hard constraints for peer use:

1. **LAN binding is opt-in.** `allow_remote_bind=false` and a non-loopback host
   are both required to listen on the LAN (`brahma_api/config.py`). Even then the
   lane is plaintext HTTP unless a reverse proxy terminates TLS.
2. **Remote callers can never approve.** `X-Brahma-Approval: gui` is rejected
   unless the caller is loopback
   (`brahma_api/server.py:332-344`). A peer therefore runs with
   `approval_channel="deny"`: every policy `ask` becomes a denial. A peer task can
   only do what the executing node's policy already `allow`s, or the run fails.

This is not a defect — it is the correct security posture — but it defines what a
swarm task can be: *autonomous within a pre-approved envelope*, not *interactive*.

### 2.4 `core/agent_bridge` — the federation client that has no server

Files: `core/agent_bridge/{bridge,models,registry,detectors,artifacts,adapter}.py`,
`adapters/{remote,orchestrator,local_model,cli_agent,mcp_server}.py`.

What is real:

- `AgentRecord.kind` already reserves `"remote_http" | "remote_agent" | "peer"`
  (`models.py:61`), and `AgentBridge._create_adapter` routes all three to
  `RemoteAgentAdapter` (`bridge.py:55-57`).
- `RemoteAgentAdapter` is a genuine A2A **client**:
  - probe: `GET /.well-known/agent.json`, `/agent-card`, `/health`
  - `run_task` → `POST /tasks` with `{prompt, context}`
  - `query_status` → `GET /tasks/{task_id}`
  - `cancel_task` → `POST /tasks/{task_id}/cancel`
  (`adapters/remote.py:127-269`)
- `AgentBridge.publish_tools()` registers each adapter verb as a canonical
  `ToolSpec` (`agent_<id>_run_task`, …) plus `read_agent_artifact`
  (`bridge.py:147-177`). Calls then flow through `AgentRuntime.execute` like any
  other tool — *this is the correct publication path for a peer*.
- Oversized outputs are saved to `agent-artifact://` and read back through a
  bounded `read_agent_artifact` tool (`core/agent_bridge/artifacts.py`).
- Remote records can already be configured without code:
  `BRAHMA_REMOTE_AGENTS` (JSON list) or `~/.brahma/remote_agents.json`
  (`detectors.py:215-251`).

The blockers:

- **No server.** Nothing in the tree defines `/tasks`, `/tasks/{id}`,
  `/tasks/{id}/cancel`, or `/.well-known/agent.json`. A Brahma peer is a client
  with nothing to call.
- **LAN is rejected by default.** `validate_remote_url` requires HTTPS and refuses
  private/loopback IPs unless `record.tier3["allow_insecure"]` is true
  (`adapters/remote.py:40-76`). Normal `192.168.x.x` three-machine use therefore
  fails at the first probe unless explicitly overridden — and the override then
  disables the only transport guarantee.
- **No recursion guard on peers.** `OrchestratorAdapter._check_self_target`
  refuses to target the local orchestrator, but `RemoteAgentAdapter` has no
  equivalent origin/hop check. With a peer on each end, A→B→A is constructible.
- **`peer` is not distinct from `remote_http`.** It shares the same adapter and
  the same single-bearer-token credential lookup (`remote_agent_<id>` from DPAPI,
  or `BRAHMA_AGENT_TOKEN_<ID>`).

### 2.5 `agent/mcp_server.py` — outbound MCP, not a swarm transport

`agent/mcp_server.py` exposes Brahma's `ToolSpec`s over **stdio** JSON-RPC for
desktop clients (Claude Desktop, Cursor, …). It is a local child process surface;
there is no socket, no auth, and no peer identity. `agent/mcp.py` is the outbound
client counterpart. Neither is usable across machines without a new HTTP/SSE
transport layer, and MCP is a tool protocol, not a task/result protocol with
deadlines and cancellation semantics. Not the swarm path.

### 2.6 Sessions, memory, delegation, audit

- Sessions: one SQLite DB per user at `~/.brahma/sessions.db`
  (`agent/session.py`), tables `agent_sessions`, `agent_messages`,
  `agent_events`, plus compaction summary. This is **per node** and must stay
  per node.
- Delegation: `core/agent_delegation.py` runs a background `AgentLoop` per
  `DelegatedTask` with id/status/turns, the right local precedent a peer task
  server can mirror.
- Audit: `core/audit.py` writes a hash-chained JSONL log with `verify()`.
  A peer request should add one audit record per hop, keyed by a shared trace id.
- Conversation context, budgets, retries, cancellation, and parallel tool dispatch
  live in `agent/loop.py`; any peer worker reuses them unchanged.

---

## 3. Gap analysis — what is actually missing

| # | Gap | Why it blocks three machines |
| --- | --- | --- |
| G1 | **Discovery** | No `ServiceBrowser`; peers must be hard-coded. mDNS advertises but never listens. |
| G2 | **Peer identity** | `brahma_api` tokens are per-client secrets; there is no node keypair, no fingerprint pinning, no "this is Alice's laptop" concept. |
| G3 | **Peer task server** | The A2A client has no counterpart; `brahma_api` is a chat API, not a task API with ids/status/cancel. |
| G4 | **Transport security on LAN** | `http://192.168.x.x:8010` is plaintext; `RemoteAgentAdapter` refuses it; WS-8 requires TLS/SPKI or Noise/NaCl. |
| G5 | **Capability advertisement** | `AgentRecord.capabilities` exists per adapter, but no node publishes "I have a 4090", "I have the repo", "I can run PowerShell". |
| G6 | **Scheduling / fan-out** | Nothing decides which node runs what, enforces a task DAG, or bounds total fan-out. `run_subagent_batch` is local-only. |
| G7 | **Recursion / loop control** | No hop counter / origin id on peer calls; A→B→A can deadlock or amplify cost. |
| G8 | **Remote approval policy** | Remote callers cannot approve; no way for the executing node's human to approve a peer task. |
| G9 | **Shared state** | Sessions are local only; no way to hand a task the right workspace snapshot or artifacts. |
| G10 | **Operator visibility** | No UI/API to see peers, tasks in flight, or cross-node traces; `brahma_api` shows runs only per token. |

---

## 4. Reference topology for three machines

Do **not** build a mesh first. Build **one coordinator + N workers**, with the
coordinator role transferable. A full mesh multiplies identity, scheduling, and
partition problems by N².

```
                ┌─────────────────────────────┐
                │  Node A — coordinator       │
                │  GUI + AgentLoop            │
                │  AgentBridge (peer tools)   │
                │  Peer directory + scheduler │
                └───────┬─────────────┬───────┘
             task       │             │        task
             (HTTPS)    │             │        (HTTPS)
                ┌───────▼──────┐ ┌────▼─────────┐
                │ Node B       │ │ Node C       │
                │ worker       │ │ worker       │
                │ AgentRunner  │ │ AgentRunner  │
                │ local policy │ │ local policy │
                └──────────────┘ └──────────────┘
```

Rules that make this safe:

1. **Every node keeps its own policy.** A peer task executes under the *executing*
   node's `PermissionPolicy`, never the coordinator's. A token/profile caps the
   caller; local policy caps the callee. Two independent gates.
2. **The coordinator is a role, not a special build.** Any node can coordinate;
   the role is a setting and a directory, not a forked code path.
3. **Workers are not dumb.** A worker's agent loop may use its own local tools and
   (optionally) its own subagents; it just cannot recurse into peers by default.
4. **Exactly one hop by default.** Worker → peer is disabled unless the edge is
   explicitly allowed (see §12, recursion).

Ports (proposed, all loopback unless LAN is enabled deliberately):

| Service | Port | Default bind | Purpose |
| --- | --- | --- | --- |
| `brahma_api` | 8010 | `127.0.0.1` | OpenAI lane; Phase 1 peer transport |
| peer task server | 8011 | `127.0.0.1` | A2A `/tasks` surface (new) |
| `brahma_connect` | 8765 | `127.0.0.1` | device gateway, unchanged |
| dashboard | 8000 | loopback | operator view |

---

## 5. Discovery

Three tiers, in order of implementation cost:

### 5.1 Manual peer list (Phase 1 — required)

A file plus a UI list is not glamorous and solves the actual three-machine case.
Reuse the existing loader pattern:

```jsonc
// ~/.brahma/peers.json
[
  {
    "id": "node-b",
    "name": "Workshop PC",
    "endpoint": "https://node-b.tailnet.ts.net:8011",
    "fingerprint": "sha256:…",          // pinned node key, not a CA
    "capabilities": ["gpu", "repo:brahma"],
    "token_ref": "peer_node_b",        // DPAPI secret name, never the token
    "enabled": true
  }
]
```

This is deliberately the same shape as `~/.brahma/remote_agents.json`
(`detectors.py:215-251`), so a peer is just a `kind: "peer"` record. No new
discovery mechanism is needed to ship.

### 5.2 mDNS browse (Phase 3 — convenience)

`brahma_connect/gateway/discovery.py` advertises `_BRAHMA._tcp.local.`. Add a
`ServiceBrowser` that emits candidate peers, **never auto-trusts them**, and shows
them in a "Found on your network" list. Pairing still requires an explicit
approval and a fingerprint match. Advertised TXT records should carry only:
`node_id`, `version`, `api=1`, `capabilities` (coarse tags). Do not advertise a
token, a workspace path, or a hostname.

Also advertise the peer port separately (`_brahma-peer._tcp.local.`) so the device
gateway and the peer surface cannot be confused by a scanner.

### 5.3 Overlay network (recommended for non-LAN)

For machines not on the same subnet, **do not port-forward**: document Tailscale
or WireGuard as the supported deployment. It gives you addressing, NAT traversal,
and an encrypted link with per-device identity, which removes most of G4 at once.
The code should treat "endpoint is a private overlay name" as ordinary HTTPS; it
must not special-case Tailscale.

---

## 6. Transport options

| Option | Reuses | Pros | Cons | Verdict |
| --- | --- | --- | --- | --- |
| **A. `brahma_connect` WS peer role** | Pairing, envelope, hub | One socket, push events, existing auth | Device-shaped protocol, no task semantics, touches a shipping product surface | Later, for events only |
| **B. `brahma_api` OpenAI lane** | `AgentRunner`, tokens, profiles, limits, audit | Already runs `AgentLoop`; streaming; no new server | Chat-shaped, no task id/cancel, remote approval impossible, plaintext on LAN | **Phase 1 transport** |
| **C. A2A `/tasks` server** | `RemoteAgentAdapter` client, artifacts | Matches an existing client; ids, status, cancel; task-shaped | Must implement server + auth; no streaming in v1 | **Phase 2 target** |
| **D. MCP over HTTP** | `agent/spec` tools | Familiar | Tool protocol, not task protocol; no deadlines/cancel semantics; new transport anyway | Reject for swarm |
| **E. gRPC / custom binary** | — | Efficient | New stack, new auth, new everything | Reject |

### Recommendation: B now, C as the contract

Phase 1 proves the wiring with the **existing** API: a coordinator publishes each
peer as a `local_model`-style endpoint? No — it should publish it as a
`RemoteAgentAdapter` `peer` record **once C exists**, and before that as a thin
"peer chat" tool that calls `POST /v1/chat/completions` with a `worker` token and
returns text. Phase 1 is explicitly a prototype, not the final contract.

Phase 2 implements the A2A server behind the same `AgentRunner`, and switches
`AgentBridge` to `RemoteAgentAdapter` unchanged. That ordering has two benefits:
the permission/limits/audit stack is reused, and the client is already written and
tested (`tests/test_agent_remote_adapter.py`).

**Do not implement C as a second runtime.** The server's only job is: authenticate
peer → validate task envelope → call `AgentRunner` (or a thin wrapper) → map the
run to a task record → return artifacts. If it ever constructs its own
`AgentRuntime`, the design has failed.

---

## 7. Identity and trust

### 7.1 Node identity

Each install generates an Ed25519 keypair on first run:

- Private key: DPAPI-protected via `core/secret_store.py` (per Windows user), never
  in JSON, never exported by a UI.
- Public key + fingerprint: `%LOCALAPPDATA%\BrahmaAI\identity\node.json`.
- `node_id = base32(sha256(public_key))[:16]` — stable, human-shareable.

Existing `config/identity.json` and `core/identity.py` should be inspected before
adding a parallel identity store; if they already carry a stable install id, extend
that rather than creating a second one.

### 7.2 Pairing a peer

Reuse the SAS pattern, not the device pairing code path:

1. Node A shows a 6-digit SAS and its fingerprint.
2. Operator on B enters the SAS (or scans a QR containing endpoint + fingerprint).
3. Both sides derive a session key from the SAS + both public keys (SPAKE2-style,
   or SAS-confirmed ephemeral ECDH if a library is unacceptable; the repo is
   stdlib-first, so a documented, reviewed construction matters more than novelty).
4. B pins A's fingerprint; A pins B's. Pairing is **mutual and explicit**.
5. A long-lived peer token is minted *on the callee* (B issues a token to A) with a
   profile (`reader`/`worker`), scopes, and an expiry. The coordinator stores it in
   DPAPI under `peer_<node_id>`.

Why the callee issues the token: it keeps the authority where the work runs, and it
means revoking a peer is a local action on the machine being protected.

### 7.3 Transport security

WS-8's requirement stands: **no bearer token in clear text on a LAN.** Acceptable
options, in order:

1. **Overlay network** (Tailscale/WireGuard): documented deployment; app speaks
   HTTPS to a private name. Simplest and strongest.
2. **Per-node self-signed cert + SPKI pinning.** Each node generates a cert for its
   `node_id`; peers pin the SPKI hash from pairing. No CA, no public DNS, no
   hostname verification against a global namespace. The existing
   `RemoteAgentAdapter` needs a pinned-context mode: today it does either
   `verify_ssl=True` (system CAs) or `verify_ssl=False` (no verification). Pinning
   is the missing third option and should be added before LAN use.
3. **Noise/NaCl channel** over plain TCP. More code, no benefit over (1)/(2) for
   three Windows boxes. Not recommended.

**Never** ship `allow_insecure=True` as the documented LAN path. It exists for
tests and loopback; a peer profile must refuse it when the host is non-loopback and
not an explicitly paired overlay endpoint.

### 7.4 Trust is per edge, not transitive

- A trusts B for the scopes on B's token. A does **not** thereby trust C.
- If B needs C, C must pair with B (or with A and receive a delegated, scoped
  token — later, and explicitly).
- Revocation is local and immediate: delete the peer record and the token record
  on the callee; in-flight tasks continue only if the callee's policy says so.

---

## 8. Peer task protocol (the missing server)

Match the client that already exists. Minimum viable contract:

### 8.1 `GET /.well-known/agent.json`

```json
{
  "schema": "brahma.peer/1",
  "node_id": "7f3c…",
  "name": "Workshop PC",
  "version": "1.4.0",
  "public_key": "ed25519:…",
  "capabilities": ["gpu", "repo:brahma", "os:windows"],
  "verbs": ["run_task", "query_status", "cancel_task"],
  "limits": {"max_turns": 24, "wall_clock_s": 600, "max_body_bytes": 1000000},
  "profiles": ["reader", "worker"]
}
```

Only publish what a paired peer may know. The card is served **only after auth**
(or as a minimal unsigned card before auth, containing nothing sensitive).

### 8.2 `POST /tasks`

Request:

```json
{
  "task_id": "client-generated-uuid",      // idempotency key
  "trace_id": "origin-node:uuid",          // propagated across hops
  "origin_node": "node-a",
  "hop": 0,
  "prompt": "Find the failing test and report the cause. Do not edit.",
  "profile": "reader",
  "workspace": "brahma",                   // alias, never an absolute path
  "deadline_s": 300,
  "budget": {"max_turns": 12, "max_tool_calls": 40},
  "artifacts": ["agent-artifact://…"],     // optional input references
  "reply": {"mode": "final"}               // final | stream
}
```

Response (`202` for accepted async, or `200` for a fast completion):

```json
{
  "task_id": "…",
  "state": "queued",
  "accepted_at": "2026-11-09T12:00:00Z",
  "poll_after_s": 2
}
```

### 8.3 `GET /tasks/{id}`

```json
{
  "task_id": "…",
  "state": "running",            // queued | running | completed | failed | cancelled | expired
  "turns": 3,
  "started_at": "…",
  "finished_at": null,
  "result": null,
  "events_tail": [{"type": "tool", "name": "read_file", "ok": true}],
  "artifacts": []
}
```

On completion, `result` carries the final text, and `artifacts` carries
`agent-artifact://` references (or a peer-scoped `peer-artifact://node/id` that the
client maps onto `read_agent_artifact`).

### 8.4 `POST /tasks/{id}/cancel`

Idempotent. Returns the current state. Cancellation sets the worker's
`AgentRuntime.cancel_event`; it never kills the process (same rule as
`agent.runtime.cancel`).

### 8.5 Semantics that must be specified, not discovered later

- **Idempotency:** `task_id` is client-generated. Re-POSTing the same id returns the
  existing task; it never starts a second run.
- **Deadline:** the callee enforces `deadline_s` and its own `wall_clock_s`,
  whichever is smaller. The caller must not rely on the network to enforce it.
- **Turn/call budget:** callee clamps to its own limits; if the requested budget is
  larger, reject with `budget_exceeded` rather than silently clamp (silent clamping
  makes cost estimates lies).
- **Artifacts:** a result too large for the response body becomes an artifact on the
  callee; the caller fetches it through an authenticated read. Artifact references
  are scoped to the task and expire.
- **Error shape:** stable `code` + human `message` + `retryable` boolean. Never leak
  stack traces or absolute paths to a peer.

---

## 9. How a peer becomes a tool (the harness path)

This is the part that must not be reinvented.

1. Coordinator's `AgentBridge` holds a `peer` `AgentRecord` per node.
2. `publish_tools()` registers `agent_<node>_run_task`, `agent_<node>_query_status`,
   `agent_<node>_cancel_task` as ordinary `ToolSpec`s
   (`bridge.py:147-177`).
3. The coordinator's model calls one as a tool → `AgentRuntime.execute` → policy →
   adapter → HTTPS → peer.
4. The peer's server authenticates, checks its own policy, and runs an `AgentLoop`.
5. Result → `AgentCallResult` → possibly an artifact → tool result to the
   coordinator's loop.

Consequences worth stating explicitly:

- **Approvals are enforced on both ends.** The coordinator may ask its human before
  calling a peer (policy class `native` or a new `peer` class); the peer may deny
  the task's tools locally. Neither side can override the other.
- **A peer call is a fence, not a parallel read.** A remote task has unknown side
  effects, so it must not be dispatched in parallel with other tools the way
  independent reads are (`AGENTS.md`, "Parallel tool calls"). Treat `peer` execution
  tools as `exec`-class fences in `_effects_conflict`.
- **Artifacts are the boundary.** Do not stream a whole workspace back. Return
  bounded text plus artifact references, exactly as `artifacts.py` already does.

---

## 10. Shared state and memory

**Do not replicate `sessions.db`.** A SQLite file over a network share is corruption
plus lock contention; merging divergent transcripts is worse. Instead:

1. **Task-scoped context only.** The coordinator sends the prompt, an optional
   structured `context`, and artifact references. The worker uses its own session
   row (or none) and returns a result. The coordinator decides whether to persist
   the result into its own session.
2. **Workspace by alias.** The coordinator names `workspace: "brahma"`; the callee
   resolves it from its own `brahma_api.json` workspaces. Never send
   `C:\Users\...`. A peer cannot ask for a path it was not configured to serve.
3. **Artifacts as the exchange format.** `agent-artifact://` already gives content
   addressing-ish ids and bounded reads. Add a peer scope later if cross-node reads
   are needed; do not add a shared filesystem.
4. **Memory is per node, opt-in to share.** If the user wants a shared knowledge
   base, that is a later feature with its own replication semantics (and probably a
   read-only export), not a prerequisite for the swarm.
5. **Trace, don't merge.** A `trace_id` and `origin_node` in every task and audit
   record lets an operator reconstruct a cross-node run without a distributed log.

---

## 11. Failure modes

| Failure | Detection | Required behaviour |
| --- | --- | --- |
| Peer offline | connect timeout / probe fail | Mark record `unreachable`; fail the tool call with a retryable error; never spin |
| Peer slow | deadline | Caller deadline fires; call `cancel_task` best-effort; return partial status |
| Worker crash mid-task | missed heartbeat / poll error | Task state `unknown` after grace period; coordinator surfaces it, does not silently retry a mutating task |
| Duplicate POST | `task_id` | Return existing task |
| Clock skew | signed timestamps / nonces | ±60 s window like `gateway_auth`; reject beyond, do not "fix" clocks |
| Version skew | agent card `schema` | Refuse incompatible major; degrade gracefully on minor |
| Token revoked mid-task | 401/403 | Stop, report; do not auto-re-pair |
| Policy denial on worker | run result | Return the denial as the task result; coordinator must not retry the same call (hard-deny rule) |
| Network partition | missed polls | No split-brain writes exist because state is per task and per node; coordinator marks unknown |
| Recursion A→B→A | `hop`, `origin_node` | Refuse with `recursion_denied` before executing anything |
| Cost runaway | per-task and per-turn budgets | Hard stop at callee; no "keep going" from the caller |
| Artifact too large | size check | Store on callee, return reference |
| mDNS spoofing | fingerprint mismatch | Pairing fails; never TOFU silently after the first pin |

---

## 12. Security model — invariants for the swarm

These are the rules that make the feature defensible. They should become ADR text,
not implementation details.

1. **One execution boundary per node.** Every tool call on every node goes through
   that node's `AgentRuntime.execute`. A peer server must not call `actions/*`
   directly or construct a second loop.
2. **Two gates, no inheritance.** Caller identity/profile limits *what may be
   requested*; callee policy limits *what may run*. A coordinator cannot grant a
   worker permissions, and a worker cannot lend them back.
3. **Remote callers cannot approve.** `brahma_api` already enforces this for
   `gui` approval. Peer tasks therefore run `deny`-on-ask unless the callee's human
   opts into a local approval queue (a Phase 4 feature: "peer task waiting for
   approval" in the HUD).
4. **Explicit edges.** No auto-trust from mDNS, subnet, or "same install". Every
   peer is paired with a fingerprint and a token.
5. **Recursion is denied by default.** Carry `origin_node`, `trace_id`, and `hop`.
   Refuse `hop > max_hops` (default 1), refuse if `origin_node == self`, and refuse
   if the destination node id appears in the trace.
6. **Fan-out is bounded.** A coordinator task must carry a total budget
   (`max_peers`, `max_parallel_peer_tasks`, `max_total_turns`). Exceeding it is a
   hard failure, not a warning. Keep the default small: 2 peers, 2 parallel.
7. **No secrets cross the wire.** No API keys, no env dumps, no absolute paths, no
   session ids from another node. Peer tokens live in DPAPI. Prompts are user data
   and must be treated as sensitive.
8. **Transport is authenticated and encrypted.** HTTPS with pinned SPKI, or an
   overlay network. Plaintext HTTP is loopback-only. Bearer tokens never travel in
   clear text.
9. **Audit both ends.** Each node writes an audit record with `trace_id`,
   `origin_node`, `task_id`, decision, and outcome. The hash chain (`core/audit.py`)
   stays per node; cross-node verification is a later, separate feature.
10. **Deny is final.** A policy `deny` on the worker is not retried by the
    coordinator with different wording, and a peer cannot ask the user to override
    it. This mirrors the existing harness rule.
11. **Landmine: data exfiltration by prompt.** A worker with file-read tools can be
    asked to read a secret and put it in its answer. A peer task should therefore
    run under a restricted profile (`reader` by default), and the coordinator should
    treat peer output as untrusted input, not as instructions.
12. **Landmine: tool-name shadowing.** Peer tools are registered as
    `agent_<id>_<verb>`; do not register a peer under a core meta-tool name
    (`AGENTS.md` rule 8). The existing `publish_tools` prefix is correct — keep it.

---

## 13. Phased plan

Each phase has a gate. Do not start the next phase before the gate passes.

### Phase S0 — Decisions and contracts (docs only)

- Accept ADR-023 "Swarm is a federation of runtimes, not a second runtime".
- Accept ADR-024 "Peer transport is A2A-over-HTTPS with pinned node keys".
- Write the task schema from §8 into `docs/`.
- **Gate:** the schema and the trust model are reviewed; no code.

### Phase S1 — Manual peers over the existing API (demo, not product)

- Add `kind: "peer"` handling that calls `POST /v1/chat/completions` with a
  `worker` token and returns text.
- Add `~/.brahma/peers.json` loading on top of `detect_remote_agents`.
- Add a `peer` audit record and a trace id.
- **Gate:** three machines, one coordinator, two workers, run a read-only task
  (`reader` profile) across the LAN over an overlay network; a peer outage produces
  a clean tool error; no plaintext token on the wire.

### Phase S2 — Peer task server (the missing half)

- Serve `/.well-known/agent.json`, `POST /tasks`, `GET /tasks/{id}`,
  `POST /tasks/{id}/cancel` behind the API's token auth.
- Back it with `AgentRunner`/`AgentLoop`; one `AgentRuntime` per task; task registry
  in memory + a small SQLite table (not `sessions.db`).
- Implement idempotency, deadlines, cancellation, budget clamping, artifact return.
- Switch `AgentBridge` to `RemoteAgentAdapter` (already written).
- **Gate:** `tests/test_agent_peer_server.py` covers auth, idempotency, cancel,
  deadline, recursion refusal, and artifact round-trip; `test_agent_remote_adapter.py`
  still passes against the real server.

### Phase S3 — Identity and pairing

- Node keypair (DPAPI), fingerprint pinning, SAS pairing flow, per-edge tokens,
  revocation.
- Update `validate_remote_url` with a pinned-SPKI mode; refuse `allow_insecure` on
  non-loopback.
- **Gate:** a wrong fingerprint fails; a revoked token fails; a copied DPAPI blob
  fails on another user; SAS replay fails.

### Phase S4 — Discovery and operator UI

- mDNS browse with explicit approval; peer list in the HUD; task in-flight view;
  per-peer revoke; trace viewer.
- **Gate:** a new peer on the LAN appears as "found, not trusted" and cannot be used
  until paired.

### Phase S5 — Scheduling and budgets

- Capability-aware routing (gpu, repo, os), DAG tasks, fan-out budgets, retries only
  for idempotent tasks, per-node concurrency.
- **Gate:** a 3-node fan-out stays within the declared budget under a forced worker
  failure; no task is retried if it mutated state.

### Phase S6 — Shared knowledge (optional, separate design)

- Read-only knowledge export, or a replicated artifact index. Not a shared SQLite.
- **Gate:** its own ADR and threat model.

**Effort estimate:** S1 ≈ 1–2 days; S2 ≈ 4–6 days; S3 ≈ 3–5 days; S4 ≈ 2–4 days;
S5 ≈ 3–5 days. S2 is the real work. S3 is the real risk.

---

## 14. The smallest demo that proves the point

Before any server work, this is the honest three-machine demo:

1. On B and C: `brahma_api.json` → `enabled: true`, `host: "0.0.0.0"`,
   `allow_remote_bind: true`, a dedicated token with profile `reader` (and later
   `worker`), `auth_required: true`.
2. Bind the LAN only behind Tailscale/WireGuard; do not expose 8010 to the internet.
3. On A: `~/.brahma/remote_agents.json` with a `peer` record per node
   (`endpoint`, `tier3.allow_insecure` only if on a trusted overlay/loopback test).
4. On A: `run_task` "read-only analysis of X, return a summary". B/C run the loop
   and return text.

This is **L1**, and it is worth doing because it validates tokens, profiles,
limits, and artifact returns before writing a server. It is *not* the swarm: there
is no task id, no cancel, no status, no discovery, no recursion guard, and no
approval path on the worker. Those arrive in S2/S3.

**Explicitly do not** enable `allow_remote_bind` on a machine without a firewall
rule that restricts 8010 to the overlay subnet, and **do not** use the default
`reader` token for a `worker` task — a profile is a permission ceiling, not a hint.

---

## 15. Open decisions (proposed ADRs)

| ADR | Decision | Status |
| --- | --- | --- |
| **ADR-023** | Swarm is a federation of `AgentRuntime`s; no second runtime, no shared DB | Proposed |
| **ADR-024** | Peer transport is A2A-over-HTTPS with pinned node keys; `allow_insecure` is loopback/test only | Proposed |
| **ADR-025** | Peer identity is per-node Ed25519 + mutual SAS pairing; tokens are issued by the callee | Proposed |
| **ADR-026** | Recursion is denied by default (`max_hops=1`, origin check, trace check) | Proposed |
| **ADR-027** | Remote callers never approve; worker runs `deny`-on-ask unless a local approval queue is enabled | Proposed |
| **ADR-028** | Sessions stay per node; cross-node exchange is task-scoped prompt + artifacts | Proposed |

Open questions that need a human decision before S2:

1. Should a peer task be allowed to write, or is v1 read-only (`reader` only)?
   *Recommendation: read-only v1. A write API across three consumer Windows boxes
   is a much larger security surface.*
2. Is Tailscale/WireGuard the supported non-LAN transport, or do we need an
   in-app TLS story with pinned certs? *Recommendation: support both, document
   overlay first, implement pinning before any LAN claim.*
3. Does a coordinator have to be a human-driven GUI, or can a headless coordinator
   run? *Recommendation: human-driven first; headless is a later CLI mode.*
4. What is the revocation story for an offline peer? *Recommendation: local deny
   list; the callee always wins, and an offline node is simply unreachable.*
5. Is there an approved crypto library budget for SPAKE2/Ed25519, or must this stay
   stdlib-only? `agent/` is stdlib-only; `core/` may use `cryptography` if it is
   already a dependency. **Check before designing the pairing handshake.**

---

## 16. Code touchpoints (what changes, where)

| Concern | File | Change |
| --- | --- | --- |
| Peer records | `core/agent_bridge/detectors.py` | Add `~/.brahma/peers.json` loader (or reuse `remote_agents.json` with `kind: "peer"`) |
| Peer adapter | `core/agent_bridge/adapters/remote.py` | Add pinned-SPKI verification; no other change needed for S2 |
| Adapter factory | `core/agent_bridge/bridge.py:55-57` | `peer` already routes to `RemoteAgentAdapter`; keep |
| Tool publication | `core/agent_bridge/bridge.py:147-177` | No change; peer verbs become `ToolSpec`s already |
| Effects conflict | `agent/loop.py` `_effects_conflict` | Treat peer calls as `exec`-class fences |
| Peer server | new `core/peer/server.py` (or `brahma_api/peer.py`) | A2A routes + task registry; calls `AgentRunner` |
| Task registry | new small SQLite table | Idempotency, state, deadline; never `sessions.db` |
| Identity | `core/secret_store.py` + `core/identity.py` | Node keypair, fingerprint, peer tokens |
| Discovery | `brahma_connect/gateway/discovery.py` | Add `ServiceBrowser`; advertise `_brahma-peer._tcp` |
| Recursion | new guard in peer server + adapter | `origin_node`, `hop`, `trace_id` |
| UI | `core/ui_remote.py` / HUD peers panel | Peer list, trust state, in-flight tasks, revoke |
| Wiring | `main.py` (~2948–3016) | Start/stop the peer server next to `brahma_api`; **new code behind a `core/` seam**, `main.py` line ceiling applies |
| Audit | `core/audit.py` callers | One record per peer hop with `trace_id` |
| Config | `config/brahma_api.json`, new `config/peers.example.json` | Documented examples only; real tokens never committed |

**Hard constraints from the repo that apply to all of this:**

- `main.py` (~3,265 lines) and `ui.py` (~827) have ratchet ceilings. New behaviour
  belongs behind a `core/` seam.
- `agent/` is stdlib-only at module scope and CI-collected on 3.11–3.13; do not
  import PyQt/provider SDK/COM there.
- Every harness tool is a `ToolSpec`; never add an `elif` to `core/tool_exec.py`.
- Never register a tool under a core meta-tool name.
- `agent_permissions.json` is user data; only examples go in the repo.

---

## 17. Config sketches

`config/peers.example.json` (documentation only):

```jsonc
{
  "node": {
    "name": "Workshop PC",
    "port": 8011,
    "host": "127.0.0.1",          // set to overlay address deliberately
    "advertise": false,
    "max_hops": 1,
    "max_parallel_tasks": 2
  },
  "peers": [
    {
      "id": "node-a",
      "endpoint": "https://node-a.tailnet.ts.net:8011",
      "fingerprint": "sha256:…",
      "scopes": ["run_task", "query_status", "cancel_task"],
      "profile": "worker",
      "enabled": true
    }
  ]
}
```

`config/brahma_api.json` deltas for a worker node (no secrets in the file):

```jsonc
{
  "enabled": true,
  "host": "0.0.0.0",              // only with a firewall restricted to the overlay
  "port": 8010,
  "allow_remote_bind": true,
  "auth_required": true,
  "preselect_profile": "reader",
  "limits": {
    "requests_per_minute": 10,
    "concurrent_runs": 2,
    "concurrent_runs_per_token": 1,
    "max_turns": 12,
    "wall_clock_s": 300.0,
    "approval_timeout_s": 60.0
  }
}
```

Token creation stays where it already is: the API's token registry stores only
`sha256(token)`, and the plaintext is shown once. A peer token should be labelled
with the peer node id (`peer:node-a`) so revocation is obvious.

---

## 18. Summary of findings

1. **No swarm exists.** `brahma_connect` is a phone/companion gateway with a
   media/system allowlist, not a peer-agent surface.
2. **The A2A client exists, the server does not.** `RemoteAgentAdapter` already
   defines the contract a Brahma peer server must implement.
3. **The permission model is already right.** Two independent gates (caller profile
   + callee policy), one `AgentRuntime.execute` boundary, artifacts for large
   results, hash-chained audit. The swarm must reuse them, not bypass them.
4. **The LAN is not ready.** `brahma_api` binds loopback and remote callers cannot
   approve; `RemoteAgentAdapter` rejects private IPs and plaintext HTTP unless
   `allow_insecure=True`. A real LAN swarm needs pinned TLS or an overlay network,
   plus a decision about worker approval.
5. **Build order:** manual peers over `brahma_api` (prove it) → A2A task server
   (contract) → identity/pairing (trust) → discovery/UI (usability) → scheduling
   (real swarm). Shared memory is deliberately last, and should probably be
   read-only export rather than replication.
6. **The one-line architecture:** a peer is a `ToolSpec` on the coordinator and an
   `AgentLoop` on the worker, with a task envelope and a pinned key between them.
   Anything that adds a second runtime, a shared SQLite file, or implicit trust is
   out of scope.
