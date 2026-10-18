# Skills Library

A managed, file-backed library of agent skills, deliberately separate from
`features/` and from the tool catalogue. It is built to hold **tens of thousands**
of skills while the model's context carries almost none of them: only the skill
you ask for is loaded, and only when you ask for it.

Two discovery meta-tools are always advertised to the model:

| Tool | What it does |
|---|---|
| `search_skills(intent, limit)` | Ranks the library against an intent. Returns ids, descriptions, and whether each skill is executable. |
| `load_skill(skill_id)` | Materialises exactly one skill and injects its instructions/manual. For executable skills it also makes the callable tool reachable via `call_tool`. |

Nothing is injected automatically. A skill is searched for and loaded only when
the agent decides it needs one.

## Layout

Two roots are scanned, in order; a later root overrides an earlier one on an id
collision:

| Source | Path | Purpose |
|---|---|---|
| `builtin` | `<repo>/skills_library` | Skills that ship with the codebase. |
| `user` | `%LOCALAPPDATA%\BrahmaAI\skills_library` | Your skills — the editable layer. |

## Anatomy of a skill

**Instructional** — a directory with a definition file, or a single file:

```
skills_library/
    web-research/
        skill.yaml
    quick-summary.md          # single-file instructional skill
```

```yaml
id: web-research              # optional; defaults to the directory/file name
name: Web Research
description: Plan and run a rigorous web research pass.
category: research
tags: [web, research, sources]
active: true
executable: false
instructions: |
  When asked to research a topic:
  1. Restate the question as 2-4 concrete sub-questions.
  2. Search for primary sources first, then secondary.
  3. Record the source URL next to every claim.
manual: |
  Longer-form guidance, shown only when this skill is loaded.
```

**Executable** — a directory with an `executor.py`:

```
skills_library/
    word-count/
        skill.yaml
        executor.py
```

```yaml
id: word-count
name: Word Count
description: Count words, lines and characters in a block of text.
executable: true
# tool_name: word_count      # optional; defaults to skill_word-count
parameters:
  type: OBJECT
  properties:
    text:
      type: STRING
      description: The text to analyse.
  required: [text]
instructions: |
  Use this when the user asks for the size of a piece of text.
```

```python
# executor.py
def execute(text="", **kwargs):
    return {"words": len(text.split())}
```

Executable skills are published as tools named `skill_<id>` (unless `tool_name`
is set), so they are reachable through `search_tools` / `call_tool` and through
`load_skill`.

## Definition fields

| field | type | default | notes |
|---|---|---|---|
| `id` / `skill_id` | string | file/dir name | unique key; later root wins |
| `name` | string | `id` | display name |
| `description` | string | `""` | drives search ranking |
| `version` | string | `1.0.0` | informational |
| `category` | string | `general` | ranking hint |
| `tags` | list/CSV | `[]` | ranking hint |
| `active` | bool | `true` | `false` hides the skill entirely |
| `executable` | bool | `false` | requires `executor.py` |
| `tool_name` | string | `skill_<id>` | exposed tool name |
| `parameters` | object | `{}` | Gemini-style schema for executables |
| `instructions` | string | `""` | injected on `load_skill` |
| `manual` | string | `""` | longer guide, injected on `load_skill` |
| `dependencies` | list/CSV | `[]` | informational |
| `metadata` | object | `{}` | free-form |

Markdown (`SKILL.md` or `<name>.md`) supports optional YAML frontmatter; with no
frontmatter the whole body becomes `instructions`.
