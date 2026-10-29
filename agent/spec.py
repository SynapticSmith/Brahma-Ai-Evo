"""Self-describing tool specs for the agentic harness.

Before this module a tool existed in three unrelated places: a Gemini-style
declaration in ``main.py``'s ``TOOL_DECLARATIONS``, an ``elif`` branch in
``core/tool_exec.py``, and the implementation in ``actions/``. Adding a tool
meant editing two ~200KB files and hoping the string matched.

A :class:`ToolSpec` puts the three together: name, model-facing schema,
capability class (which the permission policy reasons about), and handler.
Registration is a decorator. The registry is the single source of truth for
the loop, the CLI, and the Gemini live path.

Schemas are authored in OpenAI's lowercase form and converted up for Gemini.
That direction is deliberate: the Gemini declaration format is the odd one
out, and converting on the way out (rather than on every model call) keeps
the stored spec canonical.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

#: Capability classes the permission policy reasons about. ``read`` and
#: ``write`` are filesystem, ``exec`` spawns processes, ``network`` leaves the
#: machine, ``device`` touches hardware/OS state, ``native`` is the catch-all
#: for the pre-existing action tools that predate this classification.
TOOL_CLASSES: Tuple[str, ...] = ("read", "write", "exec", "network", "device", "native")

#: Arguments that carry a filesystem path. The policy resolves these against
#: the workspace before deciding, so a tool cannot launder an escape through a
#: parameter name the policy does not look at.
PATH_ARG_KEYS: Tuple[str, ...] = (
    "path",
    "file",
    "filepath",
    "file_path",
    "filename",
    "cwd",
    "dir",
    "directory",
    "target",
    "source",
    "destination",
    "root",
    "workspace",
)


class ToolRegistrationError(ValueError):
    """Raised when a tool is registered twice or with an invalid class."""


@dataclass(frozen=True)
class ToolContext:
    """Everything a handler needs that is not in its arguments.

    Handlers never reach for globals: the workspace root, timeout and output
    cap are injected. That is what lets the same handler run inside the GUI,
    the CLI, and a test's ``tmp_path`` without a settings read.
    """

    workspace: Path
    timeout: float = 120.0
    max_output_chars: int = 20_000
    policy: Any = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def resolve_path(self, raw: Optional[str]) -> Path:
        """Resolve ``raw`` against the workspace root.

        ``None``/``""`` means the workspace itself. ``~`` is expanded. The
        result may sit outside the workspace; containment is the policy's
        decision, not this method's, so that "outside" can be *asked* about
        rather than silently rewritten.
        """
        if raw is None or str(raw).strip() == "":
            return self.workspace
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        return candidate.resolve()

    def display_path(self, path: Path) -> str:
        """Relative form when inside the workspace, absolute otherwise."""
        try:
            return str(path.relative_to(self.workspace))
        except ValueError:
            return str(path)


Handler = Callable[[Dict[str, Any], ToolContext], str]


def _uppercase_types(node: Any) -> Any:
    """Recursively uppercase JSON-schema ``type`` values for Gemini."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "type" and isinstance(value, str):
                out[key] = value.upper()
            elif key == "properties" and isinstance(value, dict):
                out[key] = {k: _uppercase_types(v) for k, v in value.items()}
            elif key in ("items", "additionalProperties"):
                out[key] = _uppercase_types(value)
            else:
                out[key] = _uppercase_types(value) if isinstance(value, (dict, list)) else value
        return out
    if isinstance(node, list):
        return [_uppercase_types(item) for item in node]
    return node


def lowercase_schema(node: Any) -> Any:
    """Recursively lowercase JSON-schema ``type`` values.

    The inverse of :func:`_uppercase_types`. A Gemini-style declaration that
    reaches an OpenAI-shaped surface has to come down to ``object``/``string``/
    ``integer`` or the endpoint rejects (or silently ignores) the tool.
    """
    if isinstance(node, dict):
        return {
            key: (
                value.lower()
                if key == "type" and isinstance(value, str)
                else lowercase_schema(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [lowercase_schema(item) for item in node]
    return node


@dataclass(frozen=True)
class ToolSpec:
    """One callable capability, described once and dispatched everywhere."""

    name: str
    description: str
    parameters: Dict[str, Any]
    tool_class: str
    handler: Handler
    tags: Tuple[str, ...] = ()
    #: Ask even when the class default is ``allow``. Used for genuinely
    #: irreversible one-offs that are not otherwise classifiable.
    requires_approval: bool = False
    max_output_chars: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ToolRegistrationError(f"invalid tool name: {self.name!r}")
        if self.tool_class not in TOOL_CLASSES:
            raise ToolRegistrationError(
                f"tool {self.name!r}: unknown class {self.tool_class!r} "
                f"(expected one of {TOOL_CLASSES})"
            )

    # -- schema shapes -----------------------------------------------------

    def openai(self) -> Dict[str, Any]:
        """The ``tools=[...]`` entry for an OpenAI-compatible endpoint."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def gemini(self) -> Dict[str, Any]:
        """The ``function_declarations=[...]`` entry for Gemini Live."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _uppercase_types(self.parameters),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Serializable metadata (no handler) for catalogues and docs."""
        return {
            "name": self.name,
            "description": self.description,
            "tool_class": self.tool_class,
            "parameters": self.parameters,
            "tags": list(self.tags),
            "requires_approval": self.requires_approval,
        }


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_REGISTRY: Dict[str, ToolSpec] = {}


def register(spec: ToolSpec, *, replace: bool = False) -> ToolSpec:
    """Add ``spec`` to the process-wide registry.

    ``replace=True`` exists for tests and for hot-reload tooling; production
    registration is intentionally strict so a duplicate name fails loudly
    instead of silently shadowing an existing capability.
    """
    existing = _REGISTRY.get(spec.name)
    if existing is not None and not replace and existing is not spec:
        raise ToolRegistrationError(f"tool {spec.name!r} is already registered")
    _REGISTRY[spec.name] = spec
    return spec


def tool(
    name: str,
    description: str,
    parameters: Dict[str, Any],
    *,
    tool_class: str = "native",
    tags: Iterable[str] = (),
    requires_approval: bool = False,
    max_output_chars: Optional[int] = None,
) -> Callable[[Handler], Handler]:
    """Decorator form of :func:`register`."""

    def decorator(handler: Handler) -> Handler:
        register(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters,
                tool_class=tool_class,
                handler=handler,
                tags=tuple(tags),
                requires_approval=requires_approval,
                max_output_chars=max_output_chars,
            )
        )
        return handler

    return decorator


def get(name: str) -> Optional[ToolSpec]:
    return _REGISTRY.get(name)


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def all_specs() -> List[ToolSpec]:
    """Registered tools, stable-sorted by name for reproducible prompts."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def openai_tools(names: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    specs = all_specs() if names is None else [s for s in all_specs() if s.name in set(names)]
    return [spec.openai() for spec in specs]


def gemini_declarations(names: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    specs = all_specs() if names is None else [s for s in all_specs() if s.name in set(names)]
    return [spec.gemini() for spec in specs]


def execute(spec: ToolSpec, args: Dict[str, Any], context: ToolContext) -> str:
    """Run one handler, converting any failure into a model-readable string.

    A raised exception must never abort the agent loop: the model can often
    recover from "FileNotFoundError: ..." but has no way to observe a Python
    traceback that unwound the whole turn.
    """
    try:
        result = spec.handler(dict(args or {}), context)
    except TypeError as exc:
        return f"Tool '{spec.name}' rejected its arguments: {exc}"
    except Exception as exc:  # noqa: BLE001 - deliberate: tool boundary
        return f"Tool '{spec.name}' failed: {type(exc).__name__}: {exc}"
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    cap = spec.max_output_chars or context.max_output_chars
    if cap and len(text) > cap:
        head = cap - 1200
        text = (
            text[:head]
            + f"\n... [{len(text) - cap + 1200} chars truncated; narrow the request] ...\n"
            + text[-1100:]
        )
    return text
