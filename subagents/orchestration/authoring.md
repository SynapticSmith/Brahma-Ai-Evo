# Agent Definition Authoring

<!-- Imported knowledge. Source: the `610ClaudeSubagents` corpus root guides
     (custom-agent-builder-guide.md, prompt-templates.md, tutorial.md,
     troubleshooting.md), reconciled with the hive's actual runtime
     (opencode subagent files). Structural guidance only; source metrics are
     UNVERIFIED -- see Provenance. -->

How to write one agent definition so it plugs into a topology cleanly: a tight trigger, a single owned skill, a bounded tool surface, and a legible output contract.

---

## Problem

A vague agent definition fails in ways its author never sees:

1. **Wrong agent fires** -- a description that reads like a topic, not a trigger, so the orchestrator routes the wrong task to it (or none).
2. **Tool overreach** -- the agent is handed destructive tools it never needs, so it improvises outside its lane.
3. **Undefined output** -- downstream agents cannot tell what the agent will hand back, so handoffs break.
4. **Persona over substance** -- pages of "you are a world-class genius" that consume context and change nothing about behavior.

## Solution

Author against a fixed contract with four parts.

### 1. Identity + trigger (frontmatter)

Keep frontmatter minimal and mechanical. In the hive runtime (opencode), an agent is a file at `.opencode/agent/<name>.md` (or under the project config dir) with:

```yaml
---
description: <when to use this agent, in one sentence, phrased as a trigger>
mode: subagent
tools:
  read: true
  grep: true
  glob: true
  bash: true
---
```

Rules:
- **`description` is a trigger, not a bio.** Write the condition under which the orchestrator should pick this agent ("Use when ... , not for ..."). This is the single most important field; a bad description breaks routing regardless of the body.
- **`mode: subagent`** for agents the `Task` tool should be able to invoke. Primary agents (like `god`) are separate.
- **Tool surface = least privilege.** Grant the minimum tool set the job needs. A read-only analyst gets no `write`/`edit`/`bash`. Fewer tools = fewer failure modes and a smaller permissions surface.
- **One agent, one primary skill.** If the description needs "and" between two unrelated jobs, split it.

### 2. Principle block (the operating stance)

State the agent's non-negotiables up front, briefly: truthfulness over agreement, cite evidence, name gaps instead of guessing, refuse out-of-lane work. Keep this to a short list -- it is a stance, not a persona essay. (The source's "Principle 0: Radical Candor" and "INTJ + Type 8" archetypes are a personality framework; the transferable part is *"be candid, evidence-driven, and honest about uncertainty,"* not the MBTI decoration.)

### 3. Scope + method (the body)

The body answers four questions, in this order:
1. **What I own** -- the exact deliverable and its boundary (what I do NOT do).
2. **How I work** -- the sequence of steps the agent should take.
3. **What good looks like** -- acceptance criteria the output must meet.
4. **When to stop / escalate** -- the exit condition and the escalation path (ask the operator or hand back to the coordinator) when blocked.

Prefer **task decomposition** and **integration-first** framing: define the artifact's interface (inputs it consumes, outputs it produces) before describing how to produce it. Downstream agents depend on the interface, not on the method.

### 4. Output contract

State the shape of what the agent returns (a file at path X with sections A/B/C; a JSON with fields ...; a diff). An agent with no output contract cannot be composed into a topology.

### Authoring anti-patterns

| Anti-pattern | Why it breaks | Fix |
|--------------|---------------|-----|
| Description is a topic ("Expert in X") | Routing can't tell when to fire | Phrase as a use-trigger |
| Persona prose dominates the body | Burns context, changes nothing | Cut to a short stance list |
| Tools granted "just in case" | Overreach, larger attack surface | Least privilege |
| Two unrelated skills in one agent | Fires for the wrong half | Split into two |
| No output contract | Handoffs break | Name the artifact + shape |
| No stop condition | Agent loops or over-produces | Define done + escalation |

### Testing an agent definition

Before promoting an agent into the live set: (1) give it one representative task and one off-scope task; it should perform the first and *refuse* the second; (2) confirm its output matches its stated contract; (3) confirm it stays inside its tool surface; (4) confirm its `description` makes the *router* choose it for the right task and skip it for wrong ones.

## Example

**Without this pattern:**

```markdown
---
name: helper
description: A helpful expert who can do many things with AI and code.
tools: Read, Write, Edit, Bash, WebFetch, ...
---
You are a world-class 10x genius with 30 years of experience...
```

**With this pattern:**

```markdown
---
description: Use to verify a produced artifact against its stated acceptance criteria and return a pass/fail with evidence. Not for producing the artifact.
mode: subagent
tools:
  read: true
  grep: true
  glob: true
---
## Stance
- Report what the artifact actually is, not what it claims. Cite file:line. Name gaps; never fill them by guessing.

## Scope
- Own: a verdict + evidence for one artifact.
- Do NOT: edit the artifact.

## Method / Output contract
1. Read the artifact and its acceptance criteria.
2. Check each criterion; record evidence.
3. Return: `{ verdict: pass|fail, criteria: [{id, met, evidence}], gaps: [...] }`.
```

## When to Use

- Creating any new subagent in `.opencode/agent/`.
- Reviewing/fixing an agent that fires for the wrong tasks or oversteps its lane.
- Adapting an external Claude Code agent def into this runtime.

## When NOT to Use

- For a one-off prompt you will never reuse (just write the prompt).
- For primary agents/coordinators (`god`) -- they need a broader contract, not a single skill.

## Related Patterns

- [Agent Orchestration Patterns](agent-orchestration-patterns.md) -- the topologies these definitions compose into
- [Templates](../templates/index.md) -- repo templates (concept/guide/reference) for consistent doc shape
- [OKF format](../references/okf-format.md) -- the structured-doc convention this module aligns with

## Provenance and Caveats

- **Source:** `610ClaudeSubagents` guides `custom-agent-builder-guide.md`, `prompt-templates.md`, `tutorial.md`, `troubleshooting.md`.
- **Faithfully imported:** the identity/trigger + scope + method + output-contract structure, least-privilege tooling, one-skill-per-agent, the anti-pattern table, and the four-step test.
- **Reconciled, not copied:** the source targets Claude Code `.claude/agents` frontmatter (`name`/`description`/`tools` as a comma list). The hive runs on opencode, so the frontmatter here uses opencode's shape (`description` + `mode` + per-tool enable map). Body structure is unchanged.
- **UNVERIFIED:** source claims about tiers/scorecards and "success-rate" uplift are not measured here; omitted.
