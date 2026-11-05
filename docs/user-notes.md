# User Punchlist &amp; Feedback Ledger

The ticked items below have been investigated, fixed in code, tested in CI, and documented in `docs/USER-GUIDE.md` and `docs/ARCHITECTURE-SPEC.md`. The unticked user reports and the **Handoff** section at the end are still open.

- [x] **Orca ADE Orchestrator Probe Crash**: Fixed string formatting in `run_captured` (`cwd=Path.cwd()`), guarded thread exceptions in `core/agent_bridge/adapters/orchestrator.py`.
- [x] **Clipboard Monitor Feedback**: Added `tool_index.get_index().is_disabled("clipboard_processor")` check so unchecking the tool halts audio feedback.
- [x] **Skills Library in Standalone Installer**: Added `skills_library` asset bundling to `installer/BrahmaEvo.spec` and enabled multi-root discovery across `skills/` and `.brahma/skills/`.
- [x] **Voice &amp; Avatar Setup Streamlining**: Exposed Gemini Live voice selector (`Charon`, `Puck`, `Aoede`, `Fenrir`, `Kore`) in Settings Hub with zero-developer configuration.
- [x] **Behavior Mode Customization**: Added persistent `Custom Instructions` multi-line editor in Settings Hub under Identity &amp; Profile.
- [x] **Chat GUI Truncation &amp; Streaming Latency**: Removed text clamping from result cards, implemented adaptive typing animation (&lt;=350ms cap), and added instant click-to-reveal on chat bubbles.
- [x] **Auto-Heal Sentry Verification**: Validated AST verification, atomic backup, and one-click rollback in `tests/test_auto_heal_engine.py`.
- [x] **Modular External Memory Subsystem**: Built modular connectors (`Local`, `Mem0`, `Letta`) in `memory/connectors/` with DPAPI encryption and live recall test button.
- [x] **Proactive Comments Control**: Added `proactive_voice_comments_enabled` toggle in Settings Hub under Speech Engines (`core/ui_connectivity.py`, `core/audio_io.py`).
- [x] **Permission Overlay Sizing &amp; Chat Spacing**: Relaxed height constraints on `ConfirmationOverlay` in `core/ui_overlays.py` and improved spacing/margins in `core/ui_chat.py`.
- [x] **Chat File Attachment Handling**: Handled drag-and-drop file attachments in Live chat sessions without disconnecting the WebSocket audio loop (`core/ui_runtime.py`, `core/audio_io.py`).
- [x] **Memory Modules Setup Documentation**: Documented step-by-step setup for Local, Mem0, and Letta in `docs/USER-GUIDE.md` Section 6.
- [x] **Exhaustive Documentation Catch-up**: Updated `docs/USER-GUIDE.md`, `docs/ARCHITECTURE-SPEC.md`, and `docs/OPENAI-COMPATIBLE-API-DESIGN.md` covering all newly landed subsystems.
- [x] **API Provider & Fallback Chain Resolution**: API now honours `default_ai_provider`, provider router chain, and settings-enabled flags instead of hardcoding local-first (`agent/providers.py`, `brahma_api/runner.py`). Added first-call failover and Gemini harness adapter.
- [x] **Harness Native Web Tools**: Added stdlib `web_search` and `web_fetch` in `agent/web.py` gated under `network` permission class (`agent/spec.py`). Available to `worker` and `operator` profiles and propagated to subagents.
- [x] **Local Inference Timeouts & Configurable API Wall Clock**: Fixed JSON probe parsing in `core/local_brain.py`, made timeouts settings-backed (`core/popup_settings.py`), and raised/configured API wall clock in `brahma_api/config.py`.
- [x] **Subagent SSE Observability**: Emitted structured subagent events (`subagent_started`, `subagent_tool`, `subagent_report`) in SSE streams (`agent/subagent.py`, `agent/loop.py`); ensured reports cross back on turn exhaustion or cancellation.
- [x] **Configurable Popup Auto-Dismiss**: Added user settings for task card & daily briefing auto-dismissal and timeout seconds in Settings Hub with separate bounded approval timeout (`core/popup_settings.py`, `core/ui_connectivity.py`).
- [x] **Concurrent Token Minting & Provider Usage Reporting**: Resolved token temp file collisions on Windows via unique temp paths, path normalization, and retry backoff (`brahma_api/auth.py`, `brahma_api/config.py`). Preserved token usage reporting from Gemini (`usage_metadata`) and OpenAI (`usage`) across passthrough and agent lanes (`agent/providers.py`, `llm_client.py`, `brahma_api/runner.py`).


---

- when using the api to call brahma, i have notice that: 1. the local model is polled only. no matter which provider gateway is set in the apps settings, or what the fallback sequence is set to. (i understand this might be the default configuration, but i'd like to know if we can make it respect the set up chose provider, and fallback sequence if any)  
  
  
2 the sessions doesn't have access to tools like websearch, (i tried the worker api tier) here is a log of our conversations:   
  
"hi how are you

Hey! I'm doing great, thanks for asking. 😊 Just here and ready to help you with whatever you need — whether it's coding, research, browser stuff, or anything else. What's on your mind today?

get the latest news about the coup of the constitutional tribunal in poland

I don't have live web access, so I can't pull the very latest headlines or confirm whether a coup-related move involving Poland's Constitutional Tribunal just happened. My knowledge has a cutoff, and I wouldn't want to give you outdated or made-up details.

If you'd like, I can:

•Summarize what I know from my training about Poland's Constitutional Tribunal (e.g., its role, past controversies, legal challenges) up to my last update.

•Help you draft a search query or set up a reminder to check current sources.

•Assist with anything else on your screen right now.

Just let me know how you'd like to proceed!

what tools do you have?

Here's a quick rundown of the tools I can use in this workspace:

**File &amp; text operations**

•`read_file` – read a specific file (line ranges optional)

•`write_file` / `edit_file` – create or modify files

•`grep_files` – search for patterns in files

•`glob_files` – find files matching a glob pattern

**Command execution**

•`run_command` – run shell commands (with timeout controls)

•`run_subagent` – delegate a focused task to a bounded child agent (read‑only tools only)

•`run_subagent_batch` – fire off several independent read‑only subagent tasks in parallel

**Project skills**

•`load_project_skill` – load a skill's full instructions (e.g., `api-mock-engine`, `browser-automation`, `code-review`, etc.) from the `.brahma/skills` folder

**Harness / MCP**

•Built‑in `agent/[mcp.py](http://mcp.py)` for MCP server communication (via `initialize`, `tools/call`)

•Workspace tools wired through `WorkspaceMCP` and `agent/[session.py](http://session.py)`

So I can read, edit, search, run scripts, spin up subagents, load domain‑specific skills, and talk to the MCP layer — all within the workspace and respecting the permission policy. Want me to kick off any of these?"  
  


I also tried the operator api tier with similar results. where the ai insists it doesn't have access to webtools.  
  
i might need this explained, as perhaps that is just the consequence of our configurations that we set up.



- there also might be an issue with the timeout limit we set on the local machine response time, in case of some cpu compute depending on the size of the ram, the model, and the context size. the inference time is longer than our limits. is it not possible to set those timeouts to none? and would there be detrimental consequences?
- i tried to get the ai to Spin up a subagent task "look up the latest news in poland about the coup of the constitutional tribunal" (this is through the openai compatible api call to Brahma) i saw the computer do 2 turns of inference, but nothing was reported back. this might be because of our timeout limitations, or worse, because the subagent doesn't feedback the result to brahma, in order for it to respond. again i'm only speculating about the cause. (oh i also suspect it could be that permissions need to be granted, as they do in the main app, but that doesn't go through the api to the session, so it dropped the task as it was considered rejected)
- when the agent does task, there are popup windows on the gui, which is great, but they have an automatic timeout, can we either add a setting for the user to toggle the automatic count down, and set the timeout timer. sometimes its better to have all the popups visibile of what the agent did, and info it got.

---

## Handoff — API provider, web tools, timeouts, subagents, popups (approved 2026-11-04)

> **Status: approved in principle, NOT implemented.** No code has been changed. This section is the pickup point for a later agent. It records the traced root cause (`file:line`), the agreed direction, concrete tasks, and acceptance checks.

### Environment and gates

- Use `py -3.11` for every gate; the repo `.venv` and default `python` are 3.14 without dev tools. Shell is PowerShell; do not chain with `&&`.
- If `py` is not on PATH in your shell, point at a Python 3.11 executable explicitly; do not run the gates on 3.12/3.14 (the dev tools may be missing).
- Full verify:
  ```powershell
  py -3.11 -m ruff check .
  $env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
  ```
- Fast agent subset:
  ```powershell
  py -3.11 -m pytest tests -q -k agent
  py -3.11 -m agent run "say hello" --provider stub --json --no-session
  ```

### Hard constraints (from `AGENTS.md`)

- `agent/` is stdlib-only at import: no PyQt, provider SDK, or Windows COM at module scope. Import `core.*` lazily inside a function (existing pattern in `agent/providers.py`).
- Every tool call goes through `AgentRuntime.execute`. Add a `ToolSpec`; never an `elif` branch in `core/tool_exec.py`.
- Never register a harness tool under a core meta-tool name (`load_skill`, `search_tools`, `recall_memory`, ...).
- `agent/__init__.py` stays empty.
- `agent_permissions.json` is user data; only `config/agent_permissions.example.json` is committed.
- Approvals are in-process only; never persist a clicked button.
- Keep the OpenAI transcript invariant: one `tool` message per `tool_calls` entry, in order.

### Suggested order

1. Timeout bug + API wall clock (items 3 and 4 improve together).
2. Provider resolution (item 1).
3. Native web tools (item 2).
4. Subagent observability (item 4).
5. Popup settings (item 5, pure UI/config).

---

### 1. API ignores the configured provider and fallback chain

**Root cause**

- `brahma_api/runner.py:172` (and `:285`) picks `principal.token.provider or self.config.default_provider`.
- `brahma_api/config.py` defaults `default_provider` to `"auto"`.
- `agent/providers.py:76-97` resolves `"auto"` as local -> custom -> openrouter. It never reads `default_ai_provider`, never calls `core.provider_router.chain()`, and never checks enabled flags.
- The GUI text path does honour all three (`core/text_turn_controller.py:1659-1668`), so the surfaces diverge.
- There is **no failover**: the runner builds one completer; `ProviderUnavailable` fails the request.
- `agent/providers.py` has adapters only for `local`, `custom`, `openrouter`, `stub`. There is **no `gemini` adapter**. Gemini text today is `main._gemini_text_reply`, which is text-only and not tool-calling capable.

**Decision (approved)**

- The API must honour the app's `default_ai_provider` and the enabled provider chain instead of always resolving local-first.
- Implement true mid-run failover as a **separate** follow-up; first-call failover is in scope, failover after tool calls is not.
- Add a Gemini harness adapter if feasible without breaking the stdlib-only rule; otherwise document that Gemini is unsupported on the API lane and fail with a clear message.

**Tasks**

- [ ] Add a helper in `agent/providers.py` (lazy `core` import) that returns the preferred + fallback order: start from `default_ai_provider`, then `provider_router.chain(only_configured=True)`, de-duplicated, mapped to harness adapter ids.
- [ ] Change `resolve_provider("auto")` (or the API runner) to use that order, not local-first. Keep `BRAHMA_AGENT_PROVIDER` as an override.
- [ ] Add `gemini` to `PROVIDERS` only if an adapter exists. Research the OpenAI-compatible Gemini endpoint (`/v1beta/openai/chat/completions`) and reuse `llm_client._openai_chat_completion`; do not import `google.genai` at module scope. If not feasible, raise `ProviderUnavailable("gemini is not available on the agent lane; pin local/custom/openrouter")`.
- [ ] Add first-call failover: build a small chain completer that tries the next adapter on `ProviderUnavailable` / connection errors only, before any tool call is emitted.
- [ ] Record the resolved provider + chain in the API audit row and response metadata.
- [ ] Document token-level `provider` pinning as the explicit override.

**Acceptance**

- With `default_ai_provider = "Custom"` and a custom endpoint configured, an API call uses custom even when a local server is running.
- Disabling a provider in Settings removes it from the API chain without a restart.
- Pinning `provider` on the token still wins.
- `py -3.11 -m pytest tests -q -k agent` passes, plus new tests under `tests/test_agent_provider_chain.py` and `tests/test_brahma_api_provider.py`.

**Files:** `agent/providers.py`, `brahma_api/runner.py`, `brahma_api/config.py`, `brahma_api/models.py` (docs), `tests/`.

---

### 2. API sessions have no web search / network tools

**Root cause**

- `agent/loop.py:231-235` builds the surface as `[spec for spec in all_specs() if "desktop" not in spec.tags]`.
- `web_search` is registered in `core/desktop_tools.py` with `DESKTOP_TAG`, so it is dropped for the API **and** the CLI.
- `agent/` cannot import `core/desktop_tools.py` (stdlib-only CI), so simply removing the filter does not give the harness a web tool.
- The `worker` profile sets `network: ALLOW` (`brahma_api/models.py:199-218`), but the harness has **no network-class tool**, so the permission is currently unexercised.
- The transcript where the model claimed `run_command` is confabulation: `worker` denies `exec`.

**Decision (approved)**

- Add harness-native `web_search` and `web_fetch` ToolSpecs in `agent/` using stdlib `urllib`, gated by the `network` class.
- Do **not** remove the desktop-tag filter as the fix; the desktop bridge is not the design.
- Allow `network` in the child subagent surface when the parent profile allows it.

**Tasks**

- [ ] Create `agent/web.py` (stdlib only) with `web_fetch` (URL -> text, size cap, timeout, redirect cap) and `web_search`.
- [ ] Search backend: make it configurable (`BRAHMA_SEARCH_BACKEND` / settings). Default to a keyless DuckDuckGo HTML parse; support Brave/Tavily/Serp with a key when configured. Return `title`, `url`, `snippet`.
- [ ] Register both as `ToolSpec`s with `network` class, `native`-style tags (not `desktop`), and bounded output (e.g. 8 KB) to protect the context window.
- [ ] Add SSRF/URL guard: block `file:`, `ftp:`, private/link-local hosts unless explicitly allowed; no credentials in logs.
- [ ] Ensure `AgentLoop` advertises them for `worker`/`operator` and for the CLI; keep `chat`/`reader` without them.
- [ ] Update `agent/subagent.py:select_tools` / `select_batch_tools` so a parent with `network` gives the child `web_search`/`web_fetch` (still no `run_subagent`, no writes beyond the existing read-only default).
- [ ] Update `docs/OPENAI-COMPATIBLE-API-DESIGN.md` §6.3 to say the `worker`/`operator` surface includes native web tools.
- [ ] Tests: `tests/test_agent_web_tools.py` with a local HTTP fixture (no real network), plus a surface test proving the desktop filter still hides `core` tools.

**Acceptance**

- A `worker` API token can answer "latest news ..." by calling `web_search` then `web_fetch` (fixture test) and the transcript contains the tool calls.
- `chat`/`reader` still see no web tools.
- `py -3.11 -m agent run "..." --provider stub` still works; `agent/` imports with only pytest installed.

**Files:** `agent/web.py` (new), `agent/loop.py`, `agent/subagent.py`, `agent/spec.py`/registry, `brahma_api/models.py` (docs), `docs/OPENAI-COMPATIBLE-API-DESIGN.md`, `tests/`.

---

### 3. Local inference timeouts

**Root cause / current values (`core/local_brain.py`)**

| Knob | Value |
| --- | --- |
| `fast` response / first token | 900 s |
| `slow` response / first token | 1500 s |
| `STREAM_IDLE_TIMEOUT` | 60 s |
| `PROBE_TIMEOUT` | 3 s |
| API `limits.wall_clock_s` | **300 s** |

- **Real bug:** `_get_json` (`core/local_brain.py:563-571`) does `with self._open(req, PROBE_TIMEOUT) as resp:`, but `_open` returns a `str` (`:398-404`). The probe raises every time, is cached as `None`, and `"auto"` therefore always resolves to `slow`. `fast` can never be earned.
- **API wall clock is stricter than the model budget:** 300 s cancels the run before 900/1500 s matters. Cancellation is only observed at turn boundaries, so one long call overruns it and the run ends `cancelled` with little/no text.
- Once streaming, `STREAM_IDLE_TIMEOUT = 60` aborts a legitimate CPU-bound pause between chunks.

**Decision (approved)**

- Keep a finite bound, but make it configurable. Do **not** pass `timeout=None` to the socket; add an explicit "unlimited" only at the outer layer (API wall clock / GUI ladder deadline) with a warning.
- Fix the probe bug.
- Raise or make per-token the API wall clock.

**Tasks**

- [ ] Fix `_get_json`: `body = self._open(req, PROBE_TIMEOUT); return json.loads(body)`.
- [ ] Make the four timeout values settings-backed (`local_response_timeout_s`, `local_first_token_timeout_s`, `local_stream_idle_timeout_s`) with the current values as defaults and validation (>= 1 or "unlimited").
- [ ] Scale `STREAM_IDLE_TIMEOUT` with the active profile instead of a flat 60 s.
- [ ] Add an explicit "unlimited" sentinel (e.g. `0` or `"none"`) only for the outer bound; document that a wedged server then holds a thread and blocks failover.
- [ ] Raise `brahma_api` `limits.wall_clock_s` (e.g. 1800) or make it per-token; validate in `brahma_api/config.py`.
- [ ] Ensure the watchdog can interrupt between chunks if feasible; if not, document that cancellation is turn-boundary only.
- [ ] Tests: probe parsing, profile resolution (auto -> fast when VRAM-resident), settings validation, and an API run that survives >300 s in a fake clock.

**Acceptance**

- `local_timeout_profile: "auto"` can resolve to `fast` when `/api/ps` reports full GPU offload.
- A configured 2000 s local budget is not cut short by a 300 s API default.
- Setting an unlimited outer bound is explicit and logged.

**Files:** `core/local_brain.py`, `brahma_api/config.py`, `brahma_api/runner.py`, `docs/USER-GUIDE.md`, `tests/`.

---

### 4. `run_subagent` appears to return nothing over the API

**Root cause**

- The report **is** returned: `agent/loop.py:597-607` turns the child's final text into tool output, and `_run_one` catches child exceptions. This is not a feedback bug.
- The child default surface is read-only non-desktop tools (`agent/subagent.py:select_tools`), so it has no web search.
- `brahma_api` `wall_clock_s = 300`; cancellation is turn-boundary only, so a long child ends the run `cancelled`.
- `stream_mode = "final"` means nothing is emitted until the loop finishes, so the two inference turns are invisible.
- `X-Brahma-Approval: deny` is the default; any `ask` becomes a denial unless the token has `approve` scope and the caller sends `gui`.
- Child turn budget is clamped to 12; a parent can run out before it summarizes.

**Decision (approved)**

- Make subagent start/report events observable in SSE.
- Give the child `network` when the parent has it.
- Make the wall clock configurable and raise the default.
- Document the deny default.

**Tasks**

- [ ] Emit structured subagent events (`subagent_started`, `subagent_tool`, `subagent_report`) through the loop `on_event` sink so SSE can show progress.
- [ ] Include the child report in the parent tool message even when the child hit its turn budget (mark it truncated).
- [ ] If the parent is cancelled after a child already produced a report, return that report instead of an empty `cancelled` body.
- [ ] Allow `network` in `select_tools` when the parent profile allows it (depends on item 2).
- [ ] Add an API config knob for wall clock per token/profile; document `X-Brahma-Approval: gui` + `approve` scope.
- [ ] Tests: child report crosses back; child exception becomes tool text; cancelled-after-report returns text; SSE emits subagent events.

**Acceptance**

- A subagent call shows at least `subagent_started` and `subagent_report` in SSE.
- A subagent that searches the web can return sources (once item 2 lands).
- A 300 s+ child does not silently produce an empty response.

**Files:** `agent/loop.py`, `agent/subagent.py`, `brahma_api/runner.py`, `brahma_api/server.py`, `docs/OPENAI-COMPATIBLE-API-DESIGN.md`, `tests/`.

---

### 5. GUI popups auto-dismiss

**Root cause — three independent timers**

| Popup | Where | Behaviour |
| --- | --- | --- |
| Task card ("Task Workspace" / "Task Complete") | `core/ui_chat.py:1699`, `:2224`; reset at `:304` | Hides after **4 s**, resets workspace after **5 s** |
| Daily briefing overlay | `core/ui_overlays.py:276`, `:616-621` | **30 s** countdown, then closes |
| Approval banner | `core/confirm.py:48`, `:159`; API passes `approval_timeout_s` (60 s) | Times out -> **deny** |

**Decision (approved)**

- Add one Settings group for cosmetic popups (task card + briefing): auto-dismiss on/off and seconds, or "keep until next task".
- Keep the approval prompt on a **separate** setting with a sane cap; never default to infinite (security-relevant).

**Tasks**

- [ ] Add settings: `popup_auto_dismiss_enabled` (bool), `popup_dismiss_seconds` (int, e.g. 4/30 defaults per surface), `approval_timeout_seconds` (capped, e.g. 10-600).
- [ ] Wire the task-card hide/reset (`ui_chat.py:304`, `:1699`, `:2224`) to the setting; "keep" leaves the card until the next task or manual clear.
- [ ] Wire `DailyBriefingOverlay._remaining_secs` / `_tick` (`ui_overlays.py:276`, `:616`) to the setting; hide the countdown label when disabled.
- [ ] Wire `core/confirm.TIMEOUT_SECONDS` and `ask_blocking_choice(timeout=...)` to the approval setting; the API keeps its own `limits.approval_timeout_s` but clamps to the same cap.
- [ ] Add the controls to the Settings Hub with labels that say which popups they affect.
- [ ] Tests: settings round-trip; a disabled timer does not call `hide()`; approval still expires at the cap.

**Acceptance**

- A user can turn off auto-dismiss and keep the task card / briefing visible.
- Approval prompts still cannot hang forever by default.
- No timer fires when the setting is off.

**Files:** `core/ui_chat.py`, `core/ui_overlays.py`, `core/confirm.py`, `core/ui_connectivity.py` (Settings Hub), `memory/config_manager.py` defaults, `brahma_api/config.py`, `tests/`.

---

### Still-open product choices (revisit only if needed)

- **Search backend:** keyless DuckDuckGo by default vs. a keyed Brave/Tavily/Serp backend. Approved direction: configurable, keyless default.
- **"Unlimited" timeouts:** approved only at the outer bound, never on the socket.
- **Popup scope:** approved task card + briefing toggle; approval separate and capped.

### What "done" looks like

- All five items have tests and the full suite passes on 3.11 with `QT_QPA_PLATFORM=offscreen`.
- `docs/OPENAI-COMPATIBLE-API-DESIGN.md`, `docs/USER-GUIDE.md`, and `docs/ARCHITECTURE-SPEC.md` reflect the new behaviour.
- No new `elif` in `core/tool_exec.py`; no PyQt/SDK import at `agent/` module scope; no persisted approvals.
- The user-notes bullets above can be ticked with a one-line summary each.  
  
