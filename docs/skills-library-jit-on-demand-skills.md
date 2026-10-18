# Skills Library — JIT / On-Demand Skills

**Status**: Implemented (2026-10-17)
**Purpose**: A standalone, JIT-loadable skills library that can hold tens of
thousands of skills while almost none of them are resident in the model's
context.

---

## 1. Executive summary

Brahma already keeps its *tool* catalogue small with a just-in-time discovery
layer: only pinned tools plus the always-advertised meta-tools (`search_tools`,
`call_tool`, `get_skill_manual`, and — added with the library itself —
`search_skills`, `load_skill`) plus the top-k retrieved tools are ever sent to
Gemini, and everything else is reached *through* a tool rather than *as* a tool.

The Skills Library applies the same idea to a second, distinct kind of content:
**skills** — task playbooks and small capabilities kept deliberately apart from
`features/` and from the tool catalogue. Two shapes are supported:

- **Instructional skills** — pure context (a method, a house style, a
  checklist). They have no callable tool; their value is their instructions.
- **Executable skills** — they ship an `executor.py` and become ordinary tools
  once the agent has asked for them.

Nothing is injected automatically. A skill is *searched* and then *loaded* only
when the model decides it needs one, which is what makes a library of 10,000+
skills cost almost nothing per turn.

---

## 2. What was built

| File | Role |
|---|---|
| `core/skills_library.py` | The library: discovery, metadata index, lazy materialisation, LRU cache, search, publish, execute. |
| `core/tool_index.py` | Two new meta-tool declarations (`search_skills`, `load_skill`); `META_TOOLS` / `META_DECLARATIONS` extended. |
| `core/tool_bridge.py` | Wires the library into the session: `sync_skills()`, the two meta-tool handlers, a `get_skill_manual` fallback, and the management API. |
| `core/ui_connectivity.py` | Settings card: per-skill toggles, a master switch, and a live re-scan. |
| `core/ui_startup.py` | Defaults for `skills_library_enabled` / `disabled_skills`. |
| `core/prompt.txt` | The agent's *SKILLS LIBRARY* instructions, so the layer is reachable, not just advertised. |
| `requirements.txt` | PyYAML pinned (the bundled example skills are YAML). |
| `skills_library/` | Shipped built-in skills plus `README.md` documenting the format. |
| `tests/test_skills_library.py` | 24 unit tests (discovery, laziness, search, execution, publish, scale). |
| `tests/test_skills_bridge.py` | 19 integration tests (advertising, both meta-tools, executable publish/call, management surface). |

No change to `main.py` was required: `tool_bridge.configure()` applies the
library's settings, and `tool_bridge.prime()` calls `sync_skills()` before the
first catalog sync.

---

## 3. Storage layout (as built)

A skill is a **directory**:

```
<root>/<dir>/
    skill.yaml      # or skill.yml / skill.json / SKILL.md
    executor.py     # optional; required when executable: true
```

…or a **single file** (instructional only):

```
<root>/<name>.yaml | .yml | .json | .md
```

Two roots are scanned, in order, and a **later root overrides an earlier one**
on an id collision:

1. `<repo>/skills_library` — built-ins that ship with the codebase (`builtin`).
2. `<user data>/skills_library` — the editable/user layer (`user`),
   `%LOCALAPPDATA%\BrahmaAI\skills_library` on Windows.

> **Delta from the original plan.** The plan proposed
> `skills_library/builtin/…` and `…/skills_library/user/…` subdirectories. The
> two *roots* already encode that split, so the extra `builtin/` and `user/`
> levels were dropped: a skill lives directly under its root. Override order is
> what gives the user layer its "shadows the built-in" behaviour.

Markdown skills (`SKILL.md`, `<name>.md`) support optional YAML frontmatter;
with no frontmatter the whole body becomes `instructions`, so the simplest
possible skill is just prose. `README.md`, `index.md`, `changelog`, `license`
and `skill` stems are never treated as skills.

Everything is fail-soft: a malformed or hostile definition is skipped with a
warning and can never stop the library (or the app) from loading.

---

## 4. Data model

Two dataclasses, deliberately split by cost:

- **`SkillMeta`** — the always-resident catalogue row: `skill_id`, `name`,
  `description`, `category`, `tags`, `executable`, `active`, `source`, `path`,
  `version`, `mtime`, `size`, `checksum`. It has **no** `instructions`, `manual`
  or `parameters`. This is what a 10,000-skill scan retains.
- **`SkillSpec`** — the fully materialised skill, including `instructions`,
  `manual` and `parameters`. Returned by `SkillsLibrary.get()` and cached.

`SkillHit` (a ranked result) and `LibraryStats` (diagnostics) round it out.

> **Delta from the original plan.** The plan named the full-spec dataclass
> `SkillDefinition`. It is `SkillSpec` here, with `SkillMeta` split out so the
> resident catalogue is provably body-free (asserted in the tests).

### Parsing

`_read_definition()` reads the file **once** and returns `(mapping, checksum)`.
The checksum is taken from the same bytes that were parsed, so a large scan
never pays a second read per skill. YAML uses PyYAML (present, but treated as
optional: JSON and Markdown still work without it).

---

## 5. Lazy indexing and caching

- `SkillsLibrary()` construction reads nothing.
- `refresh()` scans every definition **once** to build the metadata catalogue and
  a lightweight inverted index (`token -> row indices`). It does **not** retain
  instructions or import executor modules (verified by
  `test_scan_does_not_import_executor_modules`).
- `get(skill_id)` materialises a full `SkillSpec` on demand and remembers it in
  an LRU cache (default 256 entries). Entries are invalidated by **checksum**,
  so an edit is picked up on the next `get` after a `refresh`.
- `execute_skill()` imports `executor.py` **only** when the skill is actually
  run; imported modules are cached separately.

**Why not reuse the Tool Index's RRF/embeddings for skills?** The tool index's
semantic backends build a dense `(n, 4096)` n-gram matrix; at 10,000 skills that
is ~160 MB. The library instead uses a weighted token-overlap scorer over a
prebuilt inverted index (name ×3.0, tags ×2.5, category ×2.0, description ×1.0,
plus exact-id and phrase boosts), which is memory-light and fast at that scale.
Executable skills still get the full tool-index machinery for free, because they
are published as ordinary tools (see §7).

---

## 6. Discovery: two meta-tools

Two declarations are added to `META_DECLARATIONS` (and therefore to
`META_TOOLS`, which the budget maths already accounts for):

| Tool | Behaviour |
|---|---|
| `search_skills(intent, limit=6, executable_only=false)` | Scores the metadata catalogue and returns matching ids, descriptions, category, and whether each is executable. Loads nothing. |
| `load_skill(skill_id)` | Materialises exactly one skill and returns its instructions (and manual, if any) as a tool result. For an executable skill it also publishes and indexes the underlying tool so `call_tool` can run it immediately. |

Both fail soft: an empty or disabled library returns a readable message, never
an exception.

`get_skill_manual(skill_id)` also falls back to the library, so any id
`search_skills` can return is readable by the existing manual tool. This keeps
one id space instead of two, without auto-publishing thousands of manuals.

> **Delta from the original plan.** The plan proposed discovering skills through
> the existing `search_tools` and retrieving instructions through
> `get_skill_manual` only. That works for *executable* skills (they are tools)
> but not for *instructional* ones, which have no tool to be discovered by — the
> exact case the library exists to serve. Hence the two dedicated meta-tools.
> Skills are therefore a peer discovery surface, not a rider on the tool one,
> which is also what "its own thing, with its own on-demand access" asked for.

---

## 7. Executable skills

- `SkillsLibrary.publish(registry)` registers each **available executable** skill
  as an external tool via `DynamicToolRegistry.register_external_tool()`.
  Instructional skills are never published.
- The default tool name is `skill_<sanitized id>`; an explicit `tool_name` in the
  definition overrides it. Registration cannot shadow a native feature: the
  registry lets filesystem/native skills win on collision.
- Published tools get `source="skill_library"`, so `tool_bridge.sync_registry()`
  indexes them like any other tool — discoverable via `search_tools`, runnable via
  `call_tool`, and subject to the same budget.
- `publish()` is idempotent and un-registers skills that were disabled or
  removed. It skips registries that cannot host external tools.
- `execute(**kwargs)` may be synchronous or async; the library awaits coroutines.
- A skill marked `executable: true` with no `executor.py` is downgraded to
  instructional (with a warning) rather than breaking anything.
- `load_skill` on an executable skill calls `lib.publish()` and upserts a single
  `ToolEntry`, so a skill added *after* the session connected is still runnable
  that turn — without a full registry re-enumeration.

---

## 8. Management surface

On the library: `refresh(force)`, `entries()`, `get()`, `search()`,
`enable()` / `disable()` / `set_disabled()`, `publish()`, `stats()`,
`manifest()`.

On the bridge (for the Settings UI): `sync_skills()`, `skills_catalog()`,
`disabled_skills()` / `set_disabled_skills()`, `set_skills_enabled()`, and a
`skills` block inside `snapshot()`.

**Settings card.** Settings → System & Connectivity → *Skills Library* lists every
skill grouped by category with an on/off checkbox and a master switch, next to
the tool list. Checking a skill off adds it to `disabled_skills` and re-syncs
live (no restart); an executable skill that is switched off is also un-published,
so its tool disappears too. **Refresh** forces a re-scan of the library folders,
which is how a newly dropped-in skill appears. The master switch is the same
"off" path the meta-tools report.

Settings read by `tool_bridge.configure()` from `app_settings.json`:

| Key | Default | Meaning |
|---|---|---|
| `skills_library_enabled` | `true` | Master switch. Off = both meta-tools report "switched off". |
| `disabled_skills` | `[]` | Skill ids that are hidden and un-published. |

A skill can also switch itself off in its definition with `active: false`.

---

## 9. Scaling to 10,000+ skills

| Cost | Scale behaviour |
|---|---|
| Resident memory | One small `SkillMeta` per skill — no bodies retained. |
| Startup | One small file read per definition, once, on first access. |
| Search | Weighted token overlap over a prebuilt inverted index — no dense matrices. |
| Per-turn context | Two extra declarations, constant regardless of library size. |
| Execution | `executor.py` imported only when that skill is actually run. |

`test_scale_thousands_of_skills_stay_searchable` builds hundreds of skills and
asserts the catalogue stays body-free while search still returns hits.

---

## 10. Tests

- **`tests/test_skills_library.py`** (24 tests): YAML / Markdown / frontmatter /
  JSON discovery, README and index exclusion, malformed-file tolerance,
  metadata-only catalogue, lazy executor import, checksum invalidation on edit,
  user-over-builtin override, `active: false`, disable/enable, ranking,
  exact-id boost, executable-only filter, sync/async execution, publish
  idempotence, `sanitize_identifier`, and a 400-skill scale check.
- **`tests/test_skills_bridge.py`** (19 tests): both meta-tools are advertised and
  recognised, an in-budget payload, `search_skills` (match / missing intent / no
  match / executable reporting / executable-only), `load_skill` (instructions /
  unknown-id suggestion / disabled / missing id), the `get_skill_manual`
  fallback, executable publish + `call_tool`, `sync_skills` publishing only
  executables, and the management surface (`skills_catalog`, disabled setter,
  master switch, snapshot).
- The full pre-existing suite (213 tests) and `accept_391.py` still pass.

---

## 11. Definition of done

- [x] `core/skills_library.py` implemented with the full API.
- [x] True JIT verified — bodies and executor modules load only on demand.
- [x] Executable skills discoverable via `search_skills`/`search_tools` and
      callable via `call_tool`.
- [x] Instructional skills retrievable via `load_skill` and `get_skill_manual`.
- [x] Thousands of skills supported with a metadata-only resident catalogue.
- [x] No breaking changes — the full pre-existing suite still passes.
- [x] Built-in example skills, README, and this design record.
