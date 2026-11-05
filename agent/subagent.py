"""A bounded child agent exposed as one tool call.

A single loop is enough for most work, but some tasks deserve their own
context: "find every caller of this function" should not fill the parent's
window with search output, and a focused child is less likely to wander. This
module is the *contract*; the child loop itself is built in
``AgentLoop._run_subagent`` because only the parent loop owns the model
transport.

Three invariants keep delegation safe:

* the child runs through the same :class:`~agent.runtime.AgentRuntime`, so the
  policy, approvals, workspace and cancel event are not re-implemented;
* the child's tool surface is a subset of the parent's and never contains
  ``run_subagent`` itself, so delegation cannot recurse; and
* the child gets no session row: its transcript is working memory, and only
  the report crosses back. The parent's durable transcript stays the source of
  truth.

The child's final answer is one tool result. It is a report, not a transcript:
the parent model sees what the child concluded, not every file it read.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .spec import ToolContext, ToolSpec, get as get_spec, register

#: The single-child tool's model-facing name. Excluded from every child's surface.
SUBAGENT_TOOL = "run_subagent"
#: The read-only fan-out batch tool's model-facing name. Also excluded from every child's surface (I-08).
SUBAGENT_BATCH_TOOL = "run_subagent_batch"
#: All delegation tools that must never appear on a child's surface.
DELEGATION_TOOLS: Tuple[str, ...] = (SUBAGENT_TOOL, SUBAGENT_BATCH_TOOL)

DEFAULT_SUBAGENT_TURNS = 8
#: A child is a bounded run, not a second agent that can spend the night. The
#: parent's ``max_turns`` is deliberately not inherited.
MAX_SUBAGENT_TURNS = 12

DEFAULT_BATCH_WORKERS = 4
MAX_BATCH_TASKS = 8

SUBAGENT_SYSTEM = (
    "You are a focused subagent working for another agent. Complete the task "
    "you were given with the tools provided, then reply with a concise report: "
    "what you did, what you found, and anything the parent must know. Nobody "
    "will answer a question, so do not ask one. Do not repeat work the parent "
    "has already done. If a tool is denied, say so and continue with what you "
    "can do. Your transcript is discarded; only your final report survives."
)

SUBAGENT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "description": (
                "A self-contained task for the subagent. It cannot see this "
                "conversation, so state the goal, the files or directories "
                "involved, and what a finished answer looks like."
            ),
        },
        "context": {
            "type": "string",
            "description": (
                "Optional facts the child needs (paths, commands already run, "
                "constraints). Keep it short; this is a briefing, not a dump."
            ),
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional tool names for the child, chosen from this agent's "
                "surface. Omit for the read-only tools. The subagent tool "
                "itself is never available to a child."
            ),
        },
        "max_turns": {
            "type": "integer",
            "description": (
                f"Optional child turn budget (default {DEFAULT_SUBAGENT_TURNS}, "
                f"capped at {MAX_SUBAGENT_TURNS})."
            ),
        },
    },
    "required": ["task"],
}


def clamp_turns(value: Any, default: int = DEFAULT_SUBAGENT_TURNS) -> int:
    """Coerce a model-supplied turn budget into a small positive integer."""
    try:
        turns = int(value)
    except (TypeError, ValueError):
        turns = int(default)
    return max(1, min(turns, MAX_SUBAGENT_TURNS))


def _item_name(item: Any) -> str:
    """Read a tool name from a ToolSpec, a name, or an OpenAI/Gemini schema."""
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        function = item.get("function")
        if isinstance(function, Mapping):
            return str(function.get("name") or "")
        return str(item.get("name") or "")
    return str(getattr(item, "name", "") or "")


def tool_names(items: Iterable[Any]) -> List[str]:
    """The usable names in a loop's tool surface, in order, without duplicates."""
    names: List[str] = []
    for item in items:
        name = _item_name(item)
        if name and name not in names:
            names.append(name)
    return names


def select_tools(
    parent_tools: Sequence[Any],
    requested: Optional[Iterable[Any]] = None,
) -> List[Any]:
    """Choose the child's surface from the parent's, never including recursion.

    ``requested`` is intersected with what the parent actually has: a model
    cannot grant a child a tool the parent was not given. With no request the
    surface is the parent's read-class tools plus its network-class tools (the
    native web search/fetch pair), which is the useful default for research and
    review tasks and cannot mutate the workspace. The parent surface has
    already been filtered by policy, so a ``reader`` parent never has network
    tools to pass on.
    """
    wanted = {
        str(name).strip()
        for name in (requested or [])
        if str(name).strip()
    }
    chosen: List[Any] = []
    seen: set = set()
    for item in parent_tools:
        name = _item_name(item)
        if not name or name in DELEGATION_TOOLS or name in seen:
            continue
        spec = item if isinstance(item, ToolSpec) else get_spec(name)
        if wanted:
            if name not in wanted:
                continue
        elif (
            spec is None
            or "desktop" in spec.tags
            or spec.tool_class not in ("read", "network")
        ):
            continue
        chosen.append(item)
        seen.add(name)
    return chosen


def select_batch_tools(
    parent_tools: Sequence[Any],
    requested: Optional[Iterable[Any]] = None,
) -> List[Any]:
    """Choose a strictly read-only, approval-free child surface for ``run_subagent_batch`` (O-2).

    Enforces the O-2 / I-08 fan-out contract:
    * excludes ``run_subagent`` and ``run_subagent_batch`` (no recursion);
    * restricts to ``spec.tool_class == "read"`` with ``spec.classifier is None``
      (no ``mcp``, ``native``, ``write``, ``exec``, ``network``, ``device``, or
      per-call classified tools);
    * excludes any tool with ``requires_approval=True``;
    * excludes desktop host tools (``"desktop" in spec.tags``).
    """
    wanted = {
        str(name).strip()
        for name in (requested or [])
        if str(name).strip()
    }
    chosen: List[Any] = []
    seen: set = set()
    for item in parent_tools:
        name = _item_name(item)
        if not name or name in DELEGATION_TOOLS or name in seen:
            continue
        spec = item if isinstance(item, ToolSpec) else get_spec(name)
        if (
            spec is None
            or spec.tool_class != "read"
            or spec.classifier is not None
            or spec.requires_approval
            or "desktop" in spec.tags
        ):
            continue
        if wanted and name not in wanted:
            continue
        chosen.append(item)
        seen.add(name)
    return chosen


SUBAGENT_BATCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Self-contained read-only research or review task.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional short briefing for this task.",
                    },
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional subset of read-only workspace tools.",
                    },
                    "max_turns": {
                        "type": "integer",
                        "description": (
                            f"Optional turn budget for this child (default {DEFAULT_SUBAGENT_TURNS}, "
                            f"capped at {MAX_SUBAGENT_TURNS})."
                        ),
                    },
                },
                "required": ["task"],
            },
            "description": (
                f"1 to {MAX_BATCH_TASKS} independent read-only tasks to run in parallel. "
                "Each child gets only read-class workspace tools and cannot mutate files, "
                "read outside the workspace, read protected files, or spawn subagents."
            ),
        },
        "context": {
            "type": "string",
            "description": "Optional shared briefing prepended to every task in the batch.",
        },
        "tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional read-only tool names applied across the batch.",
        },
        "max_turns": {
            "type": "integer",
            "description": (
                f"Optional turn budget per child (default {DEFAULT_SUBAGENT_TURNS}, "
                f"capped at {MAX_SUBAGENT_TURNS})."
            ),
        },
        "max_concurrency": {
            "type": "integer",
            "description": (
                f"Maximum children to run at once (default {DEFAULT_BATCH_WORKERS}, "
                f"capped at {MAX_BATCH_TASKS})."
            ),
        },
    },
    "required": ["tasks"],
}


def run_subagent(args: Dict[str, Any], context: ToolContext) -> str:
    """Tool handler: hand the request to the parent loop's child runner.

    The runner is injected through ``ToolContext.extra`` by
    ``AgentLoop._run_one``. Every other dispatch path (the Gemini live tools,
    a direct ``runtime.execute``) has no completer for a child loop, so it gets
    a clear refusal instead of a broken run.
    """
    runner = (context.extra or {}).get("subagent_runner")
    if not callable(runner):
        return (
            "Not run: run_subagent needs an agent loop with a model transport. "
            "This host cannot start a subagent; answer directly instead."
        )
    task = str(args.get("task") or "").strip()
    if not task:
        return "Not run: run_subagent needs a non-empty 'task'."
    return str(runner(dict(args or {})))


def clamp_batch_workers(value: Any, default: int = DEFAULT_BATCH_WORKERS) -> int:
    """Coerce a model-supplied batch concurrency into ``1..MAX_BATCH_TASKS``."""
    try:
        workers = int(value)
    except (TypeError, ValueError):
        workers = int(default)
    return max(1, min(workers, MAX_BATCH_TASKS))


def run_subagent_batch(args: Dict[str, Any], context: ToolContext) -> str:
    """Tool handler: hand a read-only fan-out request to the parent loop's batch runner."""
    runner = (context.extra or {}).get("subagent_batch_runner")
    if not callable(runner):
        return (
            "Not run: run_subagent_batch needs an agent loop with a model transport. "
            "This host cannot start a subagent batch; answer directly instead."
        )
    tasks = args.get("tasks")
    if not isinstance(tasks, (list, tuple)) or not tasks:
        return "Not run: run_subagent_batch needs a non-empty 'tasks' list."
    if len(tasks) > MAX_BATCH_TASKS:
        return (
            f"Not run: run_subagent_batch accepts at most {MAX_BATCH_TASKS} "
            f"tasks per call (got {len(tasks)})."
        )
    return str(runner(dict(args or {})))


register(
    ToolSpec(
        name=SUBAGENT_TOOL,
        description=(
            "Delegate a self-contained task to a bounded child agent with its "
            "own context and a filtered tool surface. Use it to keep large "
            "searches or reviews out of this conversation. The child cannot "
            "see this conversation, cannot ask questions, and cannot create "
            "another subagent; it returns a report. Its tool calls still go "
            "through the same permission policy."
        ),
        parameters=SUBAGENT_SCHEMA,
        tool_class="native",
        handler=run_subagent,
        tags=("agent",),
        max_output_chars=12_000,
    )
)

register(
    ToolSpec(
        name=SUBAGENT_BATCH_TOOL,
        description=(
            "Run 1 to 8 independent read-only subagent tasks in parallel and "
            "return their combined reports in order. Every child is strictly "
            "restricted to read-only tools inside the current workspace "
            "(no writes, commands, MCP tools, approvals, protected files, or "
            "outside-workspace paths) and cannot spawn further subagents."
        ),
        parameters=SUBAGENT_BATCH_SCHEMA,
        tool_class="native",
        handler=run_subagent_batch,
        tags=("agent",),
        max_output_chars=20_000,
    )
)
