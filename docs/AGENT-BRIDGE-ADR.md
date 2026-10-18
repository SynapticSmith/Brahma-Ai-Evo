# Agent & Orchestrator Bridge — Architecture Decision Records (ADR)

- **Status:** ADR-001 … ADR-013 **accepted 2026-10-17** by the project owner.
- **Scope:** the open questions from `docs/agent-orchestrator-bridge.md` §8 (Q2–Q6) plus
  the additional build defaults raised while authoring `docs/AGENT-BRIDGE-BUILD-TODO.md`.
- **Supersession rule:** where an ADR conflicts with the design document, the ADR wins
  (TODO §0 protocol). **ADR-007 explicitly supersedes the §4.10 example defaults.**
- **WBS mapping:** these records close task `0K.1` (decisions) and the decision half of
  `0K.3` (extraction strategy). New decisions append as ADR-014+ under tasks `0K.4+`.
- **Format:** Status · Context · Decision · Consequences.

---

## ADR-001 — First delivery form: core package + plugin shim *(design Q2)*

**Status:** Accepted.

**Context:** Design §8 Q2 leaned toward a self-contained `core/agent_bridge/` package,
optionally shipped first as a plugin. Design constraint 16 forbids core-file regrowth;
the plugin seam is verified live (`plugin_manager.py:14 load_plugins` / `:46 dispatch`,
wired at `main.py:4061`) and `plugins/` currently holds only `__init__.py`.

**Decision:** Build `core/agent_bridge/` as the package of record from day one, but ship
the **boot wiring as a thin plugin shim** (`plugins/local_agent_bridge.py`) first.
`main.py` receives exactly two guarded edits later — boot resync (≤ 20 lines, Phase 1/3)
and the shutdown anchor next to `MCPServerManager.stop_all()` (`main.py:4158`, ≤ 10
lines, `3L.4`) — both watched by the line-budget guard (TODO §6: `main.py ≤ 3903`).

**Consequences:** Phases 0–2 grow zero core files; tests import the package directly;
the shim can be removed later if native boot is preferred (record as ADR-014).

---

## ADR-002 — LAN peer agents deferred *(design Q3)*

**Status:** Accepted.

**Context:** `brahma_connect` is advertise-only Zeroconf today; peer orchestration would
make Brahma a node in a federation rather than the strict top-level orchestrator the
design mandates — and a peer bridge pointed back at Brahma is the delegation-loop risk.

**Decision:** Peers (and mDNS browsing) stay **Phase 5 stubs, not schedulable**. They
become schedulable only after (a) the 2G.1 recursion/self-target guard has test evidence
and (b) Phases 0–4 are accepted. Phases 0–4 never touch `brahma_connect/`.

---

## ADR-003 — Per-agent confirmation policy *(design Q4)*

**Status:** Accepted.

**Context:** Design Q4 offered a per-agent arm or one global "local agents may run
commands" toggle.

**Decision:** `local_agent_confirm_policy: "per_agent"`. Every shell/file-capable agent
is individually armed from its UI row ("Allow to run commands"). No global blanket
toggle ships, and **no `"off"` policy value exists** — the gate cannot be disabled
through settings.

**Consequences:** one row's grant never extends to another agent; first-connect UX for
CLI agents includes one explicit arm step (fail-closed, matches `core/confirm.py`'s
headless-refuse default).

---

## ADR-004 — Generic `run_task` capability model for CLI agents *(design Q5)*

**Status:** Accepted.

**Context:** Design Q5 asked whether each CLI gets one generic verb or its
sub-capabilities are introspected. Introspection is version-drift fodder (risk R6) and
most target CLIs expose exactly one useful headless surface: "run this prompt here".

**Decision:** Each CLI agent exposes `run_task(prompt, cwd, timeout?)` plus natural read
verbs (`version`, `list_models` where applicable). Sub-capability introspection is out of
scope until a concrete need appears. The vendor catalog (ADR-010) may declare extra
verbs later **without changing the `AgentAdapter` ABC**.

---

## ADR-005 — Auto-connect: safe classes only, default OFF *(design Q6)*

**Status:** Accepted.

**Decision:** `local_agent_connect_on_boot` ships **false**. When a user opts in, only
HTTP/MCP "safe classes" (local model servers, MCP sessions) may connect at boot — CLI
agents and Tier-2/3 connectors **never** connect implicitly, in any setting combination.

---

## ADR-006 — MCP client extraction: import-and-wrap *(WBS B1 · task 0K.3)*

**Status:** Accepted (decision); importability spike executes inside `1X.2`.

**Context:** `transports/mcp_client.py` must reuse `core/mcp_manager.py:946
_stdio_connect`, `:644 _http_connectors`, `:929 _transport_for` without inheriting the
manager's class-level event loop, supervision, or singleton state.

**Decision:** **Import-and-wrap** those helpers — single source of truth, so timeout and
SDK fixes flow to the bridge automatically. The `1X.2` spike proves the import chain does
not drag in the manager loop/singleton; if it does, fall back to **copy-with-attribution**
(comment block pinning the source lines) and record the fallback in this record.

**Consequences:** covered by `1V.1` transport tests — a manager refactor that breaks the
bridge fails CI, not production.

---

## ADR-007 — Discovery is fully opt-in *(WBS B2)* — **supersedes design §4.10 example**

**Status:** Accepted. **Supersedes:** the rev-3 §4.10 config example (`enabled: true`).

**Context:** Design Phase 0 said "default-off flag" while §4.10's example showed the
master switch `true` — resolved in favor of full opt-in.

**Decision:**

1. `local_agent_bridge_enabled` ships **false** — the feature is dormant at first run.
   The UI presents a single explicit enable action; while the switch is false, **every**
   public facade entry (`discover/connect/call/status`) is a safe no-op (DoD test).
2. Once the user enables the bridge, the per-layer `local_agent_scan.*` flags apply
   (defaults `true`) — opting in is **one deliberate action, not five**, and each layer
   stays individually toggleable afterward.
3. `local_agent_connect_on_boot` remains `false` (ADR-005).
4. Discovery stays read-only / no-spawn in **all** states.
5. Tests force-enable the settings via a fixture helper (`0V.1`).

**Amended (2026-10-17, ADR-014):** the scan-layer defaults above are tightened from `true`
to `false` — discovery is *fully* opt-in at every level.

---

## ADR-008 — Fan-out budgets are user-configurable *(WBS B5)*

**Status:** Accepted.

**Decision:** budgets live in settings under `local_agent_budgets`:

```jsonc
{
  "local_agent_budgets": {
    "_default": { "max_concurrent": 2, "max_spend_usd": 5.00 },
    "<agent-id>": { "max_concurrent": 3, "max_spend_usd": 20.00 }   // per-adapter override
  }
}
```

`governance.Budget` reads the entry for an adapter at connect time (`_default` merged
with the per-adapter override). Exceeding a cap raises `FanoutBudgetExceeded`, flips the
adapter's status line, and is surfaced in the UI. **The user chooses both numbers** —
via the `3U.3` settings editor; the shipped defaults above are starting values, not
policy. Design §4.7.1 remains the enforcement layer.

**Amended (2026-10-17, ADR-016):** both numbers are user-editable settings keys with a
per-adapter override map — see ADR-016.

---

## ADR-009 — Durable, referencable agent output *(WBS B6)*

**Status:** Accepted.

**Context:** owner requirement — *all agent output must have state and be referencable,
minimal ephemerality* — while the slice that enters the model's context stays reasonable.

**Decision:**

1. **Artifact store** `core/agent_bridge/artifacts.py`: the **full output of every
   `adapter.call`** is persisted under
   `%LOCALAPPDATA%\BrahmaAI\agent_artifacts\<agent-id>\<yyyymmdd-hhmmss>-<8-hex>.txt`,
   with a JSONL index line `(ts, agent_id, verb, ok, bytes, sha256, artifact_stem)`.
2. **In-context slice** is capped by `MAX_AGENT_OUTPUT_BYTES = 262_144` (head slice).
   `AgentCallResult` carries `truncated`, `bytes_total`, and
   `artifact_ref = "agent-artifact:<agent-id>/<file-stem>"` so the model always has a
   durable handle to the rest.
3. **Retrieval tool:** `read_agent_output(ref, offset=0, limit=65536)` is registered
   **once** (source `agent:artifacts`), unadvertised-by-default (meta-tool reachable),
   returning paged slices of the stored artifact.
4. **Retention:** `local_agent_artifacts_max_mb` defaults to `0` = **never prune** (an
   operator may set a cap). Artifacts live outside the repo → never committed.
5. **Unchanged:** raw agent output still never enters the system prompt or a
   `SkillManual` — artifacts are *data*, retrieved through tool results.

**Consequences:** restarts, re-scans, and adapter churn never lose an output; disk growth
is the accepted trade (risk R21, admin-capable).

---

## ADR-010 — Vendor-agnostic CLI catalog *(owner requirement: any CLI the user has)*

**Status:** Accepted.

**Context:** the machine-specific adapter list must generalize — whatever CLI agent a
user has installed should be discoverable and integrable **later, as data**.

**Decision:**

1. `core/agent_bridge/cli_catalog.py` — declarative vendor entries:

   ```jsonc
   {
     "id": "some-cli", "display_name": "Some CLI",
     "candidate_binaries": ["some-cli", "some-cli.cmd"], "path_prefixes": ["%APPDATA%\\npm"],
     "config_files": ["~/.somecli/settings.json"],          // Layer D sweep
     "version_cmd": ["some-cli", "--version"],
     "invocation": { "template": ["some-cli", "--print", "{prompt}"], "cwd_flag": true },
     "output": { "format": "json|jsonl|text", "parser": "json", "pointer": ".result" },
     "auth": { "env_markers": ["SOME_API_KEY"], "cred_dirs": ["~/.somecli"], "needs_login": ["not authenticated"] },
     "mutating_default": true, "fixture_ref": "some_cli.json", "docs": "https://…"
   }
   ```

2. **Layer A candidate tables and Layer D config sweeps derive from the catalog**
   (`0D.2`/`0D.5` consume it) — detection of unknown vendors requires no detector change.
3. Bespoke adapters (claude/codex/gemini) stay bespoke for complex parsing but **must
   register their catalog entry**; "simple" CLIs run through the generic catalog-driven
   adapter (`2A.4`).
4. **Onboarding a new vendor = one catalog entry + one golden fixture (`4A.1`), zero new
   modules.** Seed targets for the first expansion: aider, copilot, cursor-agent,
   windsurf, cline, opencode-CLI.

---

## ADR-011 — Mutation gating defaults *(WBS B3 + B4)*

**Status:** Accepted.

- MCP/tool capabilities whose intent is unclassified default **`mutating=True`**
  (fail safe: gated, not free — worst case is one extra confirm prompt).
- Tier-2 `assign*` / `run_*` verbs default **`dry_run=True`** until explicitly armed per
  adapter — the fan-out backstop (risk R18).
- Every `mutating=True` verb routes through `core/confirm.py:82 request(...)` under the
  ADR-003 per-agent policy, with `agent:orchestrator:<id>` provenance on dispatched units.

---

## ADR-012 — Workspace sandbox default *(WBS B7)*

**Status:** Accepted.

`transport.cwd` for shell/file-capable agents defaults to
`%LOCALAPPDATA%\BrahmaAI\agent_sandbox\<agent-id>\` (created on first connect). Never
`C:\`, never the repo root, never the user profile. A per-agent explicit override is
allowed and marks the record `user_edited: true`.

---

## ADR-013 — Phase 5 stays deferred *(WBS B8)*

**Status:** Accepted.

Tier-3 remote runtimes, peer agents, and mDNS remain stub tasks `[-]` — not schedulable
until the 2G.1 recursion guard is evidenced in tests **and** Phases 0–4 are accepted.
No Phase 0–4 task may depend on a Phase 5 contract.

---

---

## ADR-014 — Fully opt-in discovery *(amends ADR-007; owner directive 2026-10-17)*

**Status:** Accepted.

**Decision:** the ship posture is opt-in at **every** level:

1. `local_agent_bridge_enabled` ships `false` (ADR-007), **and**
2. every `local_agent_scan.*` layer — `port_sweep`, `process_scan`, `vendor_configs`,
   `known_dirs`, `orchestrators` — ships **`false`**. Enabling the master switch does not
   implicitly run scans.
3. The user opts in either by flipping individual layers or by pressing the explicit
   **"Scan now"** action on the Local Agents page (one-shot, read-only, no-spawn).
4. DoD test extended: with **shipped** settings, `discover()` returns `[]` and logs
   nothing. `0D.10`'s acceptance run uses a fixture helper that force-enables the layers.

**Supersedes:** the `true` defaults shown in design §4.10 and the "default on" wording of
design §2.3 — recorded via Amendment 2 in `docs/agent-orchestrator-bridge.md`.

---

## ADR-015 — Durable, referencable agent output *(amends the B6 decision; owner directive 2026-10-17)*

**Status:** Accepted.

**Context:** owner directive — minimal ephemerality: *all agent output must have state and
be referencable*. The earlier B6 decision capped bytes but persisted only a temp file.

**Decision:**

1. `adapter.call` **persists the full output before returning** via
   `core/agent_bridge/artifacts.py` — per-agent directory under
   `%LOCALAPPDATA%\BrahmaAI\agent_artifacts\<agent-id>\`, one JSON record per call
   (`call_id, verb, ok, ts, bytes, sha256, truncated, output`) plus a `manifest.jsonl`
   index for cheap listing.
2. `AgentCallResult` gains `truncated: bool`, `output_bytes: int`, and
   `output_ref: str` (`agent-artifact://<agent-id>/<call_id>`). Only the slice handed to
   the model is capped at `MAX_AGENT_OUTPUT_BYTES = 256 * 1024`.
3. Exactly **one** extra published tool: `read_agent_artifact(ref, offset, limit)` —
   pinned (source `agent_artifacts`, class `system`, always_reachable, mirror-unregistered
   like `recall_memory`, `tool_bridge.py:191/216`) — so the model can follow a truncated
   result to the full persisted output.
4. Retention is a setting: `local_agent_artifact_retention_days`, default `0` = keep
   forever. Artifacts live outside the repo, are never logged, and never enter the system
   prompt as raw context (the injection rule is unchanged — retrieval goes through tool
   results).
5. Orchestrator episodes (`core/episodes.record()`) store `output_ref` for fan-out units,
   giving a complete provenance chain: orchestrator run → child unit → adapter call →
   full output artifact.

**Consequences:** tests `1V.4`; WBS touchpoints `1P.4` (artifact store), `1P.5` (pinned
retrieval tool).

---

## ADR-016 — User-configurable fan-out budgets *(amends ADR-008; owner directive 2026-10-17)*

**Status:** Accepted.

**Decision:** both budget knobs are user-editable settings, not hardcoded:

```jsonc
"local_agent_budget_defaults":  { "max_concurrent": 2, "max_spend_usd": 5.00 },
"local_agent_budget_overrides": { "<agent-id>": { "max_concurrent": n, "max_spend_usd": x } }
```

`governance.Budget` is constructed per adapter from defaults overlaid by the override map
at connect/call time. The Local Agents page gains a budget editor (task `3U.3`): one
defaults row + per-agent override inputs. Exceeding a cap still raises
`FanoutBudgetExceeded` and flips the status line — only the numbers become the user's.

---

## ADR-017 — Vendor-agnostic CLI catalog *(owner directive 2026-10-17: "whatever a user may have installed")*

**Status:** Accepted.

**Decision:** the candidate-name table (`0D.2`) plus the generic `custom` adapter (`1A.3`)
are the sanctioned extension point for onboarding CLI agents beyond this machine:

1. **Adding a vendor = data first:** a candidate-name entry (single constants table),
   then — when behavior parsing is needed — a golden fixture, then a bespoke adapter
   *only if* generic prompt→argv parsing (`1A.3`) cannot express it.
2. `aider`, `goose`, `qwen-code` join the candidate table immediately (detection-only
   until an adapter exists); `copilot`, `cursor-agent`, and peers follow the same recipe.
3. The runbook lands with the Phase 4 docs task (`4V.1`); the first multi-vendor adapter
   pack is `4A.2` (aider / goose / qwen-code).

**Consequences:** onboarding a new CLI requires **zero core changes**; detection of any
catalogued-but-unadapted CLI is available from Phase 0 onward.

---

*End of ADR-001 … ADR-017. New decisions append as ADR-018+ under WBS tasks `0K.4+`,
each with a supersession note if it overrides an earlier record or the design document.*
