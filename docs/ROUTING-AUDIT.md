# Brahma Evo — Routing Audit & Remediation Plan

**Status:** Design record. The original audit batch changed no code; remediation status per phase is recorded inline (§6).
**Created:** 2026-11-05
**Revised:** 2026-11-05 — Phase 0 now removes the pre-LLM smart-home claim instead of extending it; see §1 and §6.
**Revised:** 2026-11-05 — Phases 0–4 are implemented; Phase 4 is the authority registry in §6.
**Companion:** `docs/TOOL-RETRIEVAL-DESIGN.md` (short queries, same-word tools, server-aware retrieval).
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

The smart-home problem is **not** the model misreading intent. It is a deterministic matcher that runs **before** the model, decides the turn is a device command, and then returns `True` even when the command fails — so the turn can never fall through to the model. The substring bug is the mechanism; the structural cause is that a pre-LLM component is claiming **semantic authority** in an architecture whose routing inputs are live and variable.

Three properties combine into the observed failure:

1. **Substring matching without word boundaries.** `core/device_flow.py:39-45` matches `"ac"`, `"hall"`, `"fan"`, `"light"`, `"plug"`, `"office room"` anywhere inside another word. `"backup"`, `"track"`, `"practice"`, `"exact"`, `"react"`, and `"contracts"` all contain `ac`; `"challenge"` and `"shall"` contain `hall`; `"fantastic"` contains `fan`; `"highlight"` contains `light`.
2. **The predicate is the only gate.** Once it returns `True`, `execute_command` runs. With zero devices it raises, and the `except` branch at `core/device_flow.py:83-102` **speaks the error and returns `True`**. There is no path back to the LLM.
3. **The model never sees a rule that would have prevented it.** The per-domain routing table lives below the `--- MANUAL ---` marker in `core/prompt.txt:70`, and the default prompt tier is `lite` → core only (`core/prompt_parts.py:105-125`). The sentence *"Smart home: only when the user names a smart-home target"* (`core/prompt.txt:117`) is therefore not resident, and would not matter anyway because the hijack happens before the model is called.

Verified by execution against the real `DeviceFlow` handler with a fake host and zero devices: **12 of 19 ordinary prompts are hijacked**. Full transcript in §8.

**Why the word list cannot be rescued.** A better predicate is still a pre-LLM semantic router, and it cannot know the inputs that make this architecture work:

- which devices are actually connected, in which rooms, under which user-given names;
- which MCP servers are installed and what each one's "vault" means;
- which skills and orchestration paths are available;
- what the conversation just established.

"Confidently matched but no device" is not a service answer when the confidence came from the letters `a-c` inside "backup": it is a false positive with a polite error. An exhaustive library is not a fix either. Device types × rooms × verbs × brand names × languages × synonyms is unbounded, every future MCP/skill domain adds its own vocabulary, and each addition creates a new collision class. The list is not the interface.

**Recommendation — authority, not confidence.** The model becomes the router for every semantic action (smart home, email, calendar, apps, web, files). Pre-LLM code keeps only authority it can *prove*, not confidence it inferred:

| Allowed before the model | Why it is safe | Examples |
|---|---|---|
| **Explicit syntax** | The user used the control grammar; there is no semantic guess. | `/sessions`, `/resume`, `/new`, stop/cancel, modal reply modes |
| **Exact user-defined alias** | Normalised equality to a name the user configured. Not substring, not similarity. | A saved alias or an exact skill trigger |
| **State the system owns** | The system is the source of truth for the mode. | offline/online, meeting mode, attention reply window |
| **Verified live target** (optional, later) | A real device/entity resolves, the grammar is strict, and no other domain competes. If there is no live target, it is not a match. | `"turn off the bedroom lamp"` → exactly one live lamp |

Everything else — including smart home — is a **tool choice made by the model**, with availability reflected in the tool surface rather than in a word list. That removes all 12 hijacks by construction, not by enumeration, and it scales when the user installs a fifth MCP server or a new device brand.

The same substring habit recurs in the task-plan preview (`main.py:_build_task_plan`), in the mobile exclusion guard inside the same smart-home handler, and in several fast-path helpers in `core/text_turn_controller.py`. The preview should stop guessing domains entirely; the voice tool surface has a separate attribute bug (R4). The phased plan in §6 reflects the removal, not a patch.

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
    ├─ device handlers                    Brahma Connect, smart home   ← smart home removed in Phase 0
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

**Rule for the remediation — authority, not confidence.** A deterministic Plane-A handler may claim a turn only when it can prove one of four authorities:

1. **Syntax** — the user wrote the control grammar (slash command, explicit mode token).
2. **Exact alias** — the normalised text equals an alias the user configured (not a substring, not a fuzzy score).
3. **Owned state** — the system is the source of truth for the mode being changed.
4. **Verified live target** — an entity resolves uniquely *now* and the grammar is strict. No entity, no claim.

Similarity, token overlap, substring presence, and a language model's confidence are **not** authorities. A handler that cannot prove one of the four must decline (`False`/`None`) and let Plane B run. That single rule removes the smart-home class of bug without maintaining a vocabulary.

Consequence for the inventory below: the rows marked "substring words" are not all equal. Some are control-plane (A1, A3, A13) and stay. The semantic ones (A7–A11, A16–A18) are candidates to move behind the model over time; A17 is removed in Phase 0. Do not convert them all in one batch — the rule is the destination, the phases are the route.

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
| A6 | Dynamic skill match | `core/text_turn_controller.py:228-271` → `core/dynamic_registry.py:393` | token/stem score over manifest triggers | Built | Medium: allowed only as an exact alias; score-based claims should move behind the model |
| A7 | Email / calendar | `core/text_turn_controller.py:273`, `597`, `639` | substring phrase | Built | Low–Medium |
| A8 | Presentation / spreadsheet / website / code | `core/text_turn_controller.py:402`, `444`, `510` + `main.py:430-489` | substring words | Built | Medium |
| A9 | System fast paths (RAM, battery, brightness, kill, diagnostics, heal, rollback, learn) | `core/text_turn_controller.py:728-965` | substring words | Built | Medium |
| A10 | Volume | `core/text_turn_controller.py:968-1014` | substring phrases | Built | Low |
| A11 | Open app | `core/text_turn_controller.py:1055` | anchored-ish phrase | Built | Medium |
| A12 | Instagram source branch | `core/text_turn_controller.py:1085` | `source` | Built | Low |
| A13 | Meeting-mode stop | `core/text_turn_controller.py:1091` | exact set | Built | Low |
| A14 | Attention response | `core/text_turn_controller.py:1094` → `core/attention_flow.py:125` | word-in-text | Built | Medium |
| A15 | Task-plan **preview** | `core/text_turn_controller.py:1097` → `main.py:670` | substring words | Built | **High: a second, divergent router with no authority** |
| A16 | Brahma Connect | `core/text_turn_controller.py:1101` → `core/device_flow.py` | substring + device lookup | Built | Medium |
| A17 | **Smart home** | `core/text_turn_controller.py:1105` → `core/device_flow.py:24` | **substring words** | Built | **Critical: remove the claim; model chooses `smart_home_control`** |
| A18 | Screen request | `core/text_turn_controller.py:1108` → `main.py:588` | phrase + word-set | Built | Medium |
| A19 | Provider dispatch | `core/text_turn_controller.py:1155-1173` | config/state | Built | Low |
| A20 | Intent dispatcher (3-tier) | `core/intent_dispatcher.py` | regex → cache → LLM | **Dormant** | None today; trap for the fix |
| A21 | Execution reflection | `core/execution_reflection.py` | post-exec verification | **Dormant** | None today; trap for the fix |
| A22 | **Model tool choice** (the intended semantic router) | provider tool-calling → `AgentRuntime.execute` | model reads the live surface, chooses, asks when ambiguous | Built | Low; the target for A7–A11/A16–A18 |

A17 is the only Plane-A row this batch removes. A22 is not a new layer to build — it is the route that already exists (`TOOL_DECLARATIONS` → provider tool call → `AgentRuntime.execute`), now expected to be the *only* route for semantic actions. The index and the prompt are what A22 needs in order to choose well; that is the subject of `docs/TOOL-RETRIEVAL-DESIGN.md`.

**Phase 4 outcome (2026-11-05).** `core/routing.py` now holds the authoritative inventory (`ROUTERS`, 23 records, with `status` = live / converted / delegated / dormant / removed). It covers the pre-LLM consumers in the table above and adds the three sub-branches the audit treated inline (A7b briefing, A9b auto-heal, A16b active-device rewrite); A19 (provider dispatch) is routing *after* a tool is chosen and A22 is the destination, so neither is a Plane-A claim. A15 and A17 are removed; A6 and A11 are converted to exact-alias claims; A16 requires a verified target; A20/A21 stay dormant and a test fails if app code imports them. The line numbers above are evidence from the audit, not contracts — resolve an entry through `ROUTERS` when they disagree.

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

### R1 — Critical: a pre-LLM semantic claim has no authority (`core/device_flow.py:24-102`)

```python
smart_home_words = (
    "fan", "fans", "light", "lights", "lamp", "plug", "socket", "outlet", "bulb",
    "kasa", "atomberg", "bedroom", "living room", "kitchen", "office room",
    "balcony", "bathroom", "hall", "dining", "smart home", "smart-home",
    "ac", "air conditioner", "thermostat"
)
has_smart_word = any(word in normalized for word in smart_home_words)
```

This is a *lexical* gate pretending to be a semantic one. It does not read the device inventory, the MCP surface, the skill catalogue, or the conversation; it reads letters. In an agentic system that is not a smaller version of the model's decision — it is a different decision with less information and no way to ask. The substring detail is just how the mismatch becomes visible.

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

### R1a — Why an exhaustive smart-home command library is not the fix

The obvious-looking repair is to enlarge the word list until every device, room, verb, brand and synonym is covered. It fails for four reasons, and each one is structural rather than a matter of effort:

1. **The vocabulary is unbounded.** Device types (bulb, strip, fan, AC, plug, curtain, lock, sensor, IR blaster), rooms (user-named: "the small room", "Ammi's room"), verbs and phrasings ("kill the lights", "make it warmer", "is the bedroom fan on?"), brand names, and two or more languages. Every new device or integration adds terms; every added term adds collisions with ordinary English (`fan`/`fantastic`, `plug`/`plug-in`, `ac`/`backup`).
2. **The matcher still lacks the facts that decide the turn.** Even a perfect lexicon cannot know whether a device exists, whether the name is unique, or whether "office" means a room or Microsoft Office. It has to guess, and its guess is unrecoverable because it returns `True`.
3. **It does not compose with the rest of the architecture.** The same utterance may belong to an MCP server (notes, calendar, browser), a skill, or the orchestrator. A smart-home lexicon cannot see those candidates, so it cannot know it is stealing the turn from a better owner.
4. **It makes the next domain worse.** Each domain that copies this pattern adds another pre-LLM list, another precedence order, and another set of collisions. The maintenance cost grows quadratically while the model's actual capability grows independently.

**The replacement is not a better list; it is a different owner.** The model sees the live tool surface (including a gated `smart_home_control` with the current targets) and chooses. The service enforces reality (`NO_DEVICES`, `NO_MATCH`, `AMBIGUOUS_TARGET`) and returns structured errors the model can act on. A pre-LLM layer is retained only for the four authorities in §2 — and a *verified live target* fast path, if latency ever requires one, is allowed only when an entity actually resolves. "No device" then means "not a match", which is the correct semantics.

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

The preview is **cosmetic but not harmless**: it paints a domain before the model has decided, so the user sees a wrong plan even on turns where the handler declines. It has no authority (it cannot execute) and no information the model lacks. The correct fix is to **delete the domain classifier**, not to merge it with `device_flow`'s matcher — merging would preserve the false claim and create a third shared list to maintain. Show a neutral "Understanding request…" state and let the first model/tool step update the workspace. If a preview is required for perceived latency, derive it from the model's structured plan, not from words.

Note also the ordering hazard inside `_build_task_plan`: `"word"`, `"document"`, `"report"` are checked before the website branch, and `"call"` is in the screen/camera branch. This function is a stack of substring tests, so any new domain added later inherits the same class of bug.

### R3 — High: the model is never told the smart-home rule (default tier is `lite`)

- `core/prompt.txt:115-118` contains the correct policy: *PC is the default; smart home only when the user names a smart-home target; phone only when the user names it.*
- The marker `--- MANUAL ---` is at `core/prompt.txt:70`, so all of §115-118 is manual-tier only.
- `core/prompt_parts.py:105-125` defaults `prompt_tier` to `lite`, which returns `TIER_CORE`. `main.py:221` and `core/text_turn_controller.py:1311` only append the manual when tier is `full`.
- Therefore the default text/voice model **never sees the device routing table** unless it calls `get_tool_surface`.

This is a latent bug independent of R1, and it becomes **load-bearing** once A17 is removed: the model is now the router, so its resident policy has to say that the PC is the default and that a device request with no connected device should be answered honestly rather than guessed. Add a short resident device-default line to the core tier, and keep the long table in the manual.

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

The correct gate is availability, not vocabulary. The implementation choice (D2 in `docs/ROUTING-IMPLEMENTATION.md`) is to keep `smart_home_control` advertised and make it **tell the truth**: `_smart_home_control` catches the service error and returns "No smart home devices are connected yet." as ordinary tool text. A hidden tool cannot answer "do I have devices?"; a model that deliberately calls the tool and gets a truthful result has not been hijacked. The declaration says this explicitly, and the core prompt tells the model to say so rather than guess.

A read-only `smart_home_status` (or `list_smart_home_devices`) remains an option if the product wants an inventory surface, but it is not required to fix R5. The same "report availability" argument applies to `connect_*` when no phone is paired: return a truthful message rather than a dead end.

### R6 — Medium: the dynamic skill registry is a second pre-LLM matcher (`core/text_turn_controller.py:228-271`)

`DynamicToolRegistry.find_matching_skill` (`core/dynamic_registry.py:393-494`) scores the utterance against each skill's `triggers`, `aliases`, name and description, then executes on a match **before** the provider branch. It is a genuine router, not a tool the model chooses.

It is better behaved than the smart-home predicate: it uses tokenised words and stem scoring rather than raw substrings, and a miss returns `None`. But it has the same structural property — a score claims the turn, and the user never sees the threshold. Under the authority rule it is acceptable only as an **exact trigger/alias** match: the utterance equals the skill's configured trigger, not merely scores above a threshold. A fuzzy score is not authority; if the scored path is kept for UX, it belongs behind the model, not in front of it. Any new deterministic router added in Phase 4 must use the same contract: explicit authority, tokenised input, and a decline path.

### R7 — Medium: many fast-path gates use the same unanchored style

`core/text_turn_controller.py` is a long chain of `any(p in lower for p in (...))` checks (email, calendar, RAM, battery, brightness, kill, diagnostics, heal, rollback, learn-rule, volume, open-app). Most are protected by specific multi-word phrases, so the observed failure rate is low. They are still the same pattern that caused R1, and `main.py:_looks_like_code_request` is deliberately broad (`"build"`, `"write"`, `"app"`, `"project"`), so "write a light summary" can trip the code path if it gets past the smart-home gate.

Do **not** rewrite all of them in Phase 0. Do classify each gate in Phase 4 by the authority it can prove: control-plane gates (volume, brightness, kill, diagnostics, heal) may stay if they act on the local machine and use anchored forms; semantic gates (email/calendar intent, open-app, presentation/website) should move behind the model unless they are exact aliases. A shared matcher helper is useful for the ones that stay, but the bigger win is deleting the ones that should not be there.

### R8 — Low: dormant systems that look like routers

- `core/intent_dispatcher.py` — 3-tier semantic dispatcher (regex → phrase cache → Gemini classifier). Imported only by tests. Its composite-goal branch imports a missing `core/omni_planner` inside a `try/except`, so it is inert rather than crashing. **Dormant.**
- `core/skill_discovery.py` — `LEARN_PATTERNS` and `SkillDiscovery.analyze_command`. Not imported anywhere; the live skill-creation regex is `main.py:_extract_skill_creation_goal`. **Dormant.** Do not wire this as a second skill router; extend the live one.
- `core/execution_reflection.py` — post-execution verification/healing. No caller. **Dormant.**
- `core/capabilities.py` — capability parsing/matching used by policy, grants, gateway auth and the secret broker. **Not an utterance router**; it is the vocabulary the permission boundary uses. Do not change it to fix R1.
- `core/provider_router.py` — chooses a model vendor, not a tool. Out of scope except that it is the last step of Plane A.

Including these here is deliberate: the user asked whether more routing systems exist. They do, but several of them are not running. A future batch that "turns on the intent dispatcher" would add a **fourth** pre-LLM matcher and make this bug class worse.

### R9 — Low: no regression test covers any of this

`tests/` has no `test_device_flow.py`, no `test_smart_home.py`, and no test for `_build_task_plan`. The smart-home tests that do exist cover **storage** and **styling** only (`test_smart_home_storage.py`, `test_smart_home_styles.py`); nothing tests `execute_command`, `_select_targets`, `_guess_action`, or the entry predicate. This is why the bug survived: the unit under test was the persistence layer, not the gate.

### R10 — High: tool retrieval cannot disambiguate a short query or a shared noun

This is Plane B's version of the same problem. The model compresses a request into `"vault"`; two MCP servers each expose a vault tool; `search_tools` returns a flat prose list with `group: mcp:<server>`, no server filter, no domain card, no match reason, and no ambiguity signal. The index *has* the server identity (`source`, `domain`, `keywords`), but it is not a weighted field and the presentation does not use it. `call_tool` does not detect a bare name shared across servers.

Full design in `docs/TOOL-RETRIEVAL-DESIGN.md`. The short version:

- add `server`/`domain`/`context`/`detail` to `search_tools`;
- fuse the model's short `intent` with the user's actual turn;
- make `server` a weighted FTS column (`bm25` weights) and index server aliases;
- group results by server, state `matched:`, and emit an explicit `AMBIGUOUS` block with a refine hint;
- guard `call_tool` against bare names that exist on more than one server;
- add a resident, bounded server manifest so the model knows which owners exist;
- add a gold-set eval with recall and cross-server-confusion thresholds.

**Status (2026-11-05): R0, R2 and R3 implemented; R1 accepted on evidence.** `search_tools`
now takes `server`/`domain`/`context`/`detail`, returns one group header per
owning server plus `matched:` reasons and an explicit `AMBIGUOUS` block with a
refine hint, and `ToolIndex.search` applies the server/domain filter before
ranking (with a browse mode when no intent is given). `ToolIndex.ambiguity` is
the deterministic contrast set, suppressed when the winning server owns a word
the runner-up does not. `call_tool` refuses a bare name shared by two servers and
refuses a name whose registration was overwritten by a later server. R2 completes
a compressed intent from the user's actual turn on the text, agentic and Live
paths, so `intent="vault"` after "search my Obsidian notes" does not reach the
ambiguity path at all. R3 puts a live, bounded server manifest in the session
payload, so the model knows which owners exist before it searches. The synthetic
two-vault gold set (`tests/test_tool_retrieval_eval.py`) passes recall@1 1.0,
recall@3 1.0, MRR 1.0, cross-server confusion 0.0, ambiguity precision/recall
1.0. R1's ranking acceptance is met by the existing name-variant weighting
(`test_naming_the_server_wins_at_every_k`); a separate FTS column is deferred
until a real miss is observed (D9).

---

## 5. Invariants a fix must preserve

1. **Authority, not confidence.** Plane A may claim a turn only for explicit syntax, an exact user alias, state the system owns, or a verified live target. Similarity, substrings, and model-style confidence are not authority. A handler that cannot prove one of the four declines.
2. **One permission boundary.** Nothing in Plane A may execute a tool directly. If a fast path wants to act, it calls the same `ToolSpec` / `AgentRuntime.execute` route (or the service method the ToolSpec calls). `core/device_flow.py` was a *text front-end* to `SmartHomeService`; it must not grow its own device transport, and after Phase 0 it should not exist as a router at all.
3. **A failed action must be reportable, not terminal-by-accident.** `return True` after a failure is only correct when authority was proven. Otherwise it must return `False` so the normal answer path runs. Structured tool errors (`NO_DEVICES`, `NO_MATCH`, `AMBIGUOUS_TARGET`) are the model-visible form of the same rule.
4. **The tool must report availability truthfully, and no gate may contradict it.** `smart_home_control` returns the service's no-devices/no-match message; the declaration says that is a valid result; the core prompt tells the model to report it. A hidden tool is not required, and a pre-LLM gate must never claim a device turn on the tool's behalf.
5. **No new pre-LLM router without an authority class and a decline path.** Tokenised input is not enough; the claim must be provable, and a miss must return `None`/`False` so the turn continues.
6. **The core prompt stays small.** Fix R3 with one or two resident lines, not by making the manual resident.
7. **Tests are the deliverable.** A corpus of ordinary prompts that must *not* be claimed, a set of real commands that must be served, and a retrieval gold set. A change without these will regress the first time someone adds a device type or an MCP server.

---

## 6. Phased remediation plan

### Phase 0 — Remove the smart-home pre-LLM claim (P0, small)

Files: `core/text_turn_controller.py`, `core/device_flow.py`, `smart_home/service.py`, `core/desktop_tools.py`, new `tests/test_smart_home_routing.py`.

1. **Delete the A17 call site** (`core/text_turn_controller.py:1105`) and the smart-home entry predicate/handler in `core/device_flow.py` (`:24-102`), including the substring word list and the failure-swallow branch. Keep the Brahma Connect branch (A16) untouched in this batch; it is a different domain and follows the same authority rule in Phase 4. If the module is shared, keep only the Brahma Connect path.
2. **Make `execute_command` return structured outcomes** (`smart_home/service.py:176`): `NO_DEVICES`, `NO_MATCH`, `AMBIGUOUS_TARGET`, or the action result. It may still raise for programmer errors; it may not raise a user-facing "no devices" that a caller has to interpret.
3. **Availability, not vocabulary** (`core/desktop_tools.py`, `main.py`):
   - keep `smart_home_control` advertised, but make `_smart_home_control` catch the service's `ValueError` and return it as tool text ("No smart home devices are connected yet.", "I couldn't find a matching device.");
   - say in the declaration that a no-devices result is a valid answer, so the model reports it instead of guessing;
   - do **not** add a second availability registry or hide the tool. A hidden tool cannot answer "do I have devices?", and the old dead end came from the pre-LLM claim, not from the tool's error.
   - if a device inventory surface is wanted later, add a read-only status tool then; it is not required for this fix.
4. **Regression corpus** (`tests/test_smart_home_routing.py`):
   - the 19 ordinary prompts in §8 plus `"Open Microsoft Office"` / `"Mushroom risotto recipe"` must produce **no** pre-LLM claim — assert the call site is gone (or that the handler always declines) and that the turn reaches the model;
   - real commands (`"Turn off the bedroom light"`, `"Turn on the fan"`, `"Switch off the kitchen plug"`, `"Set the living room AC to 24"`, `"Turn off all devices"`) must be served through `smart_home_control` and `_select_targets` (`smart_home/service.py:283` already supports "all devices");
   - mobile-target prompts must not select a device target;
   - zero-device `smart_home_control` returns the no-devices message; the model answers rather than guessing.

**Acceptance:** 0/19 ordinary hijacks by construction; 5/5 real commands work through the tool; 0 mobile leaks; the new tests and `py -3.11 -m ruff check .` pass.

**Why this is smaller than a better predicate:** there is no list to tune, no confidence threshold, and no precedence order. The hard part is making the tool surface honest about availability, which Phase 2 needs anyway.

### Phase 1 — Delete the domain preview (P1, small)

Files: `main.py`, `core/text_turn_controller.py`.

- Remove the smart-home branch from `_build_task_plan` (`main.py:670-726`). Do not replace it with a shared matcher; the preview has no authority and no information the model lacks.
- Show a neutral "Understanding request…" state until the first model/tool step updates the workspace. If a domain preview is a product requirement, derive it from the model's structured plan, not from words.
- Add tests for the two verified mislabels and for the neutral state.

**Acceptance:** `"Mushroom risotto recipe"` and `"Open Microsoft Office"` never produce a smart-home plan; no domain is guessed before the model speaks.

### Phase 2 — Availability gating and the voice surface (P1, medium)

Files: `core/desktop_tools.py`, `core/tool_index.py` / `core/tool_bridge.py`, `core/session_lifecycle.py`.

- Make `connect_*` report "no paired phone" as a truthful tool message when the device list is empty, rather than a dead end (the same D2 rule).
- Fix `core/session_lifecycle.py:490` to read `self._host._last_user_text` so the voice path is not permanently cold-start; then decide the timing question in §7 Q4 before relying on it.
- With A17 gone, the voice path has no deterministic fallback. Make sure the resident prompt (Phase 3) and the tool surface cover the common device request; do not reintroduce a lexicon to compensate.
- If a smart-home intent hint is added to `core/tool_bridge.py:90`, make sure it does not turn into a pre-LLM claim.

**Acceptance:** on a zero-device machine, calling `smart_home_control` returns the no-devices message and the model can answer; no ordinary prompt is consumed before the model.

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

### Phase 4 — One authority registry for Plane A (P3, larger; needs a decision)

- Introduce one `RoutingClaim` shape — `authority` (`syntax` | `exact_alias` | `owned_state` | `verified_target`), `claim()`/`decline()`, `reason` — shared by the remaining fast paths. A `confidence` field is deliberately absent: it invites the bug back.
- Classify every remaining Plane-A router in §3.1. Control-plane gates stay if they act on the local machine and use explicit forms; semantic gates move behind the model unless they are exact aliases. Convert the dynamic skill matcher (`core/dynamic_registry.py:393`) to exact triggers or delegate it to the model.
- Decide the fate of `core/intent_dispatcher.py` and `core/execution_reflection.py`: delete both, or wire one with an authority class and delete the duplicate. Do not leave a dormant third router that a future batch can switch on.
- Add a routing-catalogue test that asserts the documented number of pre-LLM consumers, that each one declares an authority, and that each one has a decline test.
- **Optional verified-target fast path:** only if measured voice latency demands it. Conditions: exactly one live target resolves, a strict verb+target grammar, no competing tool/domain, reversible/low-risk, a kill switch, and an audit log of every claim. If any condition fails, decline.

**Acceptance:** one documented list of Plane-A routers, each with an authority class and a negative test; no router claims on similarity alone.

**Status (2026-11-05): landed.** `core/routing.py` owns the contract (`Authority` =
`syntax` | `exact_alias` | `owned_state` | `verified_target`; `RoutingClaim` with
`claim()`/`decline()` and no `confidence` field) and the 23-record `ROUTERS`
inventory, which `validate()` checks and `tests/test_routing_authority.py` pins.
Removed: A15 (`_build_task_plan` is now the neutral four-step plan) and A17
(Phase 0). Converted: A6 dynamic skills (exact trigger/alias/name, one
imperative stripped) and A11 open-app (exact alias; the old substring test also
matched an empty candidate). A16 Brahma Connect now needs a token-bounded phone
word *and* a device that resolves. Every remaining fixed-phrase gate uses
`routing.has_phrase`/`any_phrase`, so substrings inside other words (`ac` in
"backup", `phone` in "microphone") can no longer claim a turn. The dormant pair
is kept, not wired: a catalogue test fails if app code imports
`core.intent_dispatcher` or `core.execution_reflection`. The optional
verified-target voice path remains unbuilt, as intended, until latency is
measured (Q1).

### Phase 5 — Retrieval precision (P2; see the companion doc)

Files: `core/tool_index.py`, `core/tool_bridge.py`, `core/mcp_manager.py`, `agent/mcp.py` (coverage), tests.

Execute `docs/TOOL-RETRIEVAL-DESIGN.md` Phases R0–R3:

- R0: `search_tools` gains `server`/`domain`/`context`/`detail`; grouped output, `matched:` reasons, ambiguity block, `call_tool` bare-name guard, gold-set eval.
- R1: server as a weighted FTS column, aliases, harness-MCP coverage.
- R2: fuse the model's short intent with the user's turn.
- R3: bounded live server manifest + one line in the `search_tools` description.

**Acceptance:** on a synthetic two-vault catalogue, `search_tools(intent="vault")` reports the ambiguity and both server groups; naming a server selects it; `call_tool(tool_name="vault_search")` refuses with qualified names; recall@3 and cross-server-confusion thresholds pass.

**Status (2026-11-05): R0, R2 and R3 accepted; R1 met by evidence.** The
acceptance above passes in `tests/test_tool_retrieval_eval.py`, and the wider
bridge/index/registry suites are green. R2 (`set_turn_context` on the text,
agentic and Live paths) is covered by `tests/test_turn_context.py`; R3 (the
bounded resident manifest) by `tests/test_server_manifest.py`. R1's ranking
acceptance is already satisfied by the name-variant weighting and is guarded by
`test_naming_the_server_wins_at_every_k`; a separate FTS column is deferred until
a real miss is observed (D9). The Phase 4 authority registry has since landed
(§6 Phase 4 status); the progress log and open items live in
`docs/ROUTING-IMPLEMENTATION.md`.

---

## 7. Open questions

1. **Is the model-only smart-home path fast enough for voice?** It adds a model round-trip ("turn off the bedroom light" no longer executes locally). Recommendation: ship it, measure p50/p95 on the target host, and only then decide whether a *verified-live-target* fast path is justified. If it is, it must satisfy every condition in Phase 4; a better word list is not an option.
2. **What should "no devices connected" do?** The model calls `smart_home_control`, gets the service's truthful "No smart home devices are connected yet.", and says so. The pre-LLM layer no longer has an opinion. If the product wants an explicit inventory question ("what smart-home devices do I have?"), add a read-only status tool then; it is not needed to fix the hijack.
3. **Should `_build_task_plan` guess at all?** No domain guessing before the model. Recommendation: neutral preview, model/plan-driven update. This is a product decision about perceived latency, not a routing decision.
4. **Voice intent at connect time.** A Live socket connects before the first utterance, so `last_intent` may legitimately be empty. Options: (a) keep cold-start and rely on `search_tools` + the R3 prompt rule; (b) reconnect/re-advertise after the first utterance; (c) seed the session from the last completed turn (`_last_user_text`). Recommendation: (a) plus the manifest/prompt work; treat (b) as a separate design because Gemini Live cannot swap tools mid-session.
5. **Do we want a shared `core/routing.py` chokepoint?** The repo's stated principle is "one door per concern." A single registry of Plane-A claims with authority classes would make R1 impossible to repeat, but it is a larger change than the bug requires. Recommendation: Phase 4, with the `RoutingClaim` contract first. **Resolved (2026-11-05):** `core/routing.py` exists; the contract and the inventory are the door.
6. **Dead code:** is `intent_dispatcher` an intended future (then it needs `omni_planner`, an authority class, and a decision to wire it), or abandoned (then delete it)? Leaving it is the worst option because it looks authoritative in the tree. **Resolved for now (2026-11-05):** neither is deleted; both are recorded as `DORMANT` in `ROUTERS` and a test fails if app code imports them. Deleting them remains an allowed, separate cleanup.
7. **Dynamic skills:** should `find_matching_skill` survive as exact triggers only, or should the model choose from the manifest like any other tool? Recommendation: exact triggers for saved aliases, model choice for everything else. Do not keep fuzzy scoring as a pre-LLM claim. **Implemented (2026-11-05):** exact trigger/alias/name only; the fuzzy score is gone and the model still sees every active skill as a `dynamic_skill` declaration.
8. **Retrieval scope:** do harness MCP tools enter `core.tool_index`, or does `search_tools` explicitly cover only the core/dynamic catalogue? Recommendation: index them; a callable-but-unsearchable tool is a trap. See `docs/TOOL-RETRIEVAL-DESIGN.md` Q2.

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
| Smart-home entry predicate (**to delete in Phase 0**) | `core/device_flow.py:24` `handle_smart_home_command`, words at `:39-45`, claim at `:57-58`, failure-swallow at `:83-102` |
| Smart-home service | `smart_home/service.py:176` `execute_command`; no-device at `:181-183`; `_guess_action:259`; `_select_targets:281` (supports "all devices" at `:283`) |
| Smart-home tool surface | `core/desktop_tools.py:971` `_smart_home_control` (returns the service's truthful error); declaration `main.py` |
| Tool retrieval | `docs/TOOL-RETRIEVAL-DESIGN.md`; `core/tool_bridge.py:698` `_handle_search`; `core/tool_index.py:1134` `search`, `:1326` `resolve` |
| Server identity in the index | `core/tool_index.py:287` `server` property, `:292` `search_text`; `core/mcp_manager.py:1019` `_index_tool` |
| Text turn order | `core/text_turn_controller.py:118` `on_text_command`; Brahma Connect at `:1101`; provider branch at `:1155` (smart-home gate removed) |
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
