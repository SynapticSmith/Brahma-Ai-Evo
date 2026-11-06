# Routing & Retrieval — Implementation Progress

**Started:** 2026-11-05
**Design records:** `docs/ROUTING-AUDIT.md`, `docs/TOOL-RETRIEVAL-DESIGN.md`
**Rule:** each phase lands with its own tests and a green `ruff` run. Update this file as work moves.

## Decisions taken during implementation

| # | Decision | Why |
|---|---|---|
| D1 | Remove the smart-home pre-LLM router entirely; do not replace it with a better predicate. | Authority, not confidence. A word list cannot know the device inventory, MCP surface, or conversation. |
| D2 | Keep `smart_home_control` advertised; make the handler return the service's truthful error (`NO_DEVICES`, `NO_MATCH`). Do not hide the tool when the device count is zero. | A hidden tool cannot answer "do I have devices?". The model deliberately choosing the tool and getting a truthful result is not a hijack. This also avoids a second availability registry. |
| D3 | Delete the smart-home branch from `_build_task_plan`; do not merge it with any matcher. | The preview has no authority and contradicted the handler in both directions. |
| D4 | Keep the Brahma Connect utterance path for now. | It is a separate domain; it gets the same authority review in Phase 4, not a drive-by rewrite. |
| D5 | Retrieval first pass (R0) is structural: server/domain/context filters, grouped output, ambiguity signal, `call_tool` guard, gold-set eval. Ranking-column changes (R1) follow once the output contract is tested. | Fix the information the model sees before tuning scores. |
| D6 | `connect_*` distinguish `NO_PAIRED_DEVICE` from `DEVICE_NOT_FOUND`; an empty list is a successful answer with `paired=false` and a note. | "Nothing is paired" and "that name is wrong" need different follow-ups. A gateway exception is not proof of an empty pairing list, so it keeps `GATEWAY_UNAVAILABLE`. |
| D7 | R0 also accepts explicit `context` (weighted RRF) even though the implicit turn seam is R2. | The field has to mean something the day it is advertised; explicit context is testable now and the implicit seam can feed the same code later. R2 landed on 2026-11-05 and feeds that same code path. |
| D8 | Ambiguity is suppressed when the winning server owns a query word the runner-up does not. | Context ("Obsidian", "notes", "kernel") already discriminates; crying AMBIGUOUS there would train the model to ignore the flag. |
| D9 | Do not add a separate weighted FTS server column (R1). Lock the existing property in with a test and document harness-MCP coverage. | `search_text` already repeats name variants three times, and MCP tools index as `mcp_<server>_<tool>`, so the owner is weighted harder than a bm25 column would weight it. `test_naming_the_server_wins_at_every_k` proves it at k=1–3. A second column would duplicate an existing weight. |
| D10 | Plane A gets one contract module (`core/routing.py`) with `Authority` × 4 and a `RoutingClaim` that cannot carry a confidence value. Every pre-LLM consumer is a record in `ROUTERS`; a text-derived claim needs a named negative test. | The audit's invariant 5 ("no new pre-LLM router without an authority class and a decline path") is only enforceable if the inventory and its tests are one inspectable list. Putting `confidence` on the claim re-opens R1 by type. |
| D11 | Convert the two fuzzy routers instead of deleting them: dynamic skills match exact trigger/alias/name only, and open-app matches an exact alias only. Keep dormant routers, but pin them dormant with a test. | Both features stay reachable because the model still sees them (dynamic skills as `dynamic_skill`, apps through the `open_app` tool). Deleting `intent_dispatcher`/`execution_reflection` is a separate cleanup; the risk the audit named is an accidental *wiring*, so the test guards that directly. |
| D12 | Every remaining fixed-phrase gate is matched on word boundaries through `routing.has_phrase`/`any_phrase`; the preview (`_build_task_plan`) returns the neutral four-step plan for every input. | Whole-word matching removes the R1 collision class (`ac`/`backup`, `phone`/`microphone`) without weakening a real command. The preview has no authority and no information the model lacks (D3). |

## Checklist

### Phase 0 — Remove the smart-home pre-LLM claim

- [x] Inspect `core/device_flow.py`, the call site, the host wrapper, the tool handler, and the service contract.
- [x] Remove the `text_turn_controller.py` call site.
- [x] Remove `DeviceFlow.handle_smart_home_command` and the host wrapper.
- [x] Make `_smart_home_control` return a truthful message instead of raising.
- [x] Add routing regression tests (no pre-LLM claim; tool path still serves real commands).
- [x] `ruff` + targeted tests green (`tests/test_smart_home_routing.py`, 5 passed).

### Phase 1 — Delete the domain preview

- [x] Remove the smart-home branch from `main._build_task_plan`.
- [x] Add tests for `"Mushroom risotto recipe"` and `"Open Microsoft Office"`.
- [x] `ruff` + targeted tests green.

### Phase 2 — Availability and voice surface

- [x] Confirm no other code references the removed smart-home router.
- [x] Fix `session_lifecycle` intent seeding: read `_last_user_text` from the host, not the seam.
- [x] Make `connect_*` report "no paired phone" truthfully (`NO_PAIRED_DEVICE`), keeping `DEVICE_NOT_FOUND` for a real name miss and `GATEWAY_UNAVAILABLE` for a broken gateway.
- [x] `tests/test_device_availability.py` (6 passed).
- [x] Update the design docs to match D2/D6.

### Phase 3 — Resident device policy

- [x] Add the two core-tier lines to `core/prompt.txt` (`DEVICE TARGETING`).
- [x] Test that `lite` includes the rule (`tests/test_prompt_parts.py`).

### Phase 4 — Retrieval R0

- [x] `search_tools`: `server`, `domain`, `context`, `detail`; grouped output; `matched:`; ambiguity block; refine hint.
- [x] `ToolIndex.search(server=, domain=, context=)` constrains before ranking; filtered browse mode; no per-source cap when one server was requested.
- [x] `ToolIndex.ambiguity()` deterministic contrast set; unique-owner suppression (D8).
- [x] `call_tool`: bare-name ambiguity guard, single-candidate rename hint, collision guard for a name registered by two servers.
- [x] Retrieval gold-set test with a synthetic two-vault catalogue (`tests/test_tool_retrieval_eval.py`, 10 passed).
- [x] `ruff` + related tests green (160 passed across tool bridge/index/registry/desktop/MCP/skills/policy).

### Phase 5 — Retrieval R2 (implicit turn context)

- [x] `tool_bridge.set_turn_context` / `turn_context` / `turn_context_history` / `reset_turn_context`; memory only, two turns, 600 chars each.
- [x] Short-query rule: at most `SHORT_QUERY_TOKENS` (6), or no token in `ToolIndex.vocabulary()`.
- [x] Explicit `context` wins; `server`/`domain` still suppress ambiguity.
- [x] Seams: `TextTurnController.on_text_command`, `AgentLoop.run` (not for child subagent loops), Live input transcription.
- [x] `reset_session_learning` clears the remembered turns at explicit session end.
- [x] `tests/test_turn_context.py` (10 passed); 181 related tests + 551 agent tests green.

### Phase 6 — Retrieval R3 (resident server manifest)

- [x] `ToolIndex.server_manifest()`: live grouping by owner, most-common domain, manual hint.
- [x] Bounds: 12 servers, 1200 characters, `+N more — use list_tools()` for the rest.
- [x] `build_session_payload` returns `server_manifest` and prepends it to `skill_context`; empty when the index is off.
- [x] `tests/test_server_manifest.py` (7 passed).

### Phase 7 — R1 acceptance (evidence, no schema change — D9)

- [x] `test_naming_the_server_wins_at_every_k` locks the owner-first property at k=1, 2 and 3.
- [x] Record that aliases are covered by `domain` + `keywords`.
- [x] Close harness-MCP coverage (2026-11-06): `agent/mcp.py` indexes each registered spec into `core.tool_index`, so `search_tools`/`call_tool` reach `.mcp.json` tools too. Source-scoped removal and an app-manager ownership guard prevent a dual-configured server from colliding or losing its live entry.

### Phase 8 — Plane-A authority registry (ROUTING-AUDIT §6 Phase 4)

- [x] `core/routing.py`: `Authority` (`syntax` / `exact_alias` / `owned_state` / `verified_target`), `RoutingClaim.claim()`/`decline()` with no `confidence` field, `tokens`/`normalize`/`has_phrase`/`any_phrase`/`is_phrase`/`exact_alias`.
- [x] 23-record `ROUTERS` inventory (live / converted / delegated / dormant / removed) with `validate()` and a pinned count.
- [x] Convert `core/dynamic_registry.find_matching_skill` to exact trigger/alias/name, stripping at most one imperative verb.
- [x] Convert `open_app` to an exact alias (this also fixed the empty-candidate match where a bare `open` claimed the turn).
- [x] `DeviceFlow` (Brahma Connect) needs a token-bounded phone word **and** a device that resolves; `SmartDeviceManager.route_command` needs a live active device.
- [x] Make `_build_task_plan` return the neutral four-step plan for every input.
- [x] Make every remaining fixed-phrase gate word-bounded (`offline`, email/calendar, system/diagnostics, auto-heal, volume, meeting mode, screen, daily briefing, builder nouns); `looks_like_software_request` stops "write a script for a play" from claiming Brahma Dev.
- [x] Keep `core/intent_dispatcher.py` / `core/execution_reflection.py` dormant and pin that with a test that fails if app code imports them.
- [x] `tests/test_routing_authority.py` (105 passed); 131 passed across the routing/device/briefing/flowstate/connect suites.

### Later

- [x] Phase 4 routing authority registry; keep the dormant routers pinned dormant.

## Log

- **2026-11-05:** Phase 0 code removal complete. Design adjusted per D2 (truthful tool instead of a hidden one). Tests added; verification pending.
- **2026-11-05:** Phases 0–3 verified. Smart-home has no pre-LLM claim anywhere; `_build_task_plan` has no device branch; the service's no-devices error reaches the model as tool text; `connect_*` separates unpaired from unmatched; the `lite` prompt carries the device-targeting rule; the voice tool seed now reads the host's last utterance.
- **2026-11-05:** Retrieval R0 landed. `search_tools` returns server groups, `matched:` reasons, an AMBIGUOUS contrast set with a refine hint, and exact server/domain filters; `call_tool` refuses a bare name shared by two servers and refuses a name whose registration was overwritten. The synthetic two-vault gold set passes recall@1 1.0, recall@3 1.0, MRR 1.0, cross-server confusion 0.0, ambiguity precision/recall 1.0.
- **2026-11-05:** Full gate green after R0 (1368 passed, 5 skipped). Retrieval R2 landed: a compressed intent (≤6 tokens, or no catalogue vocabulary) is completed from the user's remembered turn on the text, agentic and Live paths; an explicit `context` wins; child subagent tasks never overwrite the parent's turn; an explicit session end clears it. 10 new tests; 181 related + 551 agent tests green.
- **2026-11-05:** Retrieval R3 landed: `build_session_payload` now carries a live, bounded server manifest (≤12 servers, ≤1200 chars) inside the instruction context, so the model can pass `server=` instead of asking an ambiguous question about a shared noun. R1 was accepted on evidence (D9): naming the server already wins at every k, aliases live in `domain`/`keywords`, and harness-MCP tools are advertised directly rather than deferred. New tests: 7 manifest tests and one R1 guard in the gold set.
- **2026-11-05:** Full gate green after R0–R3: `ruff check .` clean, `1386 passed, 5 skipped` in 169 s.
- **2026-11-05:** Plane-A authority registry landed (`core/routing.py`, §6 Phase 4). The four authorities are the whole vocabulary, `RoutingClaim` has no `confidence` field, and the 23-record `ROUTERS` inventory drives `tests/test_routing_authority.py`. Converted: dynamic skills (exact trigger/alias/name, one imperative stripped) and open-app (exact alias, which also fixed the empty-candidate match); Brahma Connect now requires a token-bounded phone word plus a device that resolves; the task preview is neutral for every input. Every fixed-phrase gate is word-bounded, so `ac`/`backup`, `phone`/`microphone`, `fan`/`fantastic`, `light`/`highlight`, `hall`/`challenge`, `room`/`mushroom` and `script`/`play` no longer claim a turn. `intent_dispatcher` and `execution_reflection` stay dormant, pinned by a test that fails if app code imports them. 105 new routing-authority tests.
- **2026-11-05:** Full gate green after the authority registry: `ruff check .` clean, `1491 passed, 5 skipped` in 181 s.
- **2026-11-06:** Harness-MCP coverage closed (G8). `agent/mcp.py` publishes every registered `ToolSpec` into `core.tool_index` at registration (`_index_spec`) with `source`/`skill_id` `mcp:<server>` and retracts it on close/refresh (`_unindex_spec`). `ToolIndex.remove(name, source=...)` makes removal source-scoped, and a name the app manager already owns (`DynamicToolRegistry.is_external_tool`) is skipped, so a server configured in both MCP clients neither creates a phantom `call_tool` collision nor loses its live entry when a harness session closes. Lazy, fail-soft imports keep `agent/` importable with only pytest. New tests: 5 in `tests/test_agent_mcp.py` (search, refresh, `call_tool`, duplicate client, fail-soft) plus a source-scoped removal test in `tests/test_tool_index.py`; targeted suites green (213 passed).
