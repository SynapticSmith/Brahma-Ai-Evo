# Brahma Evo — Tool Retrieval Design (short queries, same-word tools)

**Status:** Design record. No code changed by this batch.
**Created:** 2026-11-05
**Companion:** `docs/ROUTING-AUDIT.md` (why pre-LLM utterance routing is being removed).
**Trigger:** `search_tools` is usually called with a compressed phrase ("vault", "browser click"), and an MCP server name is the only reliable way to tell two tools apart. The current index knows the server but the model does not see it clearly enough, and the meta-tool offers no filter and no ambiguity signal.

> Line numbers are evidence, not contracts. If code and this document disagree, the code is current and this document is a bug to fix.

---

## 1. The problem in one page

A model does not search the way a user speaks. It searches the way it plans: short, noun-heavy, often one word — `"vault"`, `"screenshot"`, `"browser click"`. That is a rational compression, not a defect. The defect is that the retrieval surface does not give the model what it needs to disambiguate a compressed query:

| # | Gap | Evidence |
|---|---|---|
| G1 | `search_tools` has no structured filter. Only `intent` and `limit`. `list_tools` has `source`; search does not. | `core/tool_index.py:1559-1591`, `core/tool_bridge.py:698-745` |
| G2 | The query is not fused with the user's actual request, even though the bridge usually has it. A short model query loses the context that made it meaningful. | `core/tool_bridge.py:698-710`; `build_session_payload` receives `intent` at `:458` but search does not |
| G3 | FTS5 ranks `name` and `body` with equal weight. Server identity lives inside the body blob, not in its own weighted column. | `core/tool_index.py:483-521` |
| G4 | The result list is flat prose. `group: mcp:<server>` is present, but there is no server card, no domain, no "why matched", and no statement that two servers are competing. | `core/tool_bridge.py:721-737` |
| G5 | `_diversify` deliberately lets two servers appear (per-source cap), but presentation does not explain the difference, so the model sees near-duplicates and picks arbitrarily. | `core/tool_index.py:1248`, `:1266-1270` |
| G6 | `call_tool` does not guard same-word ambiguity. An unknown name gets similarity suggestions; a bare name that exists on two servers is not detected. | `core/tool_bridge.py:1071-1100` |
| G7 | There is no resident statement of which servers exist and what each is for. The model cannot add a server word to a short query if it does not know the server exists. | `build_session_payload` returns only `declarations` + `skill_context` |
| G8 | Harness MCP tools (`agent/mcp.py`) are registered as `ToolSpec`s but are not indexed into `core.tool_index`, so `search_tools` cannot find them. Two MCP surfaces, one searchable. | `agent/mcp.py:781`, `:1390`; only `core/mcp_manager.py:1019` and `core/tool_bridge.py` call `upsert` |
| G9 | No evaluation set measures retrieval precision, so every weight change is a guess. | no `tests/test_tool_retrieval_*` |

The user's observation is therefore correct and the fix is **not** "ask the model to write longer queries". The fix is to make a short query sufficient: constrain it with server/domain, fuse it with the turn that produced it, and make collisions explicit instead of silently ranking one above the other.

### Why "vault" is the canonical failure

Two servers can both expose a `vault` tool:

```
mcp_Calyx_vault_search      Calyx   — kernel constellation / memory vault
mcp_Obsidian_vault_search   Obsidian — notes vault
```

Both names contain `vault`; both descriptions contain "vault"; both may be returned. The current output gives the model:

```
1. ... tool_name: mcp_Calyx_vault_search
   group: mcp:Calyx
   does: Search the vault ...
2. ... tool_name: mcp_Obsidian_vault_search
   group: mcp:Obsidian
   does: Search the vault ...
```

The `group` line is the only clue, and it does not say what the group *is*. The model has no basis to choose. More ranking cannot fix that; only structure can.

---

## 2. Design principles

1. **Constrain before you rank.** A deterministic `server=` filter beats any amount of similarity tuning for a same-word collision. Ranking is for when the model does not know the owner; filters are for when it does.
2. **Fuse the turn, not just the query.** The model's `intent` is a compression of the user's request. Retrieval should score both, with the user's wording weighted for recall and the model's wording for precision.
3. **Server identity is a first-class field.** It must be a weighted index column, a line in every result, and a grouping key — not a substring inside a description.
4. **Ambiguity is a result, not a tie-break.** If two servers are genuinely plausible, say so and give the model a refinement step. Never silently pick.
5. **Never auto-execute an ambiguous name.** `call_tool` must require an exact, qualified name or return a choice.
6. **The model should not need to know the catalogue.** A compact live manifest of servers/domains is cheaper than expecting the model to guess which MCP server owns "vault".
7. **Measure it.** A gold set with recall and confusion metrics is part of the design, not a later nicety.

---

## 3. Target architecture

```
turn context (user text, plan step)
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. QUERY BUILDER                                            │
│    intent + context + explicit server/domain filter         │
│    → clause split → weighted query set                      │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. CONSTRAINED RETRIEVAL                                    │
│    allowed = server/domain/source filter (if any)           │
│    FTS5(name, server, domain, keywords, body) + ngram + emb │
│    weighted RRF, structured-match bonus                     │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. GROUP + AMBIGUITY                                        │
│    group by server; per-server best score; margin test      │
│    → groups, match reasons, ambiguity block                 │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. PRESENT                                                  │
│    server card → tools → call template → refine hint        │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. EXECUTE                                                  │
│    call_tool: exact qualified name, or a grouped choice     │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. Component design

### 4.1 Query builder: fuse the model's compression with the user's words

**New optional fields on `search_tools`:**

| Field | Type | Meaning |
|---|---|---|
| `intent` | string, required | What the model wants, as today. |
| `context` | string, optional | The user's request or the relevant plan step, verbatim. |
| `server` | string, optional | Exact server/source, e.g. `Calyx`, `mcp:Calyx`, `native`. |
| `domain` | string, optional | Domain label, e.g. `notes`, `browser`, `system`. |
| `detail` | string, optional | `names` (default) or `schema` (include full argument schema). |

**Implicit context.** The bridge already has a natural seam: `build_session_payload(intent)` is called with the user's text on the text path, and the host keeps `_last_user_text` (`core/audio_io.py:220`). Add `tool_bridge.set_turn_context(text)` called by `text_turn_controller` before dispatch and by `AgentLoop` before a turn. It stores the last two user turns in memory only (never persisted, never logged in full). `_handle_search` uses it when:

- `intent` has at most 6 tokens, **or**
- `intent` contains no token that matches a known server alias, domain, or tool-name segment.

This is the key rule: a short query is not penalised; it is completed with the turn that produced it. If the model passes `context` explicitly, that wins.

**Fusion.** Build up to three query variants and fuse with RRF:

1. `intent` — weight 1.0.
2. `intent + " " + context` — weight 0.7 (context supplies the missing noun).
3. `context` alone — weight 0.4 (recall safety net; never dominant).

The existing `_split_clauses` (`core/tool_index.py:431`) still runs per variant, so a compound request keeps both halves. No new dependency; this is a second pass through the same backends.

**Why not just ask the model to pass context?** Because it will not always do so, and a retrieval layer that depends on model discipline is fragile. The explicit `context` field exists for the harness and for cases where the model has a better summary than the raw utterance; the implicit path is the default.

### 4.2 Indexing: make the server a column

Current schema (`core/tool_index.py:483-490`):

```sql
CREATE VIRTUAL TABLE tools USING fts5(name, body, tokenize='unicode61 remove_diacritics 2');
```

Proposed:

```sql
CREATE VIRTUAL TABLE tools USING fts5(
    name, server, domain, keywords, body,
    tokenize='unicode61 remove_diacritics 2'
);
```

`bm25` takes per-column weights:

```sql
SELECT rowid, bm25(tools, 8.0, 6.0, 3.0, 2.0, 1.0)
FROM tools WHERE tools MATCH ? ORDER BY bm25(tools, ...) LIMIT ?
```

Starting weights (to be tuned by the eval set, not by taste):

| Column | Weight | Rationale |
|---|---|---|
| `name` | 8.0 | An exact tool-name mention should win outright. |
| `server` | 6.0 | Naming the owner is the strongest disambiguator for a shared noun. |
| `domain` | 3.0 | "notes" vs "kernel" separates the two vaults. |
| `keywords` | 2.0 | Server aliases, original tool name, synonyms. |
| `body` | 1.0 | Long MCP descriptions must not drown a name/server hit. |

**`ToolEntry` changes (minimal):**

- `server` is already a property (`:287-290`); use it as the column value.
- Add `aliases: str = ""` for server aliases and the original tool name (e.g. a server whose display name differs from its config key, or a tool that was sanitised from `vault.search` to `vault_search`). Merge into `keywords` if a new field is too much.
- `search_text()` keeps the name repetition for the n-gram and embedding backends; the FTS path no longer needs it because `name` is weighted. Do not remove the repetition globally — the other two backends rely on it.

**N-gram and embedding backends:** prepend a structured header to the embedded text so the vector also carries provenance:

```
Server: Calyx. Domain: kernel memory vault. Tool: vault_search. Search the vault ...
```

This is enough to separate the two vaults without a new model.

**Coverage:** index harness MCP tools too, or state explicitly that `search_tools` covers only the core/dynamic catalogue. Recommendation: index them. The harness already names them `mcp_<server>_<tool>` (`agent/mcp.py:781`); adding a `ToolEntry` at registration time is the same pattern as `core/mcp_manager._index_tool` (`:1019`), and it removes G8. If that is deferred, the `search_tools` description must say which catalogue it covers — a silent gap is worse than a documented one.

**Description hygiene:** `core/mcp_manager._index_tool` already prefixes the provider declaration with `[MCP:<server>]` (`:1042`), but the `search_tools` output uses `entry.short_description`, which does not include it. The fix is in presentation (§4.4), not by mutating the upstream description.

### 4.3 Ranking: structured-match bonus, then diversify

Keep the existing RRF fusion (`core/tool_index.py:1147-1196`) and the exact-identifier boost (`:1181-1188`). Add:

1. **Structured-match bonus** after fusion, before sorting:
   - query token equals a server alias → `+0.35`
   - query token equals a domain token → `+0.20`
   - query token equals a tool-name segment → `+0.25`
   These are small relative to an exact-name `+1.0`, so they break ties without overriding a genuine name match.

2. **Filter-aware candidate pool.** `search(...)` gains keyword-only `server=`, `domain=`, `source=`, `exclude_sources=()`. Because the FTS/ngram/embedding backends are built for the whole catalogue, apply the filter in two stages:
   - over-fetch (`k * 10`, capped) when a filter is present;
   - keep only hits whose `entry.server` / `entry.domain` / `entry.source` match.
   If fewer than `k` survive, do a second pass with the filter pushed into the FTS `server` column and a name-prefix scan over `self._order`. Do not silently widen the filter; return what matched and say the filter was applied.

3. **Diversification stays, but its job narrows.** `_diversify` (`:1227-1322`) exists to stop one server owning the whole surface. It should not be the mechanism that "represents" a competing server in an ambiguous result; §4.4 handles that explicitly. Keep the per-source cap, but when an ambiguity is detected, present the best hit per server as a **contrast set** even if `_diversify` would have ordered them differently.

### 4.4 Presentation: groups, match reasons, ambiguity

Replace the flat list in `_handle_search` (`core/tool_bridge.py:721-745`) with:

```
Found 3 tool(s) for 'vault' — 2 server group(s).

⚠️ AMBIGUOUS: 'vault' matches more than one server.
  Calyx    (kernel constellation / memory vault)  2 tools
  Obsidian (notes vault)                          1 tool
  Refine with search_tools(intent='vault', server='Calyx') or use an exact name.

── Calyx ──  mcp:Calyx  ·  kernel constellation / memory vault  ·  manual: get_skill_manual('mcp:Calyx')
1. mcp_Calyx_vault_search   [name+server]
   does: Search the Calyx kernel vault for a claim or constellation.
   arguments: query, limit
   call: call_tool(tool_name="mcp_Calyx_vault_search", arguments={"query": "<query>", "limit": "<limit>"})

── Obsidian ──  mcp:Obsidian  ·  notes vault  ·  manual: get_skill_manual('mcp:Obsidian')
2. mcp_Obsidian_vault_search   [description]
   does: Full-text search across the Obsidian vault.
   ...
```

Rules:

- **One header per server**, emitted once per group, not repeated per tool.
- **`matched:` reason** computed cheaply from the query tokens: `name`, `server`, `domain`, `description`, `keyword`. This is honest and helps the model see *why* a result is present. It is also useful in `explain`.
- **Manual link only when a manual exists** (already checked at `:736`).
- **Refine hint** is generated from the group that lost: `search_tools(intent=..., server='<server>')` or `list_tools(source='mcp:<server>')`.
- **No synthetic confidence numbers.** The model cannot calibrate a float and will over-trust it. Use the word `AMBIGUOUS` and the structural facts (server, domain, tool count).
- **Cap the header prose.** One short domain line per server. The detail is in `get_skill_manual`; do not paste it.

**Ambiguity test (deterministic):**

```
groups = best hit per server, sorted by score
ambiguous = len(groups) >= 2
            and groups[1].score >= 0.80 * groups[0].score
            and groups[0].server != groups[1].server
```

Plus a lexical check: if the query's head noun appears in more than one server's `name`/`domain`/`keywords`, lower the margin threshold to `0.70`. Both constants live in `tool_index` (not `tool_bridge`) so the eval harness can call the same function. Tune on the gold set. If the flag over-fires, raise the threshold; do not remove the flag.

### 4.5 Execution guard: `call_tool` must not guess

`_handle_call` (`core/tool_bridge.py:1071-1100`) resolves one exact name and then runs it. Change to:

1. **Exact qualified name** (`mcp_Calyx_vault_search`) → run, subject to the existing policy/approval path. If sibling tools with the same unqualified name exist on other servers, add one informational line to the result *only when the call fails*; do not nag on success.
2. **Bare/unqualified name** (`vault_search`) → resolve against unqualified names. If exactly one match, return its qualified name and ask the model to call that (or run it if the mapping is unambiguous and the user authorised the domain). If more than one, **refuse**:

```
Error: 'vault_search' is ambiguous — 2 tools share that name.
Use the qualified name:
  mcp_Calyx_vault_search    (Calyx — kernel vault)
  mcp_Obsidian_vault_search (Obsidian — notes vault)
```

3. **Unknown name** → similarity search as today (`:1084-1100`), but group suggestions by server and include the exact names. Never auto-run a suggestion.
4. **Argument mismatch** → return the schema error, not a nearest-tool guess.
5. **Lineage** → log `(requested_name, resolved_name, search_id, turn_id)` at debug level. This is the data the eval harness needs to measure whether the retrieval→call path is correct in production.

### 4.6 Resident server manifest

At connect, `build_session_payload` returns `declarations` + `skill_context` (`core/tool_bridge.py:509-514`). Add `server_manifest` to `skill_context` (or a separate block appended by the caller), generated from the live index:

```
Connected tool servers (search with search_tools(intent=..., server=<name>)):
- Calyx — kernel constellation / memory vault (24 tools; manual: mcp:Calyx)
- Synapse — desktop, browser, accessibility (157 tools; manual: mcp:Synapse)
- Obsidian — notes vault (12 tools; manual: mcp:Obsidian)
Skills Library: 1,240 playbooks in 18 categories (search_skills)
```

Constraints:

- **Live, not hardcoded.** Built from `index.entries()` grouped by `source`, with the domain line from the server's `initialize` instructions or the per-server override. Rebuild at the turn boundary, like the tool payload.
- **Bounded.** One line per server, max ~12 servers, max ~1200 characters total. If there are more, list the top N by tool count and say `+N more — list_tools()`. Never let the manifest crowd out the prompt.
- **Only when the index is enabled.** The legacy payload (`:517`) has no manifest.
- **This is not a router.** It is context for the model's own choice; it does not execute anything.

The payoff: a model that knows `Calyx` and `Obsidian` both exist will write `search_tools(intent="vault", server="Calyx")` instead of `"vault"`. That is a one-line prompt improvement with a large precision effect.

### 4.7 Evaluation harness

`tests/test_tool_retrieval_eval.py` with a fixture of ~60–100 cases:

```python
Case(
    utterance="search my notes vault for the Q3 plan",
    intent="vault",                     # what the model is likely to type
    expected_server="Obsidian",
    acceptable_tools={"mcp_Obsidian_vault_search"},
    expect_ambiguous=False,
)
Case(
    utterance="answer from the calyx vault",
    intent="vault",
    expected_server="Calyx",
    expect_ambiguous=False,
)
Case(
    utterance="search the vault",
    intent="vault",
    expected_server=None,               # genuinely ambiguous
    expect_ambiguous=True,
)
```

Metrics:

| Metric | Target | Why |
|---|---|---|
| recall@1 | ≥ 0.75 | The top result should usually be right. |
| recall@3 | ≥ 0.90 | The model gets three candidates. |
| MRR | ≥ 0.80 | Rank quality, not just presence. |
| cross-server confusion | ≤ 0.05 | The "vault" failure specifically. |
| ambiguity precision | ≥ 0.80 | Do not cry wolf. |
| ambiguity recall | ≥ 0.90 | Do not hide a real collision. |
| p95 search latency | ≤ 50 ms | Retrieval sits in the turn path. |

The fixture must include a synthetic catalogue with two same-word servers so the test does not depend on a developer's installed MCP set. Cover native/skill/MCP and the three query shapes: exact name, short noun, full user sentence.

Run it in CI with the existing gates; a weight change that lowers recall@3 fails the build. `ToolIndex.explain` (`core/tool_index.py:1523`) already exposes per-backend ranks; surface it behind a debug command so a failing case can be diagnosed without a new tool.

---

## 5. Phased plan

### Phase R0 — Make collisions visible (small, highest value)

Files: `core/tool_bridge.py`, `core/tool_index.py`, `tests/test_tool_retrieval_eval.py`.

- Add `server`/`domain`/`context`/`detail` to `SEARCH_TOOLS_DECLARATION` and `_handle_search`.
- Group results by server; add the `matched:` reason; add the ambiguity block and refine hint.
- Implement `ambiguity()` in `tool_index` and call it from `_handle_search`.
- Add the `call_tool` bare-name guard.
- Add the eval fixture and the synthetic two-vault catalogue.

**Acceptance:** `search_tools(intent="vault")` on a two-vault catalogue prints the ambiguity block and both server groups; `call_tool(tool_name="vault_search")` refuses with qualified names; eval thresholds pass.

### Phase R1 — Index the server as a column (medium)

Files: `core/tool_index.py`, `core/mcp_manager.py`, `agent/mcp.py` (coverage), tests.

- New FTS schema + `bm25` weights; rebuild is in-memory, so no migration.
- `ToolEntry.aliases` (or fold into `keywords`); server/domain in the embedded header.
- Index harness MCP tools, or document the coverage gap in the declaration.
- Structured-match bonus.

**Acceptance:** naming the server in the query moves that server's tool above a same-word tool from another server, at every k; the eval does not regress.

### Phase R2 — Fuse the turn (medium)

Files: `core/tool_bridge.py`, `core/text_turn_controller.py`, `agent/loop.py` / `agent/runtime.py`, tests.

- `set_turn_context` seam; short-query detection; weighted multi-query RRF.
- Explicit `context` overrides implicit.
- No persistence of the turn context; clear on session end.

**Acceptance:** with `intent="vault"` and the user's turn "search my Obsidian notes for the Q3 plan", the top group is Obsidian; with the same intent and "answer from the Calyx vault", it is Calyx.

### Phase R3 — Manifest + prompt (small)

Files: `core/tool_bridge.py`, `core/prompt.txt` (manual tier), tests.

- Live server manifest, bounded, rebuilt at the turn boundary.
- One line in the search_tools description: "If you know which server owns the capability, pass `server`; if your intent is a short keyword, pass the user's request as `context`."

**Acceptance:** the assembled payload contains one line per connected server and stays under the character cap.

### Phase R4 — Production lineage + tuning (ongoing)

- Log retrieval→call lineage at debug level; sample failures into the gold set.
- Re-tune weights and the ambiguity margin against the gold set, not by intuition.
- Only then consider a reranker. A cross-encoder or LLM reranker is another latency-bearing router; it must earn its place against the metrics above.

---

## 6. Non-goals

- **A universal tool ontology.** The server/domain labels come from what the server already tells us plus a small override file. Do not invent a taxonomy for third-party tools.
- **Requiring long model queries.** The model's compression is fine; the system must complete it.
- **An LLM reranker in the hot path.** It adds latency and a second place for hallucinated choices. Structure first; rerank only if the metrics say so.
- **Auto-selecting among ambiguous tools.** The model, or the user, must choose. Silent selection is the bug we are fixing.
- **Removing `_diversify`.** It solves a real problem (one server owning the surface). It just stops being the only disambiguator.

---

## 7. Open questions

1. **Server alias source.** Use the MCP `initialize` server name, the config key, or both? Recommendation: both, plus an optional per-server `aliases` list in settings; index all of them in the `server` column.
2. **Harness MCP coverage.** Index `agent/mcp.py` tools into `core.tool_index`, or keep the two surfaces separate and document it? Recommendation: index them; a tool the model can call but cannot search is a trap.
3. **`call_tool` bare-name behavior.** Refuse and list, or auto-resolve when exactly one match? Recommendation: auto-resolve only when the bare name maps to one tool **and** the server filter/context agrees; otherwise refuse.
4. **Ambiguity in the advertised surface.** Should an ambiguous `search_tools` result also affect `resolve` (the connect-time payload)? Recommendation: no — the payload is a different problem (budget), and the manifest plus search covers it. Keep the ambiguity signal on the search surface only.
5. **Context retention.** Two turns, or the current plan step too? Recommendation: two user turns plus the current plan step if one exists, cleared on session end.

---

## 8. File and symbol index

| Concern | Location |
|---|---|
| Search declaration | `core/tool_index.py:1559` `SEARCH_TOOLS_DECLARATION` |
| Search handler | `core/tool_bridge.py:698` `_handle_search` |
| Search core | `core/tool_index.py:1134` `ToolIndex.search`; fusion `:1147-1196`; diversify `:1227` |
| FTS schema/rank | `core/tool_index.py:459-529` `_Fts5Backend` |
| N-gram / embedding | `core/tool_index.py:537`, `:780` |
| Entry / server | `core/tool_index.py:229` `ToolEntry`; `server` property `:287`; `search_text` `:292` |
| MCP indexing (core) | `core/mcp_manager.py:1019` `_index_tool` |
| MCP registration (harness) | `agent/mcp.py:781` `tool_spec_name`; `:1390` `with_mcp_tools` |
| Call guard | `core/tool_bridge.py:1071` `_handle_call` |
| Payload / manifest | `core/tool_bridge.py:458` `build_session_payload` |
| List filter precedent | `core/tool_bridge.py:767` `_handle_list_tools` (`source`) |
| Explain view | `core/tool_index.py:1523` `ToolIndex.explain` |
