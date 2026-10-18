# Skills Library - JIT On-Demand Skills Architecture & Plan

**Status**: Planning Phase  
**Created**: 2026-10-17  
**Purpose**: Design for a standalone, JIT-loadable skills library supporting 10,000+ skills with on-demand access

## Executive Summary

The current codebase has:
- **Tool Index** (`core/tool_index.py`) - Hybrid search over tools with budgeted advertising (max 128 tools to Gemini). Uses meta-tools `search_tools`, `call_tool`, `get_skill_manual`.
- **SkillManual** concept - Per-group usage guides that are injected as JIT context only when a tool from that group is advertised.
- **DynamicToolRegistry** (`core/dynamic_registry.py`) - Loads executable "skills" from `features/` and external tools. These become executable tools callable via the normal tool execution path.
- **MCP Manager** (`core/mcp_manager.py`) - Ingests MCP server instructions/prompts/resources into SkillManuals.

The user wants a **standalone Skills Library** that is its own thing for "actual skills" - separate from `features/`, with its own on-demand access pattern. It should support both:
1. Injecting skill instructions/context on demand (like SkillManual does)
2. Making skills executable tools (like DynamicSkill does)

But critically - only load what is needed, JIT/on-request, supporting 10,000+ skills without bloating context.
## 1. Key Observations About Current Design

### Current Skill System

1. **DynamicSkill** (in dynamic_registry.py): Represents an executable skill. Loaded from:
- `features/` (codebase native features) - single `.py` files or directories with `skill.py` + `manifest.json`
- AppData `skills/` vault
- External tools registered via `register_external_tool()` (MCP tools)

2. **SkillManual** (in tool_index.py): Usage guide for a `skill_id` group. Rendered only when any tool from that group is advertised. Used for JIT instruction injection. MCP servers auto-publish manuals from their instructions/prompts/resources.

3. **Tool Index Groups**: Tools have `skill_id` for grouping; manuals keyed by `skill_id`. This means multiple tools can share one manual - efficient for large server APIs (e.g. 80 Synapse tools share 1 guide).

### The Gap

The user wants a dedicated "skills library" abstraction. Current "skills" in DynamicToolRegistry are conflated with "features/tools" and loaded from `features/`. The ask is for skills as a standalone concept with its own library, manageable, modifiable, supporting massive scale (10k+) with strict JIT loading.

## 2. Design Goals

1. **Standalone Skills Library** - Separate from `features/`. Own storage, lifecycle, API.
2. **Massive Scale (10,000+)** - Must not load all into memory; index/catalog only, load on demand.
3. **True JIT Loading** - Only load when explicitly requested (no automatic injection on every call). Optional call pattern only.
4. **Dual Nature** - Skills can be both: 
- Executable tools (can be invoked as functions)
- Instructional/contextual (can have manuals/instructions injected on demand)
5. **Manageable & Modifiable** - Add/edit/disable individual skills easily, without codebase changes.
6. **Searchable On-Demand** - Need a skill discovery mechanism (like `search_tools` but for skills). Should integrate with Tool Index or be parallel.
7. **Separate from Features** - Keep `features/` for built-in/core features, skills library for pluggable/extended skill definitions.

## 3. Proposed Architecture

### 3.1 New Module: `core/skills_library.py`

Create a dedicated Skills Library manager separate from DynamicToolRegistry.

**Key Classes**:

```python
@dataclass
class SkillDefinition:
    """Definition of a standalone skill in the library."""
    skill_id: str                    # Unique ID (e.g. "code-reviewer", "research-assistant")
    name: str
    description: str                 # For discovery/indexing
    version: str = "1.0.0"
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    active: bool = True
    executable: bool = False         # Whether it can be invoked as a tool
    tool_name: Optional[str] = None  # If exposed as executable tool
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "OBJECT", "properties": {}})
    instructions: str = ""           # Core instructions/context for this skill
    manual: str = ""                 # Extended usage guide/manual
    dependencies: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    path: Optional[Path] = None      # Source file/dir
    checksum: Optional[str] = None
    source: str = "library"          # "builtin" | "user" | "external"
```

**Core Classes**:
- `SkillsLibrary` - Main manager class with methods: `load()`, `get(skill_id)`, `search(query, **filters)`, `index_all()`, `reload()`, `enable/disable()`
- `SkillInstance` - Loaded/runtime instance (similar to DynamicSkill) for executable skills

### 3.2 Storage Layout

Proposed structure (split between builtin and user):

```
skills_library/                # Builtin skills shipped with repo
  ├── README.md
  ├── builtin/
  │   ├── reasoning/
  │   │   ├── skill.yaml       # Metadata + instructions/manual
  │   │   └── executor.py      # Optional executable implementation
  │   ├── writing/
  │   └── research/
  └── (examples)

# User-defined skills in user data dir
{user_data_dir}/skills_library/
  ├── user/
  │   ├── custom-skill/
  │   │   ├── skill.yaml
  │   │   └── executor.py
  │   └── ...
```

**Format**: YAML for `skill.yaml` (human-editable, supports multiline strings for instructions/manual). Executor as optional Python module for executable skills.

### 3.3 Integration with Tool Index

The Tool Index already has the right abstractions. Recommended approach:

1. **Skills Library registers with Tool Index**:
- For executable skills: Create ToolEntry with `source="skill_lib"`, `skill_id=skill.skill_id`, and register via ToolIndex. Also coordinate with DynamicToolRegistry for execution.
- For instructional-only skills: Publish as SkillManual via `tool_index.put_manual(SkillManual(skill_id=..., title=..., body=..., origin="skills_lib"))`

2. **Discovery via existing meta-tools**: By registering in Tool Index, `search_tools(intent)` will discover executable skills. Instructional skills can be discovered when searching (if their descriptions are indexed) and their manuals retrieved via existing `get_skill_manual(skill_id)`.

This reuses the battle-tested JIT discovery mechanism with no protocol changes.

### 3.4 Executable vs Instructional Skills

**Executable Skills** (`executable: true`): Have implementation that produces actions/results.
- Implementation in `executor.py` with `execute(**kwargs)` function (sync/async, matching DynamicSkill contract)
- Registered as ToolEntry in Tool Index → discoverable via `search_tools`, callable via `call_tool()`
- Loaded fully only when executed (or when first needed)

**Instructional Skills** (`executable: false`): Pure instructions/context.
- No executor needed. `instructions` and `manual` fields contain guidance.
- Published as SkillManual keyed by `skill_id` → loaded via `get_skill_manual(skill_id)` on demand
- Injected as JIT context only when explicitly requested (not auto-injected)

### 3.5 On-Demand Access Pattern (True JIT)

To support 10k+ skills without memory bloat:

1. **Metadata-Only Indexing**: On startup/library init, scan all skill.yaml files and build lightweight index (skill_id, name, description, tags, category, executable, active, source, path). Do NOT load full `instructions`, `manual`, or `executor.py` into memory.
2. **Lazy Loading**: Full SkillDefinition (including large instruction bodies) loaded only on:
- Explicit get: `skills_library.get(skill_id)` 
- When tool is executed (executable skill)
- When `get_skill_manual(skill_id)` is called for that skill
3. **LRU Caching**: Cache loaded full definitions (max N entries, e.g. 100-200) with TTL to avoid repeated file I/O while allowing edits to take effect reasonably.
4. **Search**: Search operates on metadata index only - fast, O(n) trivial even for 10k skills, or build simple in-memory search structure.
5. **No Preloading**: Never load all skills. This satisfies "only load what's needed, JIT/on-request".

## 4. Implementation Plan

### Phase 1: Core Foundation (S, ~1 day)
- [ ] Create `core/skills_library.py` with `SkillDefinition` dataclass and `SkillsLibrary` class
- [ ] Define YAML schema/loader with validation
- [ ] Create directory structure (`skills_library/builtin/`, user data dir)
- [ ] Implement metadata indexing (lazy load full content)
- [ ] Add LRU cache for loaded definitions
- [ ] Unit tests for basic CRUD/load/search

### Phase 2: Tool Index Integration (M, ~1-2 days)
- [ ] Bridge: On library init, register executable skills as ToolEntry in ToolIndex
- [ ] Bridge: Publish SkillManuals for instructional skills
- [ ] Ensure skills appear in search results via `search_tools`
- [ ] Respect disabled/active flags
- [ ] Tests for discovery through existing meta-tools

### Phase 3: Execution Integration (M, ~1 day)
- [ ] Create executor adapter for executable skills
- [ ] Integrate with ToolBridge/DynamicToolRegistry execution path
- [ ] Handle sync/async execute() like DynamicSkill
- [ ] Tests for execution

### Phase 4: Builtin Skills & Examples (S, ~0.5 day)
- [ ] Create example builtin skills (instructional + executable)
- [ ] Add README and schema docs

### Phase 5: Testing & Validation (M, ~1 day)
- [ ] Generate 10,000 synthetic skills (metadata only) to test scale
- [ ] Verify true JIT: full content not loaded until requested
- [ ] Verify context injection via `get_skill_manual()` works
- [ ] Backward compatibility - no breaking changes
- [ ] Integration tests

## 5. Recommended Design Decisions

1. **Standalone module, separate dirs** - Create `core/skills_library.py`, storage split between repo `skills_library/builtin/` and user data `skills_library/user/`.

2. **Reuse Tool Index** - Don't create parallel search; register skills in Tool Index. Leverages existing RRF fusion, MMR diversity, per-source caps. Existing meta-tools work unchanged.

3. **Lazy metadata indexing** - Index on init from file scan. Load full bodies only on demand. LRU cache prevents thrashing.

4. **YAML + optional executor.py** - Most editable for instructions/manuals. Python only where execution needed.

5. **Keep features/ separate** - Different purpose and lifecycle.

## 6. Definition of Done

- [ ] `core/skills_library.py` implemented with full API
- [ ] True JIT loading verified (content loaded only on demand)
- [ ] Executable skills discoverable via `search_tools` and callable via `call_tool`
- [ ] Instructional skills retrievable via `get_skill_manual(skill_id)`
- [ ] Supports 10k+ skills with minimal memory overhead
- [ ] No breaking changes to existing APIs
- [ ] Tests passing
- [ ] Example skills and documentation
