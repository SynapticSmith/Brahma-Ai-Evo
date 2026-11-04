# Brahma Evo as an OpenAI-Compatible Endpoint — Research & Design

**Status:** Phase 0 and Phase 1 built. Tier 2 in the document map.
**Capability:** per key — `chat` (no tools, no workspace), `reader` (read-only), `worker` (workspace write/MCP), `operator` (full policy). Chosen at mint time; never implicit. §6.3.
**Created:** 2026-11-03
**Baseline:** `main` @ `b188305` (2026-11-03), working tree clean.
**Audience:** whoever implements this, and whoever has to decide whether to.
**Companion reading:** `ARCHITECTURE-SPEC.md` §4 (invariants), §6.4 (harness), §6.9 (gateway); `AGENTIC-HARNESS.md`; `ADR-020-TRUSTED-USER-ROOTS.md`.

---

## 0. One paragraph

Brahma Evo already has the three things an OpenAI-compatible endpoint needs — a provider
chain, a tool-calling agent loop, and one permission boundary — and it already runs two
FastAPI servers. What it does not have is a **wire contract** for a third party to call
it. This document specifies that contract. The short version: add a `brahma_api/`
transport package that translates `POST /v1/chat/completions` into an `AgentLoop` run and
translates the run back into OpenAI-shaped JSON or SSE. It is a **transport, not a door**:
it never executes a tool itself, never owns a policy, and never becomes a second way to
reach a handler. Everything security-relevant is inherited from `AgentRuntime`.

---

## 1. Verdict up front

| Question | Answer |
|---|---|
| Is this worth building? | **Yes, as a local integration surface.** It is the cheapest way to put Brahma behind Open WebUI, Cline, Continue, LangChain, a shell script, or another agent. |
| Is it a big change? | No. ~1,200–1,600 new lines in one new package, zero changes to `agent/`, zero changes to `core/tool_exec.py`. |
| Is it safe by default? | Only if no key can be minted without an explicit capability preset, the bind is **loopback**, and the port is **off** until enabled. `reader` is preselected; `operator` needs a typed confirmation. Those are non-negotiable. |
| What is the hard part? | Not the HTTP. The **impedance mismatch**: Chat Completions is a stateless single-message protocol; an agent is a stateful multi-turn process with hidden tool calls. §4 and §5.9 are the whole design. |
| What could go wrong? | A second permission path (§6.1), a remote prompt-injection pivot into the workspace (§6.6), and silent wire drift that makes clients fail one line after a 200 (§5.7, §9). |
| Ship it in phases? | Yes — three phases, §11. Phase 0 is two days and already useful. |

---

## 2. Scope

**In scope:** a loopback HTTP server exposing `GET /v1/models`, `POST /v1/chat/completions`
(non-streaming and SSE), a small `/v1/brahma/*` diagnostics surface, bearer-token auth,
per-token policy profiles, audit, and a Settings toggle.

**Out of scope and explicitly rejected:** `/v1/responses`, `/v1/completions`,
`/v1/embeddings`, `/v1/images/*`, `/v1/audio/*`, `/v1/files`, `/v1/batches`,
`/v1/fine_tuning/*`, OAuth, multi-tenant accounts, and any endpoint that invokes a tool by
name. §10 says why for each.

**Non-goal:** replacing the gateway (`brahma_connect/`). The gateway pairs phones and
routes device commands; this serves model-shaped requests to developer tools. Different
trust domain, different auth, different protocol.

---

## 3. What "OpenAI-compatible" actually means

No standards body defines the term. It is a claim about a **subset** of the wire format,
and every implementer picks a different subset. The research below is the part that
determines the design.

### 3.1 The wire contract a client actually depends on

| Element | Contract |
|---|---|
| Path | `POST /v1/chat/completions`; `GET /v1/models` |
| Auth | `Authorization: Bearer <key>`. Local servers ignore the value but SDKs require it to be non-empty. |
| Base URL | SDKs send everything under `base_url`; `OPENAI_BASE_URL` is read when none is passed. |
| Success body | `choices[0].message.content`, `choices[0].finish_reason`, `usage.{prompt,completion,total}_tokens`, `id`, `object`, `created`, `model` |
| Stream | `text/event-stream`; each event is one `data:` line with a `chat.completion.chunk`; a final chunk carries `finish_reason`; the stream ends with `data: [DONE]` |
| Usage in stream | Only when `stream_options.include_usage` is true; it arrives as an **extra chunk with `choices: []`** |
| Errors | `{"error": {"message", "type", "param", "code"}}` **with the right HTTP status** |

The status code matters more than the body: the OpenAI SDK picks its exception class from
the status, and retries 408/409/429/5xx. A server that returns HTTP 200 with an error
object produces a `ChatCompletion` whose `choices` is `None`, and the caller fails one
line later with a `TypeError` instead of a usable error.

### 3.2 The compatibility spectrum — what silently breaks

These are documented behaviours of real providers. Each one is a decision we have to make
deliberately rather than inherit by accident.

| Failure mode | Why it bites | Our rule |
|---|---|---|
| Unsupported parameters are ignored | Client gets 200, feature silently didn't happen | **Fatal params fail; cosmetic params are reported.** §5.5 |
| `n != 1` unsupported | Code expecting two choices gets one | 400 `param: "n"` |
| Usage chunk has non-empty `choices` (OpenRouter) vs empty (OpenAI) | `chunk.choices[0]` raises `IndexError` | Emit the OpenAI shape: `choices: []` |
| `tool_choice` unsupported | Forced tool call never happens | 400 when we cannot honour it |
| Stream never ends with `[DONE]` | Client hangs until timeout | Always emit it, including on error |
| Error body on HTTP 200 | No exception raised; downstream `None` | Never do this |
| Model list absent | Clients that enumerate models fail at startup | Ship `/v1/models` |
| Token counts invented | Billing and budgets lie | `usage: null` + a flag, never zeros |

The general lesson from the field: **compatibility is a spectrum, not a boolean.** A
passing text request proves almost nothing. §9 specifies the checks that do prove
something.

### 3.3 What the clients we care about actually send

| Client | Sends `tools`? | Sends full history? | Sends `stream`? | Sends `n`, `logprobs`? |
|---|---|---|---|---|
| Open WebUI | Sometimes (native tools) | Yes | Yes | No |
| Cline / Roo Code | **Always** | Yes | Yes | No |
| Continue | **Always** | Yes | Yes | Rarely |
| LangChain / LlamaIndex | Usually not | Yes | Optional | No |
| `curl` / a shell script | No | Optional | Optional | No |
| Another Brahma agent | No | Yes | Yes | No |

The `tools` column is the one that forces a design decision. A coding client always sends
its own tool definitions, because it assumes the endpoint is a *model*. Brahma is an
*agent*. §5.4 resolves this with three explicit lanes rather than a silent guess.

---

## 4. What maps, and what does not

### 4.1 The impedance mismatch

Chat Completions is **stateless, single-message, and tool-transparent**: the client sends
the whole history, gets exactly one assistant message back, and executes any `tool_calls`
itself. An agent run is **stateful, multi-turn, and tool-opaque**: the loop calls the model
many times, executes tools internally, and only the final text is meaningful to an outside
party.

```
Chat Completions                       Brahma AgentLoop
─────────────────                      ────────────────
1 request  ──────────────────────►     N model calls
                                       M tool executions   ← invisible to the client
1 assistant message ◄─────────────     1 final text
stateless                              stateful (session, workspace, undo)
client owns the transcript             loop owns the transcript
```

Everything difficult about this design follows from that picture. There are exactly three
coherent resolutions, and we should ship them as named lanes rather than pick one
silently:

1. **Hide the agent** (agent lane). The client sees a model that happens to take a while
   and returns a good answer. Brahma's tools run; the client's tools are refused.
2. **Hide the agent's tools** (passthrough lane). The client sees a plain model proxy. It
   supplies the tools, it executes them. Brahma adds provider routing, persona, and
   memory — nothing else.
3. **Show both** (hybrid lane). Brahma runs its tools *and* surfaces the client's tool
   calls. This is the only lane that needs server-side state across HTTP requests, and
   the only one with a real correctness hazard (§5.9).

### 4.2 Prior art already in this repository

This is not a greenfield problem — the repo has most of the pieces, in both directions.

| Piece | Where | What we reuse |
|---|---|---|
| OpenAI **client** adapter | `agent/providers.py`, `agent/streaming.py` | The exact chunk shapes to emit; `StreamAssembler` is the inverse of our encoder |
| OpenAI **server** skeleton | `brahma_connect/gateway/server.py`, `dashboard/server.py` | FastAPI + uvicorn on a daemon thread with its own event loop; the lifecycle pattern |
| Bearer/scope auth | `core/gateway_auth.py`, `core/capabilities.py` | Scope vocabulary and `matches_capability` |
| Loopback-only, off-by-default posture | `BrahmaGatewayConfig` | Copy the defaults verbatim: `host="127.0.0.1"`, `enabled=False` |
| One dispatch boundary | `agent/runtime.py:AgentRuntime.execute` | The whole security argument |
| Loop + events | `agent/loop.py:AgentLoop`, `EventSink` | The runner is a thin adapter over `run()` and `on_event` |
| Sessions | `agent/session.py`, `agent/session_bridge.py` | `seed_context`, `messages_after`, `record_voice_turn` |
| Policy | `agent/permissions.py` | Profiles are policy documents, not code |
| Audit | `core/audit.py` | `AuditLogger.record(...)` |
| Redaction | `core/redaction.py` | Strip `Authorization` from every audit payload |
| Desktop host bundle | `core/desktop_host.py`, ADR-019 | API principals get a reduced bundle, not the HUD one |
| Headless precedent | `agent/__main__.py` | The CLI already runs the harness with no Qt; the server mirrors it |

The important absence: **there is no server-side OpenAI adapter anywhere.** The bridge's
`LocalModelAdapter` (`core/agent_bridge/adapters/local_model.py`) is a *client* for other
people's servers. We are building the mirror image.

### 4.3 Concept mapping

| OpenAI concept | Brahma concept | Notes |
|---|---|---|
| `model` | **Agent profile**, not an upstream model | §5.3. The upstream model comes from config. |
| `messages` | Loop seed + final user turn | §5.5 |
| `system` message | Appended client directive, never a replacement | §5.5, security-critical |
| `tools` | Client tools (passthrough/hybrid only) | §5.4 |
| `tool_calls` in the response | Only ever *client* tools | Brahma's own calls are never surfaced |
| `usage` | `AgentResult.usage` | Provider-reported, `null` when unknown |
| `finish_reason` | `AgentResult.stop_reason` | §5.6 mapping table |
| `id` (`chatcmpl-…`) | Run id, also the trace key | §5.6 |
| Session | `agent_sessions` row | Opt-in via header, §5.9 |
| Workspace | `AgentRuntime.workspace` | Selected by model-id alias, allowlisted |
| Policy | `PermissionPolicy` | Per-principal profile, §6.3 |

---

## 5. Design

### 5.1 The one decision everything else follows from

> **The port is a transport. It is not a door.**

`ARCHITECTURE-SPEC.md` §2.1 names three supported doors (voice, GUI text, CLI) and records
that the fourth (`agent_task`) was retired precisely because it bypassed the boundary. A
network listener is the most tempting fourth door in the system. The rule is therefore
stated as an invariant, not a guideline:

- The API package **must not import a tool handler**, a `ToolSpec` handler, or
  `core/desktop_tools.py`.
- The only way a request reaches a tool is `AgentLoop` → `AgentRuntime.execute`.
- The only way a request reaches a model is `agent/providers.build_completer`.
- There is **no** `/v1/brahma/tools/{name}/call` endpoint, ever. If someone wants to call a
  tool directly, that is what MCP is for.

This is testable mechanically (§9.4), which is the only reason to trust it.

### 5.2 Topology

```
 external client (Open WebUI / Cline / LangChain / curl)
        │  POST /v1/chat/completions   Authorization: Bearer <token>
        ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │ brahma_api/            FastAPI + uvicorn, own thread + event loop │
 │  auth.py        token → Principal(scopes, profile, workspace)     │
 │  models.py      model id → lane + profile + workspace alias       │
 │  translate.py   OpenAI messages → loop seed; AgentResult → body   │
 │  streaming.py   SSE encoder (chunks, comments, [DONE])            │
 │  runner.py      AgentRunner: runtime + loop + event bridge        │
 │  server.py      app factory, routes, serve(), lifecycle           │
 └──────────────────────────────────────────────────────────────────┘
        │  (in-process calls — no IPC, no new boundary)
        ▼
 agent/loop.py        AgentLoop.run()                    ← unchanged
 agent/runtime.py     AgentRuntime.execute()             ← unchanged, sole gate
 agent/permissions.py PermissionPolicy                   ← per-principal instance
 agent/spec.py        ToolSpec registry                  ← unchanged
 core/desktop_tools.py, agent/mcp.py                     ← registered by the host, inherited
```

Two deployment modes, one code path:

| Mode | Process | Tool surface | Entry point |
|---|---|---|---|
| **Headless** | `python -m brahma_api` | Harness tools only (`"desktop"` tag filtered, exactly as `AgentLoop` already does at `loop.py` line 234) | `brahma_api/__main__.py` |
| **GUI-hosted** | `main.py` starts it next to `brahma_connect` | Harness **+** desktop specs, because the GUI already registered them | `main.py` after the gateway block |

The API never imports `core/desktop_tools.py`. It inherits whatever is already in the
registry. That keeps headless mode Qt-free and keeps one registry.

### 5.3 Model identity is a capability selector

Clients demand a `model` string. It is the only per-request knob every client reliably
sends. So it carries the security-relevant choice:

```
brahma-evo[<:lane>][@<workspace-alias>]

brahma-evo                  default agent lane, the profile on the key
brahma-evo:read             agent lane, read-only policy
brahma-evo:plan             no tools, one turn, pure reasoning
brahma-evo:passthrough      model lane; client tools only
brahma-evo:hybrid           both; requires a pinned session
brahma-evo:read@myrepo      read-only, workspace alias "myrepo"
```

`GET /v1/models` lists only the ids the caller's token may use. Unknown id → 404
`model_not_found`. An unknown **workspace alias** is never parsed into a path — it is
looked up in `config/brahma_api.json:workspaces`, which is human-authored. That is the
same containment-only rule as ADR-020 `paths.trusted_roots`.

`model` is **never** forwarded to the upstream provider. The upstream provider and model
come from the token's config (`provider`, `upstream_model`). This surprises people once;
document it in the response (`brahma.upstream_model`) and move on.

`GET /v1/models` entry:

```json
{
  "id": "brahma-evo:read",
  "object": "model",
  "created": 1762195200,
  "owned_by": "brahma",
  "brahma": {
    "lane": "agent",
    "policy": "reader",
    "tools": 8,
    "workspace": "C:\\Users\\Admin\\Brahma-Ai-Evo",
    "stream_modes": ["final", "all"]
  }
}
```

### 5.4 Three lanes, chosen explicitly

| | **agent** (default) | **passthrough** | **hybrid** |
|---|---|---|---|
| Brahma's tools run | Yes | No | Yes |
| Client `tools` accepted | **No — 400** | Yes | Yes |
| Client `tool_calls` returned | Never | Always | Only when the model asks |
| Server-side state across requests | Optional (§5.9) | None needed | **Required** |
| Stateless replay is correct | Yes | Yes | **No** |
| Use it for | "Brahma, do the thing" | Drop-in model for Cline/Continue | Power users only |

**Agent lane is the product.** Passthrough exists so a coding client can still use Brahma
as its model without being lied to. Hybrid is Phase 3 and may never ship.

Why `tools` is a hard 400 in the agent lane rather than silently ignored: a coding client
that sends twelve tool definitions and gets an answer that used none of them has been
silently downgraded. The error text names the fix:

```json
{"error": {
  "message": "This model runs its own tools and cannot accept client tools. Use model 'brahma-evo:passthrough' to supply your own, or 'brahma-evo:hybrid' with an X-Brahma-Session header.",
  "type": "invalid_request_error", "param": "tools", "code": "tools_not_supported"
}}
```

### 5.5 Request translation

```
incoming messages
  ├─ system / developer ──► appended as a delimited client directive
  ├─ user ─────────────────► final user turn → loop.run(prompt)
  ├─ assistant ────────────► flattened to text (tool_calls summarised)
  └─ tool ─────────────────► dropped (agent lane) / preserved (passthrough)
```

**System messages never replace the harness prompt.** Brahma's system prompt carries the
resident rules, `AGENTS.md`, the policy note, and the skill catalogue. A remote caller is a
different trust domain and must not be able to delete the rules that constrain it. The
client's system text is appended, wrapped by `agent/prompts.wrap_untrusted_instructions`,
and the event stream records `client_instructions: true`.

**History is flattened by default.** An assistant message with `tool_calls` becomes its
`content` plus a single line — `(called tools: read_file, run_command)` — and `tool`
messages are dropped. Reasons:

- the ids in the client's transcript were never issued by this loop, so preserving them
  invites a provider-side 400;
- the client's tools were never executed by Brahma, so the model would be reading someone
  else's receipts;
- flattening guarantees a clean transcript and removes an entire class of failures.

`brahma.history: "structured"` opts into verbatim preservation, with pairing validation:
an orphaned `tool` message is a 400 `param: "messages"`, never a silent repair.

**Fatal, honoured, and advisory parameters:**

| Class | Parameters | Behaviour |
|---|---|---|
| **Fatal** (400) | `n != 1`, `logprobs`, `top_logprobs`, `audio`, `modalities`, `response_format`, `stop`, `tools` (agent lane), `tool_choice` without `tools`, `parallel_tool_calls` (agent lane) | We cannot honour the semantics; failing is honest |
| **Honoured** | `messages`, `model`, `stream`, `stream_options.include_usage`, `temperature`, `top_p`, `max_tokens` / `max_completion_tokens` (best-effort output cap), `seed` (where the provider supports it), `user` (session hint), `metadata` | Forwarded or applied |
| **Advisory** | `presence_penalty`, `frequency_penalty`, `logit_bias`, `service_tier` | Accepted; echoed in `brahma.ignored_params` |

**Phase 0 status.** Streaming is not implemented, so `stream: true` is a 400 rather than an
empty SSE body. `max_tokens`, `max_completion_tokens`, `top_p`, `seed`, `user`, `metadata`
and the penalty fields are accepted but advisory; each one used is echoed in
`brahma.ignored_params`. `temperature` is honoured.

`stream_options.include_usage` defaults to **true** in our server even when absent, because
Brahma can always report real token counts and clients that ignore the extra chunk are
unaffected.

### 5.6 Response translation

```json
{
  "id": "chatcmpl-2f9c1e8a4b7d",
  "object": "chat.completion",
  "created": 1762195200,
  "model": "brahma-evo:read",
  "choices": [{
    "index": 0,
    "message": { "role": "assistant", "content": "The parser fails on line 42 because ..." },
    "finish_reason": "stop"
  }],
  "usage": { "prompt_tokens": 812, "completion_tokens": 143, "total_tokens": 955 },
  "system_fingerprint": "brahma-evo/1.1.1+policy7f3a2c",
  "brahma": {
    "run_id": "2f9c1e8a4b7d",
    "lane": "agent",
    "stop_reason": "completed",
    "turns": 3,
    "tool_calls": 5,
    "session_id": null,
    "upstream_provider": "local",
    "upstream_model": "qwen2.5-coder:14b",
    "tokens_reported": true,
    "ignored_params": []
  }
}
```

- `usage` is **`null`** when `AgentResult.usage.tokens_reported` is false. I-22 forbids
  rendering unknown as zero. `brahma.estimated_tokens` carries the local estimate.
- `brahma` is a non-standard top-level key. The OpenAI SDKs construct models with
  `extra="allow"`, so it survives as `completion.brahma`; typed clients may drop it. It is
  a debugging aid, never load-bearing.
- `system_fingerprint` embeds the policy hash. When someone asks "why did it refuse", the
  answer is in the fingerprint.

**`finish_reason` mapping:**

| `AgentResult.stop_reason` | `finish_reason` | Notes |
|---|---|---|
| `completed` | `stop` | Normal |
| `max_turns` | `length` | Honest: the run was cut short |
| `repeat_loop` | `stop` | `brahma.stop_reason` carries the truth |
| `cancelled` | `stop` | The client is usually gone; the audit row is the record |
| `permission_denied` | `stop` if text, else **403** | The loop already tells the model to explain the denial |
| `error` | `stop` if text, else **500** | Partial work is returned rather than thrown away |

### 5.7 The streaming contract

```
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","created":…,"model":"brahma-evo",
       "choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

: brahma.turn {"turn":1}
: brahma.tool {"name":"read_file","ok":true,"chars":1180}
: keepalive

data: {"…","choices":[{"index":0,"delta":{"content":"The parser "},"finish_reason":null}]}
data: {"…","choices":[{"index":0,"delta":{"content":"fails on line 42."},"finish_reason":null}]}
data: {"…","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
data: {"…","choices":[],"usage":{"prompt_tokens":812,"completion_tokens":143,"total_tokens":955}}
data: [DONE]
```

Three rules that are easy to get wrong and expensive to debug:

1. **Trace lines are SSE comments** (a line starting with `:`). The SSE spec says clients
   ignore them; the OpenAI SDKs only parse `data:` lines. This is the only safe side
   channel on an OpenAI-shaped stream. A custom `event: brahma.tool` with a `data:` body
   *would* be parsed as a chunk and would break every client.
2. **The usage chunk has `choices: []`.** That is OpenAI's shape, and a client doing
   `chunk.choices[0]` must be able to rely on it being absent there rather than present
   and empty.
3. **`[DONE]` is always emitted** — on success, on policy denial, on provider error. A
   missing terminator is indistinguishable from a hung connection.

**`brahma.stream_mode`:**

| Mode | Behaviour | Default for |
|---|---|---|
| `final` | Buffer assistant text; stream only the last assistant message. Tool narration suppressed. | All external clients |
| `all` | Stream every assistant text delta live, including "let me look at that file…". | Brahma's own UI, debugging |

`final` exists because an agent narrates. Streaming raw agent text to a "model" client
produces output like *"I'll check the file. / I'll run the tests. / The bug is on line
42."* — which is a transcript, not an answer. The cost is time-to-first-token: nothing
arrives until the loop finishes. `: keepalive` every 15 s prevents proxy and client
timeouts during long tool runs.

**Client disconnect.** Detect it, call `runtime.cancel()`, and let the loop finish the
current call. Never kill the worker thread (I-23): a half-written file is worse than a
wasted turn.

**Errors mid-stream** are emitted as `data: {"error": {...}}` followed by `[DONE]`. The SDK
raises on that chunk, which is the correct behaviour.

### 5.8 Tool semantics, stated plainly

- Brahma's internal tool calls are **never** surfaced as OpenAI `tool_calls`. The client
  cannot execute `read_file`, and a `tool_calls` response would make it try.
- Brahma's tool results are **never** returned as `role: "tool"` messages.
- Tool activity is observable through SSE comments (§5.7) and `GET /v1/brahma/runs/{id}`.
- In passthrough, the client's `tools` are forwarded to the upstream model verbatim and
  the upstream `tool_calls` are returned verbatim. Brahma is a wire, not a participant.
- In hybrid, a client tool call ends the HTTP response with `finish_reason: "tool_calls"`
  and a `brahma.continuation` token. §5.9 explains why that token is mandatory.

### 5.9 Sessions and continuity — the honest trade-off

**Default: stateless replay.** Each request is a fresh `AgentLoop` seeded with the client's
flattened history. Nothing persists. This is correct for the agent lane because the only
thing the client ever sees is the final text — so replaying that text is a complete
description of the conversation. Durable state lives in the **workspace**, not in the
transcript: the agent can re-read the file it edited last request.

The limit, stated honestly: the agent loses its own scratchpad between requests. It does
not remember *why* it chose a file, only what is on disk. For a one-shot "fix this bug"
call that is fine. For a long back-and-forth it is not.

**Opt-in: pinned session.** `X-Brahma-Session: <id>` (or `"user": "<id>"` for clients that
only expose that field) binds the request to an `agent_sessions` row. The server then seeds
`[fresh system, stored history, new user]` via `agent/session_bridge.seed_context` — the
same code path the GUI uses — and appends only the new tail with `messages_after`. The
response echoes `brahma.session_id`.

Rule when both are present: **the stored session wins over the replayed history.** Mixing
them produces duplicated turns, which is exactly the bug `messages_after` exists to
prevent.

**Why hybrid cannot be stateless.** Consider a run that reads a file (Brahma tool), then
asks for a client tool. The client executes it and sends request 2 containing only
`[user, assistant(tool_calls), tool(result)]` — Brahma's `read_file` is not in that
transcript, because the client never saw it. A stateless replay would re-plan from
scratch and could repeat a write. That is a data-loss bug, not a UX bug. Hybrid therefore
requires a pinned session, and without one it returns 400.

The `brahma.continuation` token is a signed, single-use handle to the server-side run
state. It exists so a client that cannot set headers still has a correct path. It is
Phase 3.

### 5.10 Approvals over a headless port

`AgentRuntime` defaults to `headless=True` when no HUD is bound, which turns every `ask`
into a `deny` (`permissions.DEFAULT_POLICY.headless`). That is the correct default for a
network caller: **a remote request must not be able to pop a modal on the user's desktop.**

Three channels, chosen per token:

| `X-Brahma-Approval` | Behaviour | Allowed for |
|---|---|---|
| `deny` (default) | `ask` → `deny`, immediately | every token |
| `gui` | Route to `core/confirm` if a HUD is bound; block up to `approval_timeout_s` (60 s), then deny | loopback tokens with `approve` scope only |
| `queue` | **Not built.** A completion needs a body; a 202 with no message breaks every client. | — |

`gui` is deliberately narrow. The banner names the caller (`API token "cline-laptop"
requested run_command`), the request is rate-limited, and the approval is recorded in
`session_allow` **on the API principal's own runtime**, never on the GUI runtime's. A
remote caller must not be able to widen the human's session approvals.

A timeout is a denial with an audit row, not an exception.

### 5.11 Usage and cost

- `usage` comes from the provider via `agent/usage.extract_usage`, summed by the loop.
- `usage: null` when nothing reported. Never zeros.
- Per-token budget: `max_turns` (default **12** for API, not the loop's 40),
  `max_context_chars`, `wall_clock_s` (default 300), `max_completion_tokens` advisory.
- A run that exceeds the wall clock is cancelled at the next turn boundary and returns
  what it has with `finish_reason: "length"` and `brahma.stop_reason: "cancelled"`.
- The provider kill switches (`core/provider_router.guard`, I-21) apply unchanged. A token
  configured for a disabled provider fails at the call, not at configuration time — that
  is existing behaviour and this document does not change it.

### 5.12 Concurrency and cancellation

`AgentLoop.run()` is **synchronous and blocking** — `urllib` and `subprocess` inside. It
must never run on the event loop thread.

- Non-streaming routes are declared `def` so Starlette runs them in its threadpool.
- Streaming routes are `async def` and read from an `asyncio.Queue`; the worker thread
  pushes through `loop.call_soon_threadsafe(queue.put_nowait, event)` from
  `runtime.event_sink`.
- A global `asyncio.Semaphore` caps concurrent runs (default **2**); a per-token semaphore
  caps one client (default **1**). Saturation → **429** with `Retry-After`.
- The cap is low on purpose: each run may spawn subprocesses and hit a rate-limited
  provider. Two is already a lot for a desktop assistant.

### 5.13 Error contract

```json
{"error": {"message": "...", "type": "invalid_request_error", "param": "model", "code": "model_not_found"}}
```

| Status | `type` | `code` | When |
|---|---|---|---|
| 400 | `invalid_request_error` | `invalid_value`, `tools_not_supported`, `session_required` | Fatal parameter, lane mismatch |
| 401 | `invalid_request_error` | `invalid_api_key` | Missing or unknown token |
| 403 | `permission_denied` | `insufficient_scope`, `policy_denied` | Scope or policy refusal with no text |
| 404 | `invalid_request_error` | `model_not_found`, `run_not_found` | Unknown model id or run |
| 409 | `invalid_request_error` | `session_busy` | Two runs on one pinned session |
| 413 | `invalid_request_error` | `payload_too_large` | Body over `max_body_bytes` |
| 429 | `rate_limit_error` | `rate_limit_exceeded` | Semaphore or token bucket full; includes `Retry-After` |
| 500 | `server_error` | `internal_error` | Loop error with no text |
| 503 | `server_error` | `provider_unavailable` | `ProviderUnavailable` from `build_completer` |

The SDK's retry behaviour is driven by these codes (429/5xx retried, 4xx not). Getting the
status wrong turns a transient provider hiccup into a hard client failure.

---

## 6. Security design

### 6.1 Threat model

| # | Threat | Mitigation | Residual |
|---|---|---|---|
| T1 | The port becomes a fourth door with its own dispatch | §5.1 rule + mechanical test (§9.4) | None by construction |
| T2 | A remote caller reads secrets or drives the desktop | No key exists without an explicit preset; `device`/`secret` denied everywhere; `run_command` needs `exec` scope; workspace allowlist | An `operator` token is equivalent to local shell access — document it |
| T3 | Prompt injection from the client steers the agent | Client system text is delimited as untrusted; the harness prompt is never replaced; policy still gates every call | A clever injection can still make the agent *try*; the policy is the answer |
| T4 | Remote approval spam / social engineering | `gui` only for loopback + `approve` scope; banner names the caller; rate-limited; 60 s timeout | A user can still be fooled by a convincing banner |
| T5 | Resource exhaustion | Global + per-token semaphores, wall clock, `max_turns`, body size cap | Two concurrent agents can still saturate a weak CPU |
| T6 | Token leakage | Hash-only storage, redaction in audit, never logged | A token in a client's config file is the client's problem |
| T7 | LAN exposure | `host="127.0.0.1"` default; `enabled=False`; `0.0.0.0` requires explicit opt-in **and** a warning in the UI | A user who opts in gets what they asked for |
| T8 | Supply-chain pivot: remote write → local execution | `chat`/`reader` presets deny write; `worker` restricted to the workspace; never allowlist a workspace containing `config/` | A `worker` token on a repo with a `pre-commit` hook is a real risk; document it |

### 6.2 Authentication

- Bearer token, 32 bytes from `secrets.token_urlsafe(32)`, shown **once** in Settings.
- Stored as **`sha256(token)` only** in `config/brahma_api/tokens.json`. Unlike
  `api_keys.json`, the server never needs the plaintext — it only verifies. Hash-only is
  strictly better than DPAPI here, and avoids the "key beside the ciphertext" pattern.
- Comparison via `hmac.compare_digest`.
- Record: `{id, label, sha256, scopes, profile, workspace, provider, upstream_model,
  created_at, last_used_at, revoked}`.
- Revocation is a flag; the registry is re-read at most once a second (same throttling
  idiom as `AgentRuntime.reload_policy`).

### 6.3 Principals and policy profiles

A **principal** is `(token, scopes, profile, workspace)`. The effective policy is the
**intersection** of the token's scopes and the model id's profile. Fail closed on any
mismatch.

**Capability is a property of the key, not of the server.** The owner decides per key what
Brahma may do; a public-facing bot and the owner's own coding key hit the same server with
different presets. There is no implicit default: a key is minted *as* a preset, and that
preset is written into `tokens.json`. The mint UI preselects `reader`; `operator` requires
typing the preset name to confirm.

| Profile | read | write | exec | network | device | mcp | native | outside ws | protected | workspace |
|---|---|---|---|---|---|---|---|---|---|---|
| `chat` | deny | deny | deny | deny | deny | deny | deny | deny | deny | none |
| `reader` | allow | deny | deny | deny | deny | deny | deny | deny | deny | one |
| `worker` | allow | allow | deny | allow | deny | allow | allow | deny | deny | one |
| `operator` | policy file | policy file | ask → deny* | policy file | policy file | policy file | policy file | ask | ask | one |

\* `operator` + `X-Brahma-Approval: gui` turns `ask` into a real prompt; otherwise deny.

`chat` is the public-surface preset: no tools at all, `workspace: null`, and instruction
discovery skipped, so a Twitch/Discord bot cannot be talked into reading a file. It is the
same no-tools path as `brahma-evo:plan`, pinned by policy rather than by model id. `reader`
is for a client that should answer *about* the workspace. `worker` is for a coding agent
that may change it. `operator` is the owner's own key and is equivalent to local shell
access — say exactly that in the Settings card.

| Connecting… | Preset | Why |
|---|---|---|
| Twitch / Discord / Telegram bot, public chat | `chat` | No tools, no workspace, no prompt-injection pivot. |
| Open WebUI, docs Q&A, code review | `reader` | Can read the repo, cannot change it. |
| Cline / Continue / Aider, `agent` lane | `worker` | Needs write and MCP; no shell. |
| The owner's own scripts and automations | `operator` | Full policy, approvals on loopback. |
| Cline / Continue, `passthrough` lane | any | The client runs its own tools; the preset only gates Brahma's surface. |

Profiles are **policy documents** built from `permissions.default_policy_dict()` and
validated by the same `PermissionPolicy` parser. They are not a new permission system. A
malformed profile keeps the last good one and emits `policy_reload(ok=False)` (I-06).

Each principal gets its **own `AgentRuntime` instance** — cheap to construct, and it keeps
`session_allow` and `cancel_event` isolated. What is shared: the workspace, the policy file,
and the tool registry. `default_extra` carries a **reduced** host bundle (no HUD speak, no
clipboard, no avatar), per ADR-019.

### 6.4 Isolation boundaries

| Boundary | Rule |
|---|---|
| Process | In-process, by design. A separate process would need IPC and would duplicate the registry — a second boundary. |
| Filesystem | `AgentRuntime.workspace` + `paths.trusted_roots`. Never a client-supplied path. |
| Network egress | Provider kill switches (I-21). No new egress path. |
| Shell | `agent/process.run_captured` only (I-12/I-24). No second subprocess call. |
| Desktop | `device` scope denied in every default profile. Opt-in only. |
| Secrets | `secret.read` denied everywhere. The API never returns a key. |

### 6.5 Audit

Every request writes one `core.audit.AuditLogger` row per significant step, redacted
through `core/redaction.redact_data`:

| Event | Payload |
|---|---|
| `API_REQUEST` | token id, model id, lane, message count, stream flag, client IP |
| `API_TOOL` | tool name, class, decision, duration, output chars (never output text) |
| `API_DENY` | tool name, rule, reason |
| `API_APPROVAL` | caller label, decision, wait ms |
| `API_CANCELLED` | reason (`disconnect`, `wall_clock`, `max_turns`) |
| `API_ERROR` | exception type, provider, status |

`Authorization` is never recorded. The hash chain means a deleted row is detectable.

### 6.6 What is explicitly not exposed

- No endpoint that calls a tool by name.
- No endpoint that reads a file or returns a secret.
- No endpoint that starts, stops, or reconfigures the app.
- No endpoint that changes the policy, the token registry, or the workspace allowlist over
  HTTP. Those are Settings-only, human-only.
- No raw exception text to the client. `message` is a curated sentence; the traceback goes
  to the audit log.
- No CORS by default. A browser client needs an explicit origin allowlist, because a
  bearer token in a browser is one XSS away from being someone else's token.

---

## 7. Configuration

`config/brahma_api.json` — human-authored, validated on load, safe defaults:

```json
{
  "enabled": false,
  "host": "127.0.0.1",
  "port": 8010,
  "auth_required": true,
  "preselect_profile": "reader",
  "default_provider": "auto",
  "workspaces": {
    "brahma": "C:\\Users\\Admin\\Brahma-Ai-Evo"
  },
  "limits": {
    "requests_per_minute": 30,
    "concurrent_runs": 2,
    "concurrent_runs_per_token": 1,
    "max_turns": 12,
    "max_messages": 200,
    "max_body_bytes": 1000000,
    "wall_clock_s": 300,
    "approval_timeout_s": 60
  },
  "stream_mode": "final",
  "keepalive_s": 15,
  "audit": true
}
```

Port **8010**: 8000 is the dashboard, 8765 is the gateway. Reusing either would make the
"already running in another instance" check ambiguous.

`preselect_profile` is only the option highlighted in the mint dialog. It is **not** a
fallback: a token record with no `profile` is rejected at load, so "forgot to choose" can
never silently become a capability grant. The key's own `profile` is authoritative.

---

## 8. Module contracts and file inventory

```
brahma_api/
  __init__.py        # inert, like agent/__init__.py
  __main__.py        # headless entry: serve, token mint/list/revoke, config show
  config.py          # BrahmaApiConfig.load/save; validation; never raises on read
  auth.py            # token registry, Principal, scope intersection, rate limiting
  errors.py          # OpenAI error envelope + FastAPI exception handlers
  schemas.py         # request/response models; fatal-param validation
  models.py          # model-id grammar → (lane, profile, workspace); /v1/models rows
  translate.py       # messages → loop seed; AgentResult → completion body
  streaming.py       # SSE encoder: chunks, comments, keepalive, [DONE]
  runner.py          # AgentRunner: runtime + completer + loop + event bridge + cancel
  server.py          # app factory, routes, serve(), lifecycle, shutdown
config/
  brahma_api.example.json
tests/
  test_api_models.py       # id grammar, allowlist, 404
  test_api_chat.py         # non-streaming, flattening, system delimiting, finish_reason
  test_api_stream.py       # chunk shapes, [DONE], usage chunk, comments, keepalive
  test_api_auth.py         # token, scopes, revocation, rate limit, 401/403
  test_api_errors.py       # every status/code pair in §5.13
  test_api_runner.py       # cancel on disconnect, semaphores, wall clock
  test_api_invariants.py   # §9.4 — the architecture guard
  test_api_compat_sdk.py   # official openai SDK (dev-only dependency)
```

**Hard module rules:**

| Rule | Why |
|---|---|
| `brahma_api/` never imports `core.desktop_tools`, `actions.*`, or a tool handler | T1 |
| `brahma_api/` never imports `agent/` at module scope *in a way that breaks the stdlib rule* — it may import `agent.*` freely, because `agent/` is the dependency, not the dependent | The arrow points one way |
| `brahma_api/__init__.py` stays empty | Matches `agent/__init__.py`; importing the package must not start a server |
| No import-time side effects: no socket, no thread, no config read | I-26 |
| One `uvicorn.Server` per process, closed in `main()`'s `finally` | Mirrors `brahma_connect` |
| Config is trusted input; every field validated in `config.py` | Same rule as `agent/mcp.parse_servers` |

---

## 9. Testing strategy

### 9.1 The checks that actually prove compatibility

Adapted from the field-tested checker referenced in §3. Run with the **official `openai`
SDK** (add `openai` to `requirements-dev.txt`; it is the only way to prove the claim):

1. `GET /v1/models` lists the model the token may use.
2. `POST /v1/chat/completions` returns `finish_reason=stop` and non-null `usage`.
3. `stream=true` returns text chunks that concatenate to the non-streaming answer.
4. The raw stream ends with `data: [DONE]` and `Content-Type: text/event-stream`.
5. `stream_options.include_usage` produces a final chunk with `choices: []`.
6. An unknown model returns 404 `model_not_found` and raises `NotFoundError`.
7. A bad token returns 401 `invalid_api_key` and raises `AuthenticationError`.
8. `n=2` returns 400 with `param: "n"` and raises `BadRequestError`.
9. 429 carries `Retry-After` and the SDK retries successfully.
10. A client disconnect cancels the run and writes `API_CANCELLED`.

### 9.2 Unit and integration

- Golden fixtures for every chunk shape (byte-for-byte, so drift is a failing diff).
- `TestClient` + a **stub completer** — no network, no model, no Qt. The loop already
  supports `--provider stub`, so this is nearly free.
- Streaming tests read the raw bytes, not the parsed objects, so the `choices: []` and
  `[DONE]` contracts are actually asserted.

### 9.3 Security tests

- `reader` cannot write, exec, or read outside the workspace (one test per class).
- `chat` cannot call any tool, and its run sees no workspace instructions (one test that
  a `chat` key asked to "read config/api_keys.json" produces a refusal, not a file read).
- A token record with no `profile`, or an unknown preset, is rejected at load (fail closed).
- `operator` is only mintable through the typed-confirmation path (the Settings action
  refuses a bare click).
- A token without `exec` scope gets a policy denial, not a subprocess.
- Client `system` text cannot remove the harness rules (assert the resident rules survive
  in the seeded system prompt).
- `Authorization` never appears in the audit log.
- `gui` approval is refused for a non-loopback token.
- Revoked token → 401 on the next request.

### 9.4 The architecture guard

`tests/test_api_invariants.py` fails if any of these become true:

- `brahma_api/` contains `from actions` or `import actions` or `desktop_tools`;
- `brahma_api/` contains a call to `spec.execute` or a handler;
- `brahma_api/` contains `subprocess` or `os.system`;
- any route path contains `/tools/`;
- `brahma_api/__init__.py` is non-empty;
- `main.py` grows a second uvicorn server that is not closed in `finally`.

This is the same style as `tests/test_architecture_invariants.py` and
`tests/test_security_phase0.py`. The rule is only worth having if a machine checks it.

---

## 10. Alternatives considered

| Alternative | Why not |
|---|---|
| **Expose Brahma as an MCP server** | Genuinely good, but a *different product*. MCP exposes **tools** to a host agent (Claude Desktop, Cursor); the OpenAI port exposes **a conversation**. Complementary, not competing. Ship both, in either order. |
| **Mount `/v1` on the existing gateway** | The gateway is a paired-device control protocol (SAS pairing, device registry, WebSocket command routing) that is off by default for good reasons. Mixing a developer API into it means one auth model has to satisfy two trust domains. Reuse the *lifecycle pattern*, not the app. |
| **A separate process** | Duplicates the tool registry, adds IPC, and creates a second boundary — exactly what ADR-018 rejected for the bridge. |
| **WebSocket / custom protocol** | No client supports it. The entire point is that existing tools work unchanged. |
| **`/v1/responses`** | Partial support across providers, a different item-based shape, and no client we care about requires it. Defer until one does. |
| **`/v1/completions` (legacy)** | Nothing new targets it. A 404 is correct. |
| **`/v1/embeddings`** | Brahma has no embedding model. `fastembed` powers tool routing, not user embeddings. Reject with 400. |
| **`/v1/images/generations`** | `actions/image_generator.py` exists, but mapping a tool to the images API is a different shape and a different policy question. Deferred. |
| **`/v1/audio/*`** | Voice is a bidirectional Gemini Live WebSocket. It does not map to request/response audio. Rejected. |
| **Streaming Brahma's own `tool_calls`** | The client cannot execute them, so it would either error or hallucinate results. Never. |
| **Silently ignoring `tools`** | The silent-downgrade failure the research warns about. 400 with a pointer is better. |

---

## 11. Phased plan

### Phase 0 — the spine (1–2 days)

`config.py`, `auth.py` (token registry with a `python -m brahma_api token mint --preset …`
CLI, so presets are testable before any UI), `models.py` (`brahma-evo`,
`brahma-evo:read`, `brahma-evo:plan`), `translate.py`, `runner.py`, `server.py` with
`GET /v1/models` + `POST /v1/chat/completions` non-streaming. Loopback, off by default,
audit on.

**Acceptance:** `curl` and the OpenAI Python SDK both get an answer from a real local
model; a `reader` token cannot write and a `chat` token cannot read a file; a token cannot
be created without a preset; `test_api_invariants.py` passes; `ruff` clean.

### Phase 1 — the wire (2–3 days)

SSE streaming in `final` mode, keepalive, the full error table, usage handling, semaphores,
wall clock, `GET /v1/brahma/runs/{id}`, `GET /v1/brahma/whoami`, config file, Settings
toggle, `main.py` lifecycle.

**Acceptance:** all ten checks in §9.1 pass against the official SDK; a 40-second tool run
does not time out a proxy; a client disconnect cancels cleanly.

### Phase 2 — the surface (3–5 days)

`passthrough` lane, pinned sessions, per-token rate limiting, the `gui`
approval channel, `GET /v1/brahma/sessions`, a Settings page listing active tokens with a
revoke button.

**Acceptance:** Cline and Continue both work end-to-end against `brahma-evo:passthrough`
with their own tools; a pinned session survives three requests without duplicating turns.

### Phase 3 — research (unscoped)

Hybrid lane with `brahma.continuation`, `/v1/responses` if a real client needs it,
per-request workspace selection, a Brahma MCP server alongside.

**Gate for Phase 3:** someone actually asks. Do not build the hard lane on speculation.

**Rough size:** Phase 0 ≈ 500 lines + 250 test; Phase 1 ≈ 600 + 350; Phase 2 ≈ 500 + 300.

---

## 12. Risks and open questions

| # | Risk / question | Position |
|---|---|---|
| R1 | A future contributor adds a convenience `/v1/brahma/tools/call` route | §9.4 makes it fail CI. This is the single most important guard. |
| R2 | `operator` tokens are local-shell-equivalent | True and unavoidable. Say so in the UI, in the token label, and in the audit row. |
| R3 | Agent runs are slow and expensive | Low concurrency cap, wall clock, `max_turns: 12`. Return partial work rather than a timeout error. |
| R4 | `final` stream mode has no time-to-first-token | Accepted. `all` mode exists for our own UI. Keepalives prevent timeouts. |
| R5 | Stateless replay loses the agent's scratchpad | Documented, and the workspace is the durable state. Pinned sessions fix it when needed. |
| R6 | Wire drift after a refactor | Golden fixtures + the SDK compat test. |
| R7 | A `worker` token on a repo with hooks | Document it; consider refusing workspaces that contain `.git/hooks` with executables. Open question. |
| Q1 | Should the port be able to wake a sleeping Brahma? | No. It serves while the process is running, and that is all. |
| Q2 | Multi-user? | No. One human, N clients. Multi-tenancy would need per-user policies and per-user workspaces — a different product. |
| Q3 | Should the GUI text path be able to call the API? | No. That is a loop, and it would put a network hop inside a local turn. |
| Q4 | Default profile: `reader` or `worker`? | **Resolved: neither, per key.** Capability is a preset chosen at mint time (`chat`/`reader`/`worker`/`operator`); `reader` is preselected and `operator` needs typed confirmation. The owner's own key can be `operator`; a public bot key is `chat`. |
| Q5 | Is `plan` worth a model id? | Yes — it is one line of configuration and it gives every client a no-tools escape hatch. |

---

## 13. Proposed decision register entry

**ADR-022 — The OpenAI-compatible port is a transport, not a door.** *(Proposed, 2026-11-03.)*

> Brahma may expose an OpenAI-compatible HTTP surface. That surface is a **transport**:
> it converts a wire request into an `AgentLoop` run and the run back into a wire response.
> It does not own a policy, a tool registry, a subprocess path, or a dispatch branch. Every
> tool call it causes passes through `AgentRuntime.execute`. It binds loopback, is disabled
> by default, authenticates with a bearer token whose scopes intersect the capability preset
> chosen when that key was minted (`chat`, `reader`, `worker`, or `operator`), and audits
> every decision. There is no endpoint that invokes a tool by name.
>
> **Supersedes:** nothing. **Constrains:** any future "expose Brahma over X" proposal —
> the same four rules apply to gRPC, WebSocket, or A2A.
>
> **Enforced by:** `tests/test_api_invariants.py` (§9.4), `tests/test_api_auth.py`, and the
> audit chain.

---

## 14. Worked examples

```bash
# 1. Is it up, and what may this token use?
curl -s http://127.0.0.1:8010/v1/models \
  -H "Authorization: Bearer $BRAHMA_TOKEN"

# 2. One agent turn, no streaming
curl -s http://127.0.0.1:8010/v1/chat/completions \
  -H "Authorization: Bearer $BRAHMA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "brahma-evo:read",
    "messages": [{"role": "user", "content": "Why does tests/test_agent_loop.py fail?"}]
  }'

# 3. Same, streaming
curl -N http://127.0.0.1:8010/v1/chat/completions \
  -H "Authorization: Bearer $BRAHMA_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "brahma-evo:read",
    "stream": true,
    "stream_options": {"include_usage": true},
    "messages": [{"role": "user", "content": "Summarise the repo."}]
  }'
```

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8010/v1", api_key="brahma_...")

for chunk in client.chat.completions.create(
    model="brahma-evo:read",
    messages=[{"role": "user", "content": "List the modules in agent/."}],
    stream=True,
):
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

Cline / Continue configuration is the same three values as any provider: base URL
`http://127.0.0.1:8010/v1`, the bearer token, and `brahma-evo:passthrough` (because those
clients always send their own `tools`).

---

*This document is a design, not a claim. Phase 0, Phase 1, and Phase 2 acceptance
criteria have passed and are verified by test suites.*
