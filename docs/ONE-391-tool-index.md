# ONE-391 — Tool & Skill Index Engine

**Status:** implemented
**Scope:** `core/tool_index.py`, `core/tool_bridge.py`, `core/mcp_manager.py`, `main.py`, `ui.py`, `core/prompt.txt`

## The problem

Brahma Evo advertised every tool it knew about on the Gemini Live setup frame:

| Source | Tools |
|---|---|
| Native `TOOL_DECLARATIONS` | 60 |
| Calyx (`calyx-mcp`) | 31 |
| Synapse (`synapse-mcp`) | 82 |
| **Total** | **173** |

Gemini Live accepts at most **128** function declarations per session, so the
handshake was rejected:

```
1007 Request contains an invalid argument
```

The immediate workaround was deleting built-in tools. That treats the symptom:
adding one more MCP server reintroduces the failure, and even under the ceiling
the full declaration set is re-sent on every session, inflating prompt cost and
degrading tool selection.

## The constraint that decided the design

**Gemini Live cannot swap tools mid-session.** `BidiGenerateContentSetup` is
documented as *"sent in the first (and only in the first) message"*, and a
second `setup` frame mid-stream closes the socket with the *same*
`invalid argument` error we are trying to eliminate (confirmed on the Gemini
developer forum and by inspecting `google-genai`'s `AsyncSession`, which exposes
only `send_client_content` / `send_realtime_input` / `send_tool_response`).

So "retrieve the right k tools each turn and re-send them" is not available on
this transport. The advertised surface must be **small and constant**, with
everything else reachable *through* a tool rather than *as* a tool.

## Design

```
                 ┌──────────────────────────────────────────┐
  TOOL_DECLARATIONS │                                          │
  DynamicRegistry   ├─→  ToolIndex  ──→  resolve(intent, k)   │
  MCP servers ──────┘        │                    │
                              │                    ▼
                              │        pinned core  +  search_tools
                     hybrid retrieval              +  call_tool
                     (RRF fusion)                  +  get_skill_manual
                                                       │
                                                       ▼
                                       ≤ budget declarations per session
```

### Advertised surface

    pinned core tools (undo, recall_memory, …)
  + search_tools, call_tool, get_skill_manual     ← 3 constants
  + top-k retrieved tools                          ← bounded by tool_index_budget

The size is fixed regardless of catalogue size. Everything else is one
`search_tools` call away.

### Retrieval — three local scorers, fused

No external vector database, no local LLM router. This machine is CPU-only,
where a 3B router takes minutes per turn; routing must be milliseconds.

| Backend | Basis | Dependency | Latency |
|---|---|---|---|
| `fts5` | SQLite FTS5 / BM25 | stdlib (always on) | ~1 ms |
| `ngram` | hashed word-1/2-gram + char-4-gram TF-IDF cosine | numpy | ~5 ms |
| `embed` | `all-MiniLM-L6-v2` via ONNX | **optional** `fastembed` | ~3 ms |

Fusion is Reciprocal Rank Fusion (k=60). Two deliberate refinements:

- **Exact-identifier boost.** RRF cannot express "the user said the tool's name,
  so it wins". `synapse_browser_wait_for_url` shares most n-grams with
  `synapse_browser_wait_for`; without the boost the shorter sibling ranks first.
- **Per-source cap.** Fifteen Synapse tools all answer "control the browser".
  Pure MMR treats them as distinct and fills every slot from one server. The cap
  bounds how much of the surface any one provider may claim.

Multi-clause requests are scored per clause and fused, so *"find NVDA on the
web and then launch the browser"* surfaces tools for both halves.

### Just-in-time skill context

MCP server documentation is ingested automatically into a per-server manual and
appended to the system prompt **only on sessions where a tool from that server
is advertised**. 80 Synapse tools share one guide rather than 80 descriptions.

### Adding an MCP server requires no manual work

`core/mcp_manager.py` reads the standard MCP self-description surface at
connect time:

1. `initialize` → `instructions` (the canonical field)
2. `prompts/list` → `prompts/get` (prompt templates are often de facto manuals)
3. `resources/list` — filtered to `usage`/`guide`/`manual`/`readme` URIs

Anything found becomes a skill manual. A server providing none still connects
and relies on its tool descriptions. Every optional call is bounded and
fail-soft: `prompts/list` raising `method not found` must not stop boot.

## Measured results

Real `TOOL_DECLARATIONS` parsed from `main.py`, plus 31 Calyx and 82 Synapse
tools (173 total):

| Metric | Before | After |
|---|---|---|
| Declarations on the setup frame | 173 (rejected) | **24** (accepted) |
| Setup-frame tokens | ~9,000 | **~1,770** (−80%) |
| Tools unreachable | — | **0** |
| Query latency (p50 / p99) | n/a | **7 ms / 12 ms** |
| Semantic index build, 173 tools | — | ~0.9 s (background, cached) |

Backends are `['fts5', 'ngram', 'embed']` with `fastembed` installed. The model
is loaded and indexed **off the request path** — `rebuild()` returns in ~200 ms
and the semantic backend folds in when ready, so a Live handshake is never
blocked.

## Configuration

`config/app_settings.json`:

```json
"tool_index_enabled": true,
"tool_index_budget": 24
```

`%LOCALAPPDATA%\BrahmaAI\tool_routing.json` holds user routing overrides:

```json
{ "web_search": { "keywords": "internet reddit twitter news url browse" } }
```

This is the fix path for **description collision**. Some hand-written tool
descriptions are too thin to route on — `web_search` is documented only as
*"Searches the web for any information"*, so a request about Reddit matches
nothing. No retriever fixes that; the data has to change. Shipped defaults cover
the worst offenders, and the file is user-editable without a code change.

## Files

| File | Role |
|---|---|
| `core/tool_index.py` | Catalogue, backends, RRF fusion, MMR, manuals, meta-tool declarations |
| `core/tool_bridge.py` | Session payload assembly and meta-tool execution |
| `core/mcp_manager.py` | MCP connection + automatic instruction ingestion |
| `tests/test_tool_index.py` | 32 tests: ceiling, reachability, degradation, concurrency |
| `tests/test_tool_bridge.py` | 24 tests: payload + meta-tool routing |
| `tests/test_mcp_instructions.py` | 11 tests: automatic instruction ingestion |

## Bugs found and fixed during implementation

Three were only reachable because the work was exercised for real, not mocked:

1. **AB-BA lock-order deadlock.** `rebuild()` held the index lock and took the
   model lock; the background worker held the model lock and took the index lock.
   The process hung on the *second* rebuild. Fixed by a dedicated flag lock and
   an explicit ordering rule.
2. **Self-deadlock on a non-reentrant lock.** `build()` called `load()` while
   holding its own plain `Lock`, so the semantic backend silently never
   activated — the engine fell back to keyword-only and reported no error.
   Caught only by measuring real query latency; fixed with an `RLock`.
3. **Torn read across a catalog swap.** The embedding matrix could be replaced
   mid-query while MCP servers re-indexed, raising `KeyError`. Fixed with a
   consistent snapshot that returns `None` rather than mismatched rows.

## Rejected alternatives

- **Local-LLM supervisor router** — minutes per turn on CPU-only hardware.
- **Calyx as the retrieval backend** — a core routing dependency on an external
  executable that may not be running.
- **Per-turn tool re-send** — not supported by the Live transport; attempting it
  is what produced the original error.
- **Deleting built-in tools to fit under 128** — re-breaks on the next server.