# Brahma Evo — Routing Audit & Remediation Plan

**Status:** Design record. No code changed by this batch.
**Created:** 2026-11-05
**Scope:** every mechanism that decides what happens to a user utterance or decides which tools a model can see — the pre-LLM command handlers, the prompt tiers, the tool index, the skill catalogues, and the dispatch boundary.
**Trigger:** ordinary requests (`"Make a backup of my photos"`, `"Track my parcel"`) are answered as smart-home failures.
**Audience:** the engineer changing any one of these layers next. Several of them look independent and are not.

> Line numbers are evidence, not contracts. If code and this document disagree, the code is current and this document is a bug to fix.

---

## 0. How to read this document

Three labels are load-bearing and match `docs/ARCHITECTURE-SPEC.md`:

| Word | Meaning here |
|---|---|
| **Built** | Wired into a normal user surface and exercised by tests. |
| **Built-Partial** | Wired, but with a documented mismatch between what it claims and what it does. |
| **Dormant** | Present in the tree, importable maybe, but not on any live path. Treated as a proposal, not a router. |

§1 is the finding. §2 defines the two routing planes so the layers stop being confused with each other. §3 is the inventory. §4 is the per-finding detail. §5 lists the invariants a fix must not break. §6 is the phased plan. §7 is open questions. §8 reproduces the bug.

---

## 1. Executive summary

The smart-home problem is **not** the model misreading intent. It is a deterministic substring matcher that runs **before** the model, decides the turn is a device command, and then returns `True` even when the command fails — so the turn can never fall through to the model.

Three properties combine into the bug:

1. **Substring matching without word boundaries.** `core/device_flow.py:39-45` matches `"ac"`, `"hall"`, `"fan"`, `"light"`, `"plug"`, `"office room"` anywhere inside another word. `"backup"`, `"track"`, `"practice"`, `"exact"`, `"react"`, and `"contracts"` all contain `ac`; `"challenge"` and `"shall"` contain `hall`; `"fantastic"` contains `fan`; `"highlight"` contains `light`.
2. **The predicate is the only gate.** Once it returns `True`, `execute_command` runs. With zero devices it raises, and the `except` branch at `core/device_flow.py:83-102` **speaks the error and returns `True`**. There is no path back to the LLM.
3. **The model never sees a rule that would have prevented it.** The per-domain routing table lives below the `--- MANUAL ---` marker in `core/prompt.txt:70`, and the default prompt tier is `lite` → core only (`core/prompt_parts.py:105-125`). The sentence *"Smart home: only when the user names a smart-home target"* (`core/prompt.txt:117`) is therefore not resident, and would not matter anyway because the hijack happens before the model is called.

Verified by execution against the real `DeviceFlow` handler with a fake host and zero devices: **12 of 19 ordinary prompts are hijacked**. Full transcript in §8.

The same substring habit recurs in the task-plan preview (`main.py:_build_task_plan`), in the mobile exclusion guard inside the same smart-home handler, and in several fast-path helpers in `core/text_turn_controller.py`. Fixing only the word list would leave the preview mislabeling `"Mushroom risotto recipe"` and `"Open Microsoft Office"` as smart-home, and would leave the voice tool surface cold-starting forever because of a separate attribute bug.

**Recommendation:** treat this as a routing-layer problem, not a word-list problem. Phase 0 is a narrow, testable fix to the smart-home predicate. Phases 1–4 make the other layers agree with it.

---

## 2. Two routing planes (do not conflate them)

Brahma has **two independent decisions** per turn, taken at different times by different code:

```
PLANE A — utterance routing (before/around the model)
  user text
    ├─ explicit slash commands            /sessions /resume /new
    ├─ plugin hook                        on_text_command
    ├─ modal reply modes                  reply / IG reply / email
    ├─ deterministic fast paths           email, calendar, volume, open-app, brightness,
    │                                     RAM, battery, kill, diagnostics, heal, …
    ├─ skill-creation + dynamic skills    regex / manifest triggers
    ├─ device handlers                    Brahma Connect, smart home   ← P0 lives here
    ├─ screen capture path
    └─ provider dispatch                  local / OpenRouter / Gemini Live
                                          (only now does a model see the turn)

PLANE B — tool-surface routing (what the model is allowed to see/call)
  catalogue (native + dynamic + MCP + skills)
    ├─ prompt tier                         core vs core+manual
    ├─ tool index                          pins + meta tools + retrieval
    ├─ intent hints / learned pins         per-connection extras
    ├─ skills library                      search_skills / load_skill
    └─ execution boundary                  ToolExecutor → AgentRuntime.execute
                                          → ToolSpec handler
```

Plane A can prevent Plane B from ever running. That is exactly this bug: the smart-home handler is in Plane A, and `smart_home_control` is also advertised in Plane B — so there are **two** ways to reach the same capability, with different failure behavior.

**Rule for the remediation:** a deterministic Plane-A handler may only claim a turn it can actually serve, and must have a defined fall-through when it cannot. "Confidently matched but no device" is a *serviceable* claim (speak the no-device message). "Matched by accident" is not.

---

## 3. Inventory of routing layers

Ordered by when they run on the text path. `file:symbol` is authoritative.

### 3.1 Plane A — utterance routing

| # | Layer | Entry point | Match method | State | Risk |
|---|---|---|---|---|---|
| A1 | Agent session commands | `core/text_turn_controller.py:39` `handle_agent_session_command` | anchored prefix | Built | Low |
| A2 | Plugin hook | `core/text_turn_controller.py:139-145`, `plugin_manager.py:100` | plugin-defined, `True` = consumed | Built | Medium (third-party) |
| A3 | Modal reply modes | `core/text_turn_controller.py:146-158` | state flag | Built | Low |
| A4 | Offline/online mode | `core/text_turn_controller.py:160-194` | substring phrases | Built | Low |
| A5 | Skill-creation regex | `main.py:491` `_extract_skill_creation_goal` → `_forge_skill` | anchored regex (`:495-499`) | Built | Medium |
| A6 | Dynamic skill match | `core/text_turn_controller.py:228-271` → `core/dynamic_registry.py:393` | token/stem score over manifest triggers | Built | Medium |
| A7 | Email / calendar | `core/text_turn_controller.py:273`, `597`, `639` | substring phrase | Built | Low–Medium |
| A8 | Presentation / spreadsheet / website / code | `core/text_turn_controller.py:402`, `444`, `510` + `main.py:430-489` | substring words | Built | Medium |
| A9 | System fast paths (RAM, battery, brightness, kill, diagnostics, heal, rollback, learn) | `core/text_turn_controller.py:728-965` | substring words | Built | Medium |
| A10 | Volume | `core/text_turn_controller.py:968-1014` | substring phrases | Built | Low |
| A11 | Open app | `core/text_turn_controller.py:1055` | anchored-ish phrase | Built | Medium |
| A12 | Instagram source branch | `core/text_turn_controller.py:1085` | `source` | Built | Low |
| A13 | Meeting-mode stop | `core/text_turn_controller.py:1091` | exact set | Built | Low |
| A14 | Attention response | `core/text_turn_controller.py:1094` → `core/attention_flow.py:125` | word-in-text | Built | Medium |
| A15 | Task-plan **preview** | `core/text_turn_controller.py:1097` → `main.py:670` | substring words | Built | **High (cosmetic but visible)** |
| A16 | Brahma Connect | `core/text_turn_controller.py:1101` → `core/device_flow.py` | substring + device lookup | Built | Medium |
| A17 | **Smart home** | `core/text_turn_controller.py:1105` → `core/device_flow.py:24` | **substring words** | Built | **Critical** |
| A18 | Screen request | `core/text_turn_controller.py:1108` → `main.py:588` | phrase + word-set | Built | Medium |
| A19 | Provider dispatch | `core/text_turn_controller.py:1155-1173` | config/state | Built | Low |
| A20 | Intent dispatcher (3-tier) | `core/intent_dispatcher.py` | regex → cache → LLM | **Dormant** | None today; trap for the fix |
| A21 | Execution reflection | `core/execution_reflection.py` | post-exec verification | **Dormant** | None today; trap for the fix |

A20 is imported by tests only (`tests/test_intent_dispatcher.py`, `test_flowstate.py`, `test_game_sentinel.py`, …). Its composite-goal branch imports `core.omni_planner` (which does not exist) inside a `try/except`, so that branch silently does nothing rather than raising. It is **not** on the GUI, CLI, or voice path. It must not be revived as part of this fix without an explicit decision (§7).

A21 has no caller in the app. It is a post-execution idea, not a router; note it so a future "add reflection" batch does not accidentally become a second dispatch path.

### 3.2 Plane B — tool-surface routing

| # | Layer | Entry point | Method | State | Risk |
|---|---|---|---|---|---|
| B1 | Static declarations | `main.py:TOOL_DECLARATIONS` (54 entries) + harness specs appended at `main.py:2171-2180` | literal list | Built | Medium |
| B2 | Prompt tiers | `core/prompt_parts.py:105`, `main.py:221`, `text_turn_controller.py:1311` | core / core+manual | Built | **High: routing table absent by default** |
| B3 | Tool index retrieval | `core/tool_index.py:1326` `resolve` | pinned + extras + FTS5/embedding RRF | Built | Medium |
| B4 | Cold-start order | `core/tool_index.py:1432` `_cold_start_order` | native/skill first, then MCP | Built | Medium |
| B5 | Intent hints | `core/tool_bridge.py:90` `INTENT_HINTS` | intent keyword → pins | Built-Partial | Medium: no smart-home hint exists |
| B6 | Learned pins | `core/tool_bridge.py:98-105`, `build_session_payload:458` | recently discovered names | Built | Low |
| B7 | Skills Library catalogue | `core/skills_library.py:814` `search` | weighted token overlap | Built | Low |
| B8 | Project skills catalogue | `agent/skills.py` | name+description resident, body on demand | Built (harness) | Low |
| B9 | Dynamic registry declarations | `core/dynamic_registry.py:362` | active manifests | Built | Low |
| B10 | MCP tool registration | `agent/mcp.py`, `MCPSession` | `mcp_<server>_<tool>` ToolSpecs | Built | Low |
| B11 | Voice session payload | `core/session_lifecycle.py:484-513` | index resolve at connect | Built-Partial | **High: intent always empty** |
| B12 | Local model surface | `core/local_brain.py:509` `resolve_tools` | same bridge | Built | Low |
| B13 | Execution boundary | `core/tool_exec.py:74` → `agent/runtime.py:383` | policy → approval → handler | Built | Low |

### 3.3 Execution-side routing (inside a tool call)

| # | Layer | Entry point | Note |
|---|---|---|---|
| C1 | ToolExecutor | `core/tool_exec.py:74-144` | spec → runtime → meta-tools → dynamic skills |
| C2 | AgentRuntime | `agent/runtime.py:383-478` | **single permission boundary**; do not bypass |
| C3 | Meta-tools | `core/tool_bridge.py:574` `is_meta_tool` | `search_tools`, `call_tool`, `get_skill_manual`, `get_tool_surface`, … |
| C4 | Smart-home service | `smart_home/service.py:176` `execute_command` | action guess + target selection |
| C5 | Brahma Connect gateway | `brahma_connect/gateway/command_router.py:18` | target resolve, offline check |
| C6 | Agent delegation | `core/agent_delegation.py` | voice/GUI → background AgentLoop |
| C7 | Subagents | `agent/subagent.py` | child loop, filtered surface |

---

## 4. Findings

### R1 — Critical: the smart-home predicate is a substring trap (`core/device_flow.py:24-102`)

```python
smart_home_words = (
    "fan", "fans", "light", "lights", "lamp", "plug", "socket", "outlet", "bulb",
    "kasa", "atomberg", "bedroom", "living room", "kitchen", "office room",
    "balcony", "bathroom", "hall", "dining", "smart home", "smart-home",
    "ac", "air conditioner", "thermostat"
)
has_smart_word = any(word in normalized for word in smart_home_words)
```

`normalized` lowercases and strips punctuation but keeps letters, so `word in normalized` is an unanchored substring test. Two-letter and common-word entries are especially dangerous:

- `ac` → backup, track, practice, exact, react, contracts, …
- `hall` → challenge, shall, …
- `fan` → fantastic, …
- `light` → highlight, …
- `plug` → plug-in, …
- `room` is only present as `"office room"` here, but the *preview* (R2) uses bare `room`.

Measured: **12/19** ordinary prompts hijacked, including every collision above except the preview-only `room` case (§8).

Two aggravating factors:

- **Failure returns `True`.** `except Exception` at 83–102 speaks the error and returns `True`. A false positive therefore cannot degrade into a normal answer; it becomes a dead end. This is what the user experiences as "the assistant thinks everything is a light bulb."
- **The no-device case is not distinguished from a wrong guess.** `smart_home/service.py:181-183` raises `"No smart home devices are connected yet."` That message is correct *for a real smart-home request*. It is nonsense for "Track my parcel".

Also inside the same function:

- **Mobile guard is a substring test too** (`core/device_flow.py:28-33`): `"phone"` matches `"microphone"`, `"tablet"` matches any word containing it, and `"my phone"` etc. are redundant with `"phone"`. The guard's *intent* is right; its mechanism is the same bug in the opposite direction (false negatives for real smart-home requests that mention a phone-hosted plug, and accidental negatives for unrelated text).
- **PC-brightness guard** (`:36`) is a substring conjunction; it works today because `"brightness"` is specific, but it inherits the same fragility (`"pc"` inside another token).

### R2 — High: the task-plan preview uses a different, larger word list (`main.py:670-726`)

`_build_task_plan` runs on every text turn at `core/text_turn_controller.py:1097` and immediately paints a plan in the HUD. Its smart-home branch is:

```python
("fan", "light", "plug", "kasa", "atomberg", "smart home", "home device",
 "room", "bedroom", "living room", "kitchen", "office", "bathroom", "balcony")
```

This is a **second, divergent routing list**. Bare `"room"` and `"office"` are not in `device_flow`'s list, so the HUD and the handler disagree:

- `"Mushroom risotto recipe"` → preview says *"Identify the smart-home device or room"* (matches `room` inside `mushroom`), then the handler correctly declines and the LLM answers. The user sees a smart-home plan for a recipe.
- `"Open Microsoft Office"` → preview says smart-home (matches `office`), handler declines.
- `"Turn off the bedroom light"` → both agree (this is the one case the duplication gets right).

Verified by executing `main._build_task_plan` with Qt offscreen (§8). A fix that only touches `device_flow.py` leaves this visible inconsistency, which is why R2 is in scope.

Note also the ordering hazard inside `_build_task_plan`: `"word"`, `"document"`, `"report"` are checked before the website branch, and `"call"` is in the screen/camera branch. This function is a stack of substring tests, so any new domain added later inherits the same class of bug.

### R3 — High: the model is never told the smart-home rule (default tier is `lite`)

- `core/prompt.txt:115-118` contains the correct policy: *PC is the default; smart home only when the user names a smart-home target; phone only when the user names it.*
- The marker `--- MANUAL ---` is at `core/prompt.txt:70`, so all of §115-118 is manual-tier only.
- `core/prompt_parts.py:105-125` defaults `prompt_tier` to `lite`, which returns `TIER_CORE`. `main.py:221` and `core/text_turn_controller.py:1311` only append the manual when tier is `full`.
- Therefore the default text/voice model **never sees the device routing table** unless it calls `get_tool_surface`.

This is a latent bug independent of R1: even after the predicate is fixed, the model has no resident statement that PC is the default. If a user says "turn off the lights" on a PC with no smart home, the model should be able to answer sensibly rather than guess. Add a short resident device-default line to the core tier, and keep the long table in the manual.

### R4 — High: the voice tool surface always cold-starts (`core/session_lifecycle.py:490`)

```python
last_intent = (getattr(self, "_last_user_text", "") or "")
...
payload = tool_bridge.build_session_payload(last_intent)
```

`self` is `SessionLifecycle`; `_last_user_text` is only ever assigned on the **host** (`core/audio_io.py:220` sets `self._host._last_user_text`; `main.py:2239` initializes it). `SessionLifecycle` never assigns its own attribute, so `last_intent` is always `""`.

Consequence: the connect payload always takes `ToolIndex.resolve`'s cold-start branch (`tool_index.py:1407-1413`, `_cold_start_order:1432`). Retrieval by intent never happens for the voice socket. This is a Plane-B bug, but it matters here because it means *retrieval cannot compensate* for a missing resident routing rule on the voice path.

There is also a timing question: a Live socket is connected **before** the first utterance, so there may be no intent at connect time by definition. The fix is therefore not just "read the right attribute"; see §7 Q4.

### R5 — Medium: `smart_home_control` is advertised and executable with zero devices

- It is in `TOOL_DECLARATIONS` at position 19 of 54 (`main.py`, `"smart_home_control"`), with capability `("device", False)` and no `requires_approval` (`core/desktop_tools.py:99`).
- Its handler `_smart_home_control` (`core/desktop_tools.py:971-983`) calls `execute_command` unconditionally; with no devices that raises, and the exception becomes tool text.
- It is **not** cold-start advertised at the default budget: budget 24 − 5 pinned (`undo`, `recall_memory`, `read_file`, `edit_file`, `run_command`) − 8 meta tools = **11 retrieval slots** (`tool_index.py:1386`, `1397`). `smart_home_control` is the 17th non-pinned native declaration, so it is outside the cold-start 11.
- But it becomes reachable three ways: `search_tools` retrieval, `INTENT_HINTS` (no smart-home hint exists today, but adding one would pin it), and `pinned_extra` learned names after one use. On a machine with no devices, all three are dead ends.

The correct gate is availability, not vocabulary: if `device_count() == 0`, the tool should either not be advertised or be advertised with a declaration that says it currently has no targets. The same argument applies to `connect_*` when no phone is paired.

### R6 — Medium: the dynamic skill registry is a second pre-LLM matcher (`core/text_turn_controller.py:228-271`)

`DynamicToolRegistry.find_matching_skill` (`core/dynamic_registry.py:393-494`) scores the utterance against each skill's `triggers`, `aliases`, name and description, then executes on a match **before** the provider branch. It is a genuine router, not a tool the model chooses.

It is better behaved than the smart-home predicate: it uses tokenised words and stem scoring rather than raw substrings, and a miss returns `None`. But it has the same structural property — a match consumes the turn and there is no confidence threshold surfaced to the user. Any new deterministic router added in Phase 4 must use the same contract as this one: tokenised, scored, and able to decline.

### R7 — Medium: many fast-path gates use the same unanchored style

`core/text_turn_controller.py` is a long chain of `any(p in lower for p in (...))` checks (email, calendar, RAM, battery, brightness, kill, diagnostics, heal, rollback, learn-rule, volume, open-app). Most are protected by specific multi-word phrases, so the observed failure rate is low. They are still the same pattern that caused R1, and `main.py:_looks_like_code_request` is deliberately broad (`"build"`, `"write"`, `"app"`, `"project"`), so "write a light summary" can trip the code path if it gets past the smart-home gate.

Do **not** rewrite all of them in Phase 0. Do add a shared matcher helper in Phase 3 and convert the gates that are demonstrably ambiguous.

### R8 — Low: dormant systems that look like routers

- `core/intent_dispatcher.py` — 3-tier semantic dispatcher (regex → phrase cache → Gemini classifier). Imported only by tests. Its composite-goal branch imports a missing `core/omni_planner` inside a `try/except`, so it is inert rather than crashing. **Dormant.**
- `core/skill_discovery.py` — `LEARN_PATTERNS` and `SkillDiscovery.analyze_command`. Not imported anywhere; the live skill-creation regex is `main.py:_extract_skill_creation_goal`. **Dormant.** Do not wire this as a second skill router; extend the live one.
- `core/execution_reflection.py` — post-execution verification/healing. No caller. **Dormant.**
- `core/capabilities.py` — capability parsing/matching used by policy, grants, gateway auth and the secret broker. **Not an utterance router**; it is the vocabulary the permission boundary uses. Do not change it to fix R1.
- `core/provider_router.py` — chooses a model vendor, not a tool. Out of scope except that it is the last step of Plane A.

Including these here is deliberate: the user asked whether more routing systems exist. They do, but several of them are not running. A future batch that "turns on the intent dispatcher" would add a **fourth** pre-LLM matcher and make this bug class worse.

### R9 — Low: no regression test covers any of this

`tests/` has no `test_device_flow.py`, no `test_smart_home.py`, and no test for `_build_task_plan`. The smart-home tests that do exist cover **storage** and **styling** only (`test_smart_home_storage.py`, `test_smart_home_styles.py`); nothing tests `execute_command`, `_select_targets`, `_guess_action`, or the entry predicate. This is why the bug survived: the unit under test was the persistence layer, not the gate.

---

## 5. Invariants a fix must preserve

1. **One permission boundary.** Nothing in Plane A may execute a tool directly. If a fast path wants to act, it calls the same `ToolSpec` / `AgentRuntime.execute` route (or the service method the ToolSpec calls). `core/device_flow.py` is a *text front-end* to `SmartHomeService`, which is acceptable; it must not grow its own device transport.
2. **A confident match may consume the turn; an uncertain one may not.** The handler's return type should make that distinction explicit, not encode it as `True`/`False` around a `try/except`.
3. **A failed action must be reportable, not terminal-by-accident.** `return True` after a failure is only correct when the utterance was unambiguously a smart-home request. Otherwise it must return `False` so the normal answer path runs.
4. **The model's advertised surface and the pre-LLM gate must tell the same story about availability.** If `smart_home_control` is hidden because there are no devices, the text gate may still answer a confident device request with the no-device message — that is a service answer, not a transport. The two surfaces must never disagree about *whether devices exist*; they may disagree about *which surface handles the turn*.
5. **No new pre-LLM router without a decline path.** Tokenised input, a threshold, and a `None`/`False` return that lets the turn continue.
6. **The core prompt stays small.** Fix R3 with one or two resident lines, not by making the manual resident.
7. **Tests are the deliverable.** A word-list change without a corpus test will regress the first time someone adds a device type.

---

## 6. Phased remediation plan

### Phase 0 — Smart-home predicate and its tests (P0, small)

Files: `core/device_flow.py`, new `tests/test_device_flow_routing.py`.

1. Replace the substring predicate with an explicit matcher:
   - tokenise on word boundaries (reuse the normalisation already at `:25`);
   - **drop any vocabulary entry shorter than 4 characters** (`ac` is a preposition fragment, not a device noun; `fan` is kept because it is a whole token);
   - treat `light`/`lights`/`lamp`/`bulb`/`fan`/`plug`/`socket`/`outlet`/`thermostat` as device nouns only when they appear as whole tokens;
   - match rooms against the **live device list** (`name`, `room`) with whole-token equality, not substring;
   - accept the literal phrases `smart home` / `smart-home` as an explicit override;
   - require either (a) a known device/room token, or (b) a device noun **and** a control verb (`turn on/off`, `switch`, `set`, `dim`, `speed`, `increase`, `decrease`, `open`, `close`, `toggle`, `restart`), or (c) the explicit override.

   This last requirement is what kills `"Write a light summary"`: `light` is a noun, but `summary` is not a control verb and there is no known device.

2. Fix the mobile guard to whole tokens (`phone`, `mobile`, `android`, `tablet`, `flashlight`, `torch`), and keep `"brahma connect"` as a phrase.

3. Split confidence from execution:
   - `match(text, devices) -> Match | None` where `Match` carries `confidence` and `targets`;
   - `handle_smart_home_command` claims the turn only on a match;
   - on a service exception, return `True` **only** when confidence is high and the failure is a genuine device/service error; otherwise log and return `False`.

   Keep the host-facing signature `bool` at the `text_turn_controller.py:1105` call site so the seam does not spread.

4. Add a corpus test with three tables:
   - ordinary prompts that must **not** match (the 19 in §8, plus `"Open Microsoft Office"`, `"Mushroom risotto recipe"`);
   - real commands that **must** match (`"Turn off the bedroom light"`, `"Turn on the fan"`, `"Switch off the kitchen plug"`, `"Set the living room AC to 24"`, `"Turn off all devices"` — note this last one is currently **missed**, because the predicate has no `devices` token even though `smart_home/service.py:283` supports it);
   - mobile-target prompts that must not match.

**Acceptance:** 0/19 ordinary hijacks, 5/5 true positives, 0 mobile leaks; `py -3.11 -m ruff check .` and the new test file pass.

### Phase 1 — One source of truth for the preview (P1, small)

Files: `main.py`, optionally new `core/intent_hints.py`.

- Delete the smart-home branch from `_build_task_plan` and derive the plan from the same matcher used by `device_flow` (`match(...)`), or from a tiny shared `classify_domain(text, devices)` helper.
- Do not leave two word lists. If a shared helper is overkill for this batch, at minimum remove bare `"room"` and bare `"office"` and require word boundaries.
- Add tests for the two verified mislabels.

**Acceptance:** `"Mushroom risotto recipe"` and `"Open Microsoft Office"` produce the generic or productivity plan, never the smart-home plan.

### Phase 2 — Availability gating on Plane B (P1, medium)

Files: `core/desktop_tools.py`, `core/tool_index.py` / `core/tool_bridge.py`, `core/session_lifecycle.py`.

- Compute device availability once (`SmartHomeService.list_devices()` → count) and use it to gate `smart_home_control` advertisement/retrieval. Use the existing `ToolIndex.set_disabled` (`tool_index.py:1497`) rather than inventing a second filter, and recompute on device add/remove so a newly paired bulb becomes reachable without a restart.
- Same for `connect_*` when no phone is paired.
- Fix `core/session_lifecycle.py:490` to read `self._host._last_user_text` so the voice path is not permanently cold-start; then decide the timing question in §7 Q4 before relying on it.
- If a smart-home intent hint is added to `core/tool_bridge.py:90`, gate it on the same availability check.

**Acceptance:** on a zero-device machine, `search_tools("turn off the light")` does not return `smart_home_control`; on a machine with devices it does.

### Phase 3 — Resident device policy in the core prompt (P2, small)

Files: `core/prompt.txt`.

Add to the core tier (above `--- MANUAL ---`) two lines:

```
- The PC is the default target. Only use smart-home tools when the user names a
  smart-home device or room. Never route PC volume, brightness, or app commands
  to a device tool.
- If no smart-home devices are connected, say so; do not guess a device.
```

Keep the full routing table in the manual. Add a test that `load_prompt_tiers` puts the rule in the core half.

**Acceptance:** with `prompt_tier=lite`, the assembled system prompt contains the device-default rule.

### Phase 4 — Consolidation (P3, larger; needs a decision)

- Introduce one `RoutingDecision` shape (claim / decline / confidence / reason) shared by `device_flow`, `dynamic_registry`, and the fast paths. This is the only way the next domain avoids repeating R1.
- Decide the fate of `core/intent_dispatcher.py` and `core/execution_reflection.py`: wire one router (and delete the duplicate), or delete both. Do not leave a dormant third router that a future batch can switch on.
- Add a routing-catalogue test that asserts the number of pre-LLM consumers is the number the design says it is, and that every consumer has a decline test.

**Acceptance:** one documented list of Plane-A routers; no router without a negative test.

---

## 7. Open questions

1. **Should deterministic smart-home routing exist at all?** It is a good UX for "turn off the bedroom light" on a live voice session (no model round-trip). Recommendation: keep it, but narrow it to the Phase-0 predicate. If the owner prefers model-only routing, the fix is to delete the `text_turn_controller.py:1105` call and rely on `smart_home_control`; that is simpler but adds latency and depends on R3/R5 being fixed.
2. **What should "no devices connected" do?** (a) say so and stop, or (b) fall through to the model. Recommendation: (a) for a confident match, (b) for everything else. This is the core semantic decision of Phase 0.
3. **Should `_build_task_plan` guess at all?** It is a cosmetic preview that duplicates routing logic. Recommendation: derive from the matcher, or reduce it to the generic plan and let the real handler title the workspace.
4. **Voice intent at connect time.** A Live socket connects before the first utterance, so `last_intent` may legitimately be empty. Options: (a) keep cold-start and rely on `search_tools`; (b) reconnect/re-advertise after the first utterance; (c) seed the session from the last completed turn (`_last_user_text`). Recommendation: (a) plus the R3 core-prompt rule; treat (b) as a separate design because Gemini Live cannot swap tools mid-session.
5. **Do we want a shared `core/routing.py` chokepoint?** The repo's stated principle is "one door per concern." A single registry of Plane-A routers would make R1 impossible to repeat, but it is a larger change than the bug requires. Recommendation: Phase 4, with the `RoutingDecision` contract first.
6. **Dead code:** is `intent_dispatcher` an intended future (then it needs `omni_planner` and a decision to wire it), or abandoned (then delete it)? Leaving it is the worst option because it looks authoritative in the tree.

---

## 8. Reproduction appendix

Run from the repo root with Python 3.11. The probe imports the real handler; the full copy used for this audit is at `C:\Users\Admin\AppData\Local\Temp\opencode\routing_probe.py` and the corpus is reproduced below.

```python
# routing_probe.py (abridged; full copy in the batch's temp dir)
import sys
from pathlib import Path
sys.path.insert(0, r"C:\Users\Admin\Brahma-Ai-Evo")
from core.device_flow import DeviceFlow

class _UI:
    muted = True
    def update_task_workspace(self, **k): pass
    def write_log(self, *a, **k): pass
    def set_state(self, *a, **k): pass

class _SmartHome:
    def list_devices(self): return []
    def execute_command(self, text):
        raise ValueError("No smart home devices are connected yet.")

class _Host:
    def __init__(self):
        self._smart_home = _SmartHome()
        self.ui = _UI()
    def speak(self, *a, **k): pass

flow = DeviceFlow(_Host())
```

Actual output on 2026-11-05 (zero devices):

```
ordinary hijacked: 12/19
  HIJACK  'Make a backup of my photos'
  HIJACK  'Track my parcel'
  HIJACK  'Practice my Spanish'
  HIJACK  'What is the exact time'
  HIJACK  'React to this message'
  HIJACK  'Write a light summary'
  HIJACK  'Highlight the important lines'
  HIJACK  'Explain the challenge'
  HIJACK  'Shall I continue'
  HIJACK  'Show me a fantastic idea'
  HIJACK  'Click the plug-in settings'
  HIJACK  'Compare these two contracts'
true positives missed: 1/5
  MISS    'Turn off all devices'
mobile leaks: 0/3
```

The 12 failures are all one-line substring collisions: `ac` (backup, track, practice, exact, react, contracts), `light` (light, highlight), `hall` (challenge, shall), `fan` (fantastic), `plug` (plug-in).

Preview mislabels, executed with `QT_QPA_PLATFORM=offscreen`:

```
'Mushroom risotto recipe' -> Identify the smart-home device or room
'Open Microsoft Office'   -> Identify the smart-home device or room
'Compare these two contracts' -> Understand the command
'Track my parcel'             -> Understand the command
'Turn off the bedroom light'  -> Identify the smart-home device or room
```

Note the last two lines: the preview and the handler disagree in both directions. `device_flow` hijacks "Compare these two contracts" while the preview does not; the preview claims "Mushroom risotto recipe" while the handler does not. That is the concrete cost of two word lists.

---

## 9. File and symbol index

| Concern | Location |
|---|---|
| Smart-home entry predicate | `core/device_flow.py:24` `handle_smart_home_command`, words at `:39-45`, claim at `:57-58`, failure-swallow at `:83-102` |
| Smart-home service | `smart_home/service.py:176` `execute_command`; no-device at `:181-183`; `_guess_action:259`; `_select_targets:281` |
| Text turn order | `core/text_turn_controller.py:118` `on_text_command`; device gates at `:1101` and `:1105`; provider branch at `:1155` |
| Task-plan preview | `main.py:670` `_build_task_plan`; smart-home branch `:714` |
| Prompt tiers | `core/prompt_parts.py:105` `tier_for`; `main.py:221` `_prompt_tier`; `text_turn_controller.py:1311` `agentic_seed_messages` |
| Device policy text | `core/prompt.txt:115-118` (manual half, marker at `:70`) |
| Tool index | `core/tool_index.py:1326` `resolve`; budget math `:1386`, `:1397`; cold start `:1432` |
| Intent hints / payload | `core/tool_bridge.py:90`, `:458` `build_session_payload` |
| Voice payload bug | `core/session_lifecycle.py:490`; host assignment `core/audio_io.py:220` |
| Dynamic skill router | `core/text_turn_controller.py:228-271`; `core/dynamic_registry.py:393` |
| Native registry | `core/desktop_tools.py:971` `_smart_home_control`, `:1610` `register` |
| Execution boundary | `core/tool_exec.py:74`; `agent/runtime.py:383` `execute` |
| Dormant router | `core/intent_dispatcher.py` (missing `core/omni_planner`) |
| Dormant reflection | `core/execution_reflection.py` |
