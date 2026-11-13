# Skill-Selection Self-Check Loop — design record

**Status**: Implemented — M1–M6 (see §10.1). T3 model tie-break remains out of
scope. The M5 UI half is a read-only "Skill Routing Misses" card in the
System & Connect page, not a separate tab.
**Origin**: external developer's description of his own harness (see §1)
**Retrieval stance**: local-first, open source only — no online JEV dependency.
T1 keyword scoring is the contract; T2 local embeddings are a measured upgrade
(§3.2, §5.2).
**Scope**: `agent/turn_context.py` (JIT skill injection), `agent/trace.py`,
`agent/skill_audit.py`, `agent/evals.py`, `agent/__main__.py`,
`core/skill_forge.py`, `tools/skills_index.py`
**Related**: `docs/skills-library-jit-on-demand-skills.md`,
`docs/ACADEMIC-TAXONOMY-AND-SKILLS-ENGINE-SPEC.md`,
`docs/KNOWLEDGE-AND-SKILLS-ENGINE-TRACKER.md`, `docs/AGENTIC-HARNESS.md`

---

## 1. Provenance

On 2026-11-12, in the comment thread of *"More skills = worse agents (do this
instead)"* (David Ondrej), `@CodingBillionaires` described his routing approach:

> "I do not run any skills inside the claude or gpt or whatever harness itself, I
> have my own one and I only work with one orchestrator per big task which
> delegates all the skills through that web of skills to specific agents for set
> task with JEV routing and keywords and checks itself if it's relevant or not,
> this way I can have a very good library which I triage per section of set
> skills and their use case, no context bloat, no wrong skills ever selected. If
> for some reason a wrong skill was selected it auto checks at the end of the
> task and informs me, provides the fix, and rinse repeat."

> "the difference I think here is that your agent is still making the choice
> after taking it into it's context. JEV has it's own context and the lookup is
> out of your main models context so it saves tokens and goes up to 400x faster"

That is the whole of the public description. Everything below marked
**[stated]** comes from those two messages; everything marked **[inferred]** is
our reconstruction. The distinction matters: the loop is the interesting part and
he described it in one sentence.

---

## 2. The wisdom, extracted

**[stated]** — the design claims, in his words:

| # | Claim | Why it matters |
|---|---|---|
| W1 | The check runs **at the end of the task**, not per turn | One judgement per unit of work, not one per model call. Cheap by construction. |
| W2 | It is **automatic** | No human triage of a routing log; triage happens once, on skills, not per run. |
| W3 | It **informs him** | The output is a report addressed to the operator, not a silent internal retry. |
| W4 | It **provides the fix** | The artefact is a concrete patch (routing metadata / skill content), not just a "wrong" label. |
| W5 | "**rinse repeat**" | The fix is fed back and the case is re-run; the loop closes. |
| W6 | Routing lives **outside the main model's context** | This is what makes both the routing *and* the check affordable: neither costs main-context tokens. |
| W7 | Selection is **pre-made choices** refined per use case | The library is a triaged table, so a miss is a *table* defect — findable and patchable. |

**[inferred]** — the design principles behind those claims:

1. **Selection is a hypothesis; the loop is the falsifier.** "No wrong skills ever
   selected" is aspiration, not property. A keyword/taxonomy scorer at 90+
   skills will mis-route; the honest guarantee is *misses are detected and
   corrected*, not *misses never happen*.
2. **Detection must be cheaper than selection.** If checking costs a model pass
   per task, the economics that justified out-of-context routing disappear.
   Hence: deterministic rules first, model escalation only on ambiguity.
3. **A fix has an address.** Because skills are a triaged table with declared
   use cases, every miss maps to a field to edit (trigger, tag, domain,
   description, cookbook) or to a missing row. This is why "provides the fix" is
   possible at all — without the table it would degrade to "try again".
4. **The human is in the promotion step, not the detection step.** He says
   "informs me" — automation covers detect + diagnose + patch *proposal*; the
   operator approves. Fully autonomous self-modification of prompt content is
   the thing to avoid, and this repo already has that invariant (§6).
5. **Fixes must be checked against their neighbours.** Adding a trigger phrase to
   fix one skill can steal another's cases. "Rinse repeat" implies a replay set,
   not a single re-run.

**[inferred, and the part worth arguing about]** — what he probably does *not*
have, and we should not copy: unconditional end-of-task LLM review. The
interesting engineering is making the end-of-task check a **rule set over a
recorded routing decision** (§5), so it is free and explainable.

---

## 3. What this repo already has

The loop is not a new subsystem. Five of its six stages are already half-built
here.

| Loop stage | Existing seam | State |
|---|---|---|
| Routing decision | `agent/turn_context.py` — `compile_turn_context()` returns `TurnPlan.shortlisted_skills` (top 3, each with `score`), `selected_skill` (top 1), `selected_script`, `domain`, `domain_confidence` | **Built.** Weights at `turn_context.py:195-234`, cutoff `0.25` at `:218`, limit 3 at `:328`. |
| Decision event | `agent/loop.py:1210-1219` emits `skill_injected` with `skill`, `script`, `domain`, `chars` | **Built, but ephemeral** — an event on the stream, not a persisted record. |
| Per-run record | `agent/trace.py` — `TraceRecord` + `Span.attributes/events` + redacting JSONL sink | **Built.** No skill-selection attributes yet; no `skill_choice` failure label. |
| Task outcome | `agent/plan.py` (step statuses + `evidence`), `agent/verify.py` (`verify_work` records a command verdict or proof of work) | **Built.** Never correlated with the injected skill. |
| Failure taxonomy | `agent/trace.py:24-45` — `FailureTaxonomy` incl. `TOOL_CHOICE_ERROR`, `UNVERIFIED_STOP`, `LOOP_DIVERGENCE` | **Built.** No skill-level label. |
| Replay harness | `agent/evals.py` — `Scenario` (`expected_tools`, `forbidden_tools`, `expected_text`, `verify_files`) + `evals/scenarios/*.json` + `python -m agent eval` | **Built.** Categories are `tool_choice, multi_step, recovery, safety, memory` (`evals.py:25`). No `skill_choice`. |
| Staged fixes | `core/skill_forge.py` (`staged_skills_dir()`, `forge_skill()` with a bounded verify/repair loop), `agent/teacher_policy.py:242` (**"Skills are NEVER auto-installed"**) | **Built.** This is the exact policy the loop must reuse rather than re-invent. |
| Index build | `tools/skills_index.py` (`--build`, `--check`) compiles `config/skills_index.json` (90 skills, 6 domains, `triggers`/`tags`/`cookbook`/`sha256`) from `skills/<name>/SKILL.md` + `cookbook.md` | **Built.** `sha256` means index↔source drift is already detectable. |

**The gap is exactly five things**: persist the selection, correlate it with the
outcome, label the miss, generate a staged patch, and replay the case.

### 3.1 Which skill surface this targets

There are four, and they fail differently:

| Surface | Path | Who picks | Failure mode |
|---|---|---|---|
| JIT index (**primary target**) | `skills/` → `config/skills_index.json` via `agent/turn_context.py` | **the code**, before the turn | wrong row injected automatically; visible in `TurnPlan` |
| Project skills | `.brahma/skills/<name>/SKILL.md` via `load_project_skill` | the model, mid-turn | model loads the wrong body; visible in the tool call |
| Skills Library | `skills_library/` via `search_skills`/`load_skill` | the model, mid-turn | same |
| Subagent library | `subagents/catalog.json` via `run_subagent` | the model, mid-turn | same |

Start with the JIT index: it is the only surface where *we* make the choice, so
the ground truth (the row we chose, its score, its declared use case) is already
in hand. Model-initiated loads are a separate detector (M5): a load is compared
against what the router injected, and only a name that is in the compiled index
can be a routing *miss*. A load from another surface is `off_index`, and a voice
load is `unrouted` — usage, not a verdict.

### 3.2 The in-repo precedent: `agent/second_brain.py`

Before looking anywhere else, this repo already implements the local-first
retrieval pattern §5.2 describes — in `agent/second_brain.py`, stdlib-only, and
it has been through the failure modes:

| Concern | How `second_brain.py` does it | Lesson for skills |
|---|---|---|
| Embedding call | `urllib.request` to `{endpoint}/embeddings`, default `nomic-embed-text` at `http://localhost:11434/v1` (`:609-656`) | Same call shape works for a skill index; endpoint/model are config, not constants. |
| Degradation | A dead server returns `None` for the query vector; the search falls back to FTS5 keyword ranking (`:642-656`, `:1017-1062`) | T1-must-still-pass is not a new rule; it is the existing contract. |
| Storage | `vectors (chunk_id, vec BLOB, norm REAL)` in one SQLite file; `struct.pack("<Nf")` encode, `array.array("f")` decode (`:659-666`, `:277-310`) | 90 skills need a 90-row table, not a new database. |
| Scoring | In-memory `_cosine_scores` over cached vectors, lazy `numpy` fast path with a pure-Python fallback (`:914-955`) | At 90 rows, an in-memory scan beats adding `sqlite-vec` as a build dependency. |
| Acceptance | A semantic hit needs `cosine >= 0.5`; a keyword hit does not (`:153-157`) | Two thresholds, not one: semantic recall may be stricter than lexical. |
| Cache | Vector matrix cached by index `(path, mtime, size)` (`:914-932`) | Same trick: invalidate on file stat, not on a timer. |

This is the template for M6. The cortex stack below is worth reading for the
*loop*; `second_brain.py` is worth copying for the *wiring*.

### 3.3 External local reference stack (no online JEV)

JEV is an online service; nothing in this design needs it. The reference
implementation for the same job is already on this machine at
`C:\Users\Admin\local-cortex-os-agi-win` — a fully local, open-source
neuro-symbolic stack whose routing lane does exactly what §5's detector is
guessing at. Read it as *prior art*, not as a dependency: it is Go + Python,
this repo is Python + PyQt, and the two are not going to share a process.

What it actually runs:

| Concern | Their implementation | File | Takeaway |
|---|---|---|---|
| Embedding daemon | FastAPI on `:8082`, `/embed` returns a **256-dim, L2-normalised** vector | `scripts/daemon_extractor.py` | A tiny local HTTP service is enough; no SDK. |
| Embedding fallback chain | Torch model → Ollama `nomic-embed-text` → **deterministic SHA-256 hash vector** | `get_embeddings_batch()` | It degrades instead of failing. The hash tier is a seam, not production retrieval — useful as a CI stub. |
| Vector store | SQLite + `sqlite-vec`, `vec_distance_cosine`, `ORDER BY distance ASC LIMIT ?`, `limit*3` over-fetch then rescore | `backend/memory/rag.go`, `db.go` | Cosine top-k with an over-fetch is the whole index. 90 skills fit in one table row set. |
| Vector encoding | `[]float32` → little-endian BLOB; Matryoshka truncation 768→256 then re-normalise | `rag.go:14-23`, `ollama.go:592-615` | Dimension contract is explicit and tested (`embed_test.go` asserts `len == 256`). |
| Cheap triage | `System1` assigns urgency/complexity with heuristics + an EMA over the last 100 task durations; one 100 ms call to `POST /decide` on `:8082` | `backend/core/system1.go` | A *bounded-latency* classifier call, with a heuristic fallback. Never blocks the loop. |
| Retrieval scoring | MAUT: `U = 0.4·similarity + 0.4·P(tools / capabilities) + 0.2·resonance` | `backend/core/routing.go:26-40`, `llm/gateway.go:740-756` | This is our five-signal scorer with different weights, plus a tool-alignment term worth stealing (R3 in reverse). |
| Expensive tie-break | Optional LLM pass over the top candidates to emit `{ensemble_id, justification}` | `routing.go:174-216` | Correct layering: deterministic score shortlists, model only picks. Do **not** run it per turn. |
| Self-check loop | Failures land in `dead_letter_queue` with `failing_state`, `reasoning_context`, `targeted_tool`, `error_signature`; a bi-daily "Continual Harness Evolution" pass distils the last 50 failures + 50 ledger events into JSON CRUD ops for prompts/agents/skills/memory | `backend/memory/evolver.go:245-360` | This *is* "informs me, provides the fix, rinse repeat" — and it is LLM-driven, which is exactly the cost we want to avoid below. |
| Immunity / quarantine | Regex pre/post-execute checks; bad payloads moved to `quarantine_zone` rather than deleted | `backend/memory/immune.go` | Precedent for our rule: a rejected fix is quarantined with a reason, never silently dropped. |

Three design consequences for us:

1. **Our stance is local-first, not keyword-only.** The cortex stack proves the
   local lane is cheap: a 256-dim vector over 90 skills is ~92 KB of BLOB and a
   linear scan. But it also proves the *right order*: the cheap deterministic
   pass decides, the expensive pass only breaks ties. §5 keeps keyword/trigger
   scoring as the first pass and treats embeddings as a retrieval upgrade, not a
   replacement and not a per-turn model call.
2. **The detector stays rule-based.** Their evolver is a model pass over a
   failure window. Ours must not be: `agent/skill_audit.py` is a pure function
   over the record. If a rule is silent and the operator flags a run, that is the
   only place a model may diagnose, and it is labelled (§5, "Escalation").
3. **The embedding boundary is a dependency problem, not a design problem.**
   `agent/` must import stdlib-only under CI (see `AGENTS.md`). `second_brain.py`
   shows the answer: `urllib` + `sqlite3` at module scope, `numpy` lazily, and a
   dead server degrades to keywords. An embedder is therefore a *callable or a
   URL*, never an SDK import. If a vector extension is ever warranted, it goes
   behind `core/` — `agent/` must keep working with only pytest installed.

**Do not vendor cortex.** Copying its schema, its MAUT weights, or its
"bi-daily evolution pass" imports concepts tuned to a different system. Borrow
the *shape*: small local embedder over HTTP, a vector BLOB table, cosine top-k,
cheap shortlist then optional model tie-break, failures recorded with structured
signatures, fixes proposed not applied. The concrete implementation should look
like `second_brain.py`, not like `cortex.db`.

---

## 4. The loop

```
        ┌──────────────────────────────────────────────────────────────┐
        │                                                              │
   user turn                                                          │
        │                                                             │
        v                                                             │
  [1] RECORD ── TurnPlan → SkillSelection record (JSONL, ring-buffered) │
        │                                                             │
        v                                                             │
  [2] EVIDENCE ── plan steps + verify_work + tools run + stop reason    │
        │                                                             │
        v                                                             │
  [3] DETECT ── R1..R5 deterministic rules → verdict + "why"            │
        │        (retrieval tier T1/T2/T3 from the record, never a guess) │
        │                                                             │
        ├── correct ──────────────────────────────────────────────────>│ exit (no cost)
        │                                                             │
        v  wrong / ambiguous                                          │
  [4] DIAGNOSE ── one of 7 miss kinds → one fix kind                    │
        │                                                             │
        v                                                             │
  [5] STAGE FIX ── diff against source of truth, never applied          │
        │          (index fix → skills/<n>/SKILL.md frontmatter)        │
        │          (missing skill → core.skill_forge → staged_skills/)  │
        │                                                             │
        v                                                             │
  [6] REPLAY ── recorded query → evals/scenarios/skill_choice.json      │
        │      → python -m agent eval → green? ────────────────────────┘
        │                              │ red, and iterations < 3
        │                              └──> back to [4]
        └── iterations == 3 → park for human with evidence attached
```

Design rules baked into that picture:

- **Steps 1-3 run on every task, cost 0 model calls**, and are capped in size
  (one record per turn, ring buffer, redacted like the trace sink).
- **Steps 4-6 run only on a miss** and only produce proposals.
- **Step 6 is the loop.** Without a replay case, "provides the fix" is a guess.
- **Bounded at 3 iterations**, then a human decision.

---

## 5. Detection rules

Deterministic, ordered, each emits a `why` string. First match wins; several may
be recorded as evidence.

| Rule | Condition | Verdict | Fix kind |
|---|---|---|---|
| **R1** `domain_mismatch` | task's taxonomy node (confidence ≥ 0.5, via `second_brain.locate_nodes`) maps to a different **domain family** than the injected skill's `academic_domain` | `wrong` | retarget / add trigger |
| **R2** `weak_signal` | `selected.score ≤ 0.30` (cutoff is 0.25, so this is "won by noise") **and** a runner-up in the other domain has score within 0.10 | `ambiguous` | disambiguate pair |
| **R3** `unused` | skill injected, task completed, and either no tool ran at all or none of the tool names mentioned in the skill's cookbook ran | `bloat` (not wrong — *expensive*) | tighten description / drop the skill |
| **R4** `failure_attributable` | task failed (`tool_choice_error`, `argument_error`, `unverified_stop`, `unknown_error`, `skill_choice_error`) while a skill was injected, and a runner-up fits the task domain or is within 0.15 | `wrong` | retarget to the runner-up |
| **R5** `drift` | index `sha256` ≠ current `skills/<n>/SKILL.md` digest, **or** the recorded cookbook digest ≠ current cookbook | `stale` | rebuild index, re-record |

**Evaluation order deviates from the numbering on purpose.** R5 runs *first*, as
a precondition. A record written against an index that has since changed
describes a skill the repository no longer has, so every behavioural verdict
below it would be untrustworthy. When R5 and another rule both match, R5 is the
verdict and the others are appended to `verdict.evidence`; when R5 is silent the
order is R1 → R2 → R3 → R4.

R1 does not compare domain strings literally: the task side is a taxonomy path
(`applied-sciences/computing/software-engineering`) and the skill side is free
frontmatter text (`coding`), so both are mapped to a coarse family
(software/devops/security/cognitive/research/architecture/multimodal/
methodology). An unmatched value yields **no verdict** rather than a guess —
a false "wrong skill" is worse than a missed one, because it stages a bad patch.

R3 is the rule most self-check designs omit and the one that pays: a skill that
is injected and never used is pure context cost — the exact failure mode the
whole progressive-disclosure design exists to prevent. Note that a cookbook has
no machine-readable tool list (`tools/skills_index.py` stores `cookbook` as
text), so the implementation is a substring match of the cookbook body against
the registered tool names — cheap, deterministic, and good enough for a *hint*
that gets confirmed by a human.

### 5.1 Three caveats that come from the index builder

All three change how much a rule can be trusted. M1/M6 fixed all three in the
build artifact; the notes below stay because they explain **why** each field
exists, and a reader who rebuilds an old index would hit them again.

1. **`domain` is heuristic, `academic_domain` is declared.**
   `domain` is derived by substring matching over `domain + tags + name`
   (`tools/skills_index.py:139-152`), so it is a *classification guess*; the raw
   frontmatter value survives as `academic_domain` (`:171`). R1 compares
   against `academic_domain` and treats `domain` as advisory — a mismatch on the
   derived tag is a hint, not a verdict.
2. **The digest covers `SKILL.md`, not `cookbook.md`.** *(fixed)*
   `sha256` is taken over the `SKILL.md` text (`:126`, `:159`), so a cookbook-only
   edit would produce no drift signal. The builder now also emits
   `cookbook_sha256` over the resolved recipe, and R5 compares both digests.
   `config/skills_index.json` was rebuilt once so every entry carries it; a
   record written before that rebuild simply has an empty `cookbook_sha` and R5
   skips the cookbook half rather than guessing.
3. **`score` is a scalar with no attributable signal.** *(fixed)*
   `shortlist_skills()` used to sum five weighted signals
   (`turn_context.py:195-234`) and keep only the total. It now returns
   `signals` with each component and a `why` list naming what fired, which is
   exactly the split the local stack keeps between `similarity`,
   `P(tools / caps)` and `resonance` (`gateway.go:740-756`).

### 5.2 Where a local embedder fits (and where it does not)

The cortex stack's MAUT formula is `0.4·similarity + 0.4·P(tools / caps) +
0.2·resonance`. Mapped onto our five signals, that is a *scoring* analogue, not
a retrieval upgrade. The actual upgrade is narrower: add a cosine term over a
local skill-vector table and let the existing signals re-rank. The wiring mirrors
`agent/second_brain.py`, not `cortex.db`: one build artifact, an in-memory scan,
and no new runtime dependency.

| Tier | Mechanism | When it runs | Cost | Failure mode |
|---|---|---|---|---|
| T1 trigger/domain/tag scorer (**today**) | in-process substring + weights | every turn | ~0 | blurs when two skills share vocabulary |
| T2 local embedding retrieval | `POST /embeddings` → one vector per skill in `config/skills_embeddings.json` → in-memory cosine → existing signals re-rank | every turn, only if `BRAHMA_SKILL_EMBED=1` | one time-boxed HTTP call, cached per query | server down → `embed_query()` returns `None` → fall through to T1 |
| T3 model tie-break | one completion over the top 3 candidates | only when T2's margin < 0.05, or operator-flagged | one model call, **never** per turn | must be labelled `diagnosed_by: "model"` |

One dimension contract per table, chosen once: `second_brain.py` stores the raw
768-dim `nomic-embed-text` vector (`EMBED_DIM = 768`); cortex truncates to 256
for RAM. Pick 768 for skills unless the table size measurably hurts, because a
truncated table cannot be compared against a full one and re-embedding 90 skills
is cheap but re-validating 90 replay cases is not.

**As built (M6).** `tools/skills_index.py --build-embeddings` writes
`config/skills_embeddings.json` through `_embed_batch`, reusing the
`second_brain` endpoint/model defaults (`nomic-embed-text`,
`http://localhost:11434/v1`) so one local server serves both. The table stores
`{name, directory, digest, vector}` per skill plus one `model`/`dim` for the
whole file. The text that is embedded is the *routing surface* — name,
description, triggers, tags — not the cookbook, so the body never leaves the
repo and a body-only edit does not invalidate a vector. `--check` reports stale
rows. At query time `agent/turn_context.py` enforces
`SKILL_COSINE_FLOOR = 0.5` and adds at most
`SKILL_EMBED_WEIGHT = 0.35 × cosine`; that weight is deliberately below a single
0.50 trigger hit, so a semantic match can lift a borderline skill but can never
outrank a declared trigger.

Rules for adding T2, in order:

- **Build the index, don't embed per turn.** `tools/skills_index.py --build`
  writes `config/skills_embeddings.json` (name → vector, model id, dim, digest).
  If the skill digest changes, the vector is stale and must be rebuilt — the same
  invariant as `skills_index.json`.
- **The embedder is injected, not imported.** `agent/turn_context.py` takes an
  optional callable; `core/` supplies the `urllib` client (or the existing
  `second_brain._embed`). No PyQt, no SDK, no `requests` at `agent/` module
  scope; CI stays green with the callable absent.
- **Reuse the second-brain thresholds, then tune.** Its semantic path requires
  `cosine >= 0.5` while its lexical path does not (`second_brain.py:153-157`).
  Skills should start with the same split and a recorded `cosine` in the
  shortlist entry.
- **T1 is the contract.** Every T2 result must be reproducible by re-ranking
  with the T1 signals, so a degraded server changes recall, not correctness.
- **Cache the query vector.** The trace record's `query_sha256` is already the
  cache key; a repeat query in one session must not re-embed.
- **Measure before shipping.** Only adopt T2 if T1's recorded `weak_signal` (R2)
  rate over a week of real runs exceeds a threshold you can defend — otherwise
  the 201 KB index is already fast enough and embeddings add a daemon to
  babysit for no measured gain.

The deterministic hash vector from `daemon_extractor.py` is explicitly **not**
a tier here: it is a stable test double for fixture generation, not retrieval.

Escalation: only when R1-R5 are all silent **and** the operator flags the run, or
when two rules disagree. Escalated diagnosis may use one model call, and the
record must mark `diagnosed_by: "model"`, so a model verdict is never confused
with a rule verdict.

### 5.3 Model-initiated loads (M5)

R1–R5 judge the skill the router *chose*. They cannot see the opposite case: the
router chose nothing (or something else) and the model then called
`load_project_skill` or `load_skill` by name. That call is recorded on a separate
`skill_loads_<date>.jsonl` stream, because a load can happen on a turn where no
selection record exists at all.

Each load is classified, not judged:

| Outcome | Meaning | Actionable? |
|---|---|---|
| `confirmed` | loaded name == injected name | no — the router and the model agree |
| `miss` | name is in the compiled index, but the router injected something else or nothing | **yes** — a trigger/description edit could have picked it; candidate for R1/R2 |
| `off_index` | name is not in the compiled index (project skill, Skills Library) | no — T1 never sees that surface, so blaming the router is wrong |
| `unrouted` | voice/desktop path, no router decision exists | no — usage telemetry only, never a verdict |

Classification reads the *event stream*, not the tool result: a load of a name
that does not exist still happened and is still worth seeing. For a harness run,
`indexed` uses the runtime workspace's index — the same one the router read. The
voice funnel has no router and no run workspace, so it classifies against the
process workspace; its rows are `unrouted`/`off_index` either way.

---

## 6. Fix kinds and where they land

`config/skills_index.json` is a **build artifact** (`tools/skills_index.py
--build`). Patching it directly means the next build silently reverts the fix.
Every fix therefore lands in source and is followed by a rebuild.

| Miss kind | Fix | Lands in | Applied how |
|---|---|---|---|
| `missing_trigger` | add the phrase the task used | `skills/<n>/SKILL.md` frontmatter `triggers:` | staged diff → rebuild → replay |
| `weak_description` | rewrite description to name the use case | same file, `description:` | staged diff |
| `wrong_domain` | correct `domain:` / `academic_domain:` | same file | staged diff |
| `ambiguous_pair` | split the two triggers between them | both `SKILL.md` files | staged diff, reviewed as one unit |
| `missing_skill` | new skill | `core.skill_forge.SkillForge.forge_skill()` → `staged_skills/` | existing forge invariant |
| `stale_skill` | rebuild + re-record | index | `tools/skills_index.py --build` |
| `stale_embedding` (T2 only) | re-embed the changed skill | `config/skills_embeddings.json` | rebuild after `skills_index.json`; digest mismatch invalidates |
| `costly_skill` (R3) | demote: lower priority or drop a trigger | `skills/<n>/SKILL.md` | staged diff |

**As built (M3).** `draft_fix` auto-generates exactly one patch shape: a
`triggers:` addition built from query tokens the skill does not already mention.
That is the highest-precision, lowest-risk edit. `demote_skill`,
`retarget_runner_up` and description/domain rewrites emit a proposal with a
`note` and **no patch**, because they are judgement calls and a generated diff
would look authoritative without being right. A rebuild fix is likewise a
command (`python tools/skills_index.py --build`), not an edit. Every proposal
lands in `staged_skills/_audit/<stamp>_<rule>_<skill>/` with `proposal.json`,
`proposal.patch` (when there is one) and a `README.md`; nothing is applied.


Invariants this must not break (all already enforced in this repo):

1. **Nothing auto-installs.** `agent/teacher_policy.py:242` — generated skills
   are staged for human review, never promoted by the process that wrote them.
   A body edit is a staged diff for the same reason: skill bodies are prompt
   input, and prompt input is an injection surface.
2. **No per-turn model call added.** The loop must not become the context bloat
   it exists to prevent. Record + detect are pure functions. A local embedding
   call at T2 is allowed because it is not inference over the transcript: it is
   bounded (3 s), cached by `query_sha256`, and falls back to T1 — but it is
   still an opt-in daemon call, never a model completion per turn.
3. **`agent/` stays stdlib-only.** No PyQt, no provider SDK, no COM at module
   scope; CI collects only `tests/test_agent_*.py`.
4. **No second tool-filter path.** `allowed-tools` stays informational;
   `AgentRuntime` policy remains the only execution boundary.
5. **The detector must explain itself.** A fix without a `why` is a guess; if the
   rule cannot say why, the verdict is `ambiguous`, not `wrong`.
6. **Records are redacted and bounded.** Reuse the trace sink's redaction; one
   record per turn; ring buffer with an explicit retention policy.
7. **Never silently rewrite a body the user wrote.** Diff + approve.

---

## 7. Record shape

One JSONL line per turn that injected a skill (mirrors `TraceRecord` so the two
can be joined on `trace_id`):

```json
{
  "schema": 1,
  "ts": "2026-11-12T09:14:03Z",
  "ts_epoch": 1762938843.0,
  "trace_id": "tr_...",
  "session_id": "ses_...",
  "turn": 3,
  "query": "deploy the service",      // full text only when retention allows
  "query_sha256": "…",
  "intent": "procedural",
  "domain": "devops/data",
  "domain_confidence": 0.71,
  "retrieval": {"tier": "T1", "embed_model": null, "embed_sha": null},
  "shortlist": [
    {"name": "docker-devops", "score": 0.45, "score_raw": 0.45,
     "domain": "devops", "academic_domain": "devops",
     "tags": ["docker"], "signals": {"trigger": 0.5, "domain": 0.4},
     "why": ["trigger:deploy", "domain:devops"], "cosine": null},
    {"name": "git-workflow", "score": 0.20, "score_raw": 0.20, "domain": "coding"}
  ],
  "selected": "docker-devops",
  "selected_sha": "9f2c…",
  "cookbook_sha": "41ab…",
  "selected_domain": "devops",
  "selected_tags": ["docker"],
  "cookbook_tools": ["read_file", "run_command"],
  "injected_chars": 1180,
  "truncated": false,
  "index_sha": "9f2c…",
  "winner_margin": 0.25,
  "outcome": {
    "status": "completed",
    "stop_reason": "completed",
    "failure_label": null,
    "tools_run": ["read_file", "run_command"],
    "verify": ["command:PASSED (pytest -q)"],
    "cookbook_tools_used": true
  },
  "verdict": {
    "rule": "R1", "kind": "wrong", "why": "task domain 'security' ≠ skill domain 'devops'",
    "diagnosed_by": "rule", "fix_kind": "retarget",
    "candidates": ["docker-devops"], "evidence": []
  }
}
```

`why` per shortlist entry is the missing piece today: `shortlist_skills()` scores
five signals (trigger, domain, tag, description, name) but returns one number, so
a miss cannot be explained after the fact. Recording which signal fired is the
difference between a patchable defect and an unfalsifiable score.

`cookbook_tools_used` is written by the loop (normalised `set(hints) &
set(tools_run)`), so R3 stays a pure function over the file and does not need to
re-derive the intersection. `verdict` is `null` at write time: the record is
written at the end of the task, and the audit that fills it in is a separate,
repeatable pass.

`retrieval.tier` is what makes the local-first upgrade measurable: it records
whether the turn was decided by T1 keywords, T2 embeddings, or a T3 model
tie-break, plus the embedder identity/digest so a vector built by a different
model is never compared against a new one. If T2 is adopted and then a model is
swapped, the record shows exactly which turns are no longer reproducible.

---

## 8. Replay and regression

A miss becomes a fixture:

```json
{
  "id": "sc_01_deploy_pipeline",
  "name": "Deploy skill must not be selected for a security review",
  "category": "skill_choice",
  "prompt": "audit this dockerfile for privilege escalation",
  "expected_skill": "security-auditor",
  "forbidden_skills": ["docker-devops"],
  "expected_domain": "security",
  "tags": ["skill_choice", "regression"]
}
```

Two assertions per case, and both are needed:

- **positive** — the case that failed now routes to the fixed skill;
- **negative (neighbour guard)** — every case that was previously correct still
  routes the same way. Trigger-phrase fixes are zero-sum at the margin; without
  this guard the loop just moves the miss.

This is a new `skill_choice` category in `agent/evals.py:25` plus
`evals/scenarios/skill_choice.json`, evaluated by the existing
`python -m agent eval` path. No new runner.

---

## 9. Metrics

| Metric | Definition | Target |
|---|---|---|
| `selection_precision` | injected skills judged correct / injected | ↑, reported weekly |
| `top3_recall` | correct skill present in top 3 | > 0.95 — the escape hatch exists (`load_project_skill`), so recall matters more than top-1 precision |
| `wrong_pick_rate` | R1+R4 verdicts / tasks | ↓ |
| `unused_rate` (R3) | injected-but-unused / injected | ↓ — this is the context-bloat KPI |
| `fix_accept_rate` | staged fixes approved / proposed | tells us whether the diagnosis is real |
| `iterations_to_green` | replay runs per accepted fix | < 2 |
| `retrieval_tier_mix` | share of turns decided by T1 / T2 / T3 (from the record) | T3 stays < 2 %; T2 only if it beats T1 on `top3_recall` |
| `prompt_chars_per_turn` | **guardrail** | flat or down; the loop must never increase resident cost |
| `model_load_miss_rate` | model-initiated loads classified `miss` / all loads (M5) | ↓; `confirmed` and `off_index` are not misses, `unrouted` is voice usage only |

---

## 10. Milestones

| # | Deliverable | Files | Acceptance |
|---|---|---|---|
| **M1** | Selection record | `agent/turn_context.py` (return per-signal `why`), `agent/trace.py` (attributes + JSONL), `agent/loop.py` (attach outcome at turn end) | a run emits one joinable record per turn; `tests/test_agent_trace.py` covers it; suite still stdlib-only |
| **M2** | Detector + report | new `agent/skill_audit.py` (rules R1-R5), `python -m agent skills audit --since 7d` | rule verdicts on synthetic plans; no model calls; determinism test |
| **M3** | Diagnosis + staged fix | `agent/skill_audit.py`, `core/skill_forge.py` handoff, staged-diff writer | a fixture miss produces a reviewable diff and never mutates `skills/` |
| **M4** | Replay corpus | `agent/evals.py` (`skill_choice`), `evals/scenarios/skill_choice.json` | existing `agent eval` runs it; neighbour guard asserted |
| **M5** | Model-initiated loads + UI | `agent/skills.py`, `core/skills_library.py`, read-only Settings card | detection for `load_project_skill`/`load_skill`; operator sees digest |
| **M6** (optional) | Local embedding retrieval (T2) | `tools/skills_index.py` emits `config/skills_embeddings.json`; `core/` HTTP client (reuse `second_brain._embed`); `agent/turn_context.py` takes an injected `embed` callable | T1 still passes with the callable `None`; T2 improves `top3_recall` on the M4 corpus; server-down falls through to T1; `agent/` still imports stdlib-only |

M1 and M2 are the whole value. M3-M5 are mechanical if the verdicts are good.
M6 is explicitly gated: adopt the local embedder only if the M1/M2 records prove
keyword scoring blurs, not because JEV has embeddings.

### 10.1 Implementation status

| # | Status | Where it landed | How it is exercised |
|---|---|---|---|
| **M1** | **Done** | `agent/turn_context.py`: per-signal `signals`, `why`, `score_raw`, `sha256`/`cookbook_sha256`, `index_sha`, `winner_margin`, `retrieval_tier`, `selection_record()`. `agent/trace.py`: `SkillSelectionSink` + `FailureTaxonomy.SKILL_CHOICE_ERROR`. `agent/loop.py`: one record at turn end, also attached to `TraceRecord.attributes["skill_selection"]`. | `python -m agent run "…" --json` emits a `skill_selection` event; the sink writes `%LOCALAPPDATA%\BrahmaAI\skill_selections\skill_selections_<date>.jsonl`. Tests: `tests/test_agent_trace.py`, `tests/test_turn_context.py`, `tests/test_agent_skill_audit.py`. |
| **M2** | **Done** | `agent/skill_audit.py` (R1–R5, `audit_record`, `audit_records`, `summarize`, `parse_since`). `agent/__main__.py`: `skills audit`. | `python -m agent skills audit --since 7d`, `--json`. Read-only by default. No model call anywhere in the module. |
| **M3** | **Done** | `agent/skill_audit.py`: `draft_fix` (unified diff, trigger insertion) and `stage_fix` (`staged_skills/_audit/<stamp>_<rule>_<skill>/`). | `python -m agent skills audit --stage-fixes`. A proposal has `proposal.json`, `proposal.patch`, `README.md`; `skills/` is never written. |
| **M4** | **Done** | `agent/evals.py` (`skill_choice` category and its evaluator), `evals/scenarios/skill_choice.json` (6 cases). | `python -m agent eval --category skill_choice --provider stub` — no model is called, so the category is exempt from the `--allow-stub` guard. |
| **M5** | **Done** | `agent/skill_audit.py`: `LOAD_TOOLS`, `load_name_from_args`, `extract_model_loads`, `classify_model_load`, `model_load_record`, `miss_digest`, `note_skill_load` (voice funnel). `agent/trace.py`: `SkillLoadSink` (own `skill_loads_<date>.jsonl` stream, shared dir/retention). `agent/loop.py`: `skill_load_log=`, `_record_model_loads` (runs before the selection record, emits `skill_load`). `agent/turn_context.py`: `indexed_skill_names(workspace)`. `agent/__main__.py`: `skills misses`, audit digest. `core/tool_exec.py`: voice/desktop `note_skill_load` hook. `core/text_turn_controller.py`: the GUI text loop writes both streams. `core/ui_connectivity.py`: read-only "Skill Routing Misses" card. | `python -m agent skills misses [--json] [--since 7d] [--prune]`; a `load_project_skill`/`load_skill` call emits `skill_load` and writes `skill_loads_<date>.jsonl`. Classification: `confirmed` (loaded == injected), `miss` (indexed but the router injected something else/nothing), `off_index` (not in the compiled index — another surface), `unrouted` (voice; usage only, never a verdict). Tests: `tests/test_agent_skill_audit.py`, `tests/test_agent_trace.py`, `tests/test_skill_misses_card.py`. |
| **M6** | **Build + query side done** | `tools/skills_index.py`: `cookbook_sha256`, `skill_embedding_text`, `build_embeddings`, `verify_embeddings`, `--build-embeddings`, `--embed-endpoint`, `--embed-model`, `--embeddings-out`; `--check` flags stale vectors. `agent/turn_context.py`: `embed=` injection, `skills_embed_enabled()`, cached table/query vectors. `agent/second_brain.py`: public `embed_query`. | `python tools/skills_index.py --build-embeddings` once a local server is up; `BRAHMA_SKILL_EMBED=1 python -m agent eval --category skill_choice --provider stub` to compare. T1 remains the contract with no table and no env var. |

**Not built on purpose.** T3 (model tie-break) stays a documented escalation, not
a code path: nothing in this repo has yet produced evidence that a model
tie-break beats R2's deterministic ambiguity flag. Adding it early would put a
per-turn model call behind the one part of the design whose value is being
*measured*, not guessed.

**Record cadence.** One record is written **per run/task**, at turn end, matching
the "check at end of task" framing — not per model turn. A run that makes six
model calls writes one record whose `turn` field names the last turn.


---

## 11. Questions the thread left open — and what we chose

The thread gives the shape of the loop and none of the semantics. These were the
seven answers that would have changed the design; the build answered them
locally, and each choice is recorded here so a later change has to argue with it.

| # | Question | What we chose | Where |
|---|---|---|---|
| 1 | Ground truth | Not the model's opinion: the record (injected skill + scores) joined to the run's evidence (stop reason, tools actually run, verification verdict), plus replay. | §4, `agent/skill_audit.py` |
| 2 | Checker cost | Deterministic rules, zero model calls. A flagged ambiguity may spend one, and nothing does today. | §5 |
| 3 | What a fix may touch | Routing metadata only (`triggers`, `description`, `domain`); never the body. | §6 |
| 4 | Negative feedback | No automatic demotion or quarantine: a miss is a candidate, and R3's `costly_skill` is the only demote-shaped fix. | §5, §6 |
| 5 | Wrong but harmless | Tracked as its own rule (R3 `unused`, verdict `bloat`) and as `unused_rate`, the context-bloat KPI. | §5, §9 |
| 6 | Neighbour regression | Frozen replay set: a trigger fix that steals a neighbour's case is red before it ships. | §8, §10 M4 |
| 7 | Smallest version | Not measured yet. R1 and R2 are the two that need no failure evidence, so they ship first. | §5 |

His "400x faster" stays unmeasured: a local scan over a few hundred rows is
already sub-millisecond here, so for us the out-of-context win is tokens, not
latency — which is what T2 is gated on.

---

## 12. Do not build

| Tempting | Why not |
|---|---|
| An LLM call at the end of every task | Destroys the economics that justified out-of-context routing; make it opt-in and labelled. |
| Auto-applying fixes | Contradicts `agent/teacher_policy.py:242`; bodies are prompt input, i.e. an injection surface. |
| Patching `config/skills_index.json` | Build artifact — `tools/skills_index.py --build` reverts it. Patch `skills/<n>/` and rebuild. |
| A second tool-filter path from `allowed-tools` | `agent/runtime.py` policy is the only execution boundary; a second filter is a hole. |
| "Zero wrong picks" as an acceptance criterion | Unachievable with a keyword/taxonomy scorer at 90+ skills. The property is *detected and corrected*, not *absent*. |
| An online JEV (or any hosted router) dependency | The local lane is sufficient and already proven here: a vector BLOB table, an in-memory cosine scan (`second_brain.py`), a cheap scorer. A hosted call adds an outage, a cost and a data-egress to a 90-row table. |
| Scoring skills with embeddings **before** measuring T1 | The 201 KB index and `0.25` cutoff are fast and explainable; T2 is gated on the R2 rate (§5.2) and lands as M6, not M1. |
| Copying cortex's bi-daily LLM evolution pass | That is their cost model, on their hardware, over their trajectory log. Our detector is a pure function; only a flagged ambiguity may spend one model call. |
| Unbounded retry | Three iterations, then park with evidence. A loop that can't converge is a bug source, not autonomy. |

---

## 13. One-line summary

His loop is **record → evidence → detect → diagnose → stage fix → replay**, with
the human owning only the promotion step. This repo now implements all six
stages locally: the record is written at turn end, `agent/skill_audit.py` applies
R1–R5 without a model, a fix is staged as a reviewable diff under
`staged_skills/_audit/`, and the `skill_choice` eval category freezes the cases
a trigger edit must not break. The router stays **local and open source**:
keyword scoring first (T1), a local embedding table over an injected `urllib`
embedder as a measured upgrade (T2, following `second_brain.py`), and at most
one model tie-break for a flagged ambiguity (T3, deliberately not built until
the measurements justify it).

---

## Appendix A — verification gate

Any implementation of this design must pass, per `AGENTS.md`:

```powershell
py -3.11 -m ruff check .
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
py -3.11 -m pytest tests -q -k agent
```

plus the record → audit → replay loop itself:

```powershell
python -m agent skills audit --since 7d --json
python -m agent skills audit --stage-fixes        # writes proposals, never edits skills/
python -m agent skills misses --since 7d --json   # model-initiated loads vs. the router
python -m agent eval --category skill_choice --provider stub
python tools/skills_index.py --check              # flags stale embedding rows if a table exists
```

The `agent/` stdlib-only rule holds: `agent/skill_audit.py`, `agent/turn_context.py`
and `agent/evals.py` import no PyQt, provider SDK or Windows COM module at module
scope, and the new tests run with only pytest installed.

### Python support envelope

The floor is **3.11**, enforced by `pyproject.toml` (`target-version = "py311"`).
The harness CI matrix runs 3.11, 3.12 and 3.13. **3.14 is untested**: the
`requirements.txt` pins are 3.11-verified, and `discord.py` still imports
`audioop` (removed in 3.13), so a full-app import can fail on 3.14 for reasons
unrelated to this loop. Treat 3.14 as "a writer may use it, CI arbitrates" and
keep new modules on `from __future__ import annotations` with no 3.14-only
syntax (no PEP 695 `type` aliases or class parameter lists) until the matrix
includes it.

