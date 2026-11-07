# Agent Orchestration Patterns

<!-- Imported knowledge. Source: the `610ClaudeSubagents` corpus root guides
     (agent-chaining-patterns.md, swarm-combinations.md, flowstrats.md).
     The source honestly mixes durable coordination patterns with marketing
     claims. Only the STRUCTURAL patterns are treated as knowledge here;
     numeric performance claims are marked UNVERIFIED and must not be cited
     as fact. See "Provenance and Caveats" at the end. -->

Reusable coordination patterns for running multiple agents on one goal, mapped to the hive's own orchestration primitives (task DAGs, decision gates, coordinator loops, threaded messages, `worker_done`/escalation waits).

---

## Problem

A single agent handling a multi-stage, multi-skill goal has three failure modes:

1. **Context bloat** -- one agent carries every skill and every artifact, so the working context is diluted and quality drops.
2. **No verification** -- one agent both produces and grades its own work, so errors pass unchecked.
3. **Serial latency** -- independent sub-tasks run one after another when they could run together.

Naively splitting the work into more agents trades these for two new ones: *coordination overhead* (who waits for whom) and *drift* (agents disagree, duplicate, or lose the thread).

## Solution

Pick a **coordination topology** to match the work shape, then apply the **mechanics** (memory, synthesis, verification, error handling) that keep the topology from drifting.

### Part A -- Topologies (work shapes)

| Topology | Shape | Use when | Hive primitive |
|----------|-------|----------|----------------|
| **Sequential / pipeline** | A → B → C, each stage consumes the last output | Stages are dependent and order is fixed | Task DAG with linear `depends_on` |
| **Iterative refinement** | A → critic → A' → critic … until pass | Quality is subjective and improvable in rounds | Loop with an exit decision gate |
| **Fork-join (parallel)** | Fan out N workers, barrier, then join | Sub-tasks are independent | Parallel DAG children + join node |
| **Producer-consumer** | Producer streams units; consumers drain | Work arrives over time / is unbounded | Coordinator + worker pool, threaded messages |
| **Conditional / routing** | One router picks the next branch | The right next step depends on an inspection | Decision gate routing to one DAG branch |
| **Hybrid** | Pipeline whose stages fan out and re-join | Real projects | Nested DAG: stages contain sub-DAGs |

### Part B -- Scale ladder (how many agents)

The source proposes a rough size ladder. Treat the *ranges* as heuristic, not theorem:

| Tier | Agents | Typical shape | Example use |
|------|--------|---------------|-------------|
| Duo | 2 | producer + verifier | research, dev pair |
| Trio | 3 | producer + 2 specialists | strategy, analytics |
| Squad | 4--6 | 1 coordinator + workers | content, feature build |
| Engine | 7--10 | multi-track with a synthesis lead | revenue ops, risk review |
| Program | 11--20 | hierarchical (leads over pods) | enterprise-scale programs |

Rule of thumb: **add an agent only when a distinct skill, a distinct artifact, or independent verification demands it** -- never to "look bigger." Each added agent adds coordination cost.

### Part C -- Mechanics (the rules that make any topology work)

1. **Shared memory over chatter.** Agents coordinate through a shared store of artifacts/findings, not by talking to each other directly. Hive: the run-scoped workspace blackboard and `memory.md`, not ad-hoc peer chat.
2. **Single synthesizer.** In fan-out topologies exactly one agent (the "queen"/lead) merges worker outputs into the final answer. N producers, one joiner.
3. **Independent verification.** The agent that produced an artifact is never the sole grader. Pair every producer with a verifier or a check.
4. **Quality gates between stages.** A stage's output must pass an explicit gate (schema, test, checklist) before the next stage consumes it.
5. **Partial-skill overlap, not total.** Adjacent agents should share ~20--30% competence so they can cover seams, but each owns a distinct primary skill. Total overlap = waste; zero overlap = gaps.
6. **Bounded rounds.** Refinement loops need an exit condition (pass, max rounds, or no-improvement-N) or they never terminate.
7. **Error handling ladder.** Per step, in order: retry with backoff → fall back to an alternate route → deliver partial results with the gap named. Never fail silently.
8. **Confidence surfaced, not hidden.** Each finding carries a confidence; low-confidence findings are escalated to a human gate rather than averaged into the answer.

### Part D -- Communication layers

The source describes three layers; they map cleanly onto hive surfaces:

| Layer (source) | Purpose | Hive equivalent |
|----------------|---------|-----------------|
| Persistent memory | Durable state across agents/runs | `memory.md`, workspace blackboard |
| Real-time messages | Handoffs, asks, replies | `inbox`/`outbox` (`request`/`reply`), threaded messages |
| Event bus | React to state changes | task status (`worker_done`), escalation waits, board updates |

## Example

**Without this pattern -- one agent, everything inline:**

```
god: "write the report"  -> agent loads every skill, does all 6 stages,
                            grades its own work, returns in one shot.
Result: one bloated context, unverified output, no way to see which stage failed.
```

**With this pattern -- hybrid DAG, 3 producers + 1 synthesizer + 1 verifier:**

```
plan (god) ── decision gate: scope == ok?
   ├─ P1 research   ─┐
   ├─ P2 outreach   ─┼─► join ─► SYNC (queen) ─► VERIFY ─► gate: same-page?
   └─ P3 validation ─┘                                    ok -> deliver
                                                          no -> back to SYNC
```

Each edge is a `worker_done` wait; the `gate: same-page?` is a decision gate; on `no` the loop is bounded to N rounds.

## When to Use

- The goal has 3+ distinct skills or 2+ independent workstreams.
- Quality depends on verification/refinement, not one-shot generation.
- Latency matters and sub-tasks are independent (fork-join).
- The work is long-running enough that a lost thread would be costly (shared memory pays off).

## When NOT to Use

- The task is genuinely single-skill and short -- a Duo adds pure overhead.
- Sub-tasks are tightly coupled and must share one live context (split only at clean seams).
- The coordination cost exceeds the work (a 20-agent "program" for a one-page task).
- No verification is possible and no human gate exists -- fan-out just multiplies uncertainty.

## Related Patterns

- [Agent Definition Authoring](../orchestration/agent-definition-authoring.md) -- how to write each node so the topology is legible
- [Patterns Index](../patterns/index.md) -- ICM's own 15 workspace conventions (handoffs, stage contracts, checkpoints) that these patterns echo
- [Methodology](../methodology/index.md) -- the context-routing theory behind "shared memory over chatter"

## Provenance and Caveats

- **Source:** `610ClaudeSubagents` root guides `agent-chaining-patterns.md`, `swarm-combinations.md`, `flowstrats.md`.
- **Faithfully imported:** topology taxonomy, scale ladder, mechanics (shared memory, single synthesizer, independent verification, quality gates, partial overlap, error ladder), and the three communication layers.
- **UNVERIFIED (do not cite as fact):** the source attaches specific success rates (~"95--98%"), speed multipliers (~"8--45x"), and token savings to these patterns. These are marketing figures with no methodology behind them and no measurement in our environment. The *patterns* are standard distributed-systems shapes (pipeline, fork-join, map-reduce, supervisor, blackboard, event-driven); their value here is structural, not numeric.
- **Not carried over:** references to `npx claude-flow@alpha` and its named agents -- that tool is not part of the hive stack.
